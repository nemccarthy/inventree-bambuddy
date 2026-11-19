"""API views for the Bambuddy plugin.

All endpoints are served below /plugin/bambuddy/ and accept session or token
authentication, so they can also be used from scripts.
"""

from __future__ import annotations

from django.http import HttpResponse
from rest_framework import permissions, status
from rest_framework.exceptions import NotFound, PermissionDenied
from rest_framework.response import Response
from rest_framework.views import APIView

from . import PLUGIN_VERSION, auto, links, logic, runs
from .client import BambuddyError, is_sliced
from .core import SLUG

# Oldest Bambuddy version with batches which can be tagged with a reference
MIN_BAMBUDDY = (1, 2, 5, 7)


def _plugin():
    from plugin.registry import registry

    plugin = registry.get_plugin(SLUG)

    if plugin is None:
        raise NotFound('The Bambuddy plugin is not active')

    return plugin


def _can(user, role: str, permission: str) -> bool:
    from users.permissions import check_user_role

    return bool(user and check_user_role(user, role, permission))


def user_permissions(user) -> dict:
    """What the user may do."""
    return {
        'view': _can(user, 'part', 'view'),
        # The link is stored against the part
        'link': _can(user, 'part', 'change'),
        # A print run is a build order
        'print': _can(user, 'build', 'add'),
        'manage': _can(user, 'build', 'change'),
    }


def _version_tuple(text: str) -> tuple:
    parts = []

    for piece in str(text or '').split('.'):
        digits = ''.join(ch for ch in piece if ch.isdigit())

        if not digits:
            break

        parts.append(int(digits))

    return tuple(parts)


def _error(message, code=status.HTTP_400_BAD_REQUEST, **extra) -> Response:
    return Response({'detail': message, **extra}, status=code)


class BambuddyView(APIView):
    """Base class: every endpoint needs at least permission to view parts."""

    permission_classes = [permissions.IsAuthenticated]

    def initial(self, request, *args, **kwargs):
        """Check the role permissions before handling the request."""
        super().initial(request, *args, **kwargs)

        if not _can(request.user, 'part', 'view'):
            raise PermissionDenied()

    def require(self, request, name: str):
        """Raise an error unless the user has the named permission."""
        if not user_permissions(request.user).get(name):
            raise PermissionDenied()

    def handle_exception(self, exc):
        """Answer with a readable message when Bambuddy or a rule says no."""
        if isinstance(exc, BambuddyError):
            return _error(exc.message, status.HTTP_502_BAD_GATEWAY, bambuddy=True)

        if isinstance(exc, runs.RunError):
            return _error(str(exc))

        if isinstance(exc, links.LinkError):
            return _error(str(exc), errors=exc.errors)

        return super().handle_exception(exc)


def _part(pk: int):
    from part.models import Part

    part = Part.objects.filter(pk=pk).first()

    if part is None:
        raise NotFound('Part not found')

    return part


def _part_label(part) -> dict:
    return {
        'pk': part.pk,
        'name': part.name,
        'ipn': part.IPN or '',
        'description': part.description or '',
        'units': part.units or '',
        'active': part.active,
        'assembly': part.assembly,
        'trackable': part.trackable,
        'minimum_stock': float(part.minimum_stock or 0),
        'default_location': getattr(part.get_default_location(), 'pk', None),
    }


def _location_name(pk) -> str | None:
    from stock.models import StockLocation

    if not pk:
        return None

    location = StockLocation.objects.filter(pk=pk).first()

    return location.pathstring if location else None


def _link_out(link: dict | None) -> dict | None:
    if not link:
        return None

    return {**link, 'destination_name': _location_name(link.get('destination'))}


def _settings_out(plugin) -> dict:
    from common.settings import get_global_setting

    options = plugin.auto_options()

    return {
        'auto_low_stock': options['low_stock'],
        'auto_build_demand': options['build_demand'],
        'include_pending': options['include_pending'],
        'max_plates': options['max_plates'],
        'public_url': plugin.public_url(),
        'configured': plugin.client().configured,
        # Both are needed for anything to happen in the background
        'events_enabled': bool(get_global_setting('ENABLE_PLUGINS_EVENTS', False)),
        'schedule_enabled': bool(get_global_setting('ENABLE_PLUGINS_SCHEDULE', False)),
    }


