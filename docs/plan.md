# InvenTree Build Order ↔ iBOM: live placement + stock consumption

*(Supersedes the earlier iBOM-XML-generator plan in this file — that work
shipped and its design record now lives permanently in the repo at
`scripts/inventree/ibom_inventree_integration_plan.md`. This is a new,
larger feature building on top of it.)*

## Where this is up to (2026-09-03)

Everything through P4's core is built and pushed. "Generate Build iBOM" has now
been run from the KiCad GUI against BO-0004 and the board came up in the
Assembly panel, so the whole KiCad → InvenTree → assemble path works end to
end. The Sync BOM dialog is the last unexercised piece.

| | State |
|---|---|
| P0 repo scaffold | done — github.com/samturner3/inventree-kicad-assembly |
| S1 InvenTree panel mechanics | passed, real stock consumed |
| S2 iBOM bridge | passed; fullscreen confirmed on mobile, desktop untested |
| S3 KiCad environment | passed |
| P1 build-scoped generation | passed |
| P2 panel end-to-end | passed, incl. cross-machine restore |
| P3 KiCad "Generate Build iBOM" | passed from the KiCad GUI, board renders in the panel |
| P4 "Sync BOM" | core built and dry-run — **dialog untested**, LCSC endpoint not written |

**Next, in order:**

1. **Try "Sync BOM"** (Tools → External Plugins). The plugin is symlinked into
   `~/Documents/KiCad/10.0/3rdparty/plugins/` and credentials are in
   `~/.config/inventree-kicad-assembly.env`. Its review dialog is the one wx
   window never shown on screen; run it against a copied schematic first, since
   confirming it writes IPNs back into the files.
2. **Add `find-or-create` to `inventree-lcsc-import`** — a change in the
   *scripts* repo, not this one. `core/lcsc.py` documents the contract it
   probes for. Until then the create-from-LCSC button is greyed out with the
   reason, which is the intended graceful behaviour.
3. **P5**: docs, install guide, publishing.

**Un-consume (added 2026-09-03).** InvenTree has no un-consume endpoint, but
consuming has exactly three reversible effects — it splits the placed quantity
into a stock item pointed at the build, increments `BuildLine.consumed`
(a stored field, not an annotation), and reduces the allocation. Unticking
Placed now offers to reverse all three; verified as a round trip on BO-0004.
Two things had to be captured at consume time, because the reversal cannot
derive them later: the source location (consuming clears it) and which stock
item the consume produced (the response is only a task id, so it is found by
diffing this build's consumed stock afterwards). The returned unit stays a
stock item of its own — `/api/stock/merge/` refuses allocated stock, and
re-allocating matters more than tidiness.

Panel 0.1.3 fixed the two things the first real run showed: the "no interactive
BOM attached" notice flashing up before the board loaded (loading and absent
were the same null), and fullscreen hiding the status line (it fullscreened the
iframe, not the panel).

**First sync of a new design (added 2026-09-03).** The assembly chooser leads
with "create a new assembly in InvenTree", because otherwise the first sync of
a board InvenTree has never heard of dead-ends: there is nothing to pick. The
form takes name, IPN, description and category, defaulting the name to the
board filename and the category to whichever one this instance already keeps
its assemblies in — read from the data, since another user's InvenTree may have
no category called "Assemblies" at all.

**Variants and DNP (settled 2026-09-03).** KiCad 10 has first-class design
variants, and this design declares one ("Pro", which un-DNPs R1-R6/R18/R19).
Both actions now ask which variant, because a variant *is* a bill of materials:
default syncs 71 symbols, Pro syncs 79.

- **The variant maps to the InvenTree assembly part**, one each. That is what
  makes the old footgun go away: the parts a variant leaves out are simply not
  in that assembly's BOM.
- **DNP parts are not sent to InvenTree at all.** InvenTree has no
  do-not-populate concept — `optional` and `consumable` mean other things — and
  a BOM line there is something to buy, allocate and consume, which an unfitted
  part is none of. They are reported in a "Not fitted" tab instead of vanishing:
  the export no longer uses `--exclude-dnp`, it asks for the DNP and
  EXCLUDE_FROM_BOM columns and filters in Python, so it can say what it left out.
- **Two KiCad API traps, both found by testing.** `kicad-cli sch export bom
  --variant` accepts a name that does not exist, exits 0, and returns the
  default BOM — so a typo would write the base product's parts into a variant.
  Names are validated against the `.kicad_pro` first. And `FOOTPRINT.IsDNP()`
  reports the default variant even after `BOARD.SetCurrentVariant()`; the real
  answer is `GetDNPForVariant()`. Since iBOM reads `IsDNP()`, generation stamps
  the variant onto a *freshly loaded copy* of the board — never the document
  KiCad has open, which would dirty the user's design.

