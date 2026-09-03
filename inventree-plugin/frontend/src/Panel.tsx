import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { isBridgeMessage, sendHydrate, type CheckboxPayload } from "./bridge";
import {
  awaitTask,
  consumeOne,
  designatorIndex,
  emptyState,
  fetchAttachmentUrl,
  fetchBuildLines,
  findConsumedStock,
  findIbomAttachment,
  loadBoardContext,
  loadState,
  saveState,
  unconsume,
  type BoardContext,
  type BuildLine,
  type ConsumedRecord,
  type PanelState,
  type PluginContext,
} from "./inventree";

/** Which checkbox column means "this part is now on the board". */
const PLACED = "placed";

type Status = "pending" | "done" | "queued" | "error" | "returned" | "not fitted";

interface Entry {
  ref: string;
  status: Status;
  message?: string;
}

/** An untick waiting to be told whether it should move stock back. */
interface UndoPrompt {
  ref: string;
  record: ConsumedRecord;
}

function StatusPill({ status }: { status: Status }) {
  const colours: Record<Status, string> = {
    pending: "#f59f00",
    done: "#2f9e44",
    queued: "#1971c2",
    error: "#e03131",
    returned: "#868e96",
    "not fitted": "#868e96",
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
  const [undos, setUndos] = useState<UndoPrompt[]>([]);
  const [board, setBoard] = useState<BoardContext>({});
  const notFittedRef = useRef<Set<string>>(new Set());
  useEffect(() => {
    notFittedRef.current = new Set(board.not_fitted ?? []);
  }, [board]);
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
        const [att, ln, st, ctx] = await Promise.all([
          findIbomAttachment(context, buildId),
          fetchBuildLines(context, buildId),
          loadState(context, buildId),
          loadBoardContext(context, buildId),
        ]);
        if (cancelled) return;
        setAttachment(att);
        setLines(ln);
        setState(st);
        setBoard(ctx);
        notFittedRef.current = new Set(ctx.not_fitted ?? []);
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
          // A part this variant does not populate has no BOM line by design.
          // Saying so is the difference between "nothing to do" and "broken".
          if (notFittedRef.current.has(ref)) {
            setEntry(ref, "not fitted", "no stock consumed");
          } else {
            setEntry(ref, "error", "no BOM line for this designator");
          }
          return;
        }

        setEntry(ref, "pending");
        try {
          const ticket = await consumeOne(context, buildId, line);
          const outcome = await awaitTask(context, ticket.taskId);

          if (outcome === "success") {
            setEntry(ref, "done");
            // Only findable now that the task has run, and only while this
            // designator is the last thing consumed -- hence the serialised
            // queue this runs inside.
            const stockItem = await findConsumedStock(context, buildId, ticket);
            await persist({
              ...stateRef.current,
              consumed: {
                ...stateRef.current.consumed,
                [ref]: {
                  build_item: ticket.buildItem,
                  at: new Date().toISOString(),
                  stock_item: stockItem,
                  line: ticket.line,
                  quantity: ticket.quantity,
                  location: ticket.location,
                  location_name: ticket.locationName,
                  part_name: ticket.partName,
                },
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

  /**
   * Put a consumed unit back, and forget it was ever consumed.
   *
   * Queued behind the consumes so an undo cannot overtake the consume it is
   * undoing, and so the stock-item diff that identifies a consume is never
   * racing an un-consume of the same part.
   */
  const undoDesignator = useCallback(
    (ref: string, record: ConsumedRecord) => {
      setUndos((prev) => prev.filter((u) => u.ref !== ref));
      queueRef.current = queueRef.current.then(async () => {
        setEntry(ref, "pending", "returning stock…");
        try {
          await unconsume(context, buildId, record);
          setEntry(ref, "returned", `back in ${record.location_name ?? "stock"}`);
          const consumed = { ...stateRef.current.consumed };
          delete consumed[ref];
          await persist({ ...stateRef.current, consumed });
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

      // Ticking Placed consumes; unticking asks first. An untick is usually a
      // mis-tap, in which case the stock should go back -- but it can also mean
      // a part was genuinely removed from the board, where it should not. The
      // panel does not get to guess between those, so it asks.
      if (checkbox.toLowerCase() === PLACED) {
        if (checkState === "checked") {
          for (const ref of refs) consumeDesignator(ref);
        } else {
          const pending = refs
            .map((ref) => ({ ref, record: current.consumed[ref] }))
            .filter((u) => u.record) as UndoPrompt[];
          if (pending.length) {
            setUndos((prev) => [
              ...pending.filter((u) => !prev.some((p) => p.ref === u.ref)),
              ...prev,
            ]);
          }
        }
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
          {board.variant ? ` · ${board.variant} variant` : ""}
          {board.not_fitted?.length ? ` · ${board.not_fitted.length} not fitted` : ""}
        </span>
        {entries.slice(0, 3).map((e) => (
          <span key={e.ref} style={{ fontSize: 12, display: "flex", gap: 5, alignItems: "center" }}>
            <code>{e.ref}</code>
            <StatusPill status={e.status} />
            {e.message && <span style={{ opacity: 0.7 }}>{e.message}</span>}
          </span>
        ))}
      </div>

      {undos.map(({ ref, record }) => (
        <div
          key={ref}
          style={{
            display: "flex",
            alignItems: "center",
            gap: 12,
            flexWrap: "wrap",
            padding: "8px 12px",
            borderRadius: 4,
            // Translucent rather than a flat fill, so it reads on the light
            // page and on the dark fullscreen ground alike.
            border: "1px solid #f59f00",
            background: "rgba(245, 159, 0, 0.12)",
          }}
        >
          <span style={{ fontSize: 13 }}>
            <code>{ref}</code>{" "}
            <strong>{record.part_name ?? "this part"}</strong> — un-consume{" "}
            {record.quantity ?? 1} and return it to{" "}
            <strong>{record.location_name ?? "stock"}</strong>?
          </span>
          <span style={{ display: "flex", gap: 6, marginLeft: "auto" }}>
            <button
              onClick={() => undoDesignator(ref, record)}
              style={{ padding: "4px 10px", cursor: "pointer", fontWeight: 600 }}
            >
              Un-consume
            </button>
            <button
              onClick={() => setUndos((prev) => prev.filter((u) => u.ref !== ref))}
              style={{ padding: "4px 10px", cursor: "pointer" }}
            >
              Just untick
            </button>
          </span>
        </div>
      ))}

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