def _part_out(plugin, part, refresh: bool = True) -> dict:
    """Everything the part panel shows."""
    link = links.get_link(part)
    open_runs = runs.active_runs(part)
    error = None

    if refresh and open_runs and plugin.client().configured:
        result = runs.refresh(plugin, open_runs, client=plugin.client(timeout=6))
        error = result['error']
        open_runs = runs.active_runs(part)

    options = plugin.auto_options()
    numbers = auto.part_numbers(part, options['include_pending'])
    finished = runs.recent_runs(part, limit=8, finished_only=True)

    return {
        'part': _part_label(part),
        'link': _link_out(link),
        'numbers': numbers,
        'decision': auto.decision(plugin, part, link, numbers) if link else None,
        'runs': [runs.describe(build) for build in reversed(open_runs)],
        'history': [runs.describe(build) for build in finished],
        'queue_error': error,
    }


class ConfigView(BambuddyView):
    """GET /plugin/bambuddy/config/ - settings, permissions and the state of Bambuddy."""

    def get(self, request, *args, **kwargs):
        plugin = _plugin()
        client = plugin.client(timeout=6)

        server = {
            'configured': client.configured,
            'ok': False,
            'error': None,
            'version': None,
            'outdated': False,
            'printers': [],
        }

        if client.configured:
            try:
                info = client.info() or {}
                server['version'] = (info.get('app') or {}).get('version')
                server['printers'] = [
                    {
                        'id': printer.get('id'),
                        'name': printer.get('name'),
                        'model': printer.get('model'),
                        'location': printer.get('location'),
                        'active': printer.get('is_active', True),
                    }
                    for printer in client.printers()
                ]
                server['ok'] = True

                version = _version_tuple(server['version'])
                server['outdated'] = bool(version) and version < MIN_BAMBUDDY
            except BambuddyError as exc:
                server['error'] = exc.message
        else:
            server['error'] = 'The address of the Bambuddy server is not set in the plugin settings'

        return Response({
            'version': PLUGIN_VERSION,
            'permissions': user_permissions(request.user),
            'settings': _settings_out(plugin),
            'bambuddy': server,
        })


class FilesView(BambuddyView):
    """GET /plugin/bambuddy/files/ - the sliced files in Bambuddy's library."""

    def get(self, request, *args, **kwargs):
        plugin = _plugin()
        client = plugin.client()

        used: dict = {}

        for part in links.linked_parts():
            link = links.get_link(part)

            if link:
                used.setdefault(link['file_id'], []).append({
                    'pk': part.pk,
                    'name': part.name,
                    'ipn': part.IPN or '',
                    'plate_id': link.get('plate_id'),
                })

        folders = client.folder_names()
        files = []

        for file in client.files():
            if not is_sliced(file):
                continue

            files.append({
                'id': file['id'],
                'filename': file.get('filename'),
                'folder': folders.get(file.get('folder_id')),
                'print_name': file.get('print_name'),
                'print_time_seconds': file.get('print_time_seconds'),
                'filament_grams': file.get('filament_used_grams'),
                'sliced_for_model': file.get('sliced_for_model'),
                'has_thumbnail': bool(file.get('thumbnail_path')),
                'created': file.get('created_at'),
                'linked_to': used.get(file['id'], []),
            })

        files.sort(key=lambda entry: (entry['folder'] or '', (entry['filename'] or '').lower()))

        return Response({'files': files})


