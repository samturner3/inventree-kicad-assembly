"""The two menu entries this plugin adds under Tools > External Plugins.

They are separate actions on purpose. Syncing the BOM is a design-time act,
run when the schematic changes and independent of any build order, while
generating a build iBOM happens per build and may run many times against an
unchanged BOM. Re-syncing on every generation would risk overwriting BOM edits
made in InvenTree, and would be wasted work when nothing changed.
"""

import os
import traceback

import pcbnew

from .core.client import InvenTreeClient, InvenTreeError
from .core.settings import load_settings, settings_help
from .core.workflows import build_choices, generate_and_upload
from .version import version


def _wx():
    import wx

    return wx


def _message(text, caption, error=False):
    wx = _wx()
    style = wx.OK | (wx.ICON_ERROR if error else wx.ICON_INFORMATION)
    wx.MessageBox(text, caption, style)


class _BaseAction(pcbnew.ActionPlugin, object):
    def defaults(self):
        pass

    def Run(self):
        # KiCad swallows a plugin traceback into its own log, which nobody
        # thinks to look at, so surface failures where the user is looking.
        try:
            self.run()
        except InvenTreeError as e:
            _message(str(e), f"{self.name}", error=True)
        except Exception as e:
            _message(f"{e}\n\n{traceback.format_exc()}", self.name, error=True)

    def run(self):
        raise NotImplementedError

    def client(self):
        host, token = load_settings()
        if not host or not token:
            raise InvenTreeError(settings_help())
        return InvenTreeClient(host, token)


class GenerateBuildIbomAction(_BaseAction):
    """Generate a build-order-scoped iBOM and attach it to that build order."""

    def defaults(self):
        self.name = "InvenTree: Generate Build iBOM"
        self.category = "InvenTree"
        self.description = (
            "Generate an InteractiveHtmlBom for an InvenTree build order, with "
            "IPN and stock location taken from that build's allocations, and "
            "attach it to the order"
        )
        self.show_toolbar_button = False

    def run(self):
        wx = _wx()
        board = pcbnew.GetBoard()
        pcb_path = board.GetFileName()
        if not pcb_path:
            raise InvenTreeError("Save the board before generating a BOM.")

        client = self.client()
        choices = build_choices(client)
        if not choices:
            raise InvenTreeError(
                "No open build orders found. Create one in InvenTree first — "
                "the iBOM is scoped to a specific build's stock allocations."
            )

        dialog = wx.SingleChoiceDialog(
            None,
            "Generate an interactive BOM for which build order?\n\n"
            "IPN and stock location come from that build's allocations.",
            f"InvenTree: Generate Build iBOM ({version})",
            [c["label"] for c in choices],
        )
        try:
            if dialog.ShowModal() != wx.ID_OK:
                return
            chosen = choices[dialog.GetSelection()]
        finally:
            dialog.Destroy()

        progress = wx.ProgressDialog(
            "Generating", "Starting…", maximum=3, parent=None,
            style=wx.PD_APP_MODAL | wx.PD_AUTO_HIDE,
        )
        step = {"n": 0}

        def tick(msg):
            step["n"] += 1
            progress.Update(min(step["n"], 3), msg)

        try:
            attachment, summary = generate_and_upload(
                client, board, pcb_path, chosen["pk"], progress=tick
            )
        finally:
            progress.Destroy()

        _message(
            "\n".join(summary)
            + f"\n\nAttached to {chosen['reference']} as "
            + os.path.basename(str(attachment.get("attachment", "")))
            + "\n\nOpen that build order in InvenTree and use the Assembly panel.",
            self.name,
        )


class SyncBomAction(_BaseAction):
    """Match KiCad symbols to InvenTree parts and update the assembly BOM.

    Built in P4 -- see docs/plan.md. Matching is layered (symbol IPN, MPN,
    supplier SKU, create-from-LCSC, then a review dialog), and whatever
    resolves a match is written back onto the symbol so later syncs need no
    supplier data at all.
    """

    def defaults(self):
        self.name = "InvenTree: Sync BOM"
        self.category = "InvenTree"
        self.description = (
            "Match this design's components to InvenTree parts and update the "
            "assembly BOM"
        )
        self.show_toolbar_button = False

    def run(self):
        _message(
            "Not built yet — this is phase P4.\n\n"
            "Until then the assembly's BOM has to exist in InvenTree already "
            "before a build iBOM can be generated.",
            self.name,
        )
