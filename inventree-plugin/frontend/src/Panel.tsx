import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { isBridgeMessage, sendHydrate, type CheckboxPayload } from "./bridge";
import {
  awaitTask,
  consumeOne,
  designatorIndex,
  emptyState,
  fetchAttachmentUrl,
  fetchBuildLines,
  findIbomAttachment,
  loadState,
  saveState,
  type BuildLine,
  type PanelState,
  type PluginContext,
} from "./inventree";

/** Which checkbox column means "this part is now on the board". */
const PLACED = "placed";

type Status = "pending" | "done" | "queued" | "error";

interface Entry {
  ref: string;
  status: Status;
  message?: string;
}

function StatusPill({ status }: { status: Status }) {
  const colours: Record<Status, string> = {
    pending: "#f59f00",
    done: "#2f9e44",
    queued: "#1971c2",
    error: "#e03131",
  };
  return (
    <span
      style={{
        background: colours[status],
        color: "white",
        borderRadius: 10,
        padding: "1px 8px",
        fontSize: 11,
      }}
    >
      {status}
    </span>
  );
}

function AssemblyPanel({ context }: { context: PluginContext }) {
  const buildId = context.id!;
  const frameRef = useRef<HTMLIFrameElement | null>(null);

  const [attachment, setAttachment] = useState<any>(null);
  const [frameUrl, setFrameUrl] = useState<string | null>(null);
  const [lines, setLines] = useState<BuildLine[]>([]);
  const [state, setState] = useState<PanelState>(emptyState());
  const [entries, setEntries] = useState<Entry[]>([]);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [frameReady, setFrameReady] = useState(false);
  const [loading, setLoading] = useState(true);

  // Until the iframe has been given the server's state, an inbound checkbox
  // event cannot be told apart from a tick being restored -- acting on one
  // would consume stock a second time. Events are dropped until this is true.
  const [hydrated, setHydrated] = useState(false);

  // Mirrors held in refs so the message handler, which is bound once, always
  // sees current values rather than those captured at subscribe time.
  const stateRef = useRef(state);
  const indexRef = useRef(new Map<string, BuildLine>());
  const hydratedRef = useRef(false);
  const queueRef = useRef<Promise<void>>(Promise.resolve());

  useEffect(() => { stateRef.current = state; }, [state]);
  useEffect(() => { hydratedRef.current = hydrated; }, [hydrated]);
  useEffect(() => { indexRef.current = designatorIndex(lines); }, [lines]);

  const origin = useMemo(() => window.location.origin, []);

  const setEntry = useCallback((ref: string, status: Status, message?: string) => {
    setEntries((prev) => {
      const next = prev.filter((e) => e.ref !== ref);
      return [{ ref, status, message }, ...next].slice(0, 40);
    });
  }, []);

  const persist = useCallback(
    async (next: PanelState) => {
      setState(next);
      stateRef.current = next;
      try {
        await saveState(context, buildId, next);
      } catch (e: any) {
        setEntry("(save)", "error", e?.message ?? String(e));
      }
    },
    [context, buildId, setEntry]
  );

  // Initial load: the board to embed, the BOM lines to resolve designators
  // against, and whatever state the last session left on the server.
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const [att, ln, st] = await Promise.all([
          findIbomAttachment(context, buildId),
          fetchBuildLines(context, buildId),
          loadState(context, buildId),
        ]);
        if (cancelled) return;
        setAttachment(att);
        setLines(ln);
        setState(st);
        stateRef.current = st;
        if (att) {
          const url = await fetchAttachmentUrl(context, att, st);
          if (cancelled) {
            URL.revokeObjectURL(url);
            return;
          }
          setFrameUrl(url);
        }
      } catch (e: any) {
        if (!cancelled) setLoadError(e?.message ?? String(e));
      } finally {
        // Held until the board bytes are in hand, not merely until the
        // attachment is known: dropping it earlier is what made the "nothing
        // attached" notice flash up before the board appeared.
        if (!cancelled) setLoading(false);
      }
    })();
    return () => { cancelled = true; };
  }, [context, buildId]);

  /**
   * Consume one unit for a designator, start to finish.
   *
   * Serialised through a promise chain: two designators on the same BOM line
   * would otherwise race for the same allocation and one would consume stock
   * that the other had already spent.
   */
  const consumeDesignator = useCallback(
    (ref: string) => {
      queueRef.current = queueRef.current.then(async () => {
        if (stateRef.current.consumed[ref]) return; // already accounted for

        const line = indexRef.current.get(ref);
        if (!line) {
          setEntry(ref, "error", "no BOM line for this designator");
          return;
        }

        setEntry(ref, "pending");
        try {
          const { taskId, buildItem } = await consumeOne(context, buildId, line);
          const outcome = await awaitTask(context, taskId);

          if (outcome === "success") {
            setEntry(ref, "done");
            await persist({
              ...stateRef.current,
              consumed: {
                ...stateRef.current.consumed,
                [ref]: { build_item: buildItem, at: new Date().toISOString() },
              },
            });
          } else if (outcome === "pending") {
            // Deliberately not recorded as consumed: the worker may still
            // complete it, and claiming success here is how stock records
            // quietly drift from reality.
            setEntry(ref, "queued", "worker still running; re-open to confirm");
          } else {
            setEntry(ref, "error", "consume task failed");
          }
        } catch (e: any) {
          setEntry(ref, "error", e?.message ?? String(e));
        }
      });
    },
    [context, buildId, persist, setEntry]
  );

  // Bridge. Bound once; everything it needs comes from refs.
  useEffect(() => {
    function onMessage(event: MessageEvent) {
      if (event.origin !== origin || !isBridgeMessage(event.data)) return;
      const { type, payload } = event.data;

      if (type === "ready") {
        setFrameReady(true);
        if (payload?.seeded) {
          // State was injected before the document loaded, so the frame is
          // already showing this build correctly and events can be trusted now.
          setHydrated(true);
          hydratedRef.current = true;
        } else {
          sendHydrate(frameRef.current, stateRef.current.checkboxes, origin);
        }
        return;
      }
      if (type === "hydrated") {
        setHydrated(true);
        return;
      }
      if (type !== "checkboxChangeEvent" || !hydratedRef.current) return;

      const { checkbox, state: checkState, refs } = payload as CheckboxPayload;

      const current = stateRef.current;
      const ticked = new Set(current.checkboxes[checkbox] ?? []);
      for (const ref of refs) {
        if (checkState === "checked") ticked.add(ref);
        else ticked.delete(ref);
      }
      void persist({
        ...current,
        checkboxes: { ...current.checkboxes, [checkbox]: [...ticked] },
      });

      // Only ticking Placed moves stock. Unticking does not put stock back:
      // that would need a reversal InvenTree has no endpoint for, so the
      // consumed record stands and the box is cosmetic once spent.
      if (checkbox.toLowerCase() === PLACED && checkState === "checked") {
        for (const ref of refs) consumeDesignator(ref);
      }
    }

    window.addEventListener("message", onMessage);
    return () => window.removeEventListener("message", onMessage);
  }, [origin, persist, consumeDesignator]);

  useEffect(() => {
    return () => {
      if (frameUrl) URL.revokeObjectURL(frameUrl);
    };
  }, [frameUrl]);

  // Fullscreen is taken on the panel wrapper rather than the iframe, so the
  // status line goes with it -- during assembly that line is the only feedback
  // that a tick actually moved stock.
  const wrapRef = useRef<HTMLDivElement | null>(null);
  const [fullscreen, setFullscreen] = useState(false);

  useEffect(() => {
    const onChange = () => setFullscreen(document.fullscreenElement === wrapRef.current);
    document.addEventListener("fullscreenchange", onChange);
    return () => document.removeEventListener("fullscreenchange", onChange);
  }, []);

  const toggleFullscreen = useCallback(() => {
    if (document.fullscreenElement) void document.exitFullscreen();
    else void wrapRef.current?.requestFullscreen?.();
  }, []);

  const consumedCount = Object.keys(state.consumed).length;
  const placedCount = (state.checkboxes["Placed"] ?? state.checkboxes["placed"] ?? []).length;

  if (loadError) {
    return <div style={{ padding: 16, color: "#e03131" }}>Could not load: {loadError}</div>;
  }

  if (loading) {
    return (
      <div style={{ padding: 16, opacity: 0.75 }}>Loading the interactive BOM…</div>
    );
  }

  if (!attachment) {
    return (
      <div style={{ padding: 16 }}>
        <strong>No interactive BOM attached to this build order yet.</strong>
        <div style={{ marginTop: 8, opacity: 0.75 }}>
          Generate one from KiCad (Tools → External Plugins → InvenTree: Generate
          Build iBOM) and it will appear here.
        </div>
      </div>
    );
  }

  return (
    <div
      ref={wrapRef}
      style={{
        display: "flex",
        flexDirection: "column",
        height: fullscreen ? "100vh" : "75vh",
        gap: 8,
        // A fullscreened element is painted on the UA's black backdrop, which
        // the status line would otherwise have to be read against.
        background: fullscreen ? "var(--mantine-color-body, #fff)" : undefined,
        color: fullscreen ? "var(--mantine-color-text, inherit)" : undefined,
        padding: fullscreen ? 8 : 0,
        boxSizing: "border-box",
      }}
    >
      <div style={{ display: "flex", alignItems: "center", gap: 12, flexWrap: "wrap" }}>
        <button onClick={toggleFullscreen} style={{ padding: "4px 10px", cursor: "pointer" }}>
          {fullscreen ? "Exit fullscreen" : "Fullscreen"}
        </button>
        <span style={{ fontSize: 13, opacity: 0.8 }}>
          {!frameUrl
            ? "fetching board…"
            : frameReady
              ? hydrated
                ? "connected"
                : "restoring…"
              : "loading board…"}
          {" · "}
          {placedCount} placed · {consumedCount} consumed
        </span>
        {entries.slice(0, 3).map((e) => (
          <span key={e.ref} style={{ fontSize: 12, display: "flex", gap: 5, alignItems: "center" }}>
            <code>{e.ref}</code>
            <StatusPill status={e.status} />
            {e.message && <span style={{ opacity: 0.7 }}>{e.message}</span>}
          </span>
        ))}
      </div>

      <iframe
        ref={frameRef}
        src={frameUrl ?? undefined}
        allowFullScreen
        style={{ flex: 1, width: "100%", border: "1px solid #ddd", borderRadius: 4 }}
        title="Interactive BOM"
      />

      {entries.some((e) => e.status === "error") && (
        <div style={{ fontSize: 12, color: "#e03131" }}>
          {entries
            .filter((e) => e.status === "error")
            .map((e) => `${e.ref}: ${e.message}`)
            .join(" · ")}
          <button
            style={{ marginLeft: 8, cursor: "pointer" }}
            onClick={() =>
              entries.filter((e) => e.status === "error").forEach((e) => consumeDesignator(e.ref))
            }
          >
            Retry
          </button>
        </div>
      )}
    </div>
  );
}

export function renderPanel(context: PluginContext) {
  return <AssemblyPanel context={context} />;
}
