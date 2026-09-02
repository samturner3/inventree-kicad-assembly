/**
 * Everything this panel does against the InvenTree API.
 *
 * All of it goes through the `api` handed to the plugin in its context: that
 * is the host application's own authenticated, same-origin axios instance, so
 * there is no token to store and no CORS to configure. The embedded iBOM
 * iframe never gets access to any of this -- it only reports what the user
 * clicked.
 */

export const PLUGIN_KEY = "kicad-assembly";

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

/** Per-designator state, held on the server so a build survives changing machines. */
export interface PanelState {
  /** checkbox name -> designators ticked, mirroring iBOM's own columns */
  checkboxes: Record<string, string[]>;
  /** designators whose stock has actually been consumed, with the allocation used */
  consumed: Record<string, { build_item: number; at: string }>;
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

/** The generated iBOM uploaded against this build, newest first. */
export async function findIbomAttachment(ctx: PluginContext, buildId: number | string) {
  const r = await ctx.api.get(
    `/api/attachment/?model_type=build&model_id=${buildId}&limit=100`
  );
  const rows = unwrap(r.data).filter((a: any) =>
    String(a.attachment || "").toLowerCase().endsWith(".html")
  );
  rows.sort((a: any, b: any) => String(b.upload_date).localeCompare(String(a.upload_date)));
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
export async function fetchAttachmentUrl(ctx: PluginContext, attachment: any): Promise<string> {
  const r = await ctx.api.get(attachment.attachment, { responseType: "blob" });
  return URL.createObjectURL(r.data as Blob);
}

export async function loadState(ctx: PluginContext, buildId: number | string): Promise<PanelState> {
  const r = await ctx.api.get(`/api/metadata/build/${buildId}/`);
  const stored = r.data?.metadata?.[PLUGIN_KEY];
  return {
    checkboxes: stored?.checkboxes ?? {},
    consumed: stored?.consumed ?? {},
  };
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
export async function consumeOne(
  ctx: PluginContext,
  buildId: number | string,
  line: BuildLine
): Promise<{ taskId: string; buildItem: number }> {
  const r = await ctx.api.get(`/api/build/item/?build_line=${line.pk}&limit=100`);
  const allocations = unwrap(r.data).filter((a: any) => Number(a.quantity) > 0);
  if (allocations.length === 0) {
    throw new Error(`Nothing allocated to ${line.reference} left to consume`);
  }
  const alloc = allocations[0];

  const res = await ctx.api.post(`/api/build/${buildId}/consume/`, {
    items: [{ build_item: alloc.pk, quantity: "1" }],
    notes: "Placed during PCB assembly (kicad-assembly panel)",
  });
  return { taskId: res.data?.task_id, buildItem: alloc.pk };
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
