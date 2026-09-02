"""Read a design's BOM using KiCad's own exporter.

`kicad-cli sch export bom` is used rather than parsing the .kicad_sch files
directly, because it already resolves the sheet hierarchy (which can span
directories), applies field inheritance, and honours Do-Not-Populate. The
s-expression handling in schematic.py exists only for the write-back, where
KiCad offers no equivalent.

Field names are not standardised across libraries, so several spellings are
accepted for each concept and the first non-empty one wins.
"""

import csv
import os
import shutil
import subprocess
import tempfile

IPN_FIELDS = ["InvenTree_IPN", "InvenTree IPN", "IPN"]
MPN_FIELDS = ["Manufacturer Part", "Manufacturer Part Number", "MPN", "ManufacturerPartNumber"]
SKU_FIELDS = ["LCSC", "LCSC Part", "LCSC Part #", "JLCPCB", "SKU"]

# Superset requested from kicad-cli; absent fields simply come back empty.
_EXPORT_FIELDS = ["Reference", "Value", "Footprint", "Description"] + \
    IPN_FIELDS + MPN_FIELDS + SKU_FIELDS


class SchematicError(RuntimeError):
    pass


def find_kicad_cli():
    """Locate kicad-cli, which is not on PATH in a normal macOS install."""
    found = shutil.which("kicad-cli")
    if found:
        return found
    candidates = [
        "/Applications/KiCad/KiCad.app/Contents/MacOS/kicad-cli",
        "/usr/bin/kicad-cli",
        "/usr/local/bin/kicad-cli",
        r"C:\Program Files\KiCad\9.0\bin\kicad-cli.exe",
        r"C:\Program Files\KiCad\10.0\bin\kicad-cli.exe",
    ]
    for path in candidates:
        if os.path.isfile(path):
            return path
    raise SchematicError(
        "kicad-cli not found. It ships with KiCad; if this is an unusual "
        "install, put it on PATH."
    )


def schematic_for_board(pcb_path):
    """The .kicad_sch beside a .kicad_pcb, which KiCad keeps same-named."""
    candidate = os.path.splitext(pcb_path)[0] + ".kicad_sch"
    if not os.path.isfile(candidate):
        raise SchematicError(f"No schematic found next to the board at {candidate}")
    return candidate


def _first(row, names):
    for name in names:
        value = (row.get(name) or "").strip()
        if value:
            return value
    return ""


def read_bom(sch_path, exclude_dnp=True):
    """One row per symbol: {ref, value, footprint, ipn, mpn, sku, fields}.

    Reference ranges are disabled so each symbol is its own row -- 'D1-D4'
    would otherwise have to be expanded here, and getting that subtly wrong
    would mis-assign parts.
    """
    cli = find_kicad_cli()
    out_dir = tempfile.mkdtemp(prefix="inventree-kicad-bom-")
    csv_path = os.path.join(out_dir, "bom.csv")

    cmd = [
        cli, "sch", "export", "bom",
        "--fields", ",".join(_EXPORT_FIELDS),
        "--ref-range-delimiter", "",
        "--output", csv_path,
        sch_path,
    ]
    if exclude_dnp:
        cmd.insert(-1, "--exclude-dnp")

    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0 or not os.path.isfile(csv_path):
        raise SchematicError(
            f"kicad-cli could not export the BOM:\n{result.stderr or result.stdout}"
        )

    rows = []
    with open(csv_path, newline="", encoding="utf-8-sig") as f:
        for raw in csv.DictReader(f):
            row = {(k or "").strip(): (v or "").strip() for k, v in raw.items()}
            reference = row.get("Reference", "")
            if not reference:
                continue
            rows.append({
                "ref": reference,
                "value": row.get("Value", ""),
                "footprint": row.get("Footprint", ""),
                "description": row.get("Description", ""),
                "ipn": _first(row, IPN_FIELDS),
                "mpn": _first(row, MPN_FIELDS),
                "sku": _first(row, SKU_FIELDS),
            })
    return rows
