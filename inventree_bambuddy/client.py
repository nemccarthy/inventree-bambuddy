"""A small client for the Bambuddy REST API (/api/v1).

Plain Python: it only needs 'requests', which InvenTree already ships.
"""

from __future__ import annotations

import requests

# The source name under which print batches are tagged in Bambuddy, so that a
# batch can be traced back to the InvenTree build order it prints for
EXTERNAL_SOURCE = 'inventree'

# Library file types which are sliced and ready to print
SLICED_TYPES = ('gcode.3mf', 'gcode')


class BambuddyError(Exception):
    """A request to Bambuddy failed."""

    def __init__(self, message: str, status: int | None = None, detail=None):
        super().__init__(message)
        self.message = message
        self.status = status
        self.detail = detail

    @property
    def not_found(self) -> bool:
        return self.status == 404

    @property
    def code(self) -> str | None:
        """Bambuddy's error code, where it sends one (e.g. 'unmatched_filament')."""
        if isinstance(self.detail, dict):
            return self.detail.get('code')

        return None


def normalise_url(url: str) -> str:
    """Base address of the API, from what the user typed in the settings."""
    url = (url or '').strip().rstrip('/')

    if not url:
        return ''

    if '://' not in url:
        url = f'http://{url}'

    for suffix in ('/api/v1', '/api'):
        if url.endswith(suffix):
            url = url[: -len(suffix)]

    return f'{url}/api/v1'


def is_sliced(file: dict) -> bool:
    """True if a library file is a sliced file which can be queued."""
    file_type = (file.get('file_type') or '').lower()
    name = (file.get('filename') or '').lower()

    return file_type in SLICED_TYPES or name.endswith(('.gcode.3mf', '.gcode'))


def _describe(detail) -> str:
    """Readable text from the 'detail' of a FastAPI error response."""
    if isinstance(detail, str):
        return detail

    if isinstance(detail, dict):
        return str(detail.get('message') or detail.get('detail') or detail)

    if isinstance(detail, list):
        parts = []

        for entry in detail:
            if isinstance(entry, dict):
                where = '.'.join(str(x) for x in entry.get('loc', []) if x != 'body')
                text = entry.get('msg', '')
                parts.append(f'{where}: {text}' if where else text)
            else:
                parts.append(str(entry))

        return '; '.join(parts)

    return str(detail)


def text_snippet(body: str, limit: int = 140) -> str:
    """The readable start of a response body: tags and extra spaces removed."""
    import re

    text = re.sub(r'(?is)<(script|style)\b.*?</\1>', ' ', body or '')
    text = re.sub(r'<[^>]+>', ' ', text)
    text = ' '.join(text.split())

    if len(text) > limit:
        text = text[:limit].rstrip() + '...'

    return text


def describe_stranger(response, base: str) -> str:
    """What answered in place of Bambuddy's API, and the likely way to fix it."""
    try:
        body = response.text or ''
    except Exception:  # pragma: no cover - a body which cannot be decoded
        body = ''

    snippet = text_snippet(body)
    server = (response.headers.get('Server') or '').strip()

    parts = []

    if snippet:
        parts.append(f'It said: "{snippet}"')

    if server:
        parts.append(f'Server: {server}.')

    lowered = body.lower()

    if base.startswith('http://') and 'https' in lowered and (
        'http request' in lowered or 'plain http' in lowered
    ):
        parts.append('That port expects HTTPS: use https:// in the Bambuddy address.')
    elif (
        'inventree' in lowered
        or 'gunicorn' in server.lower()
        or 'bad request (400)' in lowered
    ):
        # Django's own error page: the request reached InvenTree, directly or
        # through its proxy on ports 80 and 443
        parts.append(
            'This looks like InvenTree itself, not Bambuddy. Give the address with '
            "Bambuddy's port, usually http://<address of the machine>:8000. Inside the "
            'InvenTree containers, localhost is the container, not the machine.'
        )
    else:
        parts.append(
            'Check the address and port in the plugin settings: Bambuddy answers on '
            'http://<host>:8000 unless it was set up differently.'
        )

    return ' '.join(parts)


