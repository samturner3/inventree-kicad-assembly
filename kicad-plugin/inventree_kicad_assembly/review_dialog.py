"""The sync review dialog.

Shows every symbol, not just the problems: what matched and how, what did not,
and what will change in InvenTree. Nothing is written until this is confirmed,
so it doubles as the dry run.

Selecting a symbol also shows its KiCad fields alongside the InvenTree part it
would be bound to, both visible while the candidate picker is in use: an
"IPN · name" label is not enough to tell eight near-identical passives apart,
whereas the footprint usually is.

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

# The row keys read_bom() produces, ordered by how much each one helps when
# choosing between near-identical candidates -- footprint high, since that is
# usually what separates them. Any key a row grows later is shown after these
# rather than being silently dropped.
_SYMBOL_FIELDS = [
    ("ref", "Reference"),
    ("value", "Value"),
    ("footprint", "Footprint"),
    ("description", "Description"),
    ("mpn", "MPN"),
    ("sku", "Supplier SKU"),
    ("ipn", "IPN on symbol"),
]

# Part-dict keys worth comparing a symbol against. Only what the search already
# returned is read: this pane redraws on every move through the picker and must
# never cost an API call.
_PART_FIELDS = [
    ("IPN", "IPN"),
    ("name", "Name"),
    ("description", "Description"),
    ("keywords", "Keywords"),
    ("revision", "Revision"),
    ("units", "Units"),
]

_PACKAGE_HINTS = ("footprint", "package", "case", "mounting")


def _symbol_lines(row):
    """Everything the KiCad row carries, known fields first."""
    lines = [f"{label}: {row.get(key) or '—'}" for key, label in _SYMBOL_FIELDS]
    known = {key for key, _ in _SYMBOL_FIELDS}
    for key in sorted(k for k in row if k not in known):
        value = row[key]
        # A nested field map (should read_bom ever pass the symbol's other
        # KiCad fields through) is worth flattening rather than printing raw.
        if isinstance(value, dict):
            lines += [f"{n}: {value[n]}" for n in sorted(value) if str(value[n]).strip()]
        elif str(value).strip():
            lines.append(f"{key}: {value}")
    return lines


def _part_parameters(part):
    """(name, value) for whatever parameter detail the part dict happens to
    carry -- empty unless the endpoint that fetched it included parameters."""
    out = []
    for entry in part.get("parameters") or []:
        if not isinstance(entry, dict):
            continue
        template = entry.get("template_detail") or {}
        name = template.get("name") or entry.get("template_name") or ""
        value = str(entry.get("data") or "").strip()
        if name and value:
            out.append((name, f"{value} {template.get('units') or ''}".strip()))
    return out


def _part_lines(part):
    """The InvenTree side of the comparison, package-ish facts first."""
    parameters = _part_parameters(part)
    packageish = [(n, v) for n, v in parameters
                  if any(h in n.lower() for h in _PACKAGE_HINTS)]
    lines = [f"{n}: {v}" for n, v in packageish]
    lines += [f"{label}: {part[key]}" for key, label in _PART_FIELDS
              if str(part.get(key) or "").strip()]

    category = part.get("category_detail") or {}
    category_name = category.get("pathstring") or category.get("name") or \
        part.get("category_name") or ""
    if category_name:
        lines.append(f"Category: {category_name}")

    stock = part.get("total_in_stock", part.get("in_stock"))
    if stock not in (None, ""):
        lines.append(f"In stock: {stock}")

    lines += [f"{n}: {v}" for n, v in parameters if (n, v) not in packageish]
    return lines


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

        # Side by side and always on screen, rather than a pop-up: the point is
        # to read the symbol's footprint off one pane while stepping through
        # candidates in the other. Kept short so the list still shows a useful
        # number of rows at the dialog's default height; both panes scroll, and
        # the dialog resizes.
        symbol_box, self.symbol_details = self._details_pane(panel, "KiCad symbol")
        part_box, self.part_details = self._details_pane(
            panel, "InvenTree part (the match, or the candidate picked below)")
        details = wx.BoxSizer(wx.HORIZONTAL)
        details.Add(symbol_box, 1, wx.EXPAND | wx.RIGHT, 5)
        details.Add(part_box, 1, wx.EXPAND)
        sizer.Add(details, 0, wx.EXPAND | wx.LEFT | wx.RIGHT, 5)

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
        self.picker.Bind(wx.EVT_CHOICE, self._on_candidate)
        self._show_details(None, None)
        panel.SetSizer(sizer)
        return panel

    def _details_pane(self, panel, label):
        """(sizer, control) for a read-only text pane in a labelled box."""
        box = wx.StaticBoxSizer(wx.VERTICAL, panel, label)
        text = wx.TextCtrl(
            box.GetStaticBox(), size=(-1, 112),
            style=wx.TE_MULTILINE | wx.TE_READONLY | wx.TE_BESTWRAP,
        )
        box.Add(text, 1, wx.EXPAND | wx.ALL, 4)
        return box, text

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

    def _show_details(self, match, part):
        self.symbol_details.SetValue(
            "\n".join(_symbol_lines(match.row)) if match
            else "Select a symbol above to see its KiCad fields."
        )
        self.part_details.SetValue(
            ("\n".join(_part_lines(part)) or f"Part {part.get('pk', '?')}") if part
            else "No part yet — choose a candidate below, or create from LCSC."
        )

    def _on_select(self, _event):
        _idx, match = self._selected_match()
        self.picker.Clear()
        if match is None or match.matched:
            self.picker.Disable()
            self._show_details(match, match.part if match else None)
            return
        labels = [f"{p.get('IPN') or '—'} · {p.get('name','')}" for p in match.candidates]
        if labels:
            self.picker.AppendItems(labels)
            self.picker.SetSelection(0)
            self.picker.Enable()
        else:
            self.picker.Disable()
        self._show_details(match, match.candidates[0] if match.candidates else None)

    def _on_candidate(self, _event):
        """Keep the right-hand pane on whichever candidate is highlighted."""
        _idx, match = self._selected_match()
        choice = self.picker.GetSelection()
        if match is not None and 0 <= choice < len(match.candidates):
            self._show_details(match, match.candidates[choice])

    def _on_assign(self, _event):
        idx, match = self._selected_match()
        if match is None or not match.candidates:
            return
        choice = self.picker.GetSelection()
        if choice < 0:
            return
        match.resolve(match.candidates[choice], matching.BY_MANUAL)
        self._refresh_row(idx, match)
        self._on_select(None)

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
