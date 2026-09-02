"""KiCad Action Plugin: sync a design's BOM to InvenTree, and generate a
build-order-scoped InteractiveHtmlBom for it.

KiCad imports this package by path when it is placed (or symlinked) into the
KiCad plugin directory -- see kicad-plugin/README.md for the install step.
Registration happens on import, matching how InteractiveHtmlBom's own plugin
does it.

Importing this module outside KiCad (for tests, or to reuse `core`) is safe:
registration is skipped when pcbnew is unavailable.
"""

try:
    import pcbnew  # noqa: F401  (presence check only)
except ImportError:
    pcbnew = None

if pcbnew is not None:
    from .actions import GenerateBuildIbomAction, SyncBomAction

    SyncBomAction().register()
    GenerateBuildIbomAction().register()