class Bambuddy:
    """Client for one Bambuddy server."""

    def __init__(self, url: str, api_key: str = '', timeout: float = 15):
        self.base = normalise_url(url)
        self.api_key = (api_key or '').strip()
        self.timeout = timeout

    @property
    def configured(self) -> bool:
        return bool(self.base)

    def _headers(self) -> dict:
        headers = {'Accept': 'application/json'}

        if self.api_key:
            headers['X-API-Key'] = self.api_key

        return headers

    def _send(self, method: str, path: str, **kwargs) -> requests.Response:
        if not self.base:
            raise BambuddyError('The address of the Bambuddy server is not set')

        try:
            response = requests.request(
                method,
                f'{self.base}{path}',
                headers=self._headers(),
                timeout=self.timeout,
                **kwargs,
            )
        except requests.exceptions.Timeout:
            raise BambuddyError(
                f'Bambuddy did not answer within {self.timeout:g} seconds'
            ) from None
        except requests.exceptions.RequestException as exc:
            reason = type(exc).__name__

            if isinstance(exc, requests.exceptions.SSLError):
                reason = (
                    'the HTTPS certificate was not accepted (SSLError). If Bambuddy is '
                    'also reachable without HTTPS on the local network, use that address'
                )
            elif isinstance(exc, requests.exceptions.ConnectionError):
                reason = 'no connection (wrong address or port, or blocked by a firewall)'

            raise BambuddyError(f'Could not reach Bambuddy at {self.base}: {reason}') from None

        if response.status_code >= 400:
            detail = None

            try:
                detail = response.json().get('detail')
            except (ValueError, AttributeError):
                pass

            if response.status_code in (401, 403):
                message = (
                    'Bambuddy refused the request: check the API key and its permissions'
                )

                if detail:
                    message = f'{message} ({_describe(detail)})'
            elif detail:
                message = _describe(detail)
            else:
                # Bambuddy gives a reason with its errors. Without one, something
                # else answered: say what, so that the address can be put right.
                message = (
                    f'The address {self.base} answered with error {response.status_code}, '
                    f'but not the way Bambuddy does. {describe_stranger(response, self.base)}'
                )

            raise BambuddyError(message, status=response.status_code, detail=detail)

        return response

    def _json(self, method: str, path: str, **kwargs):
        response = self._send(method, path, **kwargs)

        if not response.content:
            return None

        try:
            return response.json()
        except ValueError:
            raise BambuddyError(
                f'The address {self.base} answered, but not with Bambuddy data. '
                f'{describe_stranger(response, self.base)}'
            ) from None

    # Server

    def info(self) -> dict:
        """Version and database statistics."""
        return self._json('GET', '/system/info')

    def printers(self) -> list[dict]:
        return self._json('GET', '/printers/') or []

    # Library

    def files(self) -> list[dict]:
        """Every file in the library (all folders)."""
        return self._json('GET', '/library/files/', params={'include_root': 'false'}) or []

    def folder_names(self) -> dict:
        """Path of every library folder, by id. Empty if the tree cannot be read."""
        try:
            tree = self._json('GET', '/library/folders') or []
        except BambuddyError:
            return {}

        names: dict = {}

        def walk(nodes, prefix):
            for node in nodes or []:
                path = f"{prefix}/{node.get('name')}" if prefix else str(node.get('name'))
                names[node.get('id')] = path
                walk(node.get('children'), path)

        walk(tree, '')

        return names

    def file(self, file_id: int) -> dict:
        return self._json('GET', f'/library/files/{int(file_id)}')

    def plates(self, file_id: int) -> dict:
        return self._json('GET', f'/library/files/{int(file_id)}/plates')

    def thumbnail(self, file_id: int) -> tuple[bytes, str]:
        """Thumbnail image of a library file: (content, content type)."""
        response = self._send('GET', f'/library/files/{int(file_id)}/thumbnail')

        return response.content, response.headers.get('Content-Type', 'image/png')

    # Queue

    def queue(self) -> list[dict]:
        """Every queue item which Bambuddy still knows about, in any state."""
        return self._json('GET', '/queue/') or []

    def queue_item(self, item_id: int) -> dict:
        return self._json('GET', f'/queue/{int(item_id)}')

    def create_batch(
        self,
        *,
        name: str,
        file_id: int,
        plate_id: int | None,
        plates: int,
        reference: str,
        notes: str = '',
    ) -> dict:
        """Create an empty batch which is tagged with the build order reference.

        Bambuddy refuses a second batch with the same reference (error 409).
        """
        return self._json(
            'POST',
            '/queue/batches',
            json={
                'name': name[:200],
                'library_file_id': int(file_id),
                'plates': [{'plate_id': plate_id, 'quantity_target': int(plates)}],
                'notes': notes or None,
                'external_source': EXTERNAL_SOURCE,
                'external_ref': reference,
            },
        )

    def set_batch_plates(self, batch_id: int, plate_id: int | None, plates: int) -> dict:
        """Change the number of plates a batch is meant to print."""
        return self._json(
            'PATCH',
            f'/queue/batches/{int(batch_id)}',
            json={'plates': [{'plate_id': plate_id, 'quantity_target': int(plates)}]},
        )

    def add_to_queue(
        self,
        *,
        file_id: int,
        plate_id: int | None,
        copies: int,
        printer_id: int | None = None,
        target_model: str | None = None,
        target_location: str | None = None,
        batch_id: int | None = None,
        staged: bool = True,
    ) -> dict:
        """Queue copies of a library file. Returns the first queue item."""
        payload: dict = {
            'library_file_id': int(file_id),
            'manual_start': bool(staged),
            'quantity': int(copies),
        }

        if plate_id is not None:
            payload['plate_id'] = int(plate_id)

        if printer_id:
            payload['printer_id'] = int(printer_id)
        elif target_model:
            payload['target_model'] = target_model

            if target_location:
                payload['target_location'] = target_location

        if batch_id is not None:
            payload['batch_id'] = int(batch_id)

        return self._json('POST', '/queue/', json=payload)

    def start_item(self, item_id: int, skip_filament_check: bool = False) -> dict:
        """Release a staged queue item, so that Bambuddy starts it when it can."""
        params = {'skip_filament_check': 'true'} if skip_filament_check else None

        return self._json('POST', f'/queue/{int(item_id)}/start', params=params)

    def cancel_item(self, item_id: int) -> None:
        """Cancel a queue item which has not started printing."""
        self._json('POST', f'/queue/{int(item_id)}/cancel')

    def cancel_batch(self, batch_id: int) -> None:
        """Cancel every pending item of a batch."""
        self._json('DELETE', f'/queue/batches/{int(batch_id)}')
