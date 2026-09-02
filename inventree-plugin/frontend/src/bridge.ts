/**
 * The postMessage protocol spoken with the embedded iBOM.
 *
 * The other end is kicad-plugin/inventree_kicad_assembly/ibom_bridge/user.js,
 * inlined into every generated ibom.html through InteractiveHtmlBom's own
 * ///USERJS/// extension point. Keep the protocol string in step across both.
 */

export const PROTOCOL = "inventree-kicad-assembly/1";

export interface ReadyPayload {
  title?: string;
  revision?: string;
  checkboxes?: string[];
}

export interface CheckboxPayload {
  checkbox: string;
  state: "checked" | "unchecked";
  /** Designators, e.g. ["C2","C12"]. A grouped row reports all of them at once. */
  refs: string[];
}

export interface BridgeMessage {
  protocol: string;
  type: string;
  payload: any;
}

export function isBridgeMessage(data: any): data is BridgeMessage {
  return !!data && data.protocol === PROTOCOL && typeof data.type === "string";
}

/** Push server-held checkbox state into the iframe, overwriting its localStorage. */
export function sendHydrate(
  frame: HTMLIFrameElement | null,
  state: Record<string, string[]>,
  targetOrigin: string
) {
  frame?.contentWindow?.postMessage(
    { protocol: PROTOCOL, type: "hydrate", payload: { state } },
    targetOrigin
  );
}
