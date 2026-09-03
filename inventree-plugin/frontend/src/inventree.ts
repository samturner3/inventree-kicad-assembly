/**
 * Everything this panel does against the InvenTree API.
 *
 * All of it goes through the `api` handed to the plugin in its context: that
 * is the host application's own authenticated, same-origin axios instance, so
 * there is no token to store and no CORS to configure. The embedded iBOM
 * iframe never gets access to any of this -- it only reports what the user
 * clicked.
 */

import { PROTOCOL } from "./bridge";

export const PLUGIN_KEY = "kicad-assembly";
/**
 * Written by the KiCad plugin at generation time, read-only here.
 *
 * A key of its own rather than a field on PLUGIN_KEY: saveState() replaces
 * that key wholesale on every checkbox, so anything sharing it would be gone
 * the first time someone ticked a box.
 */
export const BOARD_KEY = "kicad-assembly:board";

/** What the generated board says about itself. */
export interface BoardContext {
  variant?: string;
  /** designators this variant does not populate */
  not_fitted?: string[];
  generated_at?: string;
}

export interface PluginContext {
  api: {
    get: (url: string, config?: any) => Promise<{ data: any }>;
    post: (url: string, data?: any, config?: any) => Promise<{ data: any }>;
    patch: (url: string, data?: any, config?: any) => Promise<{ data: any }>;
  };
  user?: any;
  model?: string;
  id?: number | string;
  instance?: any;
}

export interface BuildLine {
  pk: number;
  reference: string;
  quantity: number;
  allocated: number;
  consumed: number;
  part_detail?: { IPN?: string; name?: string };
}

/** One stock allocation against a build line. */
export interface Allocation {
  pk: number;
  build_line: number;
  quantity: number | string;
  location_detail?: { pathstring?: string; name?: string };
}

/** The two columns InvenTree owns, per designator. */
export interface LiveFields {
  [ref: string]: { IPN: string; Location: string };
}

/**
 * What one consume did, kept so it can be undone.
 *
 * Consuming splits the placed quantity off into a stock item of its own and
 * points it at the build, so undoing needs that item's pk -- which the consume
 * response does not give, since the work happens in a background task. It is
 * discovered afterwards by diffing this build's consumed stock.
 */
export interface ConsumedRecord {
  build_item: number;
  at: string;
  /** the stock item the consume created; null when it could not be identified */
  stock_item?: number | null;
  line?: number;
  quantity?: number;
  /** where the stock came from, so an undo can put it back there */
  location?: number | null;
  location_name?: string;
  part_name?: string;
}

/** Per-designator state, held on the server so a build survives changing machines. */
export interface PanelState {
  /** checkbox name -> designators ticked, mirroring iBOM's own columns */
  checkboxes: Record<string, string[]>;
  /** designators whose stock has actually been consumed, with the allocation used */
  consumed: Record<string, ConsumedRecord>;
}

export const emptyState = (): PanelState => ({ checkboxes: {}, consumed: {} });

function unwrap(data: any): any[] {
  return Array.isArray(data) ? data : (data?.results ?? []);
}

export async function fetchBuildLines(ctx: PluginContext, buildId: number | string) {
  const r = await ctx.api.get(`/api/build/line/?build=${buildId}&part_detail=true&limit=1000`);
  return unwrap(r.data) as BuildLine[];
}

/** designator -> the build line it belongs to. */
export function designatorIndex(lines: BuildLine[]): Map<string, BuildLine> {
  const index = new Map<string, BuildLine>();
  for (const line of lines) {
    for (const ref of (line.reference || "").split(",")) {
      const key = ref.trim();
      if (key) index.set(key, line);
    }
  }
  return index;
}

export async function fetchAllocations(ctx: PluginContext, buildId: number | string) {
  const r = await ctx.api.get(
    `/api/build/item/?build=${buildId}&location_detail=true&limit=1000`
  );
  return unwrap(r.data) as Allocation[];
}

