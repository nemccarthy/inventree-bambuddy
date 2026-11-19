"""Tests of the plugin's rules. Plain Python: no InvenTree needed."""

import importlib.util
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent / 'inventree_bambuddy'


def _load(name):
    """Load one module of the plugin without importing the package (which needs InvenTree)."""
    spec = importlib.util.spec_from_file_location(f'bambuddy_{name}', ROOT / f'{name}.py')
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


logic = _load('logic')
client = _load('client')


def decide(**overrides):
    values = {
        'stock': 0,
        'incoming': 0,
        'demand': 0,
        'minimum': 0,
        'restock_to': None,
        'per_plate': 4,
        'use_low_stock': True,
        'use_demand': True,
        'max_plates': None,
    }
    values.update(overrides)
    return logic.decide(**values)


# Plates


@pytest.mark.parametrize(
    'quantity, per_plate, plates',
    [(0, 4, 0), (-3, 4, 0), (1, 4, 1), (4, 4, 1), (5, 4, 2), (10, 4, 3), (12, 4, 3), (7, 1, 7)],
)
def test_plates_for(quantity, per_plate, plates):
    assert logic.plates_for(quantity, per_plate) == plates


def test_plates_for_needs_a_plate_size():
    with pytest.raises(ValueError):
        logic.plates_for(5, 0)


def test_plates_for_ignores_float_noise():
    # 0.1 + 0.2 style rounding must not add a plate
    assert logic.plates_for(1.2000000000000002, 0.4) == 3


# When to print


def test_nothing_to_do_without_minimum_or_demand():
    assert decide(stock=0)['plates'] == 0


def test_stock_at_minimum_is_not_low():
    # InvenTree calls a part low when stock is *below* the minimum
    assert decide(stock=20, minimum=20)['plates'] == 0


def test_low_stock_prints_up_to_the_minimum():
    result = decide(stock=11, minimum=20)

    assert result['reason'] == 'low_stock'
    assert result['shortfall'] == 9
    assert result['plates'] == 3
    assert result['quantity'] == 12


def test_low_stock_prints_up_to_the_restock_level():
    result = decide(stock=11, minimum=20, restock_to=40)

    assert result['target'] == 40
    assert result['plates'] == 8


def test_restock_level_below_minimum_is_ignored():
    assert decide(stock=11, minimum=20, restock_to=5)['target'] == 20


def test_restock_level_alone_does_not_trigger():
    # Above the minimum: nothing is printed, however high the restock level
    assert decide(stock=25, minimum=20, restock_to=100)['plates'] == 0


def test_open_runs_count_as_incoming():
    assert decide(stock=11, incoming=12, minimum=20)['plates'] == 0
    assert decide(stock=11, incoming=4, minimum=20)['plates'] == 2


def test_build_demand_without_minimum():
    result = decide(stock=3, demand=10)

    assert result['reason'] == 'build_demand'
    assert result['projected'] == -7
    assert result['plates'] == 2


def test_build_demand_covered_by_stock():
    assert decide(stock=10, demand=10)['plates'] == 0


def test_build_demand_keeps_the_minimum_too():
    # 22 in stock covers the 10 needed, but leaves less than the minimum of 20
    result = decide(stock=22, demand=10, minimum=20)

    assert result['reason'] == 'build_demand'
    assert result['plates'] == 2


def test_reason_is_low_stock_when_stock_alone_is_short():
    assert decide(stock=5, demand=10, minimum=20)['reason'] == 'low_stock'


def test_demand_is_ignored_when_switched_off():
    result = decide(stock=3, demand=10, use_demand=False)

    assert result['plates'] == 0
    assert result['projected'] == 3


def test_minimum_is_ignored_when_low_stock_is_switched_off():
    assert decide(stock=3, minimum=20, use_low_stock=False)['plates'] == 0
    assert decide(stock=3, demand=5, minimum=20, use_low_stock=False)['plates'] == 1


def test_restock_level_is_ignored_when_low_stock_is_switched_off():
    result = decide(stock=3, demand=5, restock_to=50, use_low_stock=False)

    assert result['target'] == 0
    assert result['plates'] == 1


