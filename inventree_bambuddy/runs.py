"""Print runs.

A print run is an InvenTree build order for the printed part, plus the queue
items in Bambuddy which print it. What ties them together is stored in the
metadata of the build order, under the key 'bambuddy':

    {
        "file_id": 12, "filename": "...", "plate_id": null,
        "parts_per_plate": 4,
        "printer_id": null, "target_model": "P1S", "target_location": null,
        "batch_id": 7,                    # the batch in Bambuddy
        "items": [                        # one per plate
            {"id": 31, "status": "pending", "manual_start": true, "counted": false, ...}
        ],
        "source": "manual" | "low_stock" | "build_demand",
        "trigger_build": 55,              # the build order which needed the parts
        "confirmations": [{"good": 11, "plates": 3, "by": "nate", "at": "..."}],
        "checked": "...",                 # when Bambuddy was last asked
        "closed": false
    }

The build order is what the rest of InvenTree sees: while it is open, the
quantity still to come counts as incoming stock.
"""

from __future__ import annotations

import logging
from decimal import Decimal

from . import logic
from .client import Bambuddy, BambuddyError
from .links import KEY, LinkError, check_file, get_link

logger = logging.getLogger('inventree')

SOURCE_MANUAL = 'manual'

# The most plates one request may queue, as a guard against typing errors
PLATE_LIMIT = 200


class RunError(Exception):
    """A print run could not be created or changed."""


def run_meta(build) -> dict | None:
    """The plugin's data on a build order, or None if it is not a print run."""
    metadata = build.metadata if isinstance(build.metadata, dict) else {}
    meta = metadata.get(KEY)

    if isinstance(meta, dict) and 'items' in meta:
        return meta

    return None


def _now() -> str:
    from django.utils import timezone

    return timezone.now().isoformat(timespec='seconds')


def _builds():
    from build.models import Build

    return Build.objects.filter(metadata__has_key=KEY).select_related(
        'part', 'issued_by', 'parent', 'destination'
    )


def is_finished(build, meta: dict) -> bool:
    """True once a run is over.

    The plugin marks a run when it closes or cancels it, because InvenTree
    may complete the build order itself a moment later, in the background.
    """
    from build.status_codes import BuildStatusGroups

    if meta.get('closed') or meta.get('cancelled'):
        return True

    return build.status not in BuildStatusGroups.ACTIVE_CODES


def active_runs(part=None) -> list:
    """Print runs which are still open, oldest first."""
    from build.status_codes import BuildStatusGroups

    builds = _builds().filter(status__in=BuildStatusGroups.ACTIVE_CODES)

    if part is not None:
        builds = builds.filter(part=part)

    result = []

    for build in builds.order_by('pk'):
        meta = run_meta(build)

        if meta is not None and not is_finished(build, meta):
            result.append(build)

    return result


def recent_runs(part=None, limit: int = 20, finished_only: bool = False) -> list:
    """The latest print runs, newest first."""
    builds = _builds()

    if part is not None:
        builds = builds.filter(part=part)

    result = []

    for build in builds.order_by('-pk')[: limit * 4]:
        meta = run_meta(build)

        if meta is None or (finished_only and not is_finished(build, meta)):
            continue

        result.append(build)

        if len(result) >= limit:
            break

    return result


def get_run(pk: int):
    """The print run with the given build order id, or None."""
    build = _builds().filter(pk=pk).first()

    if build is None or run_meta(build) is None:
        return None

    return build


def _mutate(build, change):
    """Change the plugin's data on a build order, under a row lock.

    'change' is called with the locked build order and its data, which it
    edits in place. Only the metadata column is written, so that this never
    overwrites a status which InvenTree is changing at the same moment.
    """
    from build.models import Build
    from django.db import transaction

    with transaction.atomic():
        locked = Build.objects.select_for_update().get(pk=build.pk)
        meta = run_meta(locked) or {'items': []}

        result = change(locked, meta)

        metadata = dict(locked.metadata) if isinstance(locked.metadata, dict) else {}
        metadata[KEY] = meta
        locked.metadata = metadata
        locked.save(update_fields=['metadata'])

    build.refresh_from_db()

    return result


def _build_state(build, meta: dict) -> str:
    from build.status_codes import BuildStatus

    if build.status == BuildStatus.CANCELLED.value or meta.get('cancelled'):
        return 'cancelled'

    if build.status == BuildStatus.COMPLETE.value:
        return 'complete'

    return 'active'


