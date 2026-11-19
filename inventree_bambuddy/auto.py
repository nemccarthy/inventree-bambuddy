"""Printing automatically: when stock is low, or when a build order needs parts.

Both triggers use the same sum for a part:

    projected = in stock + still to come from open build orders
                - still needed by build orders in production

A print run is queued when the projected stock is below the floor (the
minimum stock of the part, or zero when only build orders are watched). Open
print runs count as "still to come", so a shortage is only ever queued once.

Print runs which are queued automatically are staged in Bambuddy, like any
other: somebody still has to release them.
"""

from __future__ import annotations

import logging

from . import logic, runs
from .client import BambuddyError
from .links import get_link, linked_parts

logger = logging.getLogger('inventree')


def demand_codes(include_pending: bool) -> list:
    """Status codes of the build orders whose requirements count as demand."""
    from build.status_codes import BuildStatus

    codes = [BuildStatus.PRODUCTION.value]

    if include_pending:
        codes.append(BuildStatus.PENDING.value)

    return codes


def part_numbers(part, include_pending: bool = False) -> dict:
    """Stock, incoming quantity and demand of a part."""
    from build.models import Build, BuildLine
    from build.status_codes import BuildStatusGroups

    stock = float(part.get_stock_count(include_variants=False))

    incoming = 0.0
    incoming_runs = 0.0

    for build in Build.objects.filter(
        part=part, status__in=BuildStatusGroups.ACTIVE_CODES
    ):
        meta = runs.run_meta(build)

        if meta is not None and runs.is_finished(build, meta):
            # Closed by the plugin: the build order is about to complete
            continue

        outstanding = max(0.0, float(build.quantity) - float(build.completed))
        incoming += outstanding

        if meta is not None:
            incoming_runs += outstanding

    demand = 0.0
    demand_builds = []

    lines = BuildLine.objects.filter(
        bom_item__sub_part=part, build__status__in=demand_codes(include_pending)
    ).select_related('build')

    for line in lines:
        build = line.build
        total = float(build.quantity)
        outstanding = max(0.0, total - float(build.completed))
        remaining = float(line.quantity) - float(line.consumed)

        # Outputs can be completed without their components being consumed:
        # never count more than the outputs still to build would need
        if total > 0:
            remaining = min(remaining, float(line.quantity) * outstanding / total)

        if remaining <= 0:
            continue

        demand += remaining
        demand_builds.append({
            'pk': build.pk,
            'reference': build.reference,
            'quantity': remaining,
        })

    return {
        'stock': stock,
        'incoming': incoming,
        'incoming_runs': incoming_runs,
        'demand': demand,
        'demand_builds': demand_builds,
        'minimum': float(part.minimum_stock or 0),
    }


def decision(plugin, part, link: dict | None = None, numbers: dict | None = None) -> dict:
    """What the automatic check would do for a part right now."""
    link = link or get_link(part) or {}
    options = plugin.auto_options()
    numbers = numbers or part_numbers(part, options['include_pending'])

    result = logic.decide(
        stock=numbers['stock'],
        incoming=numbers['incoming'],
        demand=numbers['demand'],
        minimum=numbers['minimum'],
        restock_to=link.get('restock_to'),
        per_plate=float(link.get('parts_per_plate') or 1),
        use_low_stock=options['low_stock'],
        use_demand=options['build_demand'],
        max_plates=link.get('max_plates') or options['max_plates'],
    )

    result['enabled'] = bool(link.get('auto')) and (
        options['low_stock'] or options['build_demand']
    )

    return result


def check(plugin, parts=None, trigger_build=None, dry_run: bool = False, user=None) -> list:
    """Queue a print run for every linked part which needs one.

    Arguments:
        parts: the parts to check (default: every linked part)
        trigger_build: the build order whose issue started the check
        dry_run: only report what would be queued
        user: recorded as the issuer of the build orders

    Returns one entry for each part which needs printing.
    """
    from django.db import transaction
    from part.models import Part

    options = plugin.auto_options()
    results = []

    if not (options['low_stock'] or options['build_demand']):
        return results

    if parts is None:
        parts = linked_parts()

    client = None

    for part in parts:
        link = get_link(part)

        if not link or not link.get('auto') or not part.active:
            continue

        entry = {'part': part.pk, 'name': part.name, 'ipn': part.IPN or ''}
        build = None

        try:
            # The row lock keeps two checks which run at the same moment (the
            # schedule and a build order being issued) from both queueing the
            # same shortage: the second one sees the first one's build order
            with transaction.atomic():
                locked = Part.objects.select_for_update().get(pk=part.pk)
                result = decision(plugin, locked, link)
                entry.update({
                    'plates': result['plates'],
                    'quantity': result['quantity'],
                    'reason': result['reason'],
                    'projected': result['projected'],
                    'capped': result['capped'],
                })

                if result['plates'] < 1:
                    continue

                if not dry_run:
                    build = runs.new_build(
                        plugin,
                        locked,
                        link,
                        result['plates'],
                        user,
                        result['reason'],
                        trigger_build if result['reason'] == logic.REASON_BUILD_DEMAND else None,
                    )

            if build is not None:
                client = client or plugin.client()
                build = runs.create_run(
                    plugin,
                    part,
                    result['plates'],
                    user,
                    source=result['reason'],
                    trigger_build=build.parent,
                    build=build,
                    client=client,
                )
                entry.update({'build': build.pk, 'reference': build.reference})
                logger.info(
                    'Bambuddy: queued %s plate(s) of %s as %s (%s)',
                    result['plates'],
                    part.name,
                    build.reference,
                    result['reason'],
                )
        except (BambuddyError, runs.RunError) as exc:
            entry['error'] = str(exc)
            logger.warning('Bambuddy: could not queue %s: %s', part.name, exc)

        results.append(entry)

    return results


def check_for_build(plugin, build) -> list:
    """A build order went into production: print what it is short of."""
    from part.models import Part

    if runs.run_meta(build) is not None:
        # A print run of our own
        return []

    part_ids = build.build_lines.values_list('bom_item__sub_part', flat=True)
    parts = [
        part
        for part in Part.objects.filter(pk__in=list(part_ids))
        if (get_link(part) or {}).get('auto')
    ]

    if not parts:
        return []

    return check(plugin, parts=parts, trigger_build=build, user=build.issued_by)