/**
 * IPN and Location per designator, from this build's current allocations.
 *
 * These are the two columns InvenTree owns. They move whenever stock is
 * allocated or a bin changes -- long after the board file was generated -- so
 * the panel recomputes them rather than trusting what KiCad baked in. Anything
 * else in that file describes the board, and only KiCad can change it.
 *
 * The algorithm mirrors fields_for_build() in the KiCad plugin's
 * core/ibom_xml.py, whose docstring is the canonical description: walk a
 * line's allocations largest first and spend each one's quantity across
 * successive designators, so a line needing 7 with 5 in one bin and 2 in
 * another says which five come from where instead of naming one bin for all
 * seven. Designators past the allocated quantity get a blank Location.
 */
export function fieldsForBuild(lines: BuildLine[], allocations: Allocation[]): LiveFields {
  const byLine = new Map<number, Allocation[]>();
  for (const a of allocations) {
    const list = byLine.get(a.build_line) ?? [];
    list.push(a);
    byLine.set(a.build_line, list);
  }

  const fields: LiveFields = {};
  for (const line of lines) {
    const refs = (line.reference || "")
      .split(",")
      .map((r) => r.trim())
      .filter(Boolean);
    if (!refs.length) continue;

    const ipn = line.part_detail?.IPN || "";
    const queue = [...(byLine.get(line.pk) ?? [])].sort(
      (a, b) => Number(b.quantity) - Number(a.quantity)
    );

    const assigned: Record<string, string> = {};
    let idx = 0;
    for (const alloc of queue) {
      const location =
        alloc.location_detail?.pathstring || alloc.location_detail?.name || "";
      for (let n = 0; n < Math.floor(Number(alloc.quantity)); n += 1) {
        if (idx >= refs.length) break;
        assigned[refs[idx]] = location;
        idx += 1;
      }
    }

    for (const ref of refs) {
      fields[ref] = { IPN: ipn, Location: assigned[ref] ?? "" };
    }
  }
  return fields;
}

/** The generated iBOM uploaded against this build, newest first. */
export async function findIbomAttachment(ctx: PluginContext, buildId: number | string) {
  const r = await ctx.api.get(
    `/api/attachment/?model_type=build&model_id=${buildId}&limit=100`
  );
  const rows = unwrap(r.data).filter((a: any) =>
    String(a.attachment || "").toLowerCase().endsWith(".html")
  );
  // Order by pk, not upload_date: that field is date-only, so everything
  // uploaded on the same day ties and the "newest" is whatever order the API
  // happened to return -- which silently served a stale board.
  rows.sort((a: any, b: any) => Number(b.pk) - Number(a.pk));
  return rows[0] ?? null;
}

/**
 * Fetch an attachment and hand back a blob: URL an iframe can render.
 *
 * Attachments cannot be framed directly: InvenTree's proxy serves /media with
 * `Content-Disposition: attachment`, so the browser downloads the file instead
 * of displaying it. That header is a sensible defence -- without it anyone who
 * can upload an attachment could run script on the InvenTree origin -- so it
 * is worked with rather than removed. Fetching the bytes through the
 * authenticated session and framing a blob sidesteps it without weakening
 * anything.
 *
 * A blob URL inherits the creating document's origin (verified, not assumed),
 * so the iframe stays same-origin and the postMessage bridge works normally.
 *
 * The caller owns the returned URL and must revokeObjectURL it.
 */
export async function fetchAttachmentUrl(
  ctx: PluginContext,
  attachment: any,
  state: PanelState
): Promise<string> {
  const r = await ctx.api.get(attachment.attachment, { responseType: "text" });
  const html = injectState(String(r.data), state);
  return URL.createObjectURL(new Blob([html], { type: "text/html" }));
}

