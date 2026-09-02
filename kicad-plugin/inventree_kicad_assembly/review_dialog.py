"""The sync review dialog.

Shows every symbol, not just the problems: what matched and how, what did not,
and what will change in InvenTree. Nothing is written until this is confirmed,
so it doubles as the dry run.

Kept apart from the headless core so that everything except the presentation
can be exercised without wx.
"""

import wx

from .core import bom_sync, lcsc, matching

_STRATEGY_COLOUR = {
    matching.BY_IPN: wx.Colour(47, 158, 68),
    matching.BY_MPN: wx.Colour(25, 113, 194),
    matching.BY_SKU: wx.Colour(25, 113, 194),
    matching.BY_CREATE: wx.Colour(245, 159, 0),
    matching.BY_MANUAL: wx.Colour(245, 159, 0),
    matching.UNMATCHED: wx.Colour(224, 49, 49),
}


class ReviewDialog(wx.Dialog):
    def __init__(self, parent, matches, changes, creator, assembly_label):
        super().__init__(
            parent, title="InvenTree: Sync BOM", size=(980, 640),
            style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER,
        )
        self.matches = matches
        self.changes = changes
        self.creator = creator
        # Rows the user asked to create from their LCSC code.
        self.to_create = set()

        outer = wx.BoxSizer(wx.VERTICAL)
        outer.Add(self._header(assembly_label), 0, wx.ALL | wx.EXPAND, 10)

        notebook = wx.Notebook(self)
        notebook.AddPage(self._symbols_page(notebook), "Symbols")
        notebook.AddPage(self._changes_page(notebook), "BOM changes")
        outer.Add(notebook, 1, wx.EXPAND | wx.LEFT | wx.RIGHT, 10)

        self.write_back = wx.CheckBox(
            self, label="Write matched IPNs back onto the schematic symbols "
                        "(so later syncs need no supplier data)")
        self.write_back.SetValue(True)
        outer.Add(self.write_back, 0, wx.ALL, 10)

        self.remove_orphans = wx.CheckBox(
            self, label="Also remove BOM lines this design does not account for "
                        "(check the BOM changes tab first — some are deliberate)")
        self.remove_orphans.SetValue(False)
        outer.Add(self.remove_orphans, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, 10)

        buttons = wx.StdDialogButtonSizer()
        ok = wx.Button(self, wx.ID_OK, "Apply")
        ok.SetDefault()
        buttons.AddButton(ok)
        buttons.AddButton(wx.Button(self, wx.ID_CANCEL, "Cancel"))
        buttons.Realize()
        outer.Add(buttons, 0, wx.ALL | wx.ALIGN_RIGHT, 10)

        self.SetSizer(outer)

    def _header(self, assembly_label):
        counts = matching.summarise(self.matches)
        matched = sum(v for k, v in counts.items() if k != matching.UNMATCHED)
        parts = [f"{matched} of {len(self.matches)} symbols matched"]
        for key, count in sorted(counts.items(), key=lambda kv: -kv[1]):
            parts.append(f"{count} {matching.STRATEGY_LABELS[key]}")
        text = wx.StaticText(
            self, label=f"Assembly: {assembly_label}\n" + " · ".join(parts)
        )
        return text

    def _symbols_page(self, parent):
        panel = wx.Panel(parent)
        listing = wx.ListCtrl(panel, style=wx.LC_REPORT | wx.LC_SINGLE_SEL)
        for i, (title, width) in enumerate([
            ("Ref", 70), ("Value", 130), ("Matched by", 120),
            ("InvenTree part", 260), ("LCSC", 90), ("Note", 260),
        ]):
            listing.InsertColumn(i, title, width=width)

        for m in self.matches:
            idx = listing.InsertItem(listing.GetItemCount(), m.ref)
            listing.SetItem(idx, 1, m.row.get("value", ""))
            listing.SetItem(idx, 2, matching.STRATEGY_LABELS[m.strategy])
            listing.SetItem(idx, 3, m.ipn or (m.part or {}).get("name", "") or "—")
            listing.SetItem(idx, 4, m.row.get("sku", ""))
            note = m.note
            if not m.matched and m.row.get("sku"):
                note = note or ("can be created from LCSC" if self.creator.available
                                else "LCSC code present — " + lcsc.UNAVAILABLE_HINT)
            elif not m.matched:
                note = note or f"{len(m.candidates)} possible matches — pick one below"
            listing.SetItem(idx, 5, note)
            listing.SetItemTextColour(idx, _STRATEGY_COLOUR[m.strategy])

        self.listing = listing
        sizer = wx.BoxSizer(wx.VERTICAL)
        sizer.Add(listing, 1, wx.EXPAND | wx.ALL, 5)

        self.picker = wx.Choice(panel, choices=[])
        self.picker.Disable()
        assign = wx.Button(panel, label="Assign to selected symbol")
        assign.Bind(wx.EVT_BUTTON, self._on_assign)
        create = wx.Button(panel, label="Create from LCSC")
        create.Enable(self.creator.available)
        if not self.creator.available:
            create.SetToolTip(self.creator.reason)
        create.Bind(wx.EVT_BUTTON, self._on_create)

        row = wx.BoxSizer(wx.HORIZONTAL)
        row.Add(wx.StaticText(panel, label="Candidates:"), 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 6)
        row.Add(self.picker, 1, wx.RIGHT, 6)
        row.Add(assign, 0, wx.RIGHT, 6)
        row.Add(create, 0)
        sizer.Add(row, 0, wx.EXPAND | wx.ALL, 5)

        listing.Bind(wx.EVT_LIST_ITEM_SELECTED, self._on_select)
        panel.SetSizer(sizer)
        return panel

    def _changes_page(self, parent):
        panel = wx.Panel(parent)
        listing = wx.ListCtrl(panel, style=wx.LC_REPORT)
        for i, (title, width) in enumerate([
            ("Change", 110), ("Part", 240), ("Qty", 60), ("Designators", 480),
        ]):
            listing.InsertColumn(i, title, width=width)

        explain = {
            bom_sync.BomChange.CREATE: "add",
            bom_sync.BomChange.UPDATE: "update",
            bom_sync.BomChange.UNCHANGED: "unchanged",
            bom_sync.BomChange.ORPHAN: "not in design",
        }
        for c in self.changes:
            idx = listing.InsertItem(listing.GetItemCount(), explain[c.kind])
            listing.SetItem(idx, 1, c.ipn or c.name)
            listing.SetItem(idx, 2, str(c.quantity))
            listing.SetItem(idx, 3, c.reference)
            if c.kind == bom_sync.BomChange.ORPHAN:
                listing.SetItemTextColour(idx, wx.Colour(224, 49, 49))
            elif c.kind == bom_sync.BomChange.UNCHANGED:
                listing.SetItemTextColour(idx, wx.Colour(130, 130, 130))

        note = wx.StaticText(panel, label=(
            "'not in design' lines are in InvenTree but unmatched here. Some are "
            "deliberate (the bare PCB, hand-added hardware); others just mean a "
            "symbol above did not match. They are kept unless you tick the box."
        ))
        sizer = wx.BoxSizer(wx.VERTICAL)
        sizer.Add(listing, 1, wx.EXPAND | wx.ALL, 5)
        sizer.Add(note, 0, wx.ALL, 5)
        panel.SetSizer(sizer)
        return panel

    # --- interaction ----------------------------------------------------

    def _selected_match(self):
        idx = self.listing.GetFirstSelected()
        return (idx, self.matches[idx]) if idx >= 0 else (None, None)

    def _on_select(self, _event):
        _idx, match = self._selected_match()
        self.picker.Clear()
        if match is None or match.matched:
            self.picker.Disable()
            return
        labels = [f"{p.get('IPN') or '—'} · {p.get('name','')}" for p in match.candidates]
        if labels:
            self.picker.AppendItems(labels)
            self.picker.SetSelection(0)
            self.picker.Enable()
        else:
            self.picker.Disable()

    def _on_assign(self, _event):
        idx, match = self._selected_match()
        if match is None or not match.candidates:
            return
        choice = self.picker.GetSelection()
        if choice < 0:
            return
        match.resolve(match.candidates[choice], matching.BY_MANUAL)
        self._refresh_row(idx, match)

    def _on_create(self, _event):
        idx, match = self._selected_match()
        if match is None or not match.row.get("sku"):
            wx.MessageBox("That symbol has no LCSC code to create from.",
                          "Create from LCSC", wx.OK | wx.ICON_INFORMATION)
            return
        # Deferred: creating parts is a write, and this dialog promises that
        # nothing is written until Apply.
        self.to_create.add(match.ref)
        self.listing.SetItem(idx, 5, "will be created from LCSC on Apply")
        self.listing.SetItemTextColour(idx, _STRATEGY_COLOUR[matching.BY_CREATE])

    def _refresh_row(self, idx, match):
        self.listing.SetItem(idx, 2, matching.STRATEGY_LABELS[match.strategy])
        self.listing.SetItem(idx, 3, match.ipn or (match.part or {}).get("name", ""))
        self.listing.SetItem(idx, 5, "")
        self.listing.SetItemTextColour(idx, _STRATEGY_COLOUR[match.strategy])