def summary(build, meta: dict | None = None) -> dict:
    """State of a print run (see logic.summarise)."""
    meta = meta if meta is not None else (run_meta(build) or {'items': []})

    return logic.summarise(
        meta.get('items') or [],
        per_plate=float(meta.get('parts_per_plate') or 1),
        quantity=float(build.quantity),
        completed=float(build.completed),
        build_state=_build_state(build, meta),
        closed=bool(meta.get('closed')),
    )


def describe(build) -> dict:
    """A print run, as sent to the user interface."""
    meta = run_meta(build) or {'items': []}
    part = build.part
    trigger = None

    if build.parent_id:
        trigger = {'pk': build.parent_id, 'reference': build.parent.reference}

    return {
        'build': build.pk,
        'reference': build.reference,
        'title': build.title,
        'part': part.pk,
        'part_name': part.name,
        'part_ipn': part.IPN or '',
        'quantity': float(build.quantity),
        'completed': float(build.completed),
        'build_status': build.status,
        'created': build.creation_date.isoformat() if build.creation_date else None,
        'target_date': build.target_date.isoformat() if build.target_date else None,
        'issued_by': build.issued_by.username if build.issued_by else None,
        'destination': build.destination_id,
        'destination_name': build.destination.pathstring if build.destination else None,
        'source': meta.get('source') or SOURCE_MANUAL,
        'trigger_build': trigger,
        'file_id': meta.get('file_id'),
        'filename': meta.get('filename'),
        'plate_id': meta.get('plate_id'),
        'parts_per_plate': meta.get('parts_per_plate'),
        'printer_id': meta.get('printer_id'),
        'printer_name': meta.get('printer_name'),
        'target_model': meta.get('target_model'),
        'target_location': meta.get('target_location'),
        'print_time_seconds': meta.get('print_time_seconds'),
        'batch_id': meta.get('batch_id'),
        'items': meta.get('items') or [],
        'confirmations': meta.get('confirmations') or [],
        'checked': meta.get('checked'),
        'error': meta.get('error'),
        'summary': summary(build, meta),
    }


# Creating a run


def _batch_name(build) -> str:
    """Name of the batch in Bambuddy: the build order and the part."""
    return f'{build.reference} {build.part.name}'


def _stored_item(item: dict) -> dict:
    return logic.merge_item({'id': item['id'], 'counted': False}, item)


def _queue_plates(
    client: Bambuddy, meta: dict, build_pk: int, reference: str, plates: int, title: str
) -> list:
    """Queue plates in Bambuddy for a run. Returns the new queue items.

    The items are staged ("Queue Only" in Bambuddy): nothing starts until
    somebody releases it.
    """
    known = {item['id'] for item in meta.get('items') or []}
    batch_id = meta.get('batch_id')
    total = len(known) + plates
    created_here = False

    if batch_id is None:
        # Tag the batch with the build order reference, so that it can be
        # traced back from Bambuddy. Bambuddy refuses a second batch with the
        # same reference. That happens when InvenTree hands out a reference
        # again (after a build order was deleted): the older batch is someone
        # else's, so the build order id is added to tell them apart.
        for tag in (reference, f'{reference}-{build_pk}'):
            try:
                batch = client.create_batch(
                    name=title,
                    file_id=meta['file_id'],
                    plate_id=meta.get('plate_id'),
                    plates=plates,
                    reference=tag,
                    notes=f'InvenTree build order {reference}',
                )
                batch_id = batch.get('id')
                created_here = batch_id is not None
                break
            except BambuddyError as exc:
                if exc.status == 409:
                    continue

                if exc.status in (404, 405, 422):
                    # A Bambuddy version without tagged batches: queue without one
                    break

                raise
    else:
        try:
            client.set_batch_plates(batch_id, meta.get('plate_id'), total)
        except BambuddyError:
            # The batch may be closed or gone: queue the extra plates without it
            batch_id = None

    request = {
        'file_id': meta['file_id'],
        'plate_id': meta.get('plate_id'),
        'copies': plates,
        'printer_id': meta.get('printer_id'),
        'target_model': meta.get('target_model'),
        'target_location': meta.get('target_location'),
        'staged': True,
    }

    try:
        first = client.add_to_queue(batch_id=batch_id, **request)
    except BambuddyError as exc:
        if batch_id is None or exc.status not in (400, 404, 409, 422):
            raise

        # Either the batch no longer takes items, or the request itself is
        # wrong. Try once more without the batch: that tells the two apart.
        if created_here:
            try:
                client.cancel_batch(batch_id)
            except BambuddyError:
                pass

        batch_id = None
        first = client.add_to_queue(batch_id=None, **request)

    items = [first]

    if plates > 1:
        # Bambuddy answers with the first copy only: find the others
        group = first.get('batch_id')
        queue = client.queue()

        # The copies are created together, so they are the items from the
        # first copy onwards: in its batch, or else of the same file
        items = [
            item
            for item in queue
            if item['id'] >= first['id']
            and item['id'] not in known
            and (
                item.get('batch_id') == group
                if group is not None
                else item.get('library_file_id') == meta['file_id']
            )
        ]

        items = sorted(items, key=lambda item: item['id'])[:plates] or [first]

    if meta.get('batch_id') is None:
        meta['batch_id'] = first.get('batch_id') or batch_id

    return [_stored_item(item) for item in items]