/**
 * Put this build's checkbox state into the document before it is framed.
 *
 * iBOM reads its stored state during window.onload, long before a postMessage
 * could arrive, and it keys that storage on the board rather than the build --
 * so without this a frame would briefly show whichever build of this board was
 * open last. Injecting ahead of load means the first painted frame is already
 * correct, and the bridge keeps checkbox state out of localStorage entirely.
 */
export function injectState(html: string, state: PanelState): string {
  const payload = JSON.stringify({ checkboxes: state.checkboxes });
  const tag =
    `<script>var IBOM_BRIDGE_STATE=${payload};</script>` + STICKY_HEADER +
    FIELD_UPDATER;
  const at = html.indexOf("<script");
  return at === -1 ? tag + html : html.slice(0, at) + tag + html.slice(at);
}

/**
 * Pins the BOM table's header row while the list scrolls.
 *
 * iBOM scrolls #bomdiv (`.split { overflow-y: auto }`), so sticking the cells
 * to its top works without touching iBOM's own layout.
 *
 * Three details, all about losing to iBOM's own rules. This block is injected
 * ahead of the first <script>, which puts it after the stylesheet iBOM inlines
 * in <head> -- so equal-specificity rules go to this one. `th` inherits
 * `position: relative` from `.bom th, .bom td` and has to be overridden. The
 * checkbox headers set it again through `.bom .bom-checkbox`, which is more
 * specific and won, leaving those three columns scrolling while the rest
 * stayed put -- so they need saying again, more specifically still. And a
 * sticky cell's border does not travel with it in Chrome, hence the inset
 * shadow standing in for the bottom border.
 *
 * Injected rather than shipped in iBOM's user.css for the same reason as the
 * field updater: user.css is baked in when KiCad generates the file, so a board
 * generated earlier would never get it.
 */
const STICKY_HEADER = `<style>
  .bom th,
  .bom th.bom-checkbox,
  .bom th.numCol {
    position: sticky;
    top: 0;
    z-index: 2;
  }
  .bom th { box-shadow: inset 0 -1px 0 black; }
  .dark .bom th { box-shadow: inset 0 -1px 0 #777; }
</style>`;

/**
 * Applies refreshed IPN/Location inside the frame, on request from the panel.
 *
 * Injected here rather than shipped in the bridge (web/user.js) on purpose:
 * the bridge is baked into each file when KiCad generates it, so a board
 * generated before this existed could never be refreshed -- which is exactly
 * the case this is for. Injecting means it works on every attachment already
 * uploaded, with no regeneration.
 *
 * iBOM keeps its table data in pcbdata.bom.fields, a
 * {componentIndex: [values in config.fields order]} map, and rebuilds the
 * table from it in populateBomTable(). The designator -> index mapping comes
 * from the same [ref, index] pairs the checkbox events already use.
 *
 * The listener is registered immediately but only touches those globals when a
 * message arrives, by which time the page has loaded.
 */
const FIELD_UPDATER = `<script>
(function () {
  window.addEventListener("message", function (event) {
    var msg = event.data || {};
    if (event.origin !== window.location.origin) return;
    if (msg.protocol !== ${JSON.stringify(PROTOCOL)} || msg.type !== "updateFields") return;
    var fields = (msg.payload || {}).fields || {};

    var byRef = {};
    ["both", "F", "B"].forEach(function (side) {
      (pcbdata.bom[side] || []).forEach(function (group) {
        (group || []).forEach(function (ref) { byRef[ref[0]] = ref[1]; });
      });
    });
    var columns = {};
    (config.fields || []).forEach(function (name, i) { columns[name] = i; });

    var changed = 0;
    Object.keys(fields).forEach(function (ref) {
      var i = byRef[ref];
      var row = i === undefined ? null : pcbdata.bom.fields[i];
      if (!row) return;
      var values = fields[ref] || {};
      Object.keys(values).forEach(function (name) {
        var col = columns[name];
        if (col !== undefined && row[col] !== values[name]) {
          row[col] = values[name];
          changed += 1;
        }
      });
    });

    // Redraws the cells. Row grouping was computed when the file was generated
    // and is not recomputed -- Location is not a grouping field, so what moves
    // day to day is covered.
    if (changed) populateBomTable();
    window.parent.postMessage(
      { protocol: ${JSON.stringify(PROTOCOL)}, type: "fieldsUpdated",
        payload: { changed: changed } },
      window.location.origin
    );
  });
})();
</script>`;