**Test fixtures still in InvenTree, delete when done:** parts 650
`ZZ-TEST-COMPONENT` / 651 `ZZ-TEST-ASSEMBLY`, BOM item 172, StockItems 665 plus
the qty-1 items the consume/undo tests split off it (667, 671, and 668-670
still consumed), build 5 `BO-0004`. Note BOM item 172's reference was repointed to real board
designators (C2,C12,R14,R20,C13) so the real board render could be tested
against fake stock. Also: `ENABLE_PLUGINS_INTERFACE` was turned on globally,
and BO-0003 has one real consume against it from S1 (line 69-ish) plus the
panel test on BO-0004.

**Sync correctness (2026-09-03).** Four things, all found by reading the real
BOMs rather than the code:

- **Matching consults the target assembly's existing BOM by designator**, last
  of the automatic strategies. A line saying `RES-0603-100R` covers
  `R10,R11,R12,R13` is a decision someone already made by hand. Rescues 7
  symbols on the default variant, 10 on Pro, and liquidates itself via the IPN
  write-back.
- **Inherited BOM lines are read-only, and that was a live bug.** A variant's
  BOM listing *does* include lines inherited from its template (contrary to a
  first reading of the filterset), and they carry the template's pk in `part` —
  so the planner treated one as a normal line and would have PATCHed it,
  changing every sibling variant at once. They are now split out and reported
  as INHERITED, never written.
- **Orphans state their cause** — "not fitted in this variant" versus "no symbol
  in this design" — because DNP symbols are matched too. This board's orphans
  drop from three to one: the bare PCB.
- **iBOM's variant DNP support is additive only.** It marks a footprint DNP if
  the base says so, then again if the variant does, and never clears the base
  flag — while KiCad's model has variants *un*-DNP what they add, which is what
  Pro does here. So `config.kicad_variant` alone renders the base fitted set;
  the board copy must be stamped as well. Both are done.

## Context

Sam's physical assembly workflow uses InteractiveHtmlBom (iBOM) to click
through components on the rendered PCB with a "Placed" checkbox. Today that's
a one-off static file (`inventree_to_ibom_xml.py` bakes IPN/Location into an
XML the KiCad-side `generate_interactive_bom.py` consumes) with no tie back
to InvenTree — checking a box doesn't move any stock, and it's a manual
two-script, two-machine (KiCad Python + system Python) hand-off.

The goal: tie the whole thing to a specific InvenTree **Build Order**, make
IPN/Location first-class (sourced from that build's real allocations, not a
generic "most stock" guess), and make checking "Placed" actually call
InvenTree's real stock-consumption action — `POST /api/build/{id}/consume/`
(confirmed live against Sam's own instance's OpenAPI schema; consumes a
specific `BuildItem` allocation by quantity, as a background task, keeping
`BuildLine.consumed` in sync — this is the real mechanism, not a workaround).

Sam wants this to be a first-class **InvenTree plugin** with a full
React-based UI (confirmed acceptable — Sam's instance is InvenTree 1.3.2,
squarely on the modern Platform UI line that `UserInterfaceMixin` targets),
rendering the actual PCB visually via iBOM, not just a designator table. And
the KiCad ingest should become one step (KiCad → InvenTree), replacing the
current two-script pipeline.

## Project structure: a new standalone repo (decided)

This is a new, complex project — it gets its **own git repo in a new folder,
outside both existing repos** (e.g. `/Users/sam/code_personal/inventree-kicad-assembly/`
— working name, confirm at scaffold time). One repo holding the whole
integration project:

```
inventree-kicad-assembly/
├── docs/
│   └── plan.md              ← this plan, kept current as the project evolves
├── kicad-plugin/            ← the KiCad Action Plugin (Sync BOM + Generate Build iBOM)
│   └── ...                  ← includes the web/user.js bridge + install docs
├── inventree-plugin/        ← the InvenTree UserInterfaceMixin plugin (React panel)
│   └── ...
└── README.md
```

Existing scripts (`kicad_bom_to_inventree.py`, `inventree_to_ibom_xml.py`)
stay untouched in `scripts/inventree/` — their logic gets **ported into** the
new repo as proper modules, not edited in place. They can be deprecated later
once the plugin supersedes them in practice.

The one change outside this repo: `inventree-lcsc-import` (in
`scripts/inventree/`) gains the find-or-create-part endpoint (see matching
strategy 4) — a normal versioned release of that plugin, no coupling beyond
the KiCad plugin probing for it at runtime.

Development proceeds as **spikes first, then phased development, pausing to
test at every step** (see the phase breakdown at the bottom).

## The key finding: don't port iBOM into React — embed it and use its own event hook

Literally importing iBOM's JS (`ibom.js`/`render.js`/`util.js`/`table-util.js`,
~3,489 lines, read in full this session) into a React component tree is
genuinely harder than it looks and not worth it: it's non-module script that
assumes it owns the whole page (`document.onkeydown` globally, fixed element
IDs, many implicit globals) — mixing that with React's own DOM reconciliation
in the same document is a well-known footgun, and porting it to properly
scoped modules means forking upstream code that has to be re-merged on every
iBOM update.

