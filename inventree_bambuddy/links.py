"""The link between an InvenTree part and its sliced file in Bambuddy.

The link is stored in the metadata of the part, under the key 'bambuddy':

    {
        "file_id": 12,              # library file in Bambuddy
        "filename": "insert-x4_P1S_PETG.gcode.3mf",
        "file_hash": "bd36a0...",   # to notice when the file is replaced
        "plate_id": null,           # plate of a multi-plate file
        "parts_per_plate": 4,
        "printer_id": null,         # a specific printer, or
        "target_model": "P1S",      # any printer of a model
        "target_location": null,    # ... optionally at one location
        "auto": true,               # may be queued automatically
        "restock_to": 40,           # level to print up to (optional)
        "max_plates": null,         # most plates to queue at once (optional)
        "destination": 7            # stock location for the printed parts (optional)
    }

plus a copy of what Bambuddy says about the file (print time, filament), for
display.
"""

from __future__ import annotations

from .client import Bambuddy, BambuddyError, is_sliced

KEY = 'bambuddy'


class LinkError(Exception):
    """The link could not be saved. 'errors' maps a field name to a message."""

    def __init__(self, errors: dict | str):
        if isinstance(errors, str):
            errors = {'detail': errors}

        super().__init__('; '.join(str(v) for v in errors.values()))
        self.errors = errors


def get_link(part) -> dict | None:
    """The stored link of a part, or None."""
    metadata = part.metadata if isinstance(part.metadata, dict) else {}
    link = metadata.get(KEY)

    if isinstance(link, dict) and link.get('file_id'):
        return link

    return None


def linked_parts():
    """Queryset of the parts which are linked to a Bambuddy file."""
    from part.models import Part

    return Part.objects.filter(metadata__has_key=KEY).order_by('name', 'pk')


def _positive_int(value, field: str, errors: dict, optional: bool = True):
    if value in (None, ''):
        if not optional:
            errors[field] = 'This value is required'

        return None

    try:
        number = int(value)
    except (TypeError, ValueError):
        errors[field] = 'Enter a whole number'
        return None

    if number < 1 or number != float(value):
        errors[field] = 'Enter a whole number greater than zero'
        return None

    return number


def _plate_details(client: Bambuddy, file: dict, plate_id, errors: dict) -> dict:
    """Print time and filament of the chosen plate, checked against the file."""
    details = {
        'plate_id': None,
        'plate_name': None,
        'print_time_seconds': file.get('print_time_seconds'),
        'filament_grams': file.get('filament_used_grams'),
    }

    try:
        info = client.plates(file['id'])
    except BambuddyError:
        # Not every sliced file can be read plate by plate (plain .gcode)
        info = {}

    plates = info.get('plates') or []
    indexes = [plate.get('index') for plate in plates]

    if len(plates) > 1:
        if plate_id is None:
            errors['plate_id'] = 'This file has more than one plate: choose the plate to print'
            return details

        if plate_id not in indexes:
            errors['plate_id'] = 'This plate does not exist in the file'
            return details

        details['plate_id'] = plate_id
    elif plate_id is not None and plates and plate_id not in indexes:
        errors['plate_id'] = 'This plate does not exist in the file'
        return details

    for plate in plates:
        if plate.get('index') == (details['plate_id'] or indexes[0]):
            details['plate_name'] = plate.get('name') or None

            if plate.get('print_time_seconds') is not None:
                details['print_time_seconds'] = plate['print_time_seconds']

            if plate.get('filament_used_grams') is not None:
                details['filament_grams'] = plate['filament_used_grams']

            filaments = plate.get('filaments') or []
            details['filaments'] = [
                {
                    'type': filament.get('type'),
                    'color': filament.get('color'),
                    'grams': filament.get('used_grams'),
                }
                for filament in filaments
            ]

    return details