export async function loadState(ctx: PluginContext, buildId: number | string): Promise<PanelState> {
  const r = await ctx.api.get(`/api/metadata/build/${buildId}/`);
  const stored = r.data?.metadata?.[PLUGIN_KEY];
  return {
    checkboxes: stored?.checkboxes ?? {},
    consumed: stored?.consumed ?? {},
  };
}

export async function loadBoardContext(
  ctx: PluginContext,
  buildId: number | string
): Promise<BoardContext> {
  try {
    const r = await ctx.api.get(`/api/metadata/build/${buildId}/`);
    return (r.data?.metadata?.[BOARD_KEY] ?? {}) as BoardContext;
  } catch {
    // Absent for any board generated before this existed; the panel just
    // loses the nicety of naming unfitted parts.
    return {};
  }
}

export async function saveState(
  ctx: PluginContext,
  buildId: number | string,
  state: PanelState
) {
  // The metadata endpoint merges top-level keys, so writing our own key whole
  // leaves any other plugin's metadata on this build untouched.
  await ctx.api.patch(`/api/metadata/build/${buildId}/`, {
    metadata: { [PLUGIN_KEY]: state },
  });
}

/**
 * Consume one unit of a build line's allocated stock.
 *
 * Allocations are re-fetched immediately before consuming rather than trusted
 * from a snapshot: quantities change as the build progresses, and picking an
 * allocation that has already been spent would fail or consume the wrong one.
 *
 * Returns the background task id -- /consume/ is asynchronous, so the stock has
 * not moved yet when this resolves.
 */
export interface ConsumeTicket {
  taskId: string;
  buildItem: number;
  line: number;
  part: number;
  location: number | null;
  locationName: string;
  partName: string;
  quantity: number;
  /** stock already consumed by this build for this part, before this call */
  consumedBefore: number[];
}

/** Stock items this build has consumed of one part. */
async function consumedStock(
  ctx: PluginContext,
  buildId: number | string,
  part: number
): Promise<number[]> {
  const r = await ctx.api.get(
    `/api/stock/?consumed_by=${buildId}&part=${part}&limit=1000`
  );
  return unwrap(r.data).map((s: any) => Number(s.pk));
}

export async function consumeOne(
  ctx: PluginContext,
  buildId: number | string,
  line: BuildLine
): Promise<ConsumeTicket> {
  const r = await ctx.api.get(`/api/build/item/?build_line=${line.pk}&limit=100`);
  const allocations = unwrap(r.data).filter((a: any) => Number(a.quantity) > 0);
  if (allocations.length === 0) {
    throw new Error(`Nothing allocated to ${line.reference} left to consume`);
  }
  const alloc = allocations[0];

  // Where this unit is coming from, read before it moves. An undo has to put
  // it back somewhere, and the consume itself clears the location.
  const source = await ctx.api.get(
    `/api/stock/${alloc.stock_item}/?location_detail=true`
  );
  const part = Number(source.data?.part);
  const consumedBefore = await consumedStock(ctx, buildId, part);

  const res = await ctx.api.post(`/api/build/${buildId}/consume/`, {
    items: [{ build_item: alloc.pk, quantity: "1" }],
    notes: "Placed during PCB assembly (kicad-assembly panel)",
  });

  const loc = source.data?.location_detail;
  return {
    taskId: res.data?.task_id,
    buildItem: alloc.pk,
    line: line.pk,
    part,
    location: source.data?.location ?? null,
    locationName: loc?.pathstring || loc?.name || "no location",
    partName: line.part_detail?.IPN || line.part_detail?.name || `part ${part}`,
    quantity: 1,
    consumedBefore,
  };
}