Instead — confirmed by reading `core/ibom.py:270-314` and `web/util.js:617-647`
directly — iBOM already has a first-class, **unforked** extension point built
for exactly this:

- `generate_file()` in `core/ibom.py` template-replaces `///USERJS///` with
  the contents of `web/user.js` if that file exists next to the other web
  assets (see the existing `web/user-file-examples/` for the intended
  pattern) — nothing else about the generated HTML needs to change.
- `web/util.js` defines `IBOM_EVENT_TYPES` / `EventHandler.registerCallback`,
  a real pub-sub the core code already fires into. Critically,
  `web/ibom.js:195-233` (`createCheckboxHandlers`) already emits
  `CHECKBOX_CHANGE_EVENT` with exactly the payload needed:
  `{checkbox: "Placed", refs: [[value, ref], ...], state: 'checked'|'unchecked'}`.

So the design is: **embed the existing, unmodified, upstream-generated
`ibom.html` in an `<iframe>`**, and add one small `web/user.js` (installed
once into the KiCad plugin's directory, applies to every board generated
afterward) that relays `CHECKBOX_CHANGE_EVENT` out via
`window.parent.postMessage(...)`. iBOM's canvas/table/keyboard-shortcut code
stays 100% untouched. All the real InvenTree write logic (resolving a
designator to a `BuildItem` and calling `/consume/`) lives in the parent
React panel, using its own already-authenticated same-origin `api` client —
never inside the iframe.

## Components to build

