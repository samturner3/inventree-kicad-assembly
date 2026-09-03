"""Search every InvenTree part, for when the suggestions are no use.

The candidate list is built from the symbol's own words, so a symbol whose
value and description match nothing gets no candidates at all -- and "0
possible matches, pick one below" is not an instruction anybody can follow.
This is the way out: the whole catalogue, filtered as you type.

No network calls. The matcher has already fetched every part to build its
indexes, so the search is over a dict that is sitting in memory.
"""

import wx

from .core import matching


class PartPickerDialog(wx.Dialog):
    def __init__(self, parent, parts, initial="", symbol_ref=""):
        title = f"InvenTree: choose a part for {symbol_ref}" if symbol_ref \
            else "InvenTree: choose a part"
        super().__init__(
            parent, title=title, size=(680, 460),
            style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER,
        )
        self.parts = list(parts)
        self.chosen = None

        self.search = wx.SearchCtrl(self, value=initial)
        self.search.ShowCancelButton(True)

        self.listing = wx.ListCtrl(self, style=wx.LC_REPORT | wx.LC_SINGLE_SEL)
        for i, (label, width) in enumerate([
            ("IPN", 180), ("Name", 260), ("Package", 90), ("Stock", 70),
        ]):
            self.listing.InsertColumn(i, label, width=width)

        hint = wx.StaticText(self, label=(
            "Any part in InvenTree, not just the suggested ones. Type to "
            "filter on IPN, name, description or keywords."
        ))
        hint.SetForegroundColour(wx.Colour(120, 120, 120))

        buttons = wx.StdDialogButtonSizer()
        self.ok = wx.Button(self, wx.ID_OK, "Use this part")
        self.ok.SetDefault()
        self.ok.Disable()
        buttons.AddButton(self.ok)
        buttons.AddButton(wx.Button(self, wx.ID_CANCEL, "Cancel"))
        buttons.Realize()

        outer = wx.BoxSizer(wx.VERTICAL)
        outer.Add(self.search, 0, wx.EXPAND | wx.ALL, 10)
        outer.Add(self.listing, 1, wx.EXPAND | wx.LEFT | wx.RIGHT, 10)
        outer.Add(hint, 0, wx.ALL, 10)
        outer.Add(buttons, 0, wx.ALIGN_RIGHT | wx.LEFT | wx.RIGHT | wx.BOTTOM, 10)
        self.SetSizer(outer)

        self.search.Bind(wx.EVT_TEXT, self._on_search)
        self.listing.Bind(wx.EVT_LIST_ITEM_SELECTED, self._on_select)
        self.listing.Bind(wx.EVT_LIST_ITEM_ACTIVATED, self._on_activate)

        self._fill(initial)
        self.search.SetFocus()

    # --- filtering ------------------------------------------------------

    def _matches(self, part, terms):
        if not terms:
            return True
        haystack = " ".join(
            str(part.get(f) or "")
            for f in ("IPN", "name", "description", "keywords")
        ).upper()
        return all(t in haystack for t in terms)

    def _fill(self, query):
        terms = [t for t in (query or "").upper().split() if t]
        self.shown = [p for p in self.parts if self._matches(p, terms)]
        self.shown.sort(key=lambda p: ((p.get("IPN") or p.get("name") or "").upper()))
        # A long unfiltered list is slower to draw than it is useful; the
        # search box is right there.
        self.shown = self.shown[:400]

        self.listing.DeleteAllItems()
        for part in self.shown:
            idx = self.listing.InsertItem(
                self.listing.GetItemCount(), part.get("IPN") or "—")
            self.listing.SetItem(idx, 1, str(part.get("name") or ""))
            self.listing.SetItem(idx, 2, matching.package_of(part))
            self.listing.SetItem(idx, 3, str(part.get("total_in_stock") or 0))
        self.ok.Disable()
        self.chosen = None

    def _on_search(self, _event):
        self._fill(self.search.GetValue())

    def _on_select(self, _event):
        idx = self.listing.GetFirstSelected()
        self.chosen = self.shown[idx] if 0 <= idx < len(self.shown) else None
        self.ok.Enable(self.chosen is not None)

    def _on_activate(self, event):
        """Double-click picks and closes, which is what a list invites."""
        self._on_select(event)
        if self.chosen is not None:
            self.EndModal(wx.ID_OK)


def choose_part(parent, parts, initial="", symbol_ref=""):
    """The part the user picked, or None."""
    dialog = PartPickerDialog(parent, parts, initial, symbol_ref)
    try:
        return dialog.chosen if dialog.ShowModal() == wx.ID_OK else None
    finally:
        dialog.Destroy()