def create_run(
    plugin,
    part,
    plates: int,
    user=None,
    *,
    source: str = SOURCE_MANUAL,
    trigger_build=None,
    build=None,
    client: Bambuddy | None = None,
):
    """Raise a build order for the part and queue its plates in Bambuddy.

    'build' may be a build order which was already created for this run (the
    automatic check creates it while it holds a lock on the part).
    """
    link = get_link(part)

    if not link:
        raise RunError('This part is not linked to a file in Bambuddy')

    if plates < 1:
        raise RunError('Nothing to print: the number of plates must be at least one')

    if plates > PLATE_LIMIT:
        raise RunError(f'{plates} plates is more than can be queued at once ({PLATE_LIMIT})')

    client = client or plugin.client()

    try:
        try:
            checked = check_file(link, client)
        except LinkError as exc:
            raise RunError(str(exc)) from None

        if build is None:
            build = new_build(plugin, part, link, plates, user, source, trigger_build)

        meta = _initial_meta(link, source, trigger_build, checked['changed'])
        meta['items'] = _queue_plates(
            client, meta, build.pk, build.reference, plates, _batch_name(build)
        )
        meta['checked'] = _now()
    except Exception:
        # Nothing was queued: do not leave an empty build order behind
        if build is not None and build.pk:
            build.delete()

        raise

    def store(locked, stored):
        stored.clear()
        stored.update(meta)

    _mutate(build, store)

    # In production from the start: the work is in the print queue
    try:
        build.issue_build()
    except Exception:  # pragma: no cover - the run itself is in place
        logger.exception('Bambuddy: could not issue build order %s', build.reference)

    build.refresh_from_db()

    return build


def _initial_meta(link: dict, source: str, trigger_build, changed: bool) -> dict:
    return {
        'file_id': link['file_id'],
        'filename': link.get('filename'),
        'plate_id': link.get('plate_id'),
        'parts_per_plate': link['parts_per_plate'],
        'printer_id': link.get('printer_id'),
        'printer_name': link.get('printer_name'),
        'target_model': link.get('target_model'),
        'target_location': link.get('target_location'),
        'print_time_seconds': link.get('print_time_seconds'),
        'batch_id': None,
        'items': [],
        'source': source,
        'trigger_build': trigger_build.pk if trigger_build is not None else None,
        'file_changed': changed,
        'confirmations': [],
        'created': _now(),
    }


def new_build(plugin, part, link: dict, plates: int, user, source, trigger_build=None):
    """Create the (pending) build order of a print run."""
    import datetime

    import InvenTree.helpers
    from build.models import Build
    from django.core.exceptions import ValidationError
    from stock.models import StockLocation

    per_plate = int(link['parts_per_plate'])
    name = link.get('print_name') or link.get('filename') or part.name
    plural = 'plate' if plates == 1 else 'plates'

    destination = None

    if link.get('destination'):
        destination = StockLocation.objects.filter(pk=link['destination']).first()

    if destination is None:
        destination = part.get_default_location() or plugin.default_location()

    lead_days = max(0, plugin.int_setting('LEAD_DAYS', 1))
    today = InvenTree.helpers.current_date()

    build = Build(
        part=part,
        quantity=plates * per_plate,
        title=f'3D print: {plates} {plural} of {name}'[:100],
        issued_by=user,
        destination=destination,
        start_date=today,
        target_date=today + datetime.timedelta(days=lead_days),
        parent=trigger_build,
    )

    # Marks the build order as a print run from the moment it exists, so that
    # the plugin's own "build order issued" handler leaves it alone
    build.metadata = {KEY: {'items': [], 'source': source, 'pending': True}}

    try:
        build.save()
    except ValidationError as exc:
        raise RunError(
            'InvenTree refused the build order: ' + '; '.join(exc.messages)
        ) from None

    return build


