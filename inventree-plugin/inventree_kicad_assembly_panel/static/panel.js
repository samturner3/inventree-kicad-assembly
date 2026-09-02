// S1 spike panel: proves the plugin plumbing before any real UI is built.
//
// Deliberately plain JS against the externalized React global rather than the
// project's eventual React/Mantine/Vite setup -- the point of this spike is to
// find out whether the panel loads, receives a usable context and can call the
// API at all, and a build toolchain in the way would only add failure modes to
// rule out. P2 replaces this file with the real frontend.
//
// InvenTree loads this via `plugin_static_file("panel.js:renderPanel")` and
// renders whatever React element renderPanel returns.

const React = window.React;
const h = React.createElement;

const MONO = "ui-monospace, SFMono-Regular, Menlo, monospace";

function Row({ label, value, ok }) {
  return h(
    "div",
    { style: { display: "flex", gap: 8, padding: "2px 0" } },
    h("span", { style: { minWidth: 170, opacity: 0.7 } }, label),
    h(
      "span",
      {
        style: {
          fontFamily: MONO,
          color: ok === false ? "#e03131" : ok === true ? "#2f9e44" : undefined,
        },
      },
      String(value)
    )
  );
}

function Panel({ context }) {
  const [lines, setLines] = React.useState(null);
  const [error, setError] = React.useState(null);
  const [consume, setConsume] = React.useState(null);

  const buildId = context.id;

  // Check 1: can panel JS reach the API with the logged-in session?
  React.useEffect(() => {
    if (buildId === undefined || buildId === null) return;
    context.api
      .get(`/api/build/line/?build=${buildId}`)
      .then((r) => setLines(r.data.results ?? r.data))
      .catch((e) => setError(e.message ?? String(e)));
  }, [buildId]);

  // Check 2: the write path -- consume 1 unit of a real allocation, then poll
  // the background task it spawns. Behind a button on purpose: this moves real
  // stock, so it fires only when a human picks a line and clicks.
  async function testConsume(line) {
    setConsume({ state: "finding allocation" });
    try {
      const items = await context.api.get(`/api/build/item/?build_line=${line.pk}`);
      const rows = items.data.results ?? items.data;
      const alloc = rows.find((r) => Number(r.quantity) > 0);
      if (!alloc) {
        setConsume({ state: "no allocation with remaining quantity" });
        return;
      }

      setConsume({ state: `consuming 1 from BuildItem ${alloc.pk}` });
      const res = await context.api.post(`/api/build/${buildId}/consume/`, {
        items: [{ build_item: alloc.pk, quantity: "1" }],
        notes: "S1 spike: kicad-assembly panel round-trip test",
      });

      // /consume/ is async -- it returns a TaskDetail, so the real result only
      // shows up by polling. A fire-and-forget here would report success before
      // the stock had actually moved.
      const taskId = res.data.task_id;
      setConsume({ state: `queued, polling task ${taskId}` });

      for (let i = 0; i < 20; i++) {
        await new Promise((r) => setTimeout(r, 500));
        const t = await context.api.get(`/api/background-task/${taskId}/`);
        if (t.data.complete) {
          setConsume({
            state: t.data.success ? "consumed ok" : "task failed",
            ok: t.data.success,
          });
          return;
        }
      }
      setConsume({ state: "task still pending after 10s" });
    } catch (e) {
      setConsume({ state: `error: ${e.message ?? e}`, ok: false });
    }
  }

  return h(
    "div",
    { style: { padding: 12, fontSize: 13 } },
    h("strong", null, "S1 spike — plugin plumbing check"),
    h("div", { style: { marginTop: 10 } },
      h(Row, { label: "panel rendered", value: "yes", ok: true }),
      h(Row, { label: "context.model", value: context.model }),
      h(Row, { label: "context.id (build pk)", value: buildId }),
      h(Row, { label: "context.user", value: context.user?.username ?? "(none)" }),
      h(Row, {
        label: "api reachable",
        value: error ? error : lines ? `yes — ${lines.length} build lines` : "checking…",
        ok: error ? false : lines ? true : undefined,
      })
    ),

    lines &&
      h(
        "div",
        { style: { marginTop: 14 } },
        h("strong", null, "Consume round-trip"),
        h(
          "div",
          { style: { opacity: 0.7, margin: "4px 0 8px" } },
          "Consumes 1 unit of real allocated stock. Pick a line you don't mind moving."
        ),
        h(
          "div",
          null,
          lines
            .filter((l) => Number(l.allocated) > 0)
            .slice(0, 8)
            .map((l) =>
              h(
                "button",
                {
                  key: l.pk,
                  onClick: () => testConsume(l),
                  style: { margin: "0 6px 6px 0", padding: "4px 8px", cursor: "pointer" },
                },
                `line ${l.pk} · alloc ${l.allocated} · consumed ${l.consumed}`
              )
            )
        ),
        consume &&
          h("div", { style: { marginTop: 8 } },
            h(Row, { label: "consume", value: consume.state, ok: consume.ok })),
        lines.filter((l) => Number(l.allocated) > 0).length === 0 &&
          h("div", { style: { opacity: 0.7 } }, "No lines with allocated stock on this build.")
      )
  );
}

export function renderPanel(context) {
  return h(Panel, { context });
}
