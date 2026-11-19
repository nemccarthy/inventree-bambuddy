"""End-to-end check of the plugin against a live InvenTree and a live Bambuddy.

For a throwaway test setup only: it creates parts, build orders and stock in
InvenTree, queues prints in Bambuddy, and - because no printer is attached -
marks queue items as printed by writing to Bambuddy's SQLite database.

    python dev/integration_check.py \
        --inventree http://127.0.0.1:8100 --token inv-... \
        --bambuddy http://127.0.0.1:8000 --bambuddy-db /path/to/bambuddy.db \
        --files dir-with-the-two-test-files

The Bambuddy instance needs two sliced files in its library (see
make_sliced_3mf.py) and at least one printer of the model they were sliced for.
"""

from __future__ import annotations

import argparse
import math
import sqlite3
import sys
import time

import requests

PASSED = 0
FAILED = []


def check(name: str, condition, detail=''):
    global PASSED

    if condition:
        PASSED += 1
        print(f'  ok    {name}')
    else:
        FAILED.append(name)
        print(f'  FAIL  {name}  {detail}')


class Api:
    def __init__(self, base: str, headers: dict):
        self.base = base.rstrip('/')
        self.headers = headers

    def call(self, method: str, path: str, expect=None, **kwargs):
        response = requests.request(
            method, f'{self.base}{path}', headers=self.headers, timeout=60, **kwargs
        )

        if expect is not None and response.status_code != expect:
            raise AssertionError(
                f'{method} {path}: {response.status_code} {response.text[:400]}'
            )

        try:
            return response.status_code, response.json()
        except ValueError:
            return response.status_code, None

    def get(self, path, **kwargs):
        return self.call('GET', path, **kwargs)[1]

    def post(self, path, data=None, **kwargs):
        return self.call('POST', path, json=data or {}, **kwargs)[1]

    def put(self, path, data=None, **kwargs):
        return self.call('PUT', path, json=data or {}, **kwargs)[1]

    def patch(self, path, data=None, **kwargs):
        return self.call('PATCH', path, json=data or {}, **kwargs)[1]


def set_items(db: str, states: dict):
    """Pretend the printers did something: set the state of queue items."""
    connection = sqlite3.connect(db, timeout=30)

    for item_id, state in states.items():
        connection.execute(
            'update print_queue set status = ?, manual_start = 0, '
            "started_at = coalesce(started_at, datetime('now')), "
            "completed_at = case when ? in ('completed', 'failed') then datetime('now') else completed_at end "
            'where id = ?',
            (state, state, item_id),
        )

    connection.commit()
    connection.close()


