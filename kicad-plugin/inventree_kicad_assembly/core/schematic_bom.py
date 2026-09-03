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

# Generated fields KiCad computes rather than reads off the symbol. Requested
# so that excluded symbols can be reported rather than silently vanishing:
# --exclude-dnp drops the rows, and then nothing can say what was dropped.
DNP_FIELD = "DNP"
NOT_IN_BOM_FIELD = "EXCLUDE_FROM_BOM"

# Superset requested from kicad-cli; absent fields simply come back empty.
_EXPORT_FIELDS = ["Reference", "Value", "Footprint", "Description",
                  DNP_FIELD, NOT_IN_BOM_FIELD] + \
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


#: Why a symbol is not part of the InvenTree BOM, in the words the user sees.
DNP = "DNP"
NOT_IN_BOM = "excluded from BOM"


def _exclusion(row):
    """Why KiCad leaves this symbol out, or "" if it does not.

    The generated columns hold a human string ("DNP") rather than a boolean, so
    any non-empty value means the flag is set.
    """
    if (row.get(DNP_FIELD) or "").strip():
        return DNP
    # Belt and braces: kicad-cli drops excluded-from-BOM symbols before the
    # export, so this column is empty on every row it does emit (verified --
    # this design has ten such footprints and none of them reach the CSV).
    # Kept in case that ever changes, since silently importing a mounting hole
    # would be worse than an unreachable branch.
    if (row.get(NOT_IN_BOM_FIELD) or "").strip():
        return NOT_IN_BOM
    return ""


def split_excluded(rows):
    """(kept, excluded) -- what goes to InvenTree, and what deliberately does not.

    InvenTree has no notion of "fitted": a BOM line is a thing to buy, allocate
    and consume. A part the board does not populate is none of those, so it is
    left out entirely rather than added and flagged. The variant it *is* fitted
    in has its own assembly part, and syncing that variant puts it there.
    """
    kept = [r for r in rows if not r.get("excluded")]
    excluded = [r for r in rows if r.get("excluded")]
    return kept, excluded


def read_bom(sch_path, variant=""):
    """One row per symbol: {ref, value, footprint, ipn, mpn, sku, excluded, ...}.

    Reference ranges are disabled so each symbol is its own row -- 'D1-D4'
    would otherwise have to be expanded here, and getting that subtly wrong
    would mis-assign parts.

    Nothing is filtered. Symbols KiCad marks Do-Not-Populate or excluded from
    the BOM come back with `excluded` set to say which, and it is the caller's
    job to leave them out of InvenTree -- reporting what it left out. Asking
    kicad-cli to drop them instead loses the ability to say anything at all,
    and a part missing from a BOM with no explanation is the kind of thing
    that gets noticed at assembly time.

    `variant` selects a KiCad 10 design variant, which decides both the fitted
    set and any per-variant field values. It is validated by the caller --
    kicad-cli accepts an unknown name silently and hands back the default.
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
    if variant:
        cmd[-1:-1] = ["--variant", variant]

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
                "excluded": _exclusion(row),
                "value": row.get("Value", ""),
                "footprint": row.get("Footprint", ""),
                "description": row.get("Description", ""),
                "ipn": _first(row, IPN_FIELDS),
                "mpn": _first(row, MPN_FIELDS),
                "sku": _first(row, SKU_FIELDS),
            })
    return rows