def build_link(part, data: dict, client: Bambuddy, user=None) -> dict:
    """Validate the submitted link against the part and against Bambuddy.

    Returns the link to store. Raises LinkError with a message for each field
    which is wrong.
    """
    import InvenTree.helpers
    from stock.models import StockLocation

    errors: dict = {}

    if part.trackable:
        raise LinkError(
            'This part is tracked by serial number. Printed parts are booked into stock as one batch, so serialised parts cannot be linked.'
        )

    if part.virtual:
        raise LinkError('A virtual part cannot be printed.')

    if not part.active:
        raise LinkError('This part is inactive.')

    file_id = _positive_int(data.get('file_id'), 'file_id', errors, optional=False)
    per_plate = _positive_int(
        data.get('parts_per_plate'), 'parts_per_plate', errors, optional=False
    )
    plate_id = _positive_int(data.get('plate_id'), 'plate_id', errors)
    printer_id = _positive_int(data.get('printer_id'), 'printer_id', errors)
    max_plates = _positive_int(data.get('max_plates'), 'max_plates', errors)
    destination = _positive_int(data.get('destination'), 'destination', errors)

    restock_to = data.get('restock_to')

    if restock_to in (None, ''):
        restock_to = None
    else:
        try:
            restock_to = float(restock_to)

            if restock_to < 0:
                raise ValueError
        except (TypeError, ValueError):
            errors['restock_to'] = 'Enter a number, or leave it empty'
            restock_to = None

    target_model = (data.get('target_model') or '').strip() or None
    target_location = (data.get('target_location') or '').strip() or None

    if destination and not StockLocation.objects.filter(pk=destination).exists():
        errors['destination'] = 'This stock location does not exist'

    if errors.get('file_id'):
        raise LinkError(errors)

    # The file, as Bambuddy knows it

    try:
        file = client.file(file_id)
    except BambuddyError as exc:
        if exc.not_found:
            raise LinkError({'file_id': 'This file no longer exists in Bambuddy'}) from None

        raise

    if not is_sliced(file):
        errors['file_id'] = (
            'This file is not sliced. Slice it and save it to Bambuddy as .gcode.3mf first.'
        )
        raise LinkError(errors)

    metadata = file.get('metadata') or {}
    details = _plate_details(client, file, plate_id, errors)
    sliced_for = file.get('sliced_for_model') or metadata.get('sliced_for_model')

    # Where to print: a specific printer, or any printer of a model

    printer_name = None

    if printer_id:
        printers = {printer['id']: printer for printer in client.printers()}

        if printer_id not in printers:
            errors['printer_id'] = 'This printer does not exist in Bambuddy'
        else:
            printer_name = printers[printer_id].get('name')

        target_model = None
        target_location = None
    elif not target_model:
        target_model = sliced_for

        if not target_model:
            errors['printer_id'] = (
                'The file does not say which printer it was sliced for: choose a printer'
            )

    if not target_model:
        target_location = None

    if errors:
        raise LinkError(errors)

    previous = get_link(part) or {}

    return {
        'file_id': file['id'],
        'filename': file.get('filename'),
        'file_hash': file.get('file_hash'),
        'folder': file.get('folder_name'),
        'plate_id': details['plate_id'],
        'plate_name': details.get('plate_name'),
        'parts_per_plate': per_plate,
        'printer_id': printer_id,
        'printer_name': printer_name,
        'target_model': target_model,
        'target_location': target_location,
        'auto': bool(data.get('auto')),
        'restock_to': restock_to,
        'max_plates': max_plates,
        'destination': destination,
        # A copy of what Bambuddy knows about the file, for display
        'print_name': file.get('print_name') or metadata.get('print_name'),
        'sliced_for_model': sliced_for,
        'print_time_seconds': details.get('print_time_seconds'),
        'filament_grams': details.get('filament_grams'),
        'filaments': details.get('filaments')
        or [
            {
                'type': slot.get('type'),
                'color': slot.get('color'),
                'grams': slot.get('used_g'),
            }
            for slot in metadata.get('filament_slots') or []
        ],
        'has_thumbnail': bool(file.get('thumbnail_path')),
        'linked': previous.get('linked') or InvenTree.helpers.current_date().isoformat(),
        'linked_by': previous.get('linked_by') or (user.username if user else None),
    }


def save_link(part, link: dict) -> bool:
    """Store the link on the part.

    InvenTree only raises build orders for assemblies, so the part is marked
    as an assembly if it is not one yet. Returns True if that was changed.
    """
    made_assembly = False

    if not part.assembly:
        part.assembly = True
        made_assembly = True

    metadata = dict(part.metadata) if isinstance(part.metadata, dict) else {}
    metadata[KEY] = link
    part.metadata = metadata
    part.save()

    return made_assembly


def clear_link(part) -> None:
    """Remove the link from the part. Print runs which exist are not touched."""
    metadata = dict(part.metadata) if isinstance(part.metadata, dict) else {}

    if KEY in metadata:
        del metadata[KEY]
        part.metadata = metadata
        part.save()


def check_file(link: dict, client: Bambuddy) -> dict:
    """Fetch the linked file from Bambuddy, just before printing.

    Raises LinkError if the file was removed. A file which was replaced in
    place (same id, different content) is reported in 'changed'.
    """
    try:
        file = client.file(link['file_id'])
    except BambuddyError as exc:
        if exc.not_found:
            raise LinkError(
                f"The file '{link.get('filename')}' no longer exists in Bambuddy. Link the part to the new file first."
            ) from None

        raise

    return {
        'file': file,
        'changed': bool(
            link.get('file_hash')
            and file.get('file_hash')
            and link['file_hash'] != file['file_hash']
        ),
    }