class FileView(BambuddyView):
    """GET /plugin/bambuddy/files/<id>/ - one library file with its plates."""

    def get(self, request, pk, *args, **kwargs):
        client = _plugin().client()
        file = client.file(pk)

        try:
            plates = (client.plates(pk) or {}).get('plates') or []
        except BambuddyError:
            plates = []

        metadata = file.get('metadata') or {}

        return Response({
            'id': file['id'],
            'filename': file.get('filename'),
            'folder': file.get('folder_name'),
            'sliced': is_sliced(file),
            'sliced_for_model': file.get('sliced_for_model') or metadata.get('sliced_for_model'),
            'print_time_seconds': file.get('print_time_seconds'),
            'filament_grams': file.get('filament_used_grams'),
            'has_thumbnail': bool(file.get('thumbnail_path')),
            'plates': [
                {
                    'index': plate.get('index'),
                    'name': plate.get('name'),
                    'objects': plate.get('object_count'),
                    'print_time_seconds': plate.get('print_time_seconds'),
                    'filament_grams': plate.get('filament_used_grams'),
                    'filaments': [
                        {
                            'type': filament.get('type'),
                            'color': filament.get('color'),
                            'grams': filament.get('used_grams'),
                        }
                        for filament in plate.get('filaments') or []
                    ],
                }
                for plate in plates
            ],
        })


class ThumbnailView(BambuddyView):
    """GET /plugin/bambuddy/files/<id>/thumbnail/ - the preview image of a file."""

    def get(self, request, pk, *args, **kwargs):
        try:
            content, content_type = _plugin().client().thumbnail(pk)
        except BambuddyError:
            raise NotFound('No thumbnail') from None

        if not content_type.startswith('image/'):
            raise NotFound('No thumbnail')

        response = HttpResponse(content, content_type=content_type)
        response['Cache-Control'] = 'private, max-age=3600'

        return response


class PartView(BambuddyView):
    """GET / PUT / DELETE /plugin/bambuddy/part/<id>/ - a part and its link."""

    def get(self, request, pk, *args, **kwargs):
        plugin = _plugin()
        refresh = request.query_params.get('refresh', '1') != '0'

        return Response({
            **_part_out(plugin, _part(pk), refresh=refresh),
            'permissions': user_permissions(request.user),
            'settings': _settings_out(plugin),
        })

    def put(self, request, pk, *args, **kwargs):
        """Link the part to a sliced file, or change the link."""
        self.require(request, 'link')

        plugin = _plugin()
        part = _part(pk)
        data = request.data if isinstance(request.data, dict) else {}

        link = links.build_link(part, data, plugin.client(), request.user)
        made_assembly = links.save_link(part, link)

        return Response({
            **_part_out(plugin, part, refresh=False),
            'made_assembly': made_assembly,
        })

    def delete(self, request, pk, *args, **kwargs):
        """Remove the link. Print runs which exist stay as they are."""
        self.require(request, 'link')

        plugin = _plugin()
        part = _part(pk)
        links.clear_link(part)

        return Response(_part_out(plugin, part, refresh=False))


class PrintView(BambuddyView):
    """POST /plugin/bambuddy/part/<id>/print/ - queue a print run.

    Body: {"plates": 3} or {"quantity": 10} (rounded up to whole plates).
    """

    def post(self, request, pk, *args, **kwargs):
        self.require(request, 'print')

        plugin = _plugin()
        part = _part(pk)
        link = links.get_link(part)

        if not link:
            return _error('This part is not linked to a file in Bambuddy')

        data = request.data if isinstance(request.data, dict) else {}

        try:
            if data.get('plates') not in (None, ''):
                plates = int(data['plates'])
            else:
                plates = logic.plates_for(
                    float(data.get('quantity') or 0), float(link['parts_per_plate'])
                )
        except (TypeError, ValueError):
            return _error('Enter the number of plates or the quantity to print')

        build = runs.create_run(plugin, part, plates, request.user)

        return Response(
            {'run': runs.describe(build), **_part_out(plugin, part, refresh=False)},
            status=status.HTTP_201_CREATED,
        )


def _run(pk: int):
    build = runs.get_run(pk)

    if build is None:
        raise NotFound('Print run not found')

    return build


