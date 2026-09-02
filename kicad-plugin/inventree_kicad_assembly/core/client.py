"""Minimal InvenTree REST client.

Deliberately built on urllib rather than requests. This runs inside KiCad's
bundled Python, where requests is not guaranteed to be present, and asking
every user to pip-install into KiCad's interpreter is a poor install story for
a plugin. The API surface needed here is small enough that the stdlib is no
real hardship.
"""

import json
import urllib.error
import urllib.parse
import urllib.request


class InvenTreeError(RuntimeError):
    """An API call failed. Carries the server's message where there is one."""


class InvenTreeClient:
    def __init__(self, host, token, timeout=30):
        self.base = host.rstrip("/")
        self.token = token
        self.timeout = timeout

    # --- plumbing -----------------------------------------------------

    def _request(self, method, path, params=None, data=None):
        url = f"{self.base}{path}"
        if params:
            url += "?" + urllib.parse.urlencode(params)

        body = None
        headers = {"Authorization": f"Token {self.token}", "Accept": "application/json"}
        if data is not None:
            body = json.dumps(data).encode()
            headers["Content-Type"] = "application/json"

        req = urllib.request.Request(url, data=body, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read()
                return json.loads(raw) if raw else None
        except urllib.error.HTTPError as e:
            detail = e.read().decode(errors="replace")[:500]
            raise InvenTreeError(f"{method} {path} -> {e.code}: {detail}") from None
        except urllib.error.URLError as e:
            raise InvenTreeError(
                f"{method} {path} -> cannot reach {self.base}: {e.reason}"
            ) from None

    def get(self, path, params=None):
        return self._request("GET", path, params=params)

    def rows(self, path, params=None, page_size=500):
        """Every result across pagination. InvenTree returns either a bare list
        or a {count, results} envelope depending on endpoint and params."""
        params = dict(params or {})
        params["limit"] = page_size
        offset = 0
        out = []
        while True:
            params["offset"] = offset
            payload = self.get(path, params)
            if isinstance(payload, list):
                return payload
            batch = payload.get("results", [])
            out.extend(batch)
            offset += len(batch)
            if not batch or offset >= payload.get("count", 0):
                return out

    # --- reads --------------------------------------------------------

    def get_part(self, pk):
        return self.get(f"/api/part/{pk}/")

    def get_build(self, pk):
        return self.get(f"/api/build/{pk}/")

    def get_build_lines(self, build_pk):
        """Every BOM line of a build, allocated or not.

        `reference` holds the comma-joined designators and `part_detail.IPN`
        the IPN, so this alone covers the whole board -- including parts with
        nothing allocated, which still have to appear in the generated BOM.
        """
        return self.rows("/api/build/line/", {"build": build_pk, "part_detail": "true"})

    def get_build_items(self, build_pk):
        """Stock allocations for a build, with location and part inlined.

        One call carries everything else the generated file needs: the
        allocation pk (which is what /consume/ takes) and the location
        pathstring to pick from.
        """
        return self.rows("/api/build/item/", {
            "build": build_pk,
            "location_detail": "true",
            "part_detail": "true",
        })

    def get_bom(self, assembly_pk):
        return self.rows("/api/bom/", {"part": assembly_pk, "sub_part_detail": "true"})

    def get_stock_for_part(self, part_pk):
        return self.rows("/api/stock/", {"part": part_pk})

    def get_locations(self):
        """pk -> pathstring for every stock location, fetched once."""
        return {
            loc["pk"]: loc.get("pathstring") or loc.get("name") or ""
            for loc in self.rows("/api/stock/location/")
        }