def test_everything_switched_off():
    assert decide(stock=0, demand=50, minimum=50, use_low_stock=False, use_demand=False)['plates'] == 0


def test_plates_are_capped():
    result = decide(stock=0, minimum=100, max_plates=10)

    assert result['plates'] == 10
    assert result['quantity'] == 40
    assert result['capped']


def test_cap_not_reached():
    result = decide(stock=0, minimum=8, max_plates=10)

    assert result['plates'] == 2
    assert not result['capped']


# State of a run


def items(*states, counted=()):
    return [
        {
            'id': index,
            'status': state.replace('staged', 'pending'),
            'manual_start': state == 'staged',
            'counted': index in counted,
        }
        for index, state in enumerate(states)
    ]


def summarise(run_items, quantity=12, completed=0, **kwargs):
    return logic.summarise(run_items, per_plate=4, quantity=quantity, completed=completed, **kwargs)


def test_staged():
    state = summarise(items('staged', 'staged', 'staged'))

    assert state['state'] == 'staged'
    assert state['can_release'] and state['can_cancel']
    assert not state['can_confirm']


def test_partly_released_is_queued_but_can_still_release():
    state = summarise(items('pending', 'staged', 'staged'))

    assert state['state'] == 'queued'
    assert state['can_release']


def test_printing_wins_over_pending():
    assert summarise(items('printing', 'pending', 'staged'))['state'] == 'printing'


def test_finished_plates_can_be_counted_while_others_print():
    state = summarise(items('completed', 'printing', 'pending'))

    assert state['state'] == 'printing'
    assert state['can_confirm']
    assert state['awaiting_plates'] == 1
    assert state['awaiting_quantity'] == 4


def test_printed():
    state = summarise(items('completed', 'completed', 'completed'))

    assert state['state'] == 'printed'
    assert state['awaiting_quantity'] == 12


def test_printed_with_a_failed_plate():
    state = summarise(items('completed', 'completed', 'failed'))

    assert state['state'] == 'printed'
    assert state['awaiting_plates'] == 2
    assert state['counts']['lost'] == 1


def test_short_after_counting():
    state = summarise(items('completed', 'completed', 'failed', counted=(0, 1)), completed=7)

    assert state['state'] == 'short'
    assert state['remaining'] == 5
    assert state['shortfall_plates'] == 2
    assert state['can_reprint'] and state['can_close']
    assert not state['can_confirm']


def test_counted_in_full_waits_to_be_completed():
    state = summarise(items('completed', 'completed', 'completed', counted=(0, 1, 2)), completed=12)

    assert state['state'] == 'printed'
    assert state['can_confirm']


def test_nothing_printed_is_failed():
    state = summarise(items('failed', 'cancelled', 'removed'))

    assert state['state'] == 'failed'
    assert state['shortfall_plates'] == 3
    assert state['can_reprint'] and not state['can_close']


def test_all_rejected_is_short_not_failed():
    # Printed and counted, but no good parts: the run can be closed or reprinted
    state = summarise(items('completed', counted=(0,)), quantity=4, completed=0)

    assert state['state'] == 'short'


def test_reprint_after_short():
    state = summarise(items('completed', 'failed', 'staged', counted=(0,)), completed=4)

    assert state['state'] == 'staged'


def test_build_complete_or_closed_is_done():
    assert summarise(items('completed', counted=(0,)), completed=4, build_state='complete')['state'] == 'done'

    state = summarise(items('completed', counted=(0,)), completed=3, closed=True)
    assert state['state'] == 'done'
    assert not state['can_cancel'] and not state['can_confirm']


def test_build_cancelled():
    state = summarise(items('cancelled', 'cancelled'), build_state='cancelled')

    assert state['state'] == 'cancelled'
    assert not state['can_reprint']


def test_unknown_state_is_treated_as_open():
    state = summarise([{'id': 1, 'status': 'paused', 'manual_start': False}])

    assert state['state'] == 'queued'


# Following an item


def test_merge_takes_the_fresh_state():
    stored = {'id': 5, 'status': 'pending', 'manual_start': True, 'counted': False}
    fresh = {'id': 5, 'status': 'printing', 'manual_start': False, 'printer_name': 'P1S-01', 'extra': 1}

    merged = logic.merge_item(stored, fresh)

    assert merged['status'] == 'printing'
    assert merged['printer_name'] == 'P1S-01'
    assert merged['counted'] is False
    assert 'extra' not in merged


