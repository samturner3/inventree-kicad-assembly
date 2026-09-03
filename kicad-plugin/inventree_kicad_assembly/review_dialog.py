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
    matching.BY_BOM: wx.Colour(25, 113, 194),
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


#: Rows KiCad keeps out of the BOM. Grey, and never actionable.
_EXCLUDED_COLOUR = wx.Colour(150, 150, 150)
_EXCLUSION_NOTE = "{why} — not sent to InvenTree"


def _reference_key(reference):
    """Sort R2 before R18, the way a person reads a designator."""
    prefix = "".join(c for c in reference if not c.isdigit())
    digits = "".join(c for c in reference if c.isdigit())
    return (prefix, int(digits) if digits else 0, reference)


class ReviewDialog(wx.Dialog):
    def __init__(self, parent, matches, changes, creator, assembly_label,
                 excluded=None, parts=None, on_apply=None):
        super().__init__(
            parent, title="InvenTree: Sync BOM", size=(980, 640),
            style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER,
        )
        self.matches = matches
        self.changes = changes
        self.creator = creator
        self.excluded = excluded or []
        #: Every InvenTree part, for the unrestricted picker.
        self.parts = list(parts or [])
        #: Refs the user has ticked to leave out of this sync entirely.
        self.ignored = set()
        #: Called with this dialog when Apply is pressed. Modeless, so the
        #: work cannot simply follow ShowModal returning.
        self.on_apply = on_apply
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

        hint = wx.StaticText(self, label=(
            "Ignore applies to this run only. To keep a symbol out of InvenTree "
            "for good, tick Exclude from BOM on it in KiCad — that is the flag "
            "for a board feature rather than a purchased part, and those symbols "
            "never reach this dialog. Not DNP: DNP means the pad is fabricated "
            "and left unpopulated, which says something different about the board."
        ))
        hint.Wrap(940)
        hint.SetForegroundColour(wx.Colour(120, 120, 120))
        outer.Add(hint, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, 10)

        buttons = wx.StdDialogButtonSizer()
        self.apply_button = wx.Button(self, wx.ID_OK, "Apply")
        self.apply_button.SetDefault()
        self.apply_button.Bind(wx.EVT_BUTTON, self._on_apply_clicked)
        buttons.AddButton(self.apply_button)
        close = wx.Button(self, wx.ID_CANCEL, "Close")
        close.Bind(wx.EVT_BUTTON, lambda _e: self.Destroy())
        buttons.AddButton(close)
        buttons.Realize()
        outer.Add(buttons, 0, wx.ALL | wx.ALIGN_RIGHT, 10)

        self.SetSizer(outer)

    def _header(self, assembly_label):
        counts = matching.summarise(self.matches)
        matched = sum(v for k, v in counts.items() if k != matching.UNMATCHED)
        parts = [f"{matched} of {len(self.matches)} symbols matched"]
        for key, count in sorted(counts.items(), key=lambda kv: -kv[1]):
            parts.append(f"{count} {matching.STRATEGY_LABELS[key]}")
        lines = [f"Assembly: {assembly_label}", " · ".join(parts)]
        if self.excluded:
            lines.append(
                f"{len(self.excluded)} not fitted in this variant — listed "
                "greyed out below, and not written to InvenTree"
            )
        return wx.StaticText(self, label="\n".join(lines))

    def _display_rows(self):
        """Every symbol in the design, in reference order.

        Excluded symbols are listed alongside the rest rather than hidden in a
        tab of their own: this list claims to show the whole design, and a
        reader looking for R18 is better served by a greyed row saying why it
        is not going than by its absence. They carry no Match -- selecting one
        disables the candidate controls, which is the point.
        """
        rows = [(m.row.get("ref", ""), m, None) for m in self.matches]
        rows += [(r.get("ref", ""), None, r) for r in self.excluded]
        rows.sort(key=lambda item: _reference_key(item[0]))
        return rows

    def _symbols_page(self, parent):
        panel = wx.Panel(parent)
        # Split so the list can be dragged taller: on a board this size the
        # whole point is scanning every row, and a fixed details pane below
        # spends screen on three lines of text nobody is reading yet.
        splitter = wx.SplitterWindow(panel, style=wx.SP_LIVE_UPDATE | wx.SP_3DSASH)
        splitter.SetMinimumPaneSize(70)
        top = wx.Panel(splitter)
        bottom = wx.Panel(splitter)

        listing = wx.ListCtrl(top, style=wx.LC_REPORT | wx.LC_SINGLE_SEL)
        if hasattr(listing, "EnableCheckBoxes"):
            listing.EnableCheckBoxes(True)
        for i, (title, width) in enumerate([
            ("Ignore", 60), ("Ref", 70), ("Value", 130), ("Matched by", 120),
            ("InvenTree part", 240), ("LCSC", 90), ("Note", 250),
        ]):
            listing.InsertColumn(i, title, width=width)

        self.display = self._display_rows()
        for _ref, m, excluded in self.display:
            idx = listing.InsertItem(listing.GetItemCount(), "")
            if excluded is not None:
                listing.SetItem(idx, 1, excluded.get("ref", ""))
                listing.SetItem(idx, 2, excluded.get("value", ""))
                listing.SetItem(idx, 3, "not sent")
                listing.SetItem(idx, 4, "—")
                listing.SetItem(idx, 5, excluded.get("sku", ""))
                listing.SetItem(idx, 6, _EXCLUSION_NOTE.format(
                    why=excluded.get("excluded", "excluded")))
                listing.SetItemTextColour(idx, _EXCLUDED_COLOUR)
                # Already out of the sync; the tick says so and cannot move.
                if hasattr(listing, "CheckItem"):
                    listing.CheckItem(idx, True)
                continue

            listing.SetItem(idx, 1, m.ref)
            listing.SetItem(idx, 2, m.row.get("value", ""))
            listing.SetItem(idx, 3, matching.STRATEGY_LABELS[m.strategy])
            listing.SetItem(idx, 4, m.ipn or (m.part or {}).get("name", "") or "—")
            listing.SetItem(idx, 5, m.row.get("sku", ""))
            note = m.note
            if not m.matched and m.row.get("sku"):
                note = note or ("can be created from LCSC" if self.creator.available
                                else "LCSC code present — " + lcsc.UNAVAILABLE_HINT)
            elif not m.matched:
                note = note or (
                    f"{len(m.candidates)} suggestions below"
                    if m.candidates else "no suggestions — search all parts")
            listing.SetItem(idx, 6, note)
            listing.SetItemTextColour(idx, _STRATEGY_COLOUR[m.strategy])

        self.listing = listing
        top_sizer = wx.BoxSizer(wx.VERTICAL)
        top_sizer.Add(listing, 1, wx.EXPAND | wx.ALL, 5)
        top.SetSizer(top_sizer)

        sizer = wx.BoxSizer(wx.VERTICAL)

        # Side by side and always on screen, rather than a pop-up: the point is
        # to read the symbol's footprint off one pane while stepping through
        # candidates in the other. Kept short so the list still shows a useful
        # number of rows at the dialog's default height; both panes scroll, and
        # the dialog resizes.
        symbol_box, self.symbol_details = self._details_pane(bottom, "KiCad symbol")
        part_box, self.part_details = self._details_pane(
            bottom, "InvenTree part (the match, or the candidate picked below)")
        details = wx.BoxSizer(wx.HORIZONTAL)
        details.Add(symbol_box, 1, wx.EXPAND | wx.RIGHT, 5)
        details.Add(part_box, 1, wx.EXPAND)
        sizer.Add(details, 1, wx.EXPAND | wx.LEFT | wx.RIGHT, 5)

        self.picker = wx.Choice(bottom, choices=[])
        self.picker.Disable()
        assign = wx.Button(bottom, label="Assign")
        assign.Bind(wx.EVT_BUTTON, self._on_assign)
        self.search_button = wx.Button(bottom, label="Search all parts…")
        self.search_button.Bind(wx.EVT_BUTTON, self._on_search_parts)
        self.search_button.SetToolTip(
            "Pick any InvenTree part, not just the suggestions")
        create = wx.Button(bottom, label="Create from LCSC")
        create.Enable(self.creator.available)
        if not self.creator.available:
            create.SetToolTip(self.creator.reason)
        create.Bind(wx.EVT_BUTTON, self._on_create)

        row = wx.BoxSizer(wx.HORIZONTAL)
        row.Add(wx.StaticText(bottom, label="Candidates:"), 0,
                wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 6)
        row.Add(self.picker, 1, wx.RIGHT, 6)
        row.Add(assign, 0, wx.RIGHT, 6)
        row.Add(self.search_button, 0, wx.RIGHT, 6)
        row.Add(create, 0)
        sizer.Add(row, 0, wx.EXPAND | wx.ALL, 5)
        bottom.SetSizer(sizer)

        splitter.SplitHorizontally(top, bottom, -230)
        # Dragging the sash grows the list, which is the half worth more space.
        splitter.SetSashGravity(1.0)
        outer = wx.BoxSizer(wx.VERTICAL)
        outer.Add(splitter, 1, wx.EXPAND)
        panel.SetSizer(outer)

        listing.Bind(wx.EVT_LIST_ITEM_SELECTED, self._on_select)
        self.picker.Bind(wx.EVT_CHOICE, self._on_candidate)
        if hasattr(wx, "EVT_LIST_ITEM_CHECKED"):
            listing.Bind(wx.EVT_LIST_ITEM_CHECKED, self._on_ignore_changed)
            listing.Bind(wx.EVT_LIST_ITEM_UNCHECKED, self._on_ignore_changed)
        self._show_details(None, None)
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
            ("Change", 110), ("Part", 220), ("Qty", 55), ("Designators", 300),
            ("Why", 280),
        ]):
            listing.InsertColumn(i, title, width=width)

        explain = {
            bom_sync.BomChange.CREATE: "add",
            bom_sync.BomChange.UPDATE: "update",
            bom_sync.BomChange.UNCHANGED: "unchanged",
            bom_sync.BomChange.ORPHAN: "not in design",
            bom_sync.BomChange.INHERITED: "inherited",
        }
        for c in self.changes:
            idx = listing.InsertItem(listing.GetItemCount(), explain[c.kind])
            listing.SetItem(idx, 1, c.ipn or c.name)
            listing.SetItem(idx, 2, str(c.quantity))
            listing.SetItem(idx, 3, c.reference)
            # Why, for the two kinds where the answer decides what to do.
            if c.kind == bom_sync.BomChange.INHERITED:
                listing.SetItem(idx, 4, f"from {c.source}"
                                + (f" — {c.reason}" if c.reason else ""))
                listing.SetItemTextColour(idx, wx.Colour(121, 80, 242))
            elif c.kind == bom_sync.BomChange.ORPHAN:
                listing.SetItem(idx, 4, c.reason)
                listing.SetItemTextColour(idx, wx.Colour(224, 49, 49))
            elif c.kind == bom_sync.BomChange.UNCHANGED:
                listing.SetItemTextColour(idx, wx.Colour(130, 130, 130))

        note = wx.StaticText(panel, label=(
            "'not in design' lines are in InvenTree but unmatched here, and the "
            "Why column says which kind: 'not fitted in this variant' means the "
            "design dropped it, while 'no symbol in this design' is usually "
            "deliberate — the bare PCB, hand-added hardware — or a symbol above "
            "that failed to match. They are kept unless you tick the box.\n\n"
            "'inherited' lines come from a template above this assembly. They "
            "already count towards a build, so they are never added here, and "
            "never removed from here either: that would take them off every "
            "sibling variant."
        ))
        note.Wrap(940)
        sizer = wx.BoxSizer(wx.VERTICAL)
        sizer.Add(listing, 1, wx.EXPAND | wx.ALL, 5)
        sizer.Add(note, 0, wx.ALL, 5)
        panel.SetSizer(sizer)
        return panel

    def _on_apply_clicked(self, _event):
        """Modeless, so Apply does the work here rather than after ShowModal.

        The dialog closes only once the work has finished and been reported --
        leaving it open through a failure means the review is still there to
        correct and retry from.
        """
        if self.on_apply is None:
            self.Destroy()
            return
        self.apply_button.Disable()
        try:
            self.on_apply(self)
        finally:
            if self:
                self.apply_button.Enable()
        if self:
            self.Destroy()

    # --- interaction ----------------------------------------------------

    def _selected_match(self):
        """(index, Match) for the selected row -- Match is None when excluded."""
        idx = self.listing.GetFirstSelected()
        if idx < 0 or idx >= len(self.display):
            return (None, None)
        return (idx, self.display[idx][1])

    def _selected_excluded(self):
        idx = self.listing.GetFirstSelected()
        if idx < 0 or idx >= len(self.display):
            return None
        return self.display[idx][2]

    def _show_details(self, match, part, excluded=None):
        if match is not None:
            symbol_text = "\n".join(_symbol_lines(match.row))
        elif excluded is not None:
            symbol_text = "\n".join(_symbol_lines(excluded))
        else:
            symbol_text = "Select a symbol above to see its KiCad fields."
        self.symbol_details.SetValue(symbol_text)
        if part:
            self.part_details.SetValue(
                "\n".join(_part_lines(part)) or f"Part {part.get('pk', '?')}"
            )
        elif excluded is not None:
            self.part_details.SetValue(
                f"Not sent to InvenTree: {excluded.get('excluded', 'excluded')}.\n\n"
                "This symbol is not written to InvenTree. A BOM line there is "
                "something to buy, allocate and consume, and InvenTree has no "
                "do-not-populate flag to mark an unfitted part with.\n\n"
                "If it should be fitted, it belongs to another variant — sync "
                "that variant into its own assembly."
            )
        else:
            self.part_details.SetValue(
                "No part yet — choose a candidate below, or create from LCSC."
            )

    def _on_select(self, _event):
        _idx, match = self._selected_match()
        self.picker.Clear()
        # Searching the whole catalogue stays available on every real symbol,
        # including one that already matched: an automatic match can be wrong,
        # and overriding it should not mean unpicking it first.
        self.search_button.Enable(match is not None)
        if match is None:
            # An excluded row, or nothing selected. Either way there is no
            # part to assign, so the controls stay off.
            self.picker.Disable()
            self._show_details(None, None, excluded=self._selected_excluded())
            return
        if match.matched:
            self.picker.Disable()
            self._show_details(match, match.part)
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

    def _on_search_parts(self, _event):
        """Assign any part in InvenTree, suggestions or no suggestions."""
        idx, match = self._selected_match()
        if match is None:
            return
        from .part_picker import choose_part

        # Seed the search with the symbol's own words, which is usually most
        # of the query anyway.
        seed = match.row.get("mpn") or match.row.get("value") or ""
        part = choose_part(self, self.parts, initial=seed, symbol_ref=match.ref)
        if part is None:
            return
        match.resolve(part, matching.BY_MANUAL)
        self._refresh_row(idx, match)
        self._on_select(None)

    def _on_ignore_changed(self, event):
        """Ticking Ignore leaves a symbol out of this sync entirely."""
        idx = event.GetIndex()
        if idx < 0 or idx >= len(self.display):
            return
        _ref, match, excluded = self.display[idx]
        if match is None:
            # An excluded symbol is already out; the tick is a statement of
            # fact, so put it back rather than imply it can be included.
            if excluded is not None and not self.listing.IsItemChecked(idx):
                self.listing.CheckItem(idx, True)
            return
        if self.listing.IsItemChecked(idx):
            self.ignored.add(match.ref)
            self.listing.SetItem(idx, 6, "ignored — left out of this sync")
            self.listing.SetItemTextColour(idx, _EXCLUDED_COLOUR)
        else:
            self.ignored.discard(match.ref)
            self.listing.SetItem(idx, 6, match.note or "")
            self.listing.SetItemTextColour(idx, _STRATEGY_COLOUR[match.strategy])

    def _on_create(self, _event):
        idx, match = self._selected_match()
        if match is None:
            wx.MessageBox(
                "That symbol is not being sent to InvenTree, so there is "
                "nothing to create for it.",
                "Create from LCSC", wx.OK | wx.ICON_INFORMATION)
            return
        if not match.row.get("sku"):
            wx.MessageBox("That symbol has no LCSC code to create from.",
                          "Create from LCSC", wx.OK | wx.ICON_INFORMATION)
            return
        # Deferred: creating parts is a write, and this dialog promises that
        # nothing is written until Apply.
        self.to_create.add(match.ref)
        self.listing.SetItem(idx, 6, "will be created from LCSC on Apply")
        self.listing.SetItemTextColour(idx, _STRATEGY_COLOUR[matching.BY_CREATE])

    def _refresh_row(self, idx, match):
        self.listing.SetItem(idx, 3, matching.STRATEGY_LABELS[match.strategy])
        self.listing.SetItem(idx, 4, match.ipn or (match.part or {}).get("name", ""))
        self.listing.SetItem(idx, 6, match.note or "")
        self.listing.SetItemTextColour(idx, _STRATEGY_COLOUR[match.strategy])
