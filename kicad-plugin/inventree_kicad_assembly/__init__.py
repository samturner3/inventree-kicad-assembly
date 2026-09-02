"""KiCad Action Plugin: sync a design's BOM to InvenTree, and generate a
build-order-scoped InteractiveHtmlBom for it.

KiCad imports this package by path when it is placed (or symlinked) into the
KiCad plugin directory -- see kicad-plugin/README.md for the install step.
Registration happens on import, matching how InteractiveHtmlBom's own plugin
does it.

Registration is skipped when this is used as a library or from the CLI. That
distinction matters more than it looks: pcbnew is importable from KiCad's
bundled Python even outside the KiCad app, so a naive "can I import pcbnew"
check registers plugins during an ordinary command-line run, and the process
then dies with "The application handle was destroyed after running Python
plugin".
"""

import os
import sys


def _running_as_plugin():
    """True only when KiCad itself is loading this as a plugin."""
    if os.environ.get("INVENTREE_KICAD_ASSEMBLY_NO_REGISTER"):
        return False
    # Started as our own CLI rather than by KiCad's plugin loader.
    if os.path.basename(sys.argv[0] or "") in ("cli.py", "inventree_assembly_cli.py"):
        return False
    try:
        import pcbnew  # noqa: F401  (presence check only)
    except ImportError:
        return False
    return True


if _running_as_plugin():
    from .actions import GenerateBuildIbomAction, SyncBomAction

    SyncBomAction().register()
    GenerateBuildIbomAction().register()
