"""Write a small stand-in for a sliced Bambu Studio file (.gcode.3mf).

For development and testing only: it carries the metadata Bambuddy reads
(printer model, print time, filament), not printable G-code.

    python dev/make_sliced_3mf.py out.gcode.3mf --model C12 --seconds 5400 --grams 42.5
"""

from __future__ import annotations

import argparse
import hashlib
import zipfile

SLICE_INFO = """<?xml version="1.0" encoding="UTF-8"?>
<config>
  <header>
    <header_item key="X-BBL-Client-Type" value="slicer"/>
    <header_item key="X-BBL-Client-Version" value="02.02.00.85"/>
  </header>
  <plate>
    <metadata key="index" value="1"/>
    <metadata key="printer_model_id" value="{model}"/>
    <metadata key="nozzle_diameters" value="0.4"/>
    <metadata key="timelapse_type" value="0"/>
    <metadata key="prediction" value="{seconds}"/>
    <metadata key="weight" value="{grams}"/>
    <metadata key="outside" value="false"/>
    <metadata key="support_used" value="false"/>
    <metadata key="label_object_enabled" value="true"/>
    <object identify_id="101" name="{name}" skipped="false"/>
    <filament id="1" tray_info_idx="GFG00" type="{filament}" color="#161616" used_m="14.20" used_g="{grams}"/>
  </plate>
</config>
"""

MODEL_SETTINGS = """<?xml version="1.0" encoding="UTF-8"?>
<config>
  <plate>
    <metadata key="plater_id" value="1"/>
    <metadata key="plater_name" value=""/>
    <metadata key="locked" value="false"/>
    <metadata key="gcode_file" value="Metadata/plate_1.gcode"/>
  </plate>
</config>
"""

GCODE = """; HEADER_BLOCK_START
; BambuStudio 02.02.00.85
; model printing time: {seconds}s; total estimated time: {seconds}s
; total layer number: 120
; HEADER_BLOCK_END
; printer_model = Bambu Lab P1S
; filament_type = {filament}
G28
G1 X10 Y10 Z0.2 F3000
M400
"""


def build(path: str, model: str, seconds: int, grams: float, filament: str, name: str):
    gcode = GCODE.format(seconds=seconds, filament=filament)

    with zipfile.ZipFile(path, 'w', zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(
            '[Content_Types].xml',
            '<?xml version="1.0" encoding="UTF-8"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"/>',
        )
        archive.writestr(
            'Metadata/slice_info.config',
            SLICE_INFO.format(
                model=model, seconds=seconds, grams=grams, filament=filament, name=name
            ),
        )
        archive.writestr('Metadata/model_settings.config', MODEL_SETTINGS)
        archive.writestr('Metadata/plate_1.gcode', gcode)
        archive.writestr(
            'Metadata/plate_1.gcode.md5', hashlib.md5(gcode.encode()).hexdigest().upper()
        )


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('path')
    parser.add_argument('--model', default='C12', help='Bambu model code (C12 = P1S)')
    parser.add_argument('--seconds', type=int, default=5400)
    parser.add_argument('--grams', type=float, default=42.5)
    parser.add_argument('--filament', default='PETG')
    parser.add_argument('--name', default='part')
    args = parser.parse_args()

    build(args.path, args.model, args.seconds, args.grams, args.filament, args.name)
