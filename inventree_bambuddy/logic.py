"""The rules of the plugin, as plain Python (no InvenTree or Django imports).

- how many plates a quantity needs
- when a part should be printed, and how many plates
- what state a print run is in, from the state of its Bambuddy queue items
"""

from __future__ import annotations

import math

# Queue item states, as reported by Bambuddy
PENDING = 'pending'
PRINTING = 'printing'
COMPLETED = 'completed'
FAILED = 'failed'
SKIPPED = 'skipped'
CANCELLED = 'cancelled'

# Not a Bambuddy state: the item no longer exists there (deleted, or cleared
# from the queue history before the plugin saw how it ended)
REMOVED = 'removed'

OPEN_STATES = (PENDING, PRINTING)
LOST_STATES = (FAILED, SKIPPED, CANCELLED, REMOVED)
FINAL_STATES = (COMPLETED, *LOST_STATES)

# Print run states
STAGED = 'staged'  # in the Bambuddy queue, waiting to be released
QUEUED = 'queued'  # released, waiting for a printer
RUN_PRINTING = 'printing'
PRINTED = 'printed'  # printing has finished, the good parts are still to be counted
SHORT = 'short'  # counted, but fewer good parts than the build order asked for
RUN_FAILED = 'failed'  # nothing was printed
DONE = 'done'
RUN_CANCELLED = 'cancelled'

REASON_LOW_STOCK = 'low_stock'
REASON_BUILD_DEMAND = 'build_demand'

_EPSILON = 1e-9


def plates_for(quantity: float, per_plate: float) -> int:
    """Number of plates needed to print at least the given quantity."""
    if per_plate <= 0:
        raise ValueError('Parts per plate must be greater than zero')

    if quantity <= 0:
        return 0

    return int(math.ceil(quantity / per_plate - _EPSILON))


def decide(
    *,
    stock: float,
    incoming: float,
    demand: float,
    minimum: float,
    restock_to: float | None,
    per_plate: float,
    use_low_stock: bool,
    use_demand: bool,
    max_plates: int | None = None,
) -> dict:
    """Work out whether a part should be printed now, and how many plates.

    Arguments:
        stock: quantity in stock
        incoming: quantity still to come from open build orders for the part
            (print runs which are queued or printing count here, so the same
            shortage is never queued twice)
        demand: quantity which open build orders still need of the part
        minimum: minimum stock of the part
        restock_to: level to print up to when printing for low stock (never
            less than the minimum)
        per_plate: parts on one plate
        use_low_stock: print when the part goes below its minimum stock
        use_demand: print when open build orders need more than there is
        max_plates: the most plates to queue at once

    The projected stock is stock + incoming, less the demand if build orders
    are taken into account. Printing is needed when that is below the floor:
    the minimum stock when printing for low stock, otherwise zero.
    """
    counted_demand = demand if use_demand else 0.0
    available = stock + incoming
    projected = available - counted_demand

    floor = max(0.0, minimum) if use_low_stock else 0.0

    result = {
        'stock': stock,
        'incoming': incoming,
        'demand': demand,
        'projected': projected,
        'floor': floor,
        'target': floor,
        'shortfall': 0.0,
        'plates': 0,
        'quantity': 0.0,
        'reason': None,
        'capped': False,
    }

    if not (use_low_stock or use_demand):
        return result

    if projected >= floor - _EPSILON:
        return result

    target = floor

    if use_low_stock and restock_to is not None:
        target = max(floor, restock_to)

    shortfall = target - projected
    plates = plates_for(shortfall, per_plate)

    if max_plates is not None and max_plates > 0 and plates > max_plates:
        plates = max_plates
        result['capped'] = True

    # Why: without the build orders, would the stock have been enough?
    if use_low_stock and available < floor - _EPSILON:
        reason = REASON_LOW_STOCK
    else:
        reason = REASON_BUILD_DEMAND

    result.update({
        'target': target,
        'shortfall': shortfall,
        'plates': plates,
        'quantity': plates * per_plate,
        'reason': reason,
    })

    return result


def count_items(items: list[dict]) -> dict:
    """Number of queue items in each state."""
    counts = {state: 0 for state in (*OPEN_STATES, *FINAL_STATES)}
    counts['staged'] = 0
    counts['uncounted'] = 0

    for item in items:
        state = item.get('status') or PENDING

        if state not in counts:
            # A state this version does not know about: treat it as still open
            state = PENDING

        counts[state] += 1

        if state == PENDING and item.get('manual_start'):
            counts['staged'] += 1

        if state == COMPLETED and not item.get('counted'):
            counts['uncounted'] += 1

    counts['lost'] = sum(counts[state] for state in LOST_STATES)
    counts['total'] = len(items)

    return counts


def summarise(
    items: list[dict],
    *,
    per_plate: float,
    quantity: float,
    completed: float,
    build_state: str = 'active',
    closed: bool = False,
) -> dict:
    """State of a print run.

    Arguments:
        items: the queue items of the run: 'status', 'manual_start' and
            'counted' (true once the plate's parts have been booked into stock)
        per_plate: parts on one plate
        quantity: quantity of the build order
        completed: quantity already booked into stock
        build_state: 'active', 'complete' or 'cancelled'
        closed: the run was closed by the plugin (the build order may still be
            completing in the background)
    """
    counts = count_items(items)
    remaining = max(0.0, quantity - completed)

    if build_state == 'cancelled':
        state = RUN_CANCELLED
    elif build_state == 'complete' or closed:
        state = DONE
    elif counts[PRINTING] > 0:
        state = RUN_PRINTING
    elif counts[PENDING] > 0:
        state = STAGED if counts['staged'] == counts[PENDING] else QUEUED
    elif counts['uncounted'] > 0:
        state = PRINTED
    elif remaining <= _EPSILON:
        # Everything is booked: the build order only has to be completed
        state = PRINTED
    elif counts[COMPLETED] == 0 and completed <= _EPSILON:
        state = RUN_FAILED
    else:
        state = SHORT

    active = state not in (DONE, RUN_CANCELLED)

    return {
        'state': state,
        'counts': counts,
        'plates': counts['total'],
        'remaining': remaining,
        # Plates which have finished printing and are waiting to be counted
        'awaiting_plates': counts['uncounted'] if active else 0,
        'awaiting_quantity': counts['uncounted'] * per_plate if active else 0.0,
        # Plates needed to make up the quantity still missing, once nothing is
        # left in the queue
        'shortfall_plates': (
            plates_for(remaining, per_plate) if state in (SHORT, RUN_FAILED) else 0
        ),
        'can_release': active and counts['staged'] > 0,
        'can_confirm': active and (counts['uncounted'] > 0 or state == PRINTED),
        'can_reprint': state in (SHORT, RUN_FAILED),
        'can_close': state == SHORT,
        'can_cancel': active,
    }


def merge_item(stored: dict, fresh: dict | None) -> dict:
    """Update the stored copy of a queue item from what Bambuddy reports now.

    'fresh' is None when the item no longer exists in Bambuddy. A final state
    which was already seen is kept; an item which disappears while still open
    counts as removed.
    """
    item = dict(stored)

    if fresh is None:
        if item.get('status') not in FINAL_STATES:
            item['status'] = REMOVED
            item['manual_start'] = False

        return item

    for key in (
        'status',
        'manual_start',
        'printer_id',
        'printer_name',
        'target_model',
        'waiting_reason',
        'filament_short',
        'scheduled_time',
        'started_at',
        'completed_at',
        'error_message',
        'archive_id',
    ):
        if key in fresh:
            item[key] = fresh[key]

    return item
