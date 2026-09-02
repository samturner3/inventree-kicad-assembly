// inventree-kicad-assembly: relay iBOM events to an embedding page.
//
// InteractiveHtmlBom inlines this file into every generated ibom.html via its
// own ///USERJS/// extension point, after util.js and ibom.js -- so
// EventHandler and IBOM_EVENT_TYPES already exist here. iBOM's own source is
// never modified.
//
// Install: copy or symlink this file to
//   <InteractiveHtmlBom plugin dir>/web/user.js
// It then applies to every board generated on this machine. See
// kicad-plugin/README.md.
//
// The embedding page (the InvenTree Build Order panel) does all the real
// work: it holds the authenticated session and decides what a placement
// means. This file only reports what the user did.

(function () {
  "use strict";

  // Only relay when actually embedded; a standalone ibom.html behaves normally.
  if (window.parent === window) {
    return;
  }

  // Attachment and panel are served from the same InvenTree origin. Override
  // by defining IBOM_BRIDGE_TARGET_ORIGIN before this file if that changes;
  // never widen it to "*", which would leak events to any embedder.
  var targetOrigin =
    typeof IBOM_BRIDGE_TARGET_ORIGIN !== "undefined"
      ? IBOM_BRIDGE_TARGET_ORIGIN
      : window.location.origin;

  var PROTOCOL = "inventree-kicad-assembly/1";

  function post(type, payload) {
    window.parent.postMessage(
      { protocol: PROTOCOL, type: type, payload: payload },
      targetOrigin
    );
  }

  // --- checkbox state comes from the server, not this browser ---------------
  //
  // iBOM keys its localStorage on the board title and revision, so every build
  // order of the same board shares one set of checkbox keys: tick something on
  // one build and it appears ticked on another. Worse, iBOM reads that storage
  // during its window.onload init, which happens well before any hydrate
  // message could arrive, so the frame would briefly show whichever build was
  // open last.
  //
  // Both problems go away by making checkbox state never touch localStorage.
  // The panel injects this build's state as IBOM_BRIDGE_STATE before the
  // document is framed, and the checkbox_* keys are served from memory out of
  // that. Everything else -- dark mode, layout, which columns are shown -- is a
  // genuine per-viewer preference and still goes to localStorage as usual.
  //
  // This runs during parse, before window.onload, so iBOM's own init reads the
  // seeded values and renders the right state on the first frame.
  var memory = {};
  var seeded = typeof IBOM_BRIDGE_STATE !== "undefined" && IBOM_BRIDGE_STATE;

  function isCheckboxKey(key) {
    return key.indexOf("checkbox_") === 0;
  }

  function seed(state) {
    memory = {};
    var checkboxes = (state && state.checkboxes) || {};
    Object.keys(checkboxes).forEach(function (name) {
      memory["checkbox_" + name] = (checkboxes[name] || []).join(",");
    });
  }

  if (seeded) {
    seed(IBOM_BRIDGE_STATE);
    // Drop any checkbox keys an older build (or an older version of this
    // bridge) left behind. They are no longer read, and leaving them around
    // invites someone debugging this to trust a stale value.
    try {
      Object.keys(window.localStorage)
        .filter(function (k) {
          return k.indexOf("KiCad_HTML_BOM__") === 0 && k.indexOf("checkbox_") !== -1;
        })
        .forEach(function (k) {
          window.localStorage.removeItem(k);
        });
    } catch (e) {
      // Storage can be unavailable (private mode, blocked cookies); the
      // in-memory state is what matters, so this is not worth failing over.
    }
  }

  var realRead = window.readStorage;
  var realWrite = window.writeStorage;

  window.readStorage = function (key) {
    if (isCheckboxKey(key)) {
      return Object.prototype.hasOwnProperty.call(memory, key) ? memory[key] : null;
    }
    return realRead(key);
  };

  window.writeStorage = function (key, value) {
    if (isCheckboxKey(key)) {
      // Held in memory only. The panel is told separately, by the checkbox
      // event below, and it is what persists the change.
      memory[key] = value;
      return;
    }
    realWrite(key, value);
  };

  EventHandler.registerCallback(IBOM_EVENT_TYPES.ALL, function (e) {
    // args.refs is [[designator, footprintIndex], ...]; send designators
    // (r[0]). The index is meaningless outside this one generated file,
    // whereas the designator is what maps to an InvenTree BOM line. Only the
    // designators. One ref in ungrouped view, N in grouped view -- the parent
    // treats both the same way, consuming one unit per newly placed designator.
    var args = e.args || {};
    post(e.eventType, {
      checkbox: args.checkbox,
      state: args.state,
      refs: (args.refs || []).map(function (r) {
        return r[0];
      }),
    });
  });

  // Inbound: apply state pushed by the panel after load. With
  // IBOM_BRIDGE_STATE injected this is no longer how state normally arrives --
  // it is already correct on the first frame -- but it stays as the way to
  // apply a correction, or to seed a frame that was embedded without injection.
  //
  // Payload: {state: {"Placed": ["C1", "C3"], "Sourced": [...]}}
  //
  // Designator strings go straight into checkboxStoredRefs even though iBOM
  // stores footprint indices internally: its own getStoredCheckboxRefs runs
  // each entry through a convert() that falls back to a designator lookup when
  // the value is not numeric. So no index mapping is needed, and the stored
  // state stays readable.
  window.addEventListener("message", function (event) {
    if (event.origin !== targetOrigin) {
      return;
    }
    var msg = event.data || {};
    if (msg.protocol !== PROTOCOL || msg.type !== "hydrate") {
      return;
    }

    var state = (msg.payload || {}).state || {};
    seed({ checkboxes: state });
    Object.keys(state).forEach(function (checkbox) {
      settings.checkboxStoredRefs[checkbox] = (state[checkbox] || []).join(",");
    });

    // Re-render so the table, the per-column stats and the board highlights
    // all reflect the state just applied.
    populateBomTable();
    Object.keys(state).forEach(updateCheckboxStats);
    setMarkWhenChecked(settings.markWhenChecked);
    drawHighlights();

    post("hydrated", { checkboxes: Object.keys(state) });
  });

  // Fired once the page has initialised. `seeded` tells the panel whether this
  // frame already had its state injected: if so it is safe to act on checkbox
  // events immediately, and if not the panel must hydrate first and ignore
  // events until it has -- otherwise a restored tick looks like a fresh
  // placement and consumes stock twice.
  window.addEventListener("load", function () {
    post("ready", {
      protocol: PROTOCOL,
      title: pcbdata.metadata.title,
      revision: pcbdata.metadata.revision,
      checkboxes: settings.checkboxes,
      seeded: !!seeded,
    });
  });
})();
