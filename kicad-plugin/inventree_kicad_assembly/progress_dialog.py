"""A modal that keeps painting while slow work runs.

`wx.BusyInfo` only helps when the event loop is free to draw it. The sync's
work ran on the main thread, so the loop never got a turn: KiCad showed the
spinning beachball and no window at all until everything had already finished,
which reads as a crash rather than as progress.

The fix is to put the work on a thread and let `ShowModal`'s own nested event
loop drive the dialog. That also gives somewhere to say what is happening --
"loading parts", "matching symbol 40 of 71" -- so a slow step looks slow rather
than broken.

Only safe for work that touches no KiCad objects. The BOM sync qualifies: it
shells out to `kicad-cli` and talks to InvenTree, and never reaches into
`pcbnew`, whose objects are not documented as thread-safe.
"""

import threading

import wx

#: Returned instead of a result when the user closed the dialog.
CANCELLED = object()


class WorkerDialog(wx.Dialog):
    """Runs `work(report)` on a thread, showing what it reports."""

    def __init__(self, parent, title, work):
        super().__init__(
            parent, title=title, size=(460, 190),
            style=wx.CAPTION | wx.SYSTEM_MENU | wx.CLOSE_BOX,
        )
        self.work = work
        self.result = None
        self.error = None
        self._closed = False
        self._steps = []

        self.message = wx.StaticText(self, label="Starting…")
        font = self.message.GetFont()
        font.SetWeight(wx.FONTWEIGHT_BOLD)
        self.message.SetFont(font)

        self.gauge = wx.Gauge(self, range=100, size=(-1, 12))
        self.history = wx.StaticText(self, label="")
        self.history.SetForegroundColour(wx.Colour(120, 120, 120))

        cancel = wx.Button(self, wx.ID_CANCEL, "Cancel")
        cancel.Bind(wx.EVT_BUTTON, self._on_cancel)

        sizer = wx.BoxSizer(wx.VERTICAL)
        sizer.Add(self.message, 0, wx.ALL | wx.EXPAND, 12)
        sizer.Add(self.gauge, 0, wx.LEFT | wx.RIGHT | wx.EXPAND, 12)
        sizer.Add(self.history, 1, wx.ALL | wx.EXPAND, 12)
        sizer.Add(cancel, 0, wx.ALIGN_RIGHT | wx.ALL, 8)
        self.SetSizer(sizer)

        self.Bind(wx.EVT_CLOSE, self._on_cancel)
        wx.CallAfter(self._start)

    # --- reporting, called from the worker thread ----------------------

    def report(self, message, done=None, total=None):
        """Thread-safe progress update. `done`/`total` switch the bar to real
        progress; without them it just pulses."""
        wx.CallAfter(self._show, message, done, total)

    def _show(self, message, done, total):
        if self._closed:
            return
        self.message.SetLabel(message)
        self._steps.append(message)
        self.history.SetLabel("\n".join(self._steps[-3:-1]))
        if done is not None and total:
            self.gauge.SetRange(int(total))
            self.gauge.SetValue(min(int(done), int(total)))
        else:
            self.gauge.Pulse()

    # --- lifecycle ------------------------------------------------------

    def _start(self):
        thread = threading.Thread(target=self._run, daemon=True)
        thread.start()

    def _run(self):
        try:
            self.result = self.work(self.report)
        except BaseException as e:  # re-raised on the calling thread
            self.error = e
        wx.CallAfter(self._finish)

    def _finish(self):
        if self._closed:
            return
        self._closed = True
        self.EndModal(wx.ID_OK)

    def _on_cancel(self, _event):
        # The thread cannot be killed, and it is only doing reads, so it is
        # left to finish into a result nobody looks at.
        if self._closed:
            return
        self._closed = True
        self.EndModal(wx.ID_CANCEL)


def run_with_progress(title, work, parent=None):
    """Run `work(report)` behind a progress dialog.

    Returns whatever the work returned, re-raises whatever it raised, or hands
    back CANCELLED if the user closed the dialog first.
    """
    dialog = WorkerDialog(parent, title, work)
    try:
        code = dialog.ShowModal()
    finally:
        dialog.Destroy()

    if dialog.error is not None:
        raise dialog.error
    return dialog.result if code == wx.ID_OK else CANCELLED
