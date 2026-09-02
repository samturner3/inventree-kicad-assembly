# InvenTree plugin

Adds an **Assembly** panel to the Build Order detail page. The panel embeds
the InteractiveHtmlBom generated for that build (uploaded as an attachment by
the [KiCad plugin](../kicad-plugin/)), and turns placements into real stock
consumption via `POST /api/build/<pk>/consume/`.

Skeleton only right now — the panel frontend is built in spike S1 and phase
P2, see [`../docs/plan.md`](../docs/plan.md).

## Requirements

- InvenTree **1.3.0+** (uses `UserInterfaceMixin`)
- The **`ENABLE_PLUGINS_INTERFACE`** global setting must be on, or no UI
  plugin renders at all

## Why the panel, not the iframe, talks to InvenTree

The embedded `ibom.html` is a plain static file. It relays what the user did
(via InteractiveHtmlBom's own `user.js` extension point) and nothing more —
it holds no credentials and makes no API calls. The panel receives those
events and makes every write itself, using the session the user is already
logged in with. That also means no CORS setup and no API token stored in a
browser.

## State

Checkbox state and the record of which designators have been consumed live in
the build's plugin metadata (`/api/metadata/build/<pk>/`), not in browser
storage — so an assembly session survives switching machines. iBOM's own
`localStorage` is treated as a cache that the panel overwrites on load.

## Install

Standard InvenTree plugin install (add to `plugins.txt`, restart the server
and worker containers — a plugin *reload* is not enough for a new plugin).

```
git+https://github.com/samturner3/kicad-inventree-build.git#subdirectory=inventree-plugin
```

## Frontend

The panel is React + Mantine + TypeScript, built with Vite into
`inventree_build_ibom/static/` (gitignored — build output, not source). The
build step and the `frontend/` source tree land in S1.
