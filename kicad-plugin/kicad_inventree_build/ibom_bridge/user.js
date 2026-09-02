// kicad-inventree-build: relay iBOM events to an embedding page.
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

  var PROTOCOL = "kicad-inventree-build/1";

  function post(type, payload) {
    window.parent.postMessage(
      { protocol: PROTOCOL, type: type, payload: payload },
      targetOrigin
    );
  }

  EventHandler.registerCallback(IBOM_EVENT_TYPES.ALL, function (e) {
    // args.refs is [[value, reference], ...]; the parent only needs the
    // designators. One ref in ungrouped view, N in grouped view -- the parent
    // treats both the same way, consuming one unit per newly placed designator.
    var args = e.args || {};
    post(e.eventType, {
      checkbox: args.checkbox,
      state: args.state,
      refs: (args.refs || []).map(function (r) {
        return r[1];
      }),
    });
  });

  // Inbound: the server owns checkbox state, not this browser. The parent
  // sends the build's stored state on load (and after any correction), and it
  // is applied over whatever localStorage happens to hold on this machine --
  // so picking the build up on a different PC shows it as it was left.
  //
  // Payload: {state: {"Placed": ["C1", "C3"], "Sourced": [...]}}
  window.addEventListener("message", function (event) {
    if (event.origin !== targetOrigin) {
      return;
    }
    var msg = event.data || {};
    if (msg.protocol !== PROTOCOL || msg.type !== "hydrate") {
      return;
    }

    var state = (msg.payload || {}).state || {};
    Object.keys(state).forEach(function (checkbox) {
      var refs = (state[checkbox] || []).join(",");
      settings.checkboxStoredRefs[checkbox] = refs;
      writeStorage("checkbox_" + checkbox, refs);
    });

    // Re-render so the table, the per-column stats and the board highlights
    // all reflect the state just applied.
    populateBomTable();
    Object.keys(state).forEach(updateCheckboxStats);
    setMarkWhenChecked(settings.markWhenChecked);
    drawHighlights();

    post("hydrated", { checkboxes: Object.keys(state) });
  });

  // The parent cannot trust event flow until this arrives: it fires once the
  // page has initialised, and carries what the parent needs to identify which
  // board it is showing. The parent replies with "hydrate", and must ignore
  // any checkbox events until it has -- otherwise a restored tick looks like
  // a fresh placement and consumes stock twice.
  window.addEventListener("load", function () {
    post("ready", {
      protocol: PROTOCOL,
      title: pcbdata.metadata.title,
      revision: pcbdata.metadata.revision,
      checkboxes: settings.checkboxes,
    });
  });
})();