### 1. `web/user.js` — the event bridge (new, tiny)
Installed into `.../org_openscopeproject_InteractiveHtmlBom/web/user.js`
(per-machine KiCad plugin install, same place `--pcb` auto-detect already
relies on). ~20-40 lines: on `CHECKBOX_CHANGE_EVENT` and `HIGHLIGHT_EVENT`,
`window.parent.postMessage({source: 'ibom', ...event}, targetOrigin)`. Since
the attachment will be served from the same InvenTree origin as the panel
(see #3), this could even skip postMessage and read `iframe.contentWindow`
directly — but postMessage is the more robust interface and what iBOM's own
example already models.

Because the installed copy lives inside KiCad's app-managed plugin directory,
the source of truth is checked into the new repo (`kicad-plugin/`) with a
documented install step (symlink or copy) — same gap already flagged for
`--pcb` auto-detect in `ibom_inventree_integration_plan.md`.

### 2. Build-order-scoped generation (ported from `inventree_to_ibom_xml.py`)
A `--build-order <pk>` mode (alongside a generic `--assembly-id`-only mode,
still useful outside a specific build), built in the new repo's shared module
by porting the existing script's `InvenTreeClient`/BOM-walking logic; change
location/IPN resolution to source from that build's real allocations instead
of "highest quantity overall":
- `GET /api/build/line/?build=<pk>` → `bom_item` pk per line, `consumed` so far
- `GET /api/build/item/?build=<pk>` → individual `BuildItem` allocations
  (`build_line`, `stock_item`, `quantity`) — this is what feeds the
  designator → `BuildItem` mapping the consume action needs later, so it's
  worth including `BuildItem` pks (not just IPN/Location strings) as extra
  fields in the generated XML, keyed per designator.

### 3. KiCad Action Plugin — one plugin, two sequenced actions (new)
This absorbs **both** existing ingest scripts, not just the iBOM one — Sam
flagged that generating a build's iBOM already assumes the assembly's
InvenTree BOM exists (reference designators, sub_parts), which today only
happens via a separate, manually-run `kicad_bom_to_inventree.py`. Running that
BOM push and the iBOM generation as two disconnected tools means two separate
KiCad-talks-to-InvenTree code paths that can drift apart. Instead: **one**
KiCad Action Plugin package, with `kicad_bom_to_inventree.py`'s BOM-matching
logic and `inventree_to_ibom_xml.py`'s BOM-walking logic sharing the same
`InvenTreeClient`/matching module (they already overlap heavily — both walk
the KiCad BOM and resolve InvenTree parts by IPN/reference), exposing two
menu actions under Tools → External Plugins:

1. **"Sync BOM to InvenTree"** — today's `kicad_bom_to_inventree.py`, as a
   one-click action instead of a manual CLI run. This is a *design-time*
   action: run it when the KiCad BOM changes, independent of any build order.
   Not run automatically on every iBOM generation — auto-resyncing on every
   build-iBOM generation risks silently overwriting BOM edits made directly
   in InvenTree, and is wasted work when nothing changed. "Generate Build
   iBOM" (next) can still *offer* to run this first (e.g. a confirm prompt)
   rather than requiring a separate manual step every time, without making it
   implicit/silent.

   **Matching logic gets generalized, not left LCSC-locked** (decided via
   AskUserQuestion: full option). Today's script
   (`kicad_bom_to_inventree.py:64-77`) only matches by LCSC SKU (fallback: IPN
   string equality, effectively dead since it's never actually equal in this
   project's real IPN scheme). Sam flagged this as too narrow to publish.
   Automatic strategies are tried in order, exact-match only (no auto fuzzy
   matching — a false-positive silently moves the wrong stock later):
     1. **IPN field already on the KiCad symbol**, if present — supplier-agnostic,
        exact. Doesn't exist on any symbol yet; bootstrapped by the write-back.
     2. **MPN** (manufacturer part number) — genuinely supplier-agnostic,
        already stored in InvenTree as real `ManufacturerPart` records
        (confirmed: `lcsc_to_inventree.py:1053-1065` already creates these via
        `/api/company/part/manufacturer/`). Needs the KiCad symbol to also
        carry an MPN field — **unverified**, check one real symbol early;
        many LCSC/JLCPCB KiCad import plugins write this alongside the LCSC
        field, but not confirmed for Sam's setup.
     3. **Supplier SKU** — today's mechanism, kept as a fallback, named by
        supplier rather than hardcoded to LCSC.
     4. **Create-from-LCSC fallback** (Sam's addition) — the common
        design-time case: the symbol has an LCSC PN (placed via the existing
        LCSC→KiCad tool) but the part was never purchased, so InvenTree has
        never heard of it. Instead of failing, the sync offers to create the
        InvenTree part on the fly. Mechanism: a **new small API endpoint on
        the existing `inventree-lcsc-import` plugin** — find-or-create a Part
        from an LCSC PN (LCSC data fetch, designed IPN, parameters,
        datasheet, manufacturer/supplier parts) with **no PO and no stock**.
        That logic already lives server-side in that plugin
        (`importer.py`/`lcsc.py`/`ipn_assign.py`); exposing it as an endpoint
        means the KiCad plugin stays thin (one API call) and the LCSC/IPN
        machinery stays deployed in exactly one place.

        **Soft dependency, for clean publishing** (decided, Sam's suggestion):
        the two plugins stay fully independent — no hard dependency between
        published packages. The KiCad plugin probes for the endpoint at sync
        time; if `inventree-lcsc-import` isn't installed (or predates the
        endpoint), the create-from-LCSC rows in the review dialog render
        greyed out with "install the inventree-lcsc-import plugin to enable
        creating parts from LCSC". Everything else works without it.
     5. **Sync review dialog — a management UI, not an exception popup**
        (decided: Sam prefers this shape, in scope for v1). After the
        automatic strategies run, ONE summary dialog (wxPython, same
        framework iBOM's own `dialog/settings_dialog.py` uses) shows **every**
        BOM line: matched rows annotated with which strategy matched them
        (IPN / MPN / SKU), unmatched rows highlighted with either a
        pre-checked "create from LCSC" checkbox (when an LCSC PN exists and
        the endpoint is available — greyed out with the install hint
        otherwise) or a candidate picker (suggestions searched by
        value/footprint/name — suggestions only; a human confirms every one,
        which is what makes candidate-ranking heuristics safe) with
        pick-or-skip. Nothing is written — no BOM lines, no part creation, no
        IPN write-backs — until this dialog is confirmed, so it doubles as
        the dry-run review for the whole sync. Replaces the old CLI `--map`
        flag as the manual path.
   Whichever path resolves a match — automatic or human-confirmed — **the
   resulting IPN is written back onto the KiCad symbol** (decided: yes; a new
   field, e.g. `InvenTree_IPN`, clearly reported per-part, dry-run supported).
   Every future sync for that symbol then hits strategy 1 — instant, exact,
   no supplier dependency at all. LCSC/MPN matching and the review UI become
   a one-time bootstrap per part rather than a permanent dependency, which is
   what actually makes the plugin supplier-agnostic going forward.

   (The write-back is a schematic-file edit; the earlier "don't touch KiCad
   files" decision in `ibom_inventree_integration_plan.md` was about
   *volatile, build-time* data going stale in a versioned file. An IPN is a
   stable identity fact, same category as the LCSC field already stored on
   symbols today — Sam confirmed this is fine.)

   Relationship to `inventree-lcsc-import`: the *order import* flow (CSV →
   PO/stock) stays exactly where it is, untouched — it's purchase-time, not
   design-time, and has zero KiCad involvement. But the plugin **does** gain
   one addition: the find-or-create-part-from-LCSC-PN endpoint described in
   strategy 4, factoring the part-creation half of its existing importer out
   from the PO/stock half so the KiCad sync can create never-purchased parts.
   With that, the full workflow is covered whether or not a part has ever
   been ordered: design-time parts get created (no stock, correctly showing
   as shortages against a build), and later purchases via the existing order
   import attach stock to those same parts (its existing find-before-create
   logic already matches by LCSC SKU, so no duplicates — verify this
   explicitly in testing).
2. **"Generate Build iBOM"** — today's `inventree_to_ibom_xml.py` +
   `generate_interactive_bom.py` two-step CLI dance, now a single action.
   Prompts for (or reads from a config) which Build Order pk this is for,
   generates `ibom.html` locally, then uploads it to InvenTree as a **Build
   Order attachment** (`POST /api/build/attachment/`) — hosted same-origin
   with the InvenTree panel that will embed it (no CORS needed), reachable
   straight from the Build Order page.

Both run in-process under KiCad's bundled Python (`pcbnew` already available;
`requests` may need a one-time `pip install` into that interpreter — worth
checking early rather than assuming, since `kicad_bom_to_inventree.py` today
runs under system Python, not KiCad's).

Decided: this lives in the new standalone repo (`kicad-plugin/`), shared and
project-agnostic — see "Project structure" above.

### 4. InvenTree plugin — the Build Order panel (new, genuinely new territory)
A new plugin package, sibling to `inventree-lcsc-import/`
(`scripts/inventree/inventree-lcsc-import/inventree_lcsc_import/core.py` is
the local convention to follow for plugin scaffolding — `SETTINGS`, slug,
etc. — but that plugin only uses `SettingsMixin`/`UrlsMixin` with
server-rendered Django templates; this is InvenTree's *other* plugin system,
`UserInterfaceMixin`, not used anywhere in this codebase yet). Per InvenTree's
docs (confirmed current: Sam's instance is 1.3.2, matching the
`docs.inventree.org/en/1.3.x/plugins/mixins/ui/` docs exactly), this means a
real React + Mantine + TypeScript + Vite frontend, built with InvenTree's
plugin creator scaffold, shipping compiled JS in the plugin's `static/` dir —
not a loose HTML file. Requires the `ENABLE_PLUGINS_INTERFACE` global setting
enabled.

The panel, rendered on the Build Order detail page:
- `<iframe src="<the build's ibom.html attachment URL>" allowfullscreen>`
- **Fullscreen for assembly is a hard requirement** (Sam): the panel gets a
  fullscreen toggle driving the browser Fullscreen API
  (`iframe.requestFullscreen()`) — true fullscreen while remaining the same
  embedded iframe, so the parent↔iframe `postMessage` bridge (and therefore
  live consume) keeps working. Verified as a spike assumption (S2 below), not
  taken on faith.
- Listens for the relayed `postMessage` from `web/user.js`
- On a "Placed" check: resolve designator(s) → `bom_item` (already known from
  generation-time XML, or re-derivable) → that build's `BuildLine` → its
  `BuildItem`(s), pick one with remaining quantity, call
  `POST /api/build/{id}/consume/` with `{items: [{build_item, quantity: "1"}]}`
  using the panel's own authenticated `api` client (confirmed same-origin,
  no CORS/token-storage problem — this is exactly why the InvenTree-side panel
  is the one making the write call, not the iframe)
- Since `/consume/` is a background task (`TaskDetail` response), poll
  `GET /api/background-task/{task_id}/` until `complete`; show a pending
  state on the row between check and confirmed-consumed, and a clear error
  state (with retry) if it fails — a naive fire-and-forget would let a
  checkbox look done before the deduction actually happened
- A BOM line with several designators sharing one quantity (e.g.
  `C10,C11,C3`) needs the "pick a BuildItem with remaining quantity" logic to
  actually re-fetch `/api/build/item/?build_line=<pk>` right before each
  consume call, not just trust the generation-time snapshot
- **Per-designator consumption + grouped/ungrouped both retained** (Sam):
  `/consume/` happily takes `quantity: "1"` against a line allocated 3, so
  placing just C1 consumes exactly one. InvenTree only counts
  (`BuildLine.consumed`), it has no designator concept — so the panel owns
  the designator→consumed bookkeeping. iBOM's existing Grouped/Ungrouped
  table tabs both fire the same `CHECKBOX_CHANGE_EVENT` — 1 ref vs N refs in
  the payload — and the panel consumes one unit per *newly checked*
  designator either way, so the user's choice of view needs no special
  handling
- **All state is server-side; the browser holds no source of truth** (Sam:
  "I may change PCs half way through a build"). Every checkbox column
  (Placed, Sourced, DNP, Lost) and the designator→consumed map live in the
  build's plugin metadata — `GET/PUT /api/metadata/build/<pk>/` (verified
  live: returns `{"metadata": {}}`; note it is the generic
  `/api/metadata/<model>/<id>/` endpoint, not a sub-route of `/api/build/`),
  namespaced under this plugin's slug. iBOM's own `localStorage` is demoted
  to a cache that gets overwritten, never read as truth:
    - **Hydration on load.** The bridge gains an inbound direction: after the
      iframe posts `ready`, the panel fetches state from metadata and posts
      it in, and the bridge applies it using iBOM's own globals —
      `settings.checkboxStoredRefs[checkbox] = "C1,C3"`,
      `writeStorage("checkbox_" + checkbox, ...)`, then `populateBomTable()`
      / `updateCheckboxStats()` / `drawHighlights()` to re-render (all
      confirmed present and global: `ibom.js:144,230,824,1188`,
      `util.js:24,32`). Sitting down at a different PC shows the build
      exactly as it was left.
    - **Write-through on change.** Each relayed checkbox event updates
      metadata as part of handling it, so the server stays current even for
      columns with no stock side effect (Sourced/DNP).
    - Ordering matters: hydrate *before* acting on any inbound event, or a
      restored tick gets misread as a fresh placement and consumes stock a
      second time. The panel ignores events until hydration completes.
    - Metadata doubles as the reconciliation record: it stores which
      designators were actually *consumed*, so a box that was ticked but
      whose consume call failed stays distinguishable from one that
      succeeded, and can be retried rather than silently lost.
- **"Mark lost" control** (Sam): a per-line action in the panel's own status
  strip (quantity prompt) for parts lost/dropped without being placed — not
  a build consume: `POST /api/stock/remove/` (confirmed in schema) with
  `{items: [{pk: <stock_item>, quantity: N}], notes: ...}` against the same
  StockItem the row's Location came from. Caveat to verify in testing: if
  that stock is allocated to this build, removing below the allocated
  quantity may leave the allocation inconsistent — check InvenTree's
  behavior and reduce the allocation first if needed. Optional nicety, since
  the event bridge relays any checkbox column: a per-designator "Lost"
  checkbox column in iBOM itself, mapped to a qty-1 removal

## Development breakdown: spikes, then phases — pause and test at every step

Each spike answers one question with throwaway-quality code; each phase ends
with a defined test gate where we stop, Sam tests on the real instance, and
we only continue once it passes.

### Spikes (de-risk the unknowns first)

- **S1 — InvenTree UI plugin mechanics** — ✅ **PASSED 2026-09-02.**
  Panel renders on the Build Order page under "Plugin Provided → Assembly";
  `context` gives `model=build` and the build `id`; `context.api` reaches the
  API with the logged-in session; and a real consume round-trip moved stock
  on the throwaway build BO-0004 (`BuildLine.consumed` 0 → 1, `allocated`
  5 → 4, StockItem 665 100 → 99, task `complete` and `success`).

  Findings that change later phases:
    - **`POST /api/plugins/install/` must include `packagename`, not just
      `url`.** In `installer.py` the plugins.txt update, the registry reload
      *and* the static-file collection are all gated behind a `version` key
      that is only set when `packagename` is given. A url-only install still
      reports success while silently skipping all three — which is why
      `/static/plugins/kicad-assembly/panel.js` 404'd through four install
      cycles. Check the response carries `version`.
    - **Static files are collected only on install, never on reload.**
      `collect_plugins` in the reload payload means plugin *classes*.
    - **`get_ui_panels` exceptions are swallowed into `[]`** by
      `PluginUIFeatureList`, so a plugin bug and "no panels offered" look
      identical. The first failure here was the registry holding the plugin
      *class* rather than an instance (fixed by a second reload); a
      literals-only diagnostic panel is how to tell the two apart.
    - **The worker took longer than 10 s to run the consume task.** The spike
      polled 20 × 500 ms and gave up while still pending — though it
      correctly reported "queued" rather than claiming success. P2 needs a
      longer poll with backoff, and a "queued, still working" state distinct
      from both success and failure.
    - `context.user` is not a plain object with `.username`; check its real
      shape before using it in P2.

  Throwaway fixtures left in place for P2/P3 (delete when done): parts 650
  `ZZ-TEST-COMPONENT` / 651 `ZZ-TEST-ASSEMBLY`, BOM item 172, StockItem 665,
  build 5 `BO-0004`.
- **S2 — `web/user.js` bridge + hydration + fullscreen** — ✅ **PASSED
  2026-09-02** against a real generated `ibom.html` of SR-PCB-D123-MW-PRO,
  driven from a scratch harness page. `ready` reports the checkbox names and
  board title; a checkbox click emits
  `{"checkbox":"Placed","state":"checked","refs":["C2","C12"]}`; a grouped row
  correctly reports every designator in one event; hydration from the parent
  both clears and restores ticks (and drives the board highlight); the
  `hydrated` ack comes back. Fullscreen confirmed by hand on mobile; desktop
  still to check.

  The bug this spike existed to catch: **iBOM's event refs are
  `[designator, footprintIndex]` pairs and the bridge was sending `r[1]`, the
  index** — meaningless outside one generated file. Fixed to `r[0]`. Left
  unnoticed, the panel would have been looking up BOM lines by numbers like
  `57`. Hydration needed no matching change: iBOM stores indices internally
  but its `getStoredCheckboxRefs` runs entries through a `convert()` that
  falls back to a designator lookup, so writing designator strings works and
  keeps stored state readable.

  Note `requestFullscreen()` rejects a synthetic click with
  `TypeError: not granted` — it needs real user activation, so this part
  cannot be verified by automation. If it ever proves unavailable in the panel
  context, the fallback is a CSS expand-to-viewport mode, which needs no
  permission at all.
- **S3 — KiCad plugin environment** — ✅ **PASSED 2026-09-02**, all three
  questions answered, each with a surprise.

  **HTTPS.** `requests` *is* bundled with KiCad's Python (2.32.3), but stdlib
  `urllib` fails with `CERTIFICATE_VERIFY_FAILED` — KiCad's macOS Python has
  no CA bundle wired up, its "Install Certificates.command" never having run.
  Fixed in `core/client.py` by building the SSL context from `certifi` (which
  ships with requests, so it is present) and falling back to the default
  context otherwise. Verification is never disabled; a missing bundle
  produces a real error with a pointed hint. The whole CLI now runs
  identically under KiCad's Python and the system one.

  **`python -m` broke the CLI.** Importing the package runs `__init__.py`,
  which registered the ActionPlugins, and the process then died with "The
  application handle was destroyed after running Python plugin". pcbnew is
  importable from KiCad's Python outside the app, so "can I import pcbnew" is
  not a usable guard. Registration is now gated on an env var plus an argv
  check, and `inventree_assembly_cli.py` sets the opt-out *before* importing
  the package.

  **Symbol fields.** The MPN field is called **`"Manufacturer Part"`**, not
  MPN. Coverage on the real board (79 symbols, ungrouped): 18 have LCSC+MPN,
  2 LCSC only, **3 MPN only** (which SKU matching alone would miss), and
  **56 have neither**. So no single strategy comes close — the review dialog
  and the IPN write-back are the main path for this board, not a fallback.

  **Reading the BOM: use `kicad-cli sch export bom`**, not S-expression
  parsing. It resolves the hierarchy, honours DNP (`--exclude-dnp`) and can
  emit one row per symbol (`--ref-range-delimiter ""`) instead of collapsing
  to `D1-D4`. The design is hierarchical **across directories** (three of its
  four sheets live in `../base-schematic/`), so anything walking sheets has
  to follow `Sheetfile` rather than glob one folder.

  **Write-back** (`core/schematic.py`) splices a property into the exact byte
  span of a symbol, touching nothing else: a pure 9-line addition per symbol,
  landing in whichever sheet holds that designator. Idempotent when the value
  matches, updates in place when it differs (no duplicate properties), and
  `kicad-cli` still parses all 79 symbols and reads the new field back
  afterwards. Verified on a copy in its own git repo, never the real board.

### Phases

- **P0 — Repo scaffold**: create the new repo/folder, `docs/plan.md` (this
  plan), package skeletons for `kicad-plugin/` and `inventree-plugin/`,
  README. *Gate: repo pushed, structure agreed.*
- **P1 — Build-order-scoped generation** — ✅ **PASSED 2026-09-02.**
  `core/client.py` (stdlib urllib, no `requests` dependency to install into
  KiCad's Python), `core/ibom_xml.py` and a `cli.py` entry point. Generated
  against real build BO-0003: 45 designators with correct IPNs, and Locations
  matching the build's actual allocations (`Gridfinity/Bin-A1`,
  `Resistor Sample Book`, …). Verified rendering in a real `ibom.html`, with
  identical parts in different bins still grouped into one row.

  Two calls turned out to cover everything, so no BOM fetch is needed in
  build mode: `/api/build/line/?build=<pk>&part_detail=true` gives the
  designators (`reference`) and IPN for *every* line, allocated or not, and
  `/api/build/item/?build=<pk>&location_detail=true` gives the allocation pk
  and location pathstring.

  Designators are assigned to allocations in order, spending each one's
  quantity — so a line needing 7 with 5 in one bin and 2 in another tells the
  assembler which five come from where, rather than naming one bin for all
  seven. Designators past the allocated quantity get a blank Location, which
  correctly showed T5 unallocated on the throwaway build after S1 consumed
  one unit of five.
- **P2 — InvenTree panel end-to-end** — ✅ **PASSED 2026-09-02.** The board
  renders inside the Build Order page, and ticking Placed on R14 moved real
  stock: build line allocated 4→3, consumed 1→2, StockItem 665 99→98, with
  the panel showing `pending` then `done`. Clearing localStorage and
  reloading restored the tick from server metadata and consumed nothing
  further — the cross-machine case working, verified rather than assumed.

  **Attachments cannot be framed directly.** The proxy serves `/media` with
  `Content-Disposition: attachment`, so an iframe downloads instead of
  rendering. That header is worth keeping — without it anyone who can upload
  an attachment could run script on the InvenTree origin — so the panel
  fetches the bytes through the authenticated session and frames a blob
  instead. A blob URL inherits the parent origin (verified: both
  `location.origin` inside the frame and `event.origin` on messages come back
  as the real origin), so the bridge is unaffected.

  **iBOM's localStorage is per-origin, not per-build**, keyed by board title
  and revision, so two build orders of the same board shared checkbox keys.
  Rather than paper over it with hydration, checkbox state was taken out of
  the browser entirely: the panel injects the build's state into the document
  before framing it, and the bridge serves `checkbox_*` from memory. The
  first painted frame is correct, nothing is written to localStorage, and
  stale keys from earlier versions are cleared on load. Genuine per-viewer
  preferences (dark mode, layout, visible columns) still use localStorage,
  which is where they belong.

  Verified: `shimInstalled: true`, no `checkbox_*` keys in localStorage after
  interacting, `darkmode` still persisting, and the same board under BO-0003
  and BO-0004 showing different ticks in the same browser.

  A bug this exposed: attachment `upload_date` is **date-only**, so ordering
  by it left same-day uploads tied and the panel silently framed a stale
  board. Ordered by `pk` instead.

  Consumes are serialised through a promise chain: two designators on one BOM
  line would otherwise race for the same allocation. A task still running when
  the poll budget expires is reported `queued` and deliberately *not* recorded
  as consumed, so a slow worker never fakes a stock movement.
- **P3 — KiCad "Generate Build iBOM" action** — ✅ **built and verified
  headlessly 2026-09-02**; the GUI click itself is Sam's to try.
  `core/generate.py` + `core/workflows.py` + the wx action. Run against the
  real BO-0003 it produced 45 designators and attached
  `SR-PCB-D123-MW.ibom.html`, which the panel then framed.

  iBOM is driven through **its own Python API, not a subprocess**: inside
  KiCad its package is importable and the board is already parsed, so this
  avoids hunting for an interpreter (KiCad's `sys.executable` is the app
  binary, not python) and avoids re-parsing the board.

  Uploads **replace** the previous `.ibom.html` on that order rather than
  accumulating — but only files matching that suffix, so a user's own
  attachments on the build order are never touched.

  Two bugs worth remembering: InvenTree build status **20 is *production***,
  not complete (40), so an "exclude 20/30" filter offered only finished
  orders — exactly backwards. And settings must be readable from a config
  file, not just the environment: KiCad launched from the desktop inherits
  nothing from a shell, so env-only config works from a terminal and fails
  mysteriously from the dock.

  Expected noise: iBOM logs "Component X is missing from schematic data" for
  every footprint absent from the InvenTree BOM. Test points, fiducials and
  mounting holes never appear in a BOM, so this is normal rather than a
  fault.
- **P4 — KiCad "Sync BOM" action**: layered matching, the management review
  dialog, IPN write-back, and the `inventree-lcsc-import` find-or-create
  endpoint + soft-dependency probe (built as a normal versioned release of
  that plugin). *Gate: the never-purchased-part flow in Verification below.*
- **P5 — Polish & publishing prep**: docs, install guides for both plugins,
  decide on names/registry listing.

## Verification

- The spikes (S1-S3) are their own verification — they exist specifically to
  prove the riskiest assumptions before investing in the full build, and each
  phase has its own test gate above
- End-to-end: create/use a real (or test) Build Order, generate its
  `ibom.html` via the new `--build-order` flag, confirm IPN/Location match
  that build's actual allocations (not just "some stock somewhere")
- Check a "Placed" box in the embedded panel, confirm via the InvenTree web
  UI that the corresponding `BuildLine.consumed` actually incremented and the
  right `StockItem` quantity actually dropped
- Uncheck and re-check to confirm no double-consumption/drift
- Cross-machine continuity: place several parts, then open the same build
  order in a different browser (or a private window, which is a fresh
  `localStorage`) and confirm the ticks come back from the server and that
  re-opening consumes nothing extra
- Per-designator flow: on a 3-designator line (C1,C2,C3), place only C1 in
  ungrouped view — confirm `BuildLine.consumed` goes to exactly 1 and the
  designator map in build metadata records C1; then check the grouped row and
  confirm only the remaining 2 get consumed
- "Mark lost": remove 2 of an allocated part via the panel control, confirm
  the StockItem quantity drops with the note recorded, and check what
  InvenTree does to the build allocation when stock dips below the allocated
  quantity (this decides whether the control needs to reduce the allocation
  first)
- Kill the background worker mid-consume (or otherwise force a failure) to
  confirm the panel shows a real error state rather than a silently-wrong
  "placed" checkbox
- Never-purchased-part flow: put a fresh LCSC part on a test schematic (one
  that doesn't exist in InvenTree), run Sync BOM, confirm the review dialog
  offers create-from-LCSC, the part is created with IPN/parameters/datasheet
  but zero stock and no PO, the BOM line lands, and it shows as a shortage on
  a build. Then import a real order CSV containing that same LCSC PN via the
  existing `inventree-lcsc-import` upload and confirm it attaches stock to
  the same part rather than creating a duplicate