def test_open_item_which_disappears_is_removed():
    merged = logic.merge_item({'id': 5, 'status': 'pending', 'manual_start': True}, None)

    assert merged['status'] == 'removed'
    assert merged['manual_start'] is False


def test_finished_item_which_disappears_keeps_its_state():
    # Bambuddy's queue history was cleared after the plate was printed
    merged = logic.merge_item({'id': 5, 'status': 'completed', 'counted': False}, None)

    assert merged['status'] == 'completed'


# Client helpers


@pytest.mark.parametrize(
    'typed, base',
    [
        ('http://host.docker.internal:8000', 'http://host.docker.internal:8000/api/v1'),
        ('http://bambuddy:8000/', 'http://bambuddy:8000/api/v1'),
        ('http://bambuddy:8000/api/v1/', 'http://bambuddy:8000/api/v1'),
        ('192.168.1.20:8000', 'http://192.168.1.20:8000/api/v1'),
        ('https://bambuddy.example.com/api', 'https://bambuddy.example.com/api/v1'),
        ('', ''),
        ('   ', ''),
    ],
)
def test_normalise_url(typed, base):
    assert client.normalise_url(typed) == base


def test_is_sliced():
    assert client.is_sliced({'file_type': 'gcode.3mf', 'filename': 'a.gcode.3mf'})
    assert client.is_sliced({'file_type': 'gcode', 'filename': 'a.gcode'})
    assert client.is_sliced({'file_type': '3mf', 'filename': 'a.gcode.3mf'})
    assert not client.is_sliced({'file_type': '3mf', 'filename': 'a.3mf'})
    assert not client.is_sliced({'file_type': 'stl', 'filename': 'a.stl'})


def test_unconfigured_client_says_so():
    bambuddy = client.Bambuddy('')

    assert not bambuddy.configured

    with pytest.raises(client.BambuddyError):
        bambuddy.info()


def test_error_detail_is_readable():
    assert client._describe('Batch not found') == 'Batch not found'
    assert client._describe({'code': 'x', 'message': 'No tray'}) == 'No tray'
    assert (
        client._describe([{'loc': ['body', 'quantity'], 'msg': 'Input should be an integer'}])
        == 'quantity: Input should be an integer'
    )


class FakeResponse:
    def __init__(self, text, server=''):
        self.text = text
        self.headers = {'Server': server} if server else {}


def test_snippet_strips_markup():
    html = '<html><head><style>p {color: red}</style></head><body><h1>400  Bad\nRequest</h1></body></html>'

    assert client.text_snippet(html) == '400 Bad Request'
    assert client.text_snippet('x' * 500).endswith('...')
    assert client.text_snippet('') == ''


def test_stranger_on_an_https_port():
    answer = FakeResponse('Client sent an HTTP request to an HTTPS server.')
    text = client.describe_stranger(answer, 'http://host:8443/api/v1')

    assert 'Client sent an HTTP request to an HTTPS server.' in text
    assert 'use https://' in text


def test_stranger_which_is_inventree():
    answer = FakeResponse('<h1>Bad Request (400)</h1>', server='gunicorn')
    text = client.describe_stranger(answer, 'http://localhost:8000/api/v1')

    assert 'Server: gunicorn.' in text
    assert 'InvenTree itself' in text


def test_stranger_which_is_inventree_behind_its_proxy():
    # http://<hostname>/ without a port: InvenTree's proxy passes it on
    answer = FakeResponse('<h1>Bad Request (400)</h1>', server='Caddy, gunicorn')
    text = client.describe_stranger(answer, 'http://myhost/api/v1')

    assert 'InvenTree itself' in text and ':8000' in text

    answer = FakeResponse('<title>Bad Request (400)</title>', server='Caddy')
    assert 'InvenTree itself' in client.describe_stranger(answer, 'http://myhost/api/v1')


def test_stranger_unknown():
    text = client.describe_stranger(FakeResponse('', server='nginx'), 'http://host:80/api/v1')

    assert 'Server: nginx.' in text
    assert 'Check the address and port' in text
