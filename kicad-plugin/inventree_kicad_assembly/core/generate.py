"""Drive InteractiveHtmlBom's generator from inside KiCad.

iBOM is called through its own Python API rather than as a subprocess. Inside
KiCad its package is already importable and a parsed board is already in
memory, so this avoids having to locate an interpreter (KiCad's sys.executable
is the app binary, not python) and avoids re-parsing the board.

Only the settings this integration depends on are overridden; everything else
is left at whatever the user configured in iBOM's own dialog, so their
preferences carry over.
"""

import os
import sys

# The columns this integration adds, and the checkbox that means "on the board".
IPN_FIELD = "IPN"
LOCATION_FIELD = "Location"
REQUIRED_CHECKBOXES = ["Sourced", "Placed", "DNP"]


class GenerationError(RuntimeError):
    pass


def _import_ibom():
    """Import InteractiveHtmlBom, whichever plugin directory it sits in.

    KiCad has already put the 3rdparty plugin directory on sys.path when it
    loaded us, so an import normally just works. The search is a fallback for
    running outside KiCad, and for installs that use a different folder name.
    """
    try:
        from org_openscopeproject_InteractiveHtmlBom.core import ibom
        from org_openscopeproject_InteractiveHtmlBom.core.config import Config
        from org_openscopeproject_InteractiveHtmlBom.ecad.kicad import PcbnewParser
        from org_openscopeproject_InteractiveHtmlBom.version import version
        return ibom, Config, PcbnewParser, version
    except ImportError:
        pass

    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    plugins_dir = os.path.dirname(here)
    for name in sorted(os.listdir(plugins_dir)):
        if "InteractiveHtmlBom" not in name:
            continue
        candidate = os.path.join(plugins_dir, name)
        if not os.path.isfile(os.path.join(candidate, "core", "ibom.py")):
            continue
        if plugins_dir not in sys.path:
            sys.path.insert(0, plugins_dir)
        pkg = __import__(
            f"{name}.core.ibom", fromlist=["ibom"]
        )
        cfg = __import__(f"{name}.core.config", fromlist=["Config"])
        kic = __import__(f"{name}.ecad.kicad", fromlist=["PcbnewParser"])
        ver = __import__(f"{name}.version", fromlist=["version"])
        return pkg, cfg.Config, kic.PcbnewParser, ver.version

    raise GenerationError(
        "InteractiveHtmlBom not found. Install it from KiCad's Plugin and "
        "Content Manager, then try again."
    )


def generate_ibom(board, pcb_path, extra_data_file, dest_dir, name="ibom"):
    """Render an ibom.html for `board`, returning the path written.

    `board` is a live pcbnew BOARD. `extra_data_file` is the XML produced from
    the build order, which supplies the IPN and Location columns.
    """
    ibom, Config, PcbnewParser, version = _import_ibom()

    logger = ibom.Logger()
    config = Config(version, os.path.dirname(pcb_path))

    config.extra_data_file = extra_data_file
    # Location is shown but deliberately not grouped on: otherwise one part
    # split across two bins becomes two BOM rows, which is noise to assemble
    # from. Grouping stays on identity, which IPN sharpens.
    config.show_fields = ["Value", "Footprint", IPN_FIELD, LOCATION_FIELD]
    config.group_fields = ["Value", "Footprint", IPN_FIELD]
    config.checkboxes = ",".join(REQUIRED_CHECKBOXES)
    config.open_browser = False
    config.bom_dest_dir = dest_dir
    config.bom_name_format = name

    parser = PcbnewParser(pcb_path, config, logger, board)
    ibom.main(parser, config, logger)

    written = os.path.join(dest_dir, f"{name}.html")
    if not os.path.isfile(written):
        raise GenerationError(f"iBOM reported success but {written} is missing")
    return written
