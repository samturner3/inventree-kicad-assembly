"""The two menu entries this plugin adds under Tools > External Plugins.

Both are deliberately separate actions rather than one combined step: syncing
the BOM is a design-time act (run it when the schematic changes), while
generating a build iBOM is a per-build act that may happen many times against
an unchanged BOM. Auto-resyncing on every generation would risk overwriting
BOM edits made directly in InvenTree.

Neither Run() is implemented yet -- see docs/plan.md, phases P3 and P4.
"""

import pcbnew

from .version import version


def _message(text, caption):
    """Show a modal dialog, falling back to stdout if wx is unavailable."""
    try:
        import wx
    except ImportError:
        print("{}: {}".format(caption, text))
        return
    wx.MessageBox(text, caption, wx.OK | wx.ICON_INFORMATION)


class _BaseAction(pcbnew.ActionPlugin, object):
    category = "InvenTree"

    def defaults(self):
        pass

    def Run(self):
        _message(
            "Not implemented yet (kicad-inventree-build {}).\n\n"
            "See docs/plan.md for the phase that builds this.".format(version),
            self.name,
        )


class SyncBomAction(_BaseAction):
    """P4: match KiCad symbols to InvenTree parts and write the assembly BOM.

    Match strategies are tried in order -- symbol IPN field, MPN,
    supplier SKU, then create-from-LCSC -- and everything lands in one review
    dialog before anything is written. Whatever resolves a match gets written
    back onto the symbol as an IPN field, so later syncs match directly and
    stop depending on supplier data.
    """

    def defaults(self):
        self.name = "InvenTree: Sync BOM"
        self.category = self.category
        self.description = (
            "Match this design's components to InvenTree parts and update the "
            "assembly BOM"
        )
        self.show_toolbar_button = False


class GenerateBuildIbomAction(_BaseAction):
    """P3: generate a build-order-scoped iBOM and upload it to that Build Order.

    IPN and stock Location come from the build's real allocations, and the
    generated file is attached to the Build Order so the InvenTree panel can
    embed it same-origin.
    """

    def defaults(self):
        self.name = "InvenTree: Generate Build iBOM"
        self.category = self.category
        self.description = (
            "Generate an InteractiveHtmlBom for an InvenTree Build Order and "
            "attach it to that order"
        )
        self.show_toolbar_button = False