class RunView(BambuddyView):
    """GET /plugin/bambuddy/run/<build id>/ - one print run, freshly checked."""

    def get(self, request, pk, *args, **kwargs):
        plugin = _plugin()
        build = _run(pk)
        result = runs.refresh(plugin, [build])
        build.refresh_from_db()

        return Response({'run': runs.describe(build), 'queue_error': result['error']})


class RunActionView(BambuddyView):
    """POST /plugin/bambuddy/run/<build id>/<action>/

    release  start the staged plates                {"skip_filament_check": false}
    confirm  book the good parts into stock         {"good": 11, "location": 7, "notes": ""}
    reprint  queue more plates for the same run     {"plates": 1}
    close    complete the build order as it stands
    cancel   take the run out of the queue
    """

    def post(self, request, pk, action, *args, **kwargs):
        self.require(request, 'manage')

        plugin = _plugin()
        build = _run(pk)
        data = request.data if isinstance(request.data, dict) else {}
        result: dict = {}

        if action == 'release':
            result = runs.release(
                plugin, build, skip_filament_check=bool(data.get('skip_filament_check'))
            )

        elif action == 'confirm':
            from stock.models import StockLocation

            location = None

            if data.get('location'):
                location = StockLocation.objects.filter(pk=data['location']).first()

                if location is None:
                    return _error('This stock location does not exist')

            # Make sure the latest state of the queue is what gets counted
            runs.refresh(plugin, [build])
            build.refresh_from_db()

            result = runs.confirm(
                plugin,
                build,
                data.get('good'),
                request.user,
                location=location,
                notes=str(data.get('notes') or '')[:250],
            )

        elif action == 'reprint':
            try:
                plates = int(data.get('plates'))
            except (TypeError, ValueError):
                return _error('Enter the number of plates')

            result = {'items': runs.add_plates(plugin, build, plates)}

        elif action == 'close':
            result = {'closed': runs.close(build, request.user)}

        elif action == 'cancel':
            result = runs.cancel(plugin, build, request.user)

        else:
            raise NotFound('Unknown action')

        build.refresh_from_db()

        return Response({
            'result': result,
            'run': runs.describe(build),
            **_part_out(plugin, build.part, refresh=False),
        })


class OverviewView(BambuddyView):
    """GET /plugin/bambuddy/overview/ - every linked part and every open run."""

    def get(self, request, *args, **kwargs):
        plugin = _plugin()
        options = plugin.auto_options()
        open_runs = runs.active_runs()
        error = None

        if open_runs and plugin.client().configured:
            result = runs.refresh(plugin, open_runs, client=plugin.client(timeout=6))
            error = result['error']
            open_runs = runs.active_runs()

        by_part: dict = {}

        for build in open_runs:
            by_part.setdefault(build.part_id, []).append(build)

        parts = []

        for part in links.linked_parts():
            link = links.get_link(part)

            if not link:
                continue

            numbers = auto.part_numbers(part, options['include_pending'])

            parts.append({
                'part': _part_label(part),
                'link': _link_out(link),
                'numbers': numbers,
                'decision': auto.decision(plugin, part, link, numbers),
                'open_runs': len(by_part.get(part.pk, [])),
            })

        return Response({
            'parts': parts,
            'runs': [runs.describe(build) for build in reversed(open_runs)],
            'history': [
                runs.describe(build) for build in runs.recent_runs(limit=15, finished_only=True)
            ],
            'queue_error': error,
            'permissions': user_permissions(request.user),
            'settings': _settings_out(plugin),
        })


class CheckView(BambuddyView):
    """POST /plugin/bambuddy/check/ - run the automatic check now.

    Body: {"dry_run": true} only reports what would be queued.
    """

    def post(self, request, *args, **kwargs):
        plugin = _plugin()
        data = request.data if isinstance(request.data, dict) else {}
        dry_run = bool(data.get('dry_run'))

        if not dry_run:
            self.require(request, 'print')

        options = plugin.auto_options()

        return Response({
            'enabled': options['low_stock'] or options['build_demand'],
            'dry_run': dry_run,
            'results': auto.check(plugin, dry_run=dry_run, user=request.user),
        })
