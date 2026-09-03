"""The form for creating an assembly part, for a design InvenTree has not met.

Four fields and nothing else. A part has dozens of attributes, but the rest
are better set in InvenTree itself -- filling them in from KiCad would invent
records nobody asked for.
"""

import wx

from .core import assemblies


class NewAssemblyDialog(wx.Dialog):
    def __init__(self, parent, categories, default_name="", default_category=None):
        super().__init__(
            parent, title="InvenTree: new assembly", size=(520, 330),
            style=wx.DEFAULT_DIALOG_STYLE,
        )
        self.categories = categories

        intro = wx.StaticText(self, label=(
            "Create the InvenTree part this design builds. Its BOM is filled in "
            "by the sync that follows."
        ))
        intro.Wrap(480)

        self.name = wx.TextCtrl(self, value=default_name)
        self.ipn = wx.TextCtrl(self, value=default_name)
        self.description = wx.TextCtrl(self, value=default_name)
        self.category = wx.Choice(
            self, choices=[assemblies.category_label(c) for c in categories]
        )
        if categories:
            index = 0
            if default_category:
                for i, c in enumerate(categories):
                    if c.get("pk") == default_category.get("pk"):
                        index = i
                        break
            self.category.SetSelection(index)

        grid = wx.FlexGridSizer(4, 2, 8, 10)
        grid.AddGrowableCol(1, 1)
        for label, control in (
            ("Name", self.name),
            ("IPN", self.ipn),
            ("Description", self.description),
            ("Category", self.category),
        ):
            grid.Add(wx.StaticText(self, label=label), 0, wx.ALIGN_CENTER_VERTICAL)
            grid.Add(control, 1, wx.EXPAND)

        note = wx.StaticText(self, label=(
            "IPN is what later syncs match on, and what gets written back onto "
            "the symbols. Leave it blank only if this part will never be "
            "referenced by one."
        ))
        note.Wrap(480)
        note.SetForegroundColour(wx.Colour(120, 120, 120))

        buttons = wx.StdDialogButtonSizer()
        ok = wx.Button(self, wx.ID_OK, "Create")
        ok.SetDefault()
        buttons.AddButton(ok)
        buttons.AddButton(wx.Button(self, wx.ID_CANCEL, "Cancel"))
        buttons.Realize()

        outer = wx.BoxSizer(wx.VERTICAL)
        outer.Add(intro, 0, wx.ALL | wx.EXPAND, 12)
        outer.Add(grid, 0, wx.LEFT | wx.RIGHT | wx.EXPAND, 12)
        outer.Add(note, 0, wx.ALL | wx.EXPAND, 12)
        outer.Add(buttons, 0, wx.ALL | wx.ALIGN_RIGHT, 10)
        self.SetSizer(outer)

        self.name.SetFocus()

    def values(self):
        selection = self.category.GetSelection()
        category = (
            self.categories[selection].get("pk")
            if self.categories and selection >= 0
            else None
        )
        return {
            "name": self.name.GetValue().strip(),
            "ipn": self.ipn.GetValue().strip(),
            "description": self.description.GetValue().strip(),
            "category": category,
        }
