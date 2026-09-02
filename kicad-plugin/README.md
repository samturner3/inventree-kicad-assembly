# KiCad plugin

Adds two entries under **Tools → External Plugins** in the PCB editor:

- **InvenTree: Sync BOM** — matches this design's components to InvenTree
  parts and updates the assembly's BOM.
- **InvenTree: Generate Build iBOM** — generates an InteractiveHtmlBom scoped
  to an InvenTree Build Order and attaches it to that order.

Both are unimplemented skeletons right now — see [`../docs/plan.md`](../docs/plan.md),
phases P3 and P4.

## Install

KiCad loads plugin *directories* by path, and imports them as Python packages,
so the installed directory name has to stay a valid Python identifier
(`kicad_inventree_build`). Symlink rather than copy, so `git pull` updates the
installed plugin:

```bash
# macOS (KiCad 10); adjust the version/path for your install
ln -s "$PWD/kicad_inventree_build" \
      ~/Documents/KiCad/10.0/3rdparty/plugins/kicad_inventree_build
```

Then **Tools → External Plugins → Refresh Plugins** (or restart the PCB
editor).

## Install the iBOM bridge

Separately, the event bridge has to go into InteractiveHtmlBom's own plugin
directory. iBOM inlines `web/user.js` into every generated `ibom.html` through
its documented extension point, so this needs installing once per machine and
then applies to every board generated afterwards. iBOM's own source is not
modified.

```bash
ln -s "$PWD/kicad_inventree_build/ibom_bridge/user.js" \
      ~/Documents/KiCad/10.0/3rdparty/plugins/org_openscopeproject_InteractiveHtmlBom/web/user.js
```

Caveat worth knowing: this lives inside a directory KiCad's Plugin and
Content Manager owns, so updating or reinstalling InteractiveHtmlBom may
remove the symlink. Re-run the command if generated iBOMs stop talking to the
InvenTree panel.

A standalone `ibom.html` opened directly in a browser is unaffected by the
bridge — it only relays events when embedded in a page.

## Python environment

These actions run in-process under KiCad's bundled Python, which is a
different interpreter from the system Python the original
`scripts/inventree/*.py` scripts used. Whether `requests` is importable there
is unresolved — spike S3 settles it, and `core/client.py` falls back to
`urllib` if not.
