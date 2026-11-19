"""Fill a throwaway InvenTree and Bambuddy with made-up data, for screenshots.

    python dev/seed_demo.py --token inv-... --bambuddy-db /path/to/bambuddy.db

Creates three printed parts of an "Outdoor Sensor Node", links them to sliced
files in Bambuddy and leaves print runs in different states. No printer is
attached, so the state of the queue items is written to Bambuddy's SQLite
database directly. Never run this against a real system.
"""

from __future__ import annotations

import argparse
import pathlib
import sqlite3
import sys
import tempfile

import requests

sys.path.insert(0, str(pathlib.Path(__file__).parent))

from make_sliced_3mf import build as make_file  # noqa: E402

FILES = [
    # filename, model code, seconds, grams, filament, object name
    ('sensor-insert_x6_P1S_PETG.gcode.3mf', 'C12', 6300, 58.4, 'PETG', 'insert'),
    ('outer-cap_x2_P1S_ASA.gcode.3mf', 'C12', 9900, 96.0, 'ASA', 'cap'),
    ('cable-clip_x24_P1S_PETG.gcode.3mf', 'C12', 4200, 31.5, 'PETG', 'clip'),
]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--inventree', default='http://localhost:8100')
    parser.add_argument('--token', required=True)
    parser.add_argument('--bambuddy', default='http://127.0.0.1:8000')
    parser.add_argument('--bambuddy-db', required=True)
    parser.add_argument('--bambuddy-key', default='', help='API key, if Bambuddy has authentication on')
    args = parser.parse_args()

    headers = {'Authorization': f'Token {args.token}'}
    plugin = f'{args.inventree}/plugin/bambuddy'

    def it(method, path, data=None, base=args.inventree):
        response = requests.request(method, f'{base}{path}', headers=headers, json=data, timeout=60)
        response.raise_for_status()
        return response.json() if response.content else None

    def bb(method, path, **kwargs):
        key = {'X-API-Key': args.bambuddy_key} if args.bambuddy_key else {}
        response = requests.request(
            method, f'{args.bambuddy}/api/v1{path}', headers=key, timeout=60, **kwargs
        )
        response.raise_for_status()
        return response.json() if response.content else None

    def set_items(states):
        connection = sqlite3.connect(args.bambuddy_db, timeout=30)

        for item_id, state in states.items():
            connection.execute(
                'update print_queue set status = ?, manual_start = 0, '
                "started_at = coalesce(started_at, datetime('now', '-2 hours')), "
                "completed_at = case when ? in ('completed', 'failed') then datetime('now') else null end, "
                'printer_id = coalesce(printer_id, (select min(id) from printers)) '
                'where id = ?',
                (state, state, item_id),
            )

        connection.commit()
        connection.close()

    # Bambuddy: a folder with the sliced files

    folder = bb('POST', '/library/folders', json={'name': 'Sensor Node production'})
    file_ids = {}

    with tempfile.TemporaryDirectory() as directory:
        for name, model, seconds, grams, filament, label in FILES:
            path = pathlib.Path(directory) / name
            make_file(str(path), model, seconds, grams, filament, label)

            with open(path, 'rb') as handle:
                uploaded = bb(
                    'POST',
                    f"/library/files/?folder_id={folder['id']}",
                    files={'file': (name, handle)},
                )

            file_ids[label] = uploaded['id']

    # InvenTree: the parts and the product they go into

    category = it('POST', '/api/part/category/', {'name': '3D printed parts'})
    location = it('POST', '/api/stock/location/', {'name': 'Printed parts'})

    def part(name, ipn, minimum=0, **extra):
        return it(
            'POST',
            '/api/part/',
            {
                'name': name,
                'IPN': ipn,
                'description': 'Demo data',
                'category': category['pk'],
                'component': True,
                'purchaseable': False,
                'minimum_stock': minimum,
                'default_location': location['pk'],
                **extra,
            },
        )

    insert = part('Sensor insert', 'DEMO-PRT-001', minimum=30)
    cap = part('Outer cap', 'DEMO-PRT-002')
    clip = part('Cable clip', 'DEMO-PRT-003', minimum=100)
    node = part('Outdoor Sensor Node', 'DEMO-NODE-01', assembly=True, component=False)

    for sub, quantity in ((insert, 1), (cap, 1), (clip, 4)):
        it('POST', '/api/bom/', {'part': node['pk'], 'sub_part': sub['pk'], 'quantity': quantity})

    for key, value in (('AUTO_LOW_STOCK', True), ('AUTO_BUILD_DEMAND', True)):
        it('PATCH', f'/api/plugins/bambuddy/settings/{key}/', {'value': value})

    it('PUT', f"/part/{insert['pk']}/", {'file_id': file_ids['insert'], 'parts_per_plate': 6, 'auto': True, 'restock_to': 60}, base=plugin)
    it('PUT', f"/part/{cap['pk']}/", {'file_id': file_ids['cap'], 'parts_per_plate': 2, 'auto': True}, base=plugin)
    it('PUT', f"/part/{clip['pk']}/", {'file_id': file_ids['clip'], 'parts_per_plate': 24, 'auto': False}, base=plugin)

    # Some stock, so that the numbers are not all zero
    it('POST', '/api/stock/', {'part': insert['pk'], 'quantity': 14, 'location': location['pk']})
    it('POST', '/api/stock/', {'part': clip['pk'], 'quantity': 130, 'location': location['pk']})

    # A finished run of clips, and one which is being counted
    run = it('POST', f"/part/{clip['pk']}/print/", {'plates': 2}, base=plugin)['run']
    set_items({item['id']: 'completed' for item in run['items']})
    it('POST', f"/run/{run['build']}/confirm/", {'good': 48}, base=plugin)

    run = it('POST', f"/part/{clip['pk']}/print/", {'plates': 3}, base=plugin)['run']
    ids = [item['id'] for item in run['items']]
    it('POST', f"/run/{run['build']}/release/", {}, base=plugin)
    set_items({ids[0]: 'completed', ids[1]: 'completed', ids[2]: 'printing'})

    # Inserts are below their minimum: the automatic check queues them
    print(it('POST', '/check/', {}, base=plugin))

    # A build order for ten sensor nodes needs ten caps, and there are none
    build = it('POST', '/api/build/', {'part': node['pk'], 'quantity': 10})
    it('POST', f"/api/build/{build['pk']}/issue/", {})

    print('Seeded. The cap run appears once the background worker has handled the build order.')


if __name__ == '__main__':
    main()
