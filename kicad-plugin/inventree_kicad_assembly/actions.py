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
from .core import assemblies, bom_sync, lcsc, matching, variants, workflows
from .core.workflows import build_choices, generate_and_upload
from .progress_dialog import CANCELLED, run_with_progress
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

    def choose_variant(self, board, purpose):
        """Which design variant? Skipped silently when the design has only one.

        A variant decides which parts are fitted, so it decides the bill of
        materials -- picking the wrong one writes the base product's parts into
        a variant, or orders parts for a board that will not carry them.
        """
        wx = _wx()
        available = variants.from_board(board)
        if len(available) < 2:
            return variants.DEFAULT

        labels = [variants.label_for(n, d) for n, d in available]
        dialog = wx.SingleChoiceDialog(
            None,
            f"This design declares {len(available) - 1} variant(s). "
            f"Which one {purpose}?\n\n"
            "A variant changes which parts are fitted, so each one is its own "
            "bill of materials — and its own assembly part in InvenTree.",
            "InvenTree: choose a design variant",
            labels,
        )
        try:
            if dialog.ShowModal() != wx.ID_OK:
                return None
            return available[dialog.GetSelection()][0]
        finally:
            dialog.Destroy()

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

        variant = self.choose_variant(board, "are you assembling")
        if variant is None:
            return

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
                client, board, pcb_path, chosen["pk"], variant=variant,
                progress=tick
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

    #: Offered first in the list, for a design InvenTree has not met yet.
    NEW_ASSEMBLY = "+  Create a new assembly in InvenTree…"

    def choose_assembly(self, client, pcb_path, variant=""):
        """The InvenTree assembly this design builds, creating one if needed.

        Returns None if the user backed out.
        """
        wx = _wx()
        existing = assemblies.list_assemblies(client)

        if existing:
            labels = [assemblies.label_for(p) for p in existing]
            chooser = wx.SingleChoiceDialog(
                None,
                "Assemblies in InvenTree — pick the one this design builds"
                + (f" as the {variant} variant.\n\n" if variant else ".\n\n") +
                "This design's parts are written into that assembly's BOM, so "
                "the pairing matters: a variant's parts written into the base "
                "product's assembly would have it ordering parts it never "
                "fits.",
                "InvenTree: Sync BOM — choose an assembly",
                [self.NEW_ASSEMBLY] + labels,
            )
            try:
                if chooser.ShowModal() != wx.ID_OK:
                    return None
                selection = chooser.GetSelection()
            finally:
                chooser.Destroy()
            if selection > 0:
                return existing[selection - 1]
        else:
            _message(
                "InvenTree has no assembly parts yet, so there is nothing to "
                "sync this design into. Create one now.",
                "InvenTree: Sync BOM",
            )

        return self.create_assembly(client, pcb_path, existing, variant)

    def create_assembly(self, client, pcb_path, existing, variant=""):
        wx = _wx()
        from .assembly_dialog import NewAssemblyDialog

        categories = assemblies.list_categories(client)
        dialog = NewAssemblyDialog(
            None,
            categories,
            default_name=assemblies.suggested_name(pcb_path, variant),
            default_category=assemblies.default_category(existing, categories),
        )
        try:
            if dialog.ShowModal() != wx.ID_OK:
                return None
            values = dialog.values()
        finally:
            dialog.Destroy()

        if not values["name"]:
            raise InvenTreeError("An assembly needs a name.")
        created = assemblies.create_assembly(client, **values)
        _message(
            f"Created {assemblies.label_for(created)}.\n\n"
            "Its BOM is filled in by the sync that follows.",
            "InvenTree: new assembly",
        )
        return created

    def run(self):
        wx = _wx()
        board = pcbnew.GetBoard()
        pcb_path = board.GetFileName()
        if not pcb_path:
            raise InvenTreeError("Save the board before syncing its BOM.")

        client = self.client()

        # Which assembly is this design? Offered rather than guessed: picking
        # the wrong variant would write this board's parts into another
        # product's BOM.
        variant = self.choose_variant(board, "does this assembly build")
        if variant is None:
            return

        assembly = self.choose_assembly(client, pcb_path, variant)
        if assembly is None:
            return
        assembly_label = assemblies.label_for(assembly)
        if variant:
            assembly_label += f"   ({variant} variant)"

        # Exporting the BOM shells out to kicad-cli and the matching tables
        # come over the network, so this is seconds of work at best. Run it on
        # a thread: on the main thread the event loop cannot paint and KiCad
        # just beachballs, which looks like a hang.
        def work(report):
            matches, sch_path, excluded = workflows.prepare_sync(
                client, pcb_path, variant=variant, progress=report
            )
            report("Working out what changes…")
            changes = bom_sync.plan(client, assembly["pk"], matches)
            report("Checking for the LCSC import plugin…")
            creator = lcsc.LcscCreator(client)
            creator.available  # probe here rather than from the dialog
            return matches, sch_path, changes, creator, excluded

        prepared = run_with_progress("InvenTree: Sync BOM", work)
        if prepared is CANCELLED:
            return
        matches, sch_path, changes, creator, excluded = prepared

        from .review_dialog import ReviewDialog

        dialog = ReviewDialog(None, matches, changes, creator, assembly_label,
                              excluded=excluded)
        try:
            if dialog.ShowModal() != wx.ID_OK:
                return
            write_back = dialog.write_back.GetValue()
            remove_orphans = dialog.remove_orphans.GetValue()
            to_create = set(dialog.to_create)
        finally:
            dialog.Destroy()

        created, create_errors = [], []
        for match in matches:
            if match.ref not in to_create:
                continue
            try:
                part = creator.create(match.row["sku"])
                if part:
                    match.resolve(part, matching.BY_CREATE)
                    created.append(match.ref)
            except Exception as e:
                create_errors.append(f"{match.ref}: {e}")

        result = workflows.apply_sync(
            client, assembly["pk"], matches, sch_path,
            remove_orphans=remove_orphans, write_back_ipns=write_back,
        )

        counts = bom_sync.summarise(result["changes"])
        lines = [
            f"{assembly_label}",
            "",
            f"BOM lines added: {counts.get('create', 0)}, "
            f"updated: {counts.get('update', 0)}, "
            f"unchanged: {counts.get('unchanged', 0)}",
            f"IPNs written back to symbols: {len(result['written_back'])}",
        ]
        if created:
            lines.append(f"Created from LCSC: {', '.join(created)}")
        if create_errors:
            lines.append("Could not create: " + "; ".join(create_errors))
        if result["errors"]:
            lines.append("Errors: " + "; ".join(f"{c.ipn}: {e}" for c, e in result["errors"]))
        if result["written_back"]:
            lines += ["", "The schematic was modified — review the diff before committing."]

        _message("\n".join(lines), self.name)
