"""KiCad 10 design variants.

A variant changes which parts are fitted, and can override field values per
symbol, so two variants of one design are two different bills of materials.
That maps cleanly onto InvenTree, where each variant is already its own
assembly part with its own BOM -- so the variant is chosen alongside the
assembly, and the pairing is the user's to make.

Two traps worth knowing, both found by testing rather than reading:

* `kicad-cli sch export bom --variant` **silently accepts a name that does not
  exist**, exiting 0 and returning the default variant's BOM. A typo would
  therefore write the base product's parts into a variant's assembly. Names are
  validated here against what the design actually declares.
* `FOOTPRINT.IsDNP()` always reports the *default* variant, even after
  `BOARD.SetCurrentVariant()`. The variant-resolved answer comes from
  `GetDNPForVariant()`, which is what `apply_to_board` uses.
"""

import json
import os

#: KiCad's unnamed base variant.
DEFAULT = ""
#: What KiCad's own UI calls it, which is not a usable variant name.
DEFAULT_LABEL = "< Default >"


def label_for(name, description=""):
    if not name:
        return "Default (no variant)"
    return f"{name} — {description}" if description else name


def from_board(board):
    """[(name, description)] for a live pcbnew BOARD, default first."""
    out = [(DEFAULT, "")]
    try:
        names = list(board.GetVariantNamesForUI())
    except Exception:
        return out
    for name in names:
        name = str(name)
        if not name or name == DEFAULT_LABEL:
            continue
        try:
            description = board.GetVariantDescription(name) or ""
        except Exception:
            description = ""
        out.append((name, description))
    return out


def from_project(path):
    """Same, read from the .kicad_pro beside a board or schematic.

    Used where there is no pcbnew BOARD to ask -- the BOM export path runs off
    the schematic, and the sync needs to validate a name before shelling out.
    """
    out = [(DEFAULT, "")]
    project = os.path.splitext(path)[0] + ".kicad_pro"
    if not os.path.isfile(project):
        return out
    try:
        with open(project, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return out
    for entry in (data.get("schematic") or {}).get("variants") or []:
        name = (entry.get("name") or "").strip()
        if name:
            out.append((name, entry.get("description") or ""))
    return out


def is_declared(path, name):
    """Is `name` a variant this design declares? The default always is."""
    if not name:
        return True
    return any(n == name for n, _d in from_project(path))


def not_fitted(board, variant=DEFAULT):
    """References the board does not populate in this variant."""
    out = set()
    for footprint in board.GetFootprints():
        try:
            dnp = footprint.GetDNPForVariant(variant)
        except Exception:
            dnp = footprint.IsDNP()
        if dnp:
            out.add(footprint.GetReference())
    return out


def apply_to_board(pcb_path, variant):
    """A private copy of the board with a variant's fitted set stamped on.

    iBOM reads `IsDNP()`, which ignores the variant, so the variant has to be
    resolved into the flags it reads. Deliberately a freshly loaded copy: the
    board object KiCad has open is the user's document, and editing it to
    render a BOM would dirty their design.
    """
    import pcbnew

    board = pcbnew.LoadBoard(pcb_path)
    for footprint in board.GetFootprints():
        try:
            footprint.SetDNP(footprint.GetDNPForVariant(variant))
            footprint.SetExcludedFromBOM(
                footprint.GetExcludedFromBOMForVariant(variant)
            )
        except AttributeError:
            # An older KiCad has no variants; the board is already correct.
            break
    return board