# Following a run


def refresh(plugin, builds=None, client: Bambuddy | None = None) -> dict:
    """Update print runs from the state of the queue in Bambuddy."""
    builds = list(builds) if builds is not None else active_runs()
    result = {'checked': 0, 'changed': 0, 'error': None}

    builds = [
        build
        for build in builds
        if any(
            item.get('status') not in logic.FINAL_STATES
            for item in (run_meta(build) or {}).get('items') or []
        )
    ]

    if not builds:
        return result

    client = client or plugin.client()

    try:
        queue = {item['id']: item for item in client.queue()}
        fresh_by_build = {}

        for build in builds:
            fresh = {}

            for item in (run_meta(build) or {}).get('items') or []:
                if item.get('status') in logic.FINAL_STATES:
                    continue

                current = queue.get(item['id'])

                if current is None:
                    # Not in the list: ask for it by id before giving up on it
                    try:
                        current = client.queue_item(item['id'])
                    except BambuddyError as exc:
                        if not exc.not_found:
                            raise

                fresh[item['id']] = current

            fresh_by_build[build.pk] = fresh
    except BambuddyError as exc:
        result['error'] = exc.message
        return result

    for build in builds:
        fresh = fresh_by_build[build.pk]

        def merge(locked, meta, fresh=fresh):
            before = [(item['id'], item.get('status'), item.get('manual_start')) for item in meta['items']]

            meta['items'] = [
                logic.merge_item(item, fresh[item['id']]) if item['id'] in fresh else item
                for item in meta['items']
            ]
            meta['checked'] = _now()
            meta.pop('error', None)

            after = [(item['id'], item.get('status'), item.get('manual_start')) for item in meta['items']]

            return before != after

        if _mutate(build, merge):
            result['changed'] += 1

        result['checked'] += 1

    return result


def release(plugin, build, skip_filament_check: bool = False, client: Bambuddy | None = None) -> dict:
    """Release the staged plates of a run, so that Bambuddy starts them."""
    client = client or plugin.client()
    meta = run_meta(build) or {'items': []}

    released = []
    blocked = []

    for item in meta['items']:
        if item.get('status') != logic.PENDING or not item.get('manual_start'):
            continue

        try:
            client.start_item(item['id'], skip_filament_check=skip_filament_check)
            released.append(item['id'])
        except BambuddyError as exc:
            if exc.not_found:
                continue

            blocked.append({
                'id': item['id'],
                'message': exc.message,
                'code': exc.code,
                # Bambuddy asks for confirmation when the loaded filament is short
                'can_skip': exc.status == 409 and not skip_filament_check,
            })

    refresh(plugin, [build], client=client)

    return {'released': released, 'blocked': blocked}


def confirm(plugin, build, good, user, location=None, notes: str = '') -> dict:
    """Book the good parts of the plates which have finished printing.

    Creates a build output for the good parts and completes it into stock.
    Every plate which has finished is marked as counted. When nothing is left
    in the queue and the build order has its quantity, it is completed.
    """
    from django.core.exceptions import ValidationError

    try:
        good = Decimal(str(good))
    except Exception:
        raise RunError('Enter the number of good parts') from None

    if good < 0 or good != good.to_integral_value():
        raise RunError('The number of good parts must be a whole number, zero or more')

    outcome = {}

    def book(locked, meta):
        state = summary(locked, meta)

        if not state['can_confirm']:
            raise RunError('There are no printed plates waiting to be counted')

        if good > Decimal(str(state['awaiting_quantity'])):
            raise RunError(
                f"{good} is more than the {state['awaiting_quantity']:g} parts on the plates which have finished"
            )

        target = location or locked.destination or locked.part.get_default_location()

        if good > 0:
            try:
                output = locked.create_build_output(good, user=user, location=target).first()
                locked.complete_build_output(
                    output, user, location=target, notes=notes or 'Printed through Bambuddy'
                )
            except ValidationError as exc:
                raise RunError(
                    'InvenTree refused the stock: ' + '; '.join(exc.messages)
                ) from None

        plates = 0

        for item in meta['items']:
            if item.get('status') == logic.COMPLETED and not item.get('counted'):
                item['counted'] = True
                plates += 1

        meta.setdefault('confirmations', []).append({
            'good': float(good),
            'expected': state['awaiting_quantity'],
            'plates': plates,
            'by': user.username if user else None,
            'at': _now(),
            'location': target.pk if target else None,
        })

        outcome.update({'good': float(good), 'plates': plates})

    _mutate(build, book)

    outcome['closed'] = finish_if_complete(build, user)

    return outcome


