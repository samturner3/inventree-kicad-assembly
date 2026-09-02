"""Creating InvenTree parts from an LCSC code, when that is available.

The common design-time case: a symbol carries an LCSC code because it was
placed from an LCSC library, but the part was never ordered, so InvenTree has
never heard of it. Rather than failing, the sync can offer to create it.

That creation is not implemented here. The logic -- fetching LCSC data,
allocating an IPN from the designed scheme, attaching parameters, datasheet,
manufacturer and supplier records -- already exists server-side in the
inventree-lcsc-import plugin, and duplicating it would mean two
implementations of the IPN scheme drifting apart.

The dependency is deliberately soft. The two plugins are published separately
and neither requires the other: this probes for the endpoint once, and when it
is absent the create option is offered greyed out with an explanation instead
of vanishing or erroring.
"""

PLUGIN_SLUG = "lcscimport"
CREATE_PATH = f"/plugin/{PLUGIN_SLUG}/api/find-or-create/"

UNAVAILABLE_HINT = (
    "Install the inventree-lcsc-import plugin (and enable URL integration) to "
    "create InvenTree parts directly from an LCSC code."
)


class LcscCreator:
    """Wraps the optional endpoint, reporting cleanly when it is not there."""

    def __init__(self, client):
        self.client = client
        self._available = None
        self._reason = ""

    @property
    def reason(self):
        self.available  # probe if not already done
        return self._reason

    @property
    def available(self):
        if self._available is None:
            self._available = self._probe()
        return self._available

    def _probe(self):
        """Is the endpoint present and enabled?

        InvenTree's plugin URL router ends in a catch-all that redirects
        unknown paths to the web frontend, so a missing plugin route answers
        200 with HTML rather than 404. Anything that is not JSON therefore
        means "not available", not "available and broken".
        """
        try:
            plugins = self.client.rows("/api/plugins/", {})
        except Exception as e:
            self._reason = f"Could not query installed plugins: {e}"
            return False

        entry = next((p for p in plugins if p.get("key") == PLUGIN_SLUG), None)
        if entry is None:
            self._reason = UNAVAILABLE_HINT
            return False
        if not entry.get("active"):
            self._reason = f"The {PLUGIN_SLUG} plugin is installed but not active."
            return False

        try:
            self.client.get(CREATE_PATH, {"probe": "1"})
            self._reason = ""
            return True
        except Exception:
            self._reason = (
                f"The {PLUGIN_SLUG} plugin is active but does not expose "
                f"{CREATE_PATH}. Update it to a version that supports "
                "creating parts from a code, and enable URL integration."
            )
            return False

    def create(self, sku):
        """Find or create the InvenTree part for an LCSC code, no stock or PO."""
        if not self.available:
            raise RuntimeError(self._reason or UNAVAILABLE_HINT)
        response = self.client._request("POST", CREATE_PATH, data={"sku": sku})
        return (response or {}).get("part")