/**
 * Which stock item the consume produced, found by difference.
 *
 * The consume either splits a new item off the allocation or, when the
 * allocation used the whole item, consumes that item itself -- so the one
 * reliable identifier is whatever is newly marked consumed for this part.
 * Consumes are serialised by the caller, which is what makes the diff safe.
 * Returns null if that is not exactly one item, in which case the undo is
 * offered no guess rather than a wrong one.
 */
export async function findConsumedStock(
  ctx: PluginContext,
  buildId: number | string,
  ticket: ConsumeTicket
): Promise<number | null> {
  const before = new Set(ticket.consumedBefore);
  const added = (await consumedStock(ctx, buildId, ticket.part)).filter(
    (pk) => !before.has(pk)
  );
  return added.length === 1 ? added[0] : null;
}

/**
 * Put a consumed unit back: the exact inverse of what the consume did.
 *
 * InvenTree has no un-consume endpoint, so this reverses the three effects by
 * hand -- return the stock item, decrement the line's consumed count, restore
 * the allocation. Ordered so that the most important record is fixed first: if
 * a later step fails, the stock is at least back on the shelf, and the caller
 * is told exactly which steps did land.
 *
 * The returned unit stays a stock item of its own rather than merging back
 * into the item it was split from. /api/stock/merge/ refuses allocated stock,
 * and re-allocating is the more important half of the reversal -- so the bin
 * ends up holding two correct rows instead of one.
 */
export async function unconsume(
  ctx: PluginContext,
  buildId: number | string,
  record: ConsumedRecord
): Promise<void> {
  if (!record.stock_item) {
    throw new Error("the consumed stock item was never identified, so this cannot be undone here");
  }
  const quantity = record.quantity ?? 1;
  const done: string[] = [];

  try {
    await ctx.api.patch(`/api/stock/${record.stock_item}/`, {
      consumed_by: null,
      location: record.location ?? null,
    });
    done.push("returned the stock");

    if (record.line) {
      const line = await ctx.api.get(`/api/build/line/${record.line}/`);
      const consumed = Math.max(0, Number(line.data?.consumed ?? 0) - quantity);
      await ctx.api.patch(`/api/build/line/${record.line}/`, { consumed });
      done.push("corrected the consumed count");

      await ctx.api.post(`/api/build/${buildId}/allocate/`, {
        items: [
          { build_line: record.line, stock_item: record.stock_item, quantity },
        ],
      });
    }
  } catch (e: any) {
    const detail = e?.message ?? String(e);
    throw new Error(
      done.length
        ? `${detail} (already done: ${done.join(", ")} — finish the rest in InvenTree)`
        : detail
    );
  }
}

/**
 * Wait for a background task, backing off as it goes.
 *
 * The worker regularly takes longer than ten seconds, so a short fixed poll
 * reports a perfectly healthy consume as a failure. Resolves to 'pending' if
 * it is simply still running -- which is a distinct outcome from success and
 * from failure, and the UI says so rather than guessing.
 */
export async function awaitTask(
  ctx: PluginContext,
  taskId: string,
  budgetMs = 90000
): Promise<"success" | "failed" | "pending"> {
  const started = Date.now();
  let delay = 500;
  while (Date.now() - started < budgetMs) {
    await new Promise((r) => setTimeout(r, delay));
    delay = Math.min(delay * 1.5, 5000);
    try {
      const r = await ctx.api.get(`/api/background-task/${taskId}/`);
      if (r.data?.complete) return r.data?.success ? "success" : "failed";
    } catch {
      // A task that has been reaped is no longer queryable; keep trying until
      // the budget runs out rather than calling it a failure on one bad read.
    }
  }
  return "pending";
}