def finish_if_complete(build, user) -> bool:
    """Complete the build order once everything is printed, counted and booked."""
    state = summary(build)

    if state['state'] != logic.PRINTED or state['counts']['uncounted'] > 0:
        return False

    if state['remaining'] > 0:
        return False

    return close(build, user)


def close(build, user) -> bool:
    """Complete the build order with what has been booked so far."""
    from django.core.exceptions import ValidationError

    state = summary(build)

    if state['state'] in (logic.DONE, logic.RUN_CANCELLED):
        return False

    if state['counts'][logic.PENDING] or state['counts'][logic.PRINTING]:
        raise RunError('Plates of this run are still in the print queue')

    if state['counts']['uncounted']:
        raise RunError('Count the printed plates first')

    try:
        build.complete_build(user)
    except ValidationError as exc:
        raise RunError(
            'InvenTree refused to complete the build order: ' + '; '.join(exc.messages)
        ) from None
    except Exception as exc:
        raise RunError(f'InvenTree could not complete the build order: {exc}') from None

    def mark(locked, meta):
        meta['closed'] = True
        meta['closed_at'] = _now()

    _mutate(build, mark)

    return True


def add_plates(plugin, build, plates: int, client: Bambuddy | None = None) -> list:
    """Queue more plates for a run, to make up for failed plates or rejects."""
    if plates < 1:
        raise RunError('The number of plates must be at least one')

    if plates > PLATE_LIMIT:
        raise RunError(f'{plates} plates is more than can be queued at once ({PLATE_LIMIT})')

    meta = dict(run_meta(build) or {})
    state = summary(build, meta)

    if state['state'] in (logic.DONE, logic.RUN_CANCELLED):
        raise RunError('This print run is closed')

    client = client or plugin.client()

    try:
        client.file(meta['file_id'])
    except BambuddyError as exc:
        if exc.not_found:
            raise RunError(
                f"The file '{meta.get('filename')}' no longer exists in Bambuddy. Close this run and start a new one."
            ) from None

        raise

    items = _queue_plates(
        client, meta, build.pk, build.reference, plates, _batch_name(build)
    )

    def store(locked, stored):
        stored.setdefault('items', []).extend(items)
        stored['batch_id'] = meta.get('batch_id')
        stored['checked'] = _now()

    _mutate(build, store)

    return items


def cancel_queue(plugin, build, client: Bambuddy | None = None) -> dict:
    """Take the plates of a run which have not started out of the Bambuddy queue."""
    client = client or plugin.client()
    meta = run_meta(build) or {'items': []}

    cancelled = []
    failed = []

    for item in meta['items']:
        if item.get('status') != logic.PENDING:
            continue

        try:
            client.cancel_item(item['id'])
            cancelled.append(item['id'])
        except BambuddyError as exc:
            if not exc.not_found:
                failed.append({'id': item['id'], 'message': exc.message})

    refresh(plugin, [build], client=client)

    return {'cancelled': cancelled, 'failed': failed}


def cancel(plugin, build, user, client: Bambuddy | None = None) -> dict:
    """Cancel a run.

    Plates which have not started are taken out of the Bambuddy queue. A plate
    which is printing is left to finish. The build order is cancelled if
    nothing was printed; otherwise the run stays open for what was.
    """
    result = cancel_queue(plugin, build, client=client)
    state = summary(build)
    counts = state['counts']

    result['build_cancelled'] = False
    result['printing'] = counts[logic.PRINTING]

    if result['failed']:
        return result

    if counts[logic.PRINTING] or counts['uncounted'] or build.completed > 0:
        return result

    if state['state'] in (logic.DONE, logic.RUN_CANCELLED):
        return result

    build.cancel_build(user)

    def mark(locked, meta):
        meta['cancelled'] = True
        meta['closed_at'] = _now()

    _mutate(build, mark)
    result['build_cancelled'] = True

    return result