def wait_for(condition, seconds=60, step=2):
    deadline = time.time() + seconds

    while time.time() < deadline:
        value = condition()

        if value:
            return value

        time.sleep(step)

    return None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--inventree', default='http://127.0.0.1:8100')
    parser.add_argument('--token', required=True)
    parser.add_argument('--bambuddy', default='http://127.0.0.1:8000')
    parser.add_argument('--bambuddy-db', required=True)
    parser.add_argument('--bambuddy-key', default='', help='API key, if Bambuddy has authentication on')
    args = parser.parse_args()

    it = Api(args.inventree, {'Authorization': f'Token {args.token}'})
    bb = Api(f'{args.bambuddy}/api/v1', {'X-API-Key': args.bambuddy_key} if args.bambuddy_key else {})
    plugin = '/plugin/bambuddy'
    tag = str(int(time.time()))[-6:]

    def setting(key, value):
        it.patch(f'/api/plugins/bambuddy/settings/{key}/', {'value': value}, expect=200)

    setting('BAMBUDDY_URL', args.bambuddy)
    setting('BAMBUDDY_API_KEY', args.bambuddy_key)
    setting('AUTO_LOW_STOCK', False)
    setting('AUTO_BUILD_DEMAND', False)

    # ------------------------------------------------------------------
    print('Setup')

    files = {f['filename']: f for f in bb.get('/library/files/?include_root=false')}
    insert_file = next(f for name, f in files.items() if name.startswith('insert'))
    cap_file = next(f for name, f in files.items() if name.startswith('cap'))

    category = it.post('/api/part/category/', {'name': f'Printed {tag}'}, expect=201)
    location = it.post('/api/stock/location/', {'name': f'Printed parts {tag}'}, expect=201)

    def new_part(name, **extra):
        return it.post(
            '/api/part/',
            {
                'name': f'{name} {tag}',
                'description': 'Integration check',
                'category': category['pk'],
                'component': True,
                'purchaseable': False,
                **extra,
            },
            expect=201,
        )

    insert = new_part('Insert', default_location=location['pk'])
    cap = new_part('Cap')
    node = new_part('Sensor node', assembly=True, component=False)

    for sub, quantity in ((insert, 2), (cap, 1)):
        it.post(
            '/api/bom/',
            {'part': node['pk'], 'sub_part': sub['pk'], 'quantity': quantity},
            expect=201,
        )

    config = it.get(f'{plugin}/config/', expect=200)
    check('Bambuddy is reachable', config['bambuddy']['ok'], config['bambuddy'])
    check('printers are listed', len(config['bambuddy']['printers']) >= 1)
    check(
        'events and schedules are on',
        config['settings']['events_enabled'] and config['settings']['schedule_enabled'],
    )

    listed = it.get(f'{plugin}/files/', expect=200)['files']
    check('sliced files are listed', {insert_file['id'], cap_file['id']} <= {f['id'] for f in listed})

    detail = it.get(f"{plugin}/files/{insert_file['id']}/", expect=200)
    check('file detail has its plate', len(detail['plates']) == 1 and detail['sliced'])

    # ------------------------------------------------------------------
    print('Linking')

    part_url = f"{plugin}/part/{insert['pk']}/"

    code, body = it.call('PUT', part_url, json={'file_id': insert_file['id']})
    check('link without parts per plate is refused', code == 400 and 'parts_per_plate' in body.get('errors', {}), body)

    code, body = it.call('PUT', part_url, json={'file_id': 999999, 'parts_per_plate': 4})
    check('link to a missing file is refused', code == 400 and 'file_id' in body.get('errors', {}), body)

    code, body = it.call('PUT', part_url, json={'file_id': insert_file['id'], 'parts_per_plate': 4, 'printer_id': 9999})
    check('link to a missing printer is refused', code == 400 and 'printer_id' in body.get('errors', {}), body)

    code, body = it.call('POST', f'{part_url}print/', json={'plates': 1})
    check('printing an unlinked part is refused', code == 400, body)

    linked = it.put(part_url, {'file_id': insert_file['id'], 'parts_per_plate': 4}, expect=200)
    check('link is stored', linked['link']['file_id'] == insert_file['id'] and linked['link']['parts_per_plate'] == 4)
    check('target defaults to the model the file was sliced for', linked['link']['target_model'] == 'P1S', linked['link'])
    check('part was made an assembly', linked['made_assembly'] and linked['part']['assembly'])
    check('print time and filament are copied', linked['link']['print_time_seconds'] == 5400 and linked['link']['filament_grams'] == 42.5, linked['link'])

    listed = it.get(f'{plugin}/files/', expect=200)['files']
    used = next(f for f in listed if f['id'] == insert_file['id'])['linked_to']
    check('file list shows which part uses a file', [u['pk'] for u in used] == [insert['pk']], used)

    # ------------------------------------------------------------------
    print('Manual print run')

    code, body = it.call('POST', f'{part_url}print/', json={'plates': 0})
    check('zero plates is refused', code == 400, body)

    created = it.post(f'{part_url}print/', {'quantity': 10}, expect=201)
    run = created['run']
    build_pk = run['build']
    check('10 parts at 4 a plate is 3 plates', len(run['items']) == 3, run['items'])
    check('build order is for 12', run['quantity'] == 12)
    check('run is staged', run['summary']['state'] == 'staged', run['summary'])

    build = it.get(f'/api/build/{build_pk}/', expect=200)
    check('build order is in production', build['status'] == 20, build['status'])
    check('build order goes to the default location of the part', build['destination'] == location['pk'], build['destination'])

    queue = {item['id']: item for item in bb.get('/queue/')}
    ours = [queue[item['id']] for item in run['items']]
    check('items are staged in Bambuddy', all(i['status'] == 'pending' and i['manual_start'] for i in ours))
    check('items target any P1S', all(i['target_model'] == 'P1S' and i['printer_id'] is None for i in ours))
    check('items are for the linked file', all(i['library_file_id'] == insert_file['id'] for i in ours))

    batch = bb.get(f"/queue/batches/{run['batch_id']}")
    check('batch carries the build order reference', batch['external_source'] == 'inventree' and batch['external_ref'].startswith(run['reference']) and batch['name'].startswith(run['reference']), batch['external_ref'])

    data = it.get(part_url, expect=200)
    check('open run counts as incoming', data['numbers']['incoming'] == 12 and data['numbers']['stock'] == 0, data['numbers'])

    code, body = it.call('POST', f'{plugin}/run/{build_pk}/confirm/', json={'good': 4})
    check('counting before anything is printed is refused', code == 400, body)

    released = it.post(f'{plugin}/run/{build_pk}/release/', expect=200)
    check('all three plates are released', len(released['result']['released']) == 3, released['result'])
    check('run is queued', released['run']['summary']['state'] == 'queued', released['run']['summary'])
    queue = {item['id']: item for item in bb.get('/queue/')}
    check('Bambuddy no longer holds them back', all(not queue[i['id']]['manual_start'] for i in run['items']))

    ids = [item['id'] for item in run['items']]
    set_items(args.bambuddy_db, {ids[0]: 'printing'})
    state = it.get(f'{plugin}/run/{build_pk}/', expect=200)['run']['summary']
    check('run is printing', state['state'] == 'printing', state)

    set_items(args.bambuddy_db, {ids[0]: 'completed', ids[1]: 'completed', ids[2]: 'failed'})
    state = it.get(f'{plugin}/run/{build_pk}/', expect=200)['run']['summary']
    check('two plates wait to be counted', state['state'] == 'printed' and state['awaiting_plates'] == 2 and state['awaiting_quantity'] == 8, state)

    code, body = it.call('POST', f'{plugin}/run/{build_pk}/confirm/', json={'good': 9})
    check('more good parts than were printed is refused', code == 400, body)

    counted = it.post(f'{plugin}/run/{build_pk}/confirm/', {'good': 7}, expect=200)
    state = counted['run']['summary']
    check('7 good parts are booked', counted['run']['completed'] == 7 and counted['numbers']['stock'] == 7, counted['numbers'])
    check('run is short by 5, two plates', state['state'] == 'short' and state['remaining'] == 5 and state['shortfall_plates'] == 2, state)

    stock = it.get(f"/api/stock/?part={insert['pk']}&in_stock=true", expect=200)
    stock = stock.get('results', stock) if isinstance(stock, dict) else stock
    check('stock item is in the destination location', len(stock) == 1 and stock[0]['quantity'] == 7 and stock[0]['location'] == location['pk'], stock)
    check('stock item belongs to the build order', stock[0]['build'] == build_pk)

    more = it.post(f'{plugin}/run/{build_pk}/reprint/', {'plates': 2}, expect=200)
    check('two more plates are staged', more['run']['summary']['state'] == 'staged' and len(more['run']['items']) == 5, more['run']['summary'])
    new_ids = [item['id'] for item in more['run']['items'] if item['id'] not in ids]
    queue = {item['id']: item for item in bb.get('/queue/')}
    check('extra plates join the same batch', all(queue[i]['batch_id'] == run['batch_id'] for i in new_ids), [queue[i]['batch_id'] for i in new_ids])

    set_items(args.bambuddy_db, {new_ids[0]: 'completed', new_ids[1]: 'completed'})
    counted = it.post(f'{plugin}/run/{build_pk}/confirm/', {'good': 8}, expect=200)
    check('run closes itself once the quantity is reached', counted['result']['closed'] and counted['run']['summary']['state'] == 'done', counted['run']['summary'])

    build = wait_for(lambda: (b := it.get(f'/api/build/{build_pk}/')) and b['status'] == 40 and b)
    check('build order is complete', bool(build), it.get(f'/api/build/{build_pk}/')['status'])
    data = it.get(part_url, expect=200)
    check('15 in stock, nothing incoming', data['numbers']['stock'] == 15 and data['numbers']['incoming'] == 0, data['numbers'])
    check('finished run is in the history', [r['build'] for r in data['history']] == [build_pk] and not data['runs'])

    # ------------------------------------------------------------------
    print('Closing short and cancelling')

    run = it.post(f'{part_url}print/', {'plates': 2}, expect=201)['run']
    ids = [item['id'] for item in run['items']]
    set_items(args.bambuddy_db, {ids[0]: 'completed', ids[1]: 'failed'})
    counted = it.post(f"{plugin}/run/{run['build']}/confirm/", {'good': 3}, expect=200)
    check('short run can be closed', counted['run']['summary']['can_close'], counted['run']['summary'])
    closed = it.post(f"{plugin}/run/{run['build']}/close/", expect=200)
    check('closed short', closed['run']['summary']['state'] == 'done' and closed['numbers']['stock'] == 18, closed['numbers'])
    wait_for(lambda: it.get(f"/api/build/{run['build']}/")['status'] == 40)

    run = it.post(f'{part_url}print/', {'plates': 2}, expect=201)['run']
    cancelled = it.post(f"{plugin}/run/{run['build']}/cancel/", expect=200)
    check('cancel takes both plates out of the queue', len(cancelled['result']['cancelled']) == 2 and cancelled['result']['build_cancelled'], cancelled['result'])
    queue = {item['id']: item for item in bb.get('/queue/')}
    check('Bambuddy shows them cancelled', all(queue[i['id']]['status'] == 'cancelled' for i in run['items'] if i['id'] in queue))
    build = wait_for(lambda: (b := it.get(f"/api/build/{run['build']}/")) and b['status'] == 30 and b)
    check('build order is cancelled', bool(build))

    run = it.post(f'{part_url}print/', {'plates': 2}, expect=201)['run']
    ids = [item['id'] for item in run['items']]
    set_items(args.bambuddy_db, {ids[0]: 'printing'})
    cancelled = it.post(f"{plugin}/run/{run['build']}/cancel/", expect=200)
    check('a plate which is printing is left to finish', cancelled['result']['printing'] == 1 and not cancelled['result']['build_cancelled'] and cancelled['run']['summary']['state'] == 'printing', cancelled['result'])
    set_items(args.bambuddy_db, {ids[0]: 'completed'})
    counted = it.post(f"{plugin}/run/{run['build']}/confirm/", {'good': 4}, expect=200)
    check('... and can still be counted', counted['numbers']['stock'] == 22 and counted['run']['summary']['state'] == 'short', counted['run']['summary'])
    it.post(f"{plugin}/run/{run['build']}/close/", expect=200)
    wait_for(lambda: it.get(f"/api/build/{run['build']}/")['status'] == 40)

    # A run which is cancelled from the build order page in InvenTree
    run = it.post(f'{part_url}print/', {'plates': 1}, expect=201)['run']
    it.post(f"/api/build/{run['build']}/cancel/", {'remove_allocated_stock': False, 'remove_incomplete_outputs': True})
    gone = wait_for(lambda: bb.get(f"/queue/{run['items'][0]['id']}")['status'] == 'cancelled', seconds=90)
    check('cancelling the build order in InvenTree clears the Bambuddy queue', bool(gone))

    # ------------------------------------------------------------------
    print('Automatic: low stock')

    it.patch(f"/api/part/{insert['pk']}/", {'minimum_stock': 30}, expect=200)
    it.put(part_url, {'file_id': insert_file['id'], 'parts_per_plate': 4, 'auto': True, 'restock_to': 40}, expect=200)

    result = it.post(f'{plugin}/check/', {'dry_run': True}, expect=200)
    check('check does nothing while switched off', not result['enabled'] and not result['results'], result)

    setting('AUTO_LOW_STOCK', True)
    data = it.get(part_url, expect=200)
    expected = math.ceil((40 - 22) / 4)
    check('panel shows what the check would do', data['decision']['plates'] == expected and data['decision']['reason'] == 'low_stock', data['decision'])

    result = it.post(f'{plugin}/check/', {'dry_run': True}, expect=200)
    mine = [r for r in result['results'] if r['part'] == insert['pk']]
    check('dry run reports the plates', len(mine) == 1 and mine[0]['plates'] == expected and 'build' not in mine[0], mine)
    check('dry run queues nothing', not it.get(part_url)['runs'])

    result = it.post(f'{plugin}/check/', expect=200)
    mine = [r for r in result['results'] if r['part'] == insert['pk']]
    check('check queues the run', len(mine) == 1 and mine[0].get('build'), mine)
    data = it.get(part_url, expect=200)
    auto_run = data['runs'][0]
    check('run is staged and marked as low stock', auto_run['source'] == 'low_stock' and auto_run['summary']['state'] == 'staged' and len(auto_run['items']) == expected, auto_run['summary'])
    check('nothing more is needed', data['decision']['plates'] == 0, data['decision'])

    result = it.post(f'{plugin}/check/', expect=200)
    check('a second check queues nothing', not [r for r in result['results'] if r['part'] == insert['pk']], result)

    it.put(part_url, {'file_id': insert_file['id'], 'parts_per_plate': 4, 'auto': True, 'restock_to': 400, 'max_plates': 3}, expect=200)
    it.post(f"{plugin}/run/{auto_run['build']}/cancel/", expect=200)
    wait_for(lambda: it.get(f"/api/build/{auto_run['build']}/")['status'] == 30)
    result = it.post(f'{plugin}/check/', {'dry_run': True}, expect=200)
    mine = [r for r in result['results'] if r['part'] == insert['pk']]
    check('plates are capped by the limit on the part', mine and mine[0]['plates'] == 3 and mine[0]['capped'], mine)

    setting('AUTO_LOW_STOCK', False)
    it.put(part_url, {'file_id': insert_file['id'], 'parts_per_plate': 4, 'auto': True}, expect=200)
    it.patch(f"/api/part/{insert['pk']}/", {'minimum_stock': 0}, expect=200)

    # ------------------------------------------------------------------
    print('Automatic: build order demand')

    cap_url = f"{plugin}/part/{cap['pk']}/"
    it.put(cap_url, {'file_id': cap_file['id'], 'parts_per_plate': 2, 'printer_id': config['bambuddy']['printers'][0]['id'], 'auto': True, 'destination': location['pk']}, expect=200)
    setting('AUTO_BUILD_DEMAND', True)

    parent = it.post('/api/build/', {'part': node['pk'], 'quantity': 5, 'reference': f'BO-9{tag}'[:12]}, expect=201)
    data = it.get(cap_url, expect=200)
    check('a pending build order is not demand yet', data['numbers']['demand'] == 0 and data['decision']['plates'] == 0, data['numbers'])

    it.post(f"/api/build/{parent['pk']}/issue/", expect=201)

    def cap_run():
        runs = it.get(cap_url)['runs']
        return runs[0] if runs else None

    run = wait_for(cap_run, seconds=120)
    check('issuing the build order queues the caps', bool(run), it.get(cap_url)['numbers'])

    if run:
        check('5 caps at 2 a plate is 3 plates', len(run['items']) == 3 and run['quantity'] == 6, run['summary'])
        check('run names the build order which needed it', run['source'] == 'build_demand' and (run['trigger_build'] or {}).get('pk') == parent['pk'], run['trigger_build'])
        queue = {item['id']: item for item in bb.get('/queue/')}
        check('items go to the chosen printer, staged', all(queue[i['id']]['printer_id'] == config['bambuddy']['printers'][0]['id'] and queue[i['id']]['manual_start'] for i in run['items']))
        check('build order goes to the location of the link', run['destination'] == location['pk'], run['destination'])

    data = it.get(part_url, expect=200)
    check('inserts in stock cover the build order, so none are queued', data['numbers']['demand'] == 10 and not data['runs'], data['numbers'])

    time.sleep(3)
    result = it.post(f'{plugin}/check/', expect=200)
    check('the scheduled check adds nothing', not result['results'] or all('build' not in r for r in result['results']), result)
    check('still one cap run', len(it.get(cap_url)['runs']) == 1)

    overview = it.get(f'{plugin}/overview/', expect=200)
    check('overview lists the linked parts', {insert['pk'], cap['pk']} <= {p['part']['pk'] for p in overview['parts']})
    check('overview lists the open run', run and run['build'] in [r['build'] for r in overview['runs']])

    setting('AUTO_BUILD_DEMAND', False)

    # ------------------------------------------------------------------
    print('Errors and permissions')

    setting('BAMBUDDY_URL', 'http://127.0.0.1:9')
    config = it.get(f'{plugin}/config/', expect=200)
    check('config reports Bambuddy as unreachable', not config['bambuddy']['ok'] and config['bambuddy']['error'], config['bambuddy'])
    code, body = it.call('POST', f'{part_url}print/', json={'plates': 1})
    check('printing answers with a clear error', code == 502 and 'Bambuddy' in body['detail'], body)
    check('no build order is left behind', not it.get(part_url)['runs'])
    data = it.get(cap_url, expect=200)
    check('part page still loads, with the queue error', data['queue_error'] and len(data['runs']) == 1, data.get('queue_error'))
    setting('BAMBUDDY_URL', args.bambuddy)

    if args.bambuddy_key:
        setting('BAMBUDDY_API_KEY', '')
        config = it.get(f'{plugin}/config/', expect=200)
        check('without the API key, the error points at the key', not config['bambuddy']['ok'] and 'API key' in (config['bambuddy']['error'] or ''), config['bambuddy'])
        code, body = it.call('POST', f'{part_url}print/', json={'plates': 1})
        check('... and nothing is queued', code == 502 and not it.get(part_url)['runs'], body)
        setting('BAMBUDDY_API_KEY', args.bambuddy_key)
        check('with the key it is reachable again', it.get(f'{plugin}/config/')['bambuddy']['ok'])

    code, body = it.call('DELETE', part_url)
    check('unlinking works', code == 200 and body['link'] is None, body)
    code, body = it.call('GET', f'{plugin}/run/999999/')
    check('unknown run is not found', code == 404)

    anonymous = Api(args.inventree, {})
    code, _ = anonymous.call('GET', f'{plugin}/config/')
    check('anonymous requests are refused', code in (401, 403), code)

    def new_user(name):
        user = it.post(
            '/api/user/',
            {'username': name, 'email': f'{name}@example.com', 'first_name': 'Check', 'last_name': name},
            expect=201,
        )
        it.put(f"/api/user/{user['pk']}/set-password/", {'password': 'check-Pass-12345', 'override_warning': True}, expect=200)
        token = requests.get(f'{args.inventree}/api/user/token/?name=check', auth=(name, 'check-Pass-12345'), timeout=30).json()['token']
        return user, Api(args.inventree, {'Authorization': f'Token {token}'})

    _, norole = new_user(f'norole{tag}')
    code, _ = norole.call('GET', f'{plugin}/overview/')
    check('a user without roles is refused', code == 403, code)

    viewer, viewing = new_user(f'viewer{tag}')
    group = it.post('/api/user/group/', {'name': f'viewers{tag}'}, expect=201)

    for role in group['roles']:
        if role['name'] in ('part', 'build'):
            it.patch(f"/api/user/ruleset/{role['pk']}/", {'can_view': True}, expect=200)

    it.patch(f"/api/user/{viewer['pk']}/", {'group_ids': [group['pk']]}, expect=200)

    code, body = viewing.call('GET', cap_url)
    check('a view-only user can see a part', code == 200 and not body['permissions']['print'] and not body['permissions']['link'], code)
    code, _ = viewing.call('POST', f'{cap_url}print/', json={'plates': 1})
    check('... but not print', code == 403, code)
    code, _ = viewing.call('PUT', cap_url, json={'file_id': cap_file['id'], 'parts_per_plate': 2})
    check('... nor change the link', code == 403, code)
    open_run = it.get(cap_url)['runs'][0]['build']
    code, _ = viewing.call('POST', f'{plugin}/run/{open_run}/release/')
    check('... nor release a run', code == 403, code)
    code, _ = viewing.call('POST', f'{plugin}/check/', json={})
    check('... nor start the automatic check', code == 403, code)
    code, body = viewing.call('POST', f'{plugin}/check/', json={'dry_run': True})
    check('... though a dry run is allowed', code == 200, code)

    print()
    print(f'{PASSED} passed, {len(FAILED)} failed')

    for name in FAILED:
        print(f'  failed: {name}')

    sys.exit(1 if FAILED else 0)


if __name__ == '__main__':
    main()
