"""Bambuddy plugin for InvenTree.

Links 3D printed parts to their sliced file in Bambuddy, and queues prints
there: by hand from the part page, or automatically when stock runs low or a
build order needs the parts. Every print run is an InvenTree build order.
"""

import logging

from plugin import InvenTreePlugin
from plugin.mixins import (
    EventMixin,
    ScheduleMixin,
    SettingsMixin,
    UrlsMixin,
    UserInterfaceMixin,
)

from . import PLUGIN_VERSION

logger = logging.getLogger('inventree')

# Compiled frontend: a single file which exports every entry point
FRONTEND = 'Bambuddy.js'

SLUG = 'bambuddy'

EVENT_BUILD_ISSUED = 'build.issued'
EVENT_BUILD_CANCELLED = 'build.cancelled'


class BambuddyPlugin(
    EventMixin,
    ScheduleMixin,
    SettingsMixin,
    UrlsMixin,
    UserInterfaceMixin,
    InvenTreePlugin,
):
    """Print 3D printed parts through Bambuddy."""

    # Plugin metadata
    TITLE = 'Bambuddy 3D Printing'
    NAME = 'Bambuddy'
    SLUG = SLUG
    DESCRIPTION = 'Queue 3D prints in Bambuddy from InvenTree, by hand or when stock runs low'
    VERSION = PLUGIN_VERSION

    AUTHOR = 'EdgeMachines'
    WEBSITE = 'https://github.com/nemccarthy/inventree-bambuddy'
    LICENSE = 'MIT'

    # Developed and tested against InvenTree 1.7.3
    MIN_VERSION = '1.7.0'

    SETTINGS = {
        'BAMBUDDY_URL': {
            'name': 'Bambuddy address',
            'description': 'Address of the Bambuddy server as reached from the InvenTree server and worker, e.g. http://host.docker.internal:8000',
            'default': '',
        },
        'BAMBUDDY_API_KEY': {
            'name': 'Bambuddy API key',
            'description': 'API key from Bambuddy (Settings > API Keys) with permission to queue prints. Leave empty if Bambuddy runs without authentication',
            'default': '',
            'protected': True,
        },
        'BAMBUDDY_PUBLIC_URL': {
            'name': 'Bambuddy address for links',
            'description': 'Address of Bambuddy as opened in a browser, for the links to its print queue. Leave empty for no links',
            'default': '',
        },
        'AUTO_LOW_STOCK': {
            'name': 'Print when stock is low',
            'description': 'Queue a print run when a linked part falls below its minimum stock. Applies to parts with automatic printing switched on',
            'validator': bool,
            'default': False,
        },
        'AUTO_BUILD_DEMAND': {
            'name': 'Print when a build order needs parts',
            'description': 'Queue a print run when build orders in production need more of a linked part than there is. Applies to parts with automatic printing switched on',
            'validator': bool,
            'default': False,
        },
        'COUNT_PENDING_BUILDS': {
            'name': 'Count pending build orders',
            'description': 'Also count the requirements of build orders which have not been issued yet',
            'validator': bool,
            'default': False,
        },
        'MAX_AUTO_PLATES': {
            'name': 'Most plates to queue automatically',
            'description': 'The most plates one automatic print run may queue, unless set on the part',
            'validator': int,
            'default': 10,
        },
        'LEAD_DAYS': {
            'name': 'Days until printed parts are in stock',
            'description': 'Target date of the build order of a print run, in days from the day it is queued',
            'validator': int,
            'default': 1,
        },
        'DEFAULT_LOCATION': {
            'name': 'Stock location for printed parts',
            'description': 'Where printed parts are booked into stock, when neither the link nor the part has a default location',
            'model': 'stock.stocklocation',
            'default': '',
        },
        'SHOW_NAVIGATION': {
            'name': 'Show in the navigation bar',
            'description': 'Add a 3D Prints link to the main navigation bar',
            'validator': bool,
            'default': True,
        },
        'SHOW_PART_PANEL': {
            'name': 'Show on part pages',
            'description': 'Add a 3D Print panel to part pages',
            'validator': bool,
            'default': True,
        },
        'PANEL_LINKED_ONLY': {
            'name': 'Only on linked parts',
            'description': 'Show the 3D Print panel only on parts which are linked to a Bambuddy file. New parts are then linked from the 3D Prints page',
            'validator': bool,
            'default': False,
        },
    }

    # Background tasks (from ScheduleMixin). They need the InvenTree setting
    # "Enable schedule integration" and a running background worker.
    SCHEDULED_TASKS = {
        'poll': {'func': 'scheduled_poll', 'schedule': 'I', 'minutes': 2},
        'check': {'func': 'scheduled_check', 'schedule': 'I', 'minutes': 10},
    }

    # Helpers used by the rest of the plugin

    def client(self, timeout: float = 15):
        """A client for the configured Bambuddy server."""
        from .client import Bambuddy

        return Bambuddy(
            self.get_setting('BAMBUDDY_URL') or '',
            self.get_setting('BAMBUDDY_API_KEY') or '',
            timeout=timeout,
        )

    def int_setting(self, key: str, default: int = 0) -> int:
        try:
            return int(self.get_setting(key))
        except (TypeError, ValueError):
            return default

    def public_url(self) -> str:
        url = (self.get_setting('BAMBUDDY_PUBLIC_URL') or '').strip().rstrip('/')

        if url and '://' not in url:
            url = f'http://{url}'

        return url

    def default_location(self):
        """The stock location from the plugin settings, or None."""
        from stock.models import StockLocation

        value = self.get_setting('DEFAULT_LOCATION')

        try:
            return StockLocation.objects.filter(pk=int(value)).first()
        except (TypeError, ValueError):
            return None

    def auto_options(self) -> dict:
        return {
            'low_stock': bool(self.get_setting('AUTO_LOW_STOCK')),
            'build_demand': bool(self.get_setting('AUTO_BUILD_DEMAND')),
            'include_pending': bool(self.get_setting('COUNT_PENDING_BUILDS')),
            'max_plates': max(1, self.int_setting('MAX_AUTO_PLATES', 10)),
        }

    # Scheduled tasks

    def scheduled_poll(self, *args, **kwargs):
        """Follow the open print runs in the Bambuddy queue."""
        from . import runs

        if not self.client().configured:
            return

        result = runs.refresh(self)

        if result['error']:
            logger.warning('Bambuddy: could not read the print queue: %s', result['error'])

    def scheduled_check(self, *args, **kwargs):
        """Queue print runs for parts which are low on stock or short for a build."""
        from . import auto

        options = self.auto_options()

        if not (options['low_stock'] or options['build_demand']):
            return

        if not self.client().configured:
            return

        auto.check(self)

    # Events (from EventMixin). They need the InvenTree setting "Enable event
    # integration" and a running background worker.

    def wants_process_event(self, event: str) -> bool:
        return event in (EVENT_BUILD_ISSUED, EVENT_BUILD_CANCELLED)

    def process_event(self, event: str, *args, **kwargs) -> None:
        from build.models import Build

        from . import auto, runs

        build = Build.objects.filter(pk=kwargs.get('id')).first()

        if build is None:
            return

        if event == EVENT_BUILD_ISSUED:
            if self.get_setting('AUTO_BUILD_DEMAND') and self.client().configured:
                auto.check_for_build(self, build)

        elif event == EVENT_BUILD_CANCELLED:
            # A print run which was cancelled in InvenTree: take what has not
            # started out of the Bambuddy queue
            meta = runs.run_meta(build)

            if meta and any(
                item.get('status') == 'pending' for item in meta.get('items') or []
            ):
                result = runs.cancel_queue(self, build)

                for failure in result['failed']:
                    logger.warning(
                        'Bambuddy: could not cancel queue item %s of %s: %s',
                        failure['id'],
                        build.reference,
                        failure['message'],
                    )

    # Custom URL endpoints (from UrlsMixin), served below /plugin/bambuddy/

    def setup_urls(self):
        """Register the API endpoints."""
        from django.urls import path

        from . import views

        return [
            path('config/', views.ConfigView.as_view(), name='bambuddy-config'),
            path('overview/', views.OverviewView.as_view(), name='bambuddy-overview'),
            path('check/', views.CheckView.as_view(), name='bambuddy-check'),
            path('files/', views.FilesView.as_view(), name='bambuddy-files'),
            path('files/<int:pk>/', views.FileView.as_view(), name='bambuddy-file'),
            path(
                'files/<int:pk>/thumbnail/',
                views.ThumbnailView.as_view(),
                name='bambuddy-thumbnail',
            ),
            path('part/<int:pk>/', views.PartView.as_view(), name='bambuddy-part'),
            path('part/<int:pk>/print/', views.PrintView.as_view(), name='bambuddy-print'),
            path('run/<int:pk>/', views.RunView.as_view(), name='bambuddy-run'),
            path(
                'run/<int:pk>/<str:action>/',
                views.RunActionView.as_view(),
                name='bambuddy-run-action',
            ),
        ]

    # User interface (from UserInterfaceMixin)

    @staticmethod
    def _can_view(request) -> bool:
        from users.permissions import check_user_role

        user = getattr(request, 'user', None)

        return bool(user and check_user_role(user, 'part', 'view'))

    def get_ui_routes(self, request, context, **kwargs):
        """The 3D Prints page, at /web/plugin/bambuddy/prints."""
        return [
            {
                'key': 'bambuddy-prints',
                'title': '3D Prints',
                'source': self.plugin_static_file(f'{FRONTEND}:renderBambuddyPage'),
                'options': {'path': 'prints'},
            }
        ]

    def get_ui_navigation_items(self, request, context, **kwargs):
        """A link to the 3D Prints page in the main navigation bar."""
        if not self.get_setting('SHOW_NAVIGATION') or not self._can_view(request):
            return []

        return [
            {
                'key': 'bambuddy-prints',
                'title': '3D Prints',
                'icon': 'ti:printer:outline',
                'options': {'url': f'plugin/{self.SLUG}/prints'},
            }
        ]

    def get_ui_panels(self, request, context: dict, **kwargs):
        """Add the 3D Print panel to part pages."""
        context = context or {}

        if context.get('target_model') != 'part':
            return []

        if not self.get_setting('SHOW_PART_PANEL') or not self._can_view(request):
            return []

        try:
            part_id = int(context.get('target_id'))
        except (TypeError, ValueError):
            return []

        if self.get_setting('PANEL_LINKED_ONLY'):
            from part.models import Part

            from .links import get_link

            part = Part.objects.filter(pk=part_id).first()

            if part is None or get_link(part) is None:
                return []

        return [
            {
                'key': 'bambuddy',
                'title': '3D Print',
                'description': 'Print this part through Bambuddy',
                'icon': 'ti:printer:outline',
                'source': self.plugin_static_file(f'{FRONTEND}:renderBambuddyPartPanel'),
                'context': {'part_id': part_id, 'slug': self.SLUG},
            }
        ]
