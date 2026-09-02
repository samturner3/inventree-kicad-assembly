"""Minimal InvenTree REST client.

Deliberately built on urllib rather than requests. This runs inside KiCad's
bundled Python, where requests is not guaranteed to be present, and asking
every user to pip-install into KiCad's interpreter is a poor install story for
a plugin. The API surface needed here is small enough that the stdlib is no
real hardship.
"""

import json
import os
import ssl
import urllib.error
import urllib.parse
import urllib.request
import uuid


class InvenTreeError(RuntimeError):
    """An API call failed. Carries the server's message where there is one."""


def _ssl_context():
    """An SSL context that can actually verify a public certificate here.

    KiCad's bundled macOS Python has no CA bundle wired up -- its
    "Install Certificates.command" is never run -- so a plain HTTPS call dies
    with CERTIFICATE_VERIFY_FAILED even against a perfectly valid cert.
    certifi ships alongside requests, which KiCad does bundle, so borrow its
    bundle when it is importable. Verification is never disabled: if there is
    no usable bundle the default context still applies, and the caller gets a
    real error rather than a silently unverified connection.
    """
    try:
        import certifi

        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        return ssl.create_default_context()


class InvenTreeClient:
    def __init__(self, host, token, timeout=30):
        self.base = host.rstrip("/")
        self.token = token
        self.timeout = timeout
        self._ssl = _ssl_context()

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
            with urllib.request.urlopen(req, timeout=self.timeout, context=self._ssl) as resp:
                raw = resp.read()
                return json.loads(raw) if raw else None
        except urllib.error.HTTPError as e:
            detail = e.read().decode(errors="replace")[:500]
            raise InvenTreeError(f"{method} {path} -> {e.code}: {detail}") from None
        except urllib.error.URLError as e:
            hint = ""
            if isinstance(getattr(e, "reason", None), ssl.SSLCertVerificationError):
                hint = ("\nThe Python running this has no usable CA bundle. Installing "
                        "certifi into it fixes this (KiCad's bundled Python normally "
                        "already has it, via requests).")
            raise InvenTreeError(
                f"{method} {path} -> cannot reach {self.base}: {e.reason}{hint}"
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

    # --- writes ------------------------------------------------------

    def delete(self, path):
        self._request("DELETE", path)

    def upload_attachment(self, model_type, model_id, file_path, filename=None,
                          comment="", replace_suffix=None):
        """Attach a file to a model, optionally replacing earlier ones.

        `replace_suffix` (e.g. ".html") deletes existing attachments on this
        model whose filename ends with it, before uploading. Without that,
        regenerating a board every few minutes during a build leaves a pile of
        near-identical attachments and the panel has to guess which is current.
        """
        if replace_suffix:
            for existing in self.rows("/api/attachment/", {
                "model_type": model_type, "model_id": model_id
            }):
                name = str(existing.get("attachment") or "")
                if name.lower().endswith(replace_suffix.lower()):
                    try:
                        self.delete(f"/api/attachment/{existing['pk']}/")
                    except InvenTreeError:
                        # A stale attachment that will not delete is not worth
                        # failing the whole generation over; the panel picks
                        # the newest by pk regardless.
                        pass

        filename = filename or os.path.basename(file_path)
        with open(file_path, "rb") as f:
            payload = f.read()

        boundary = "----InvenTreeKiCadAssembly" + uuid.uuid4().hex
        body = bytearray()

        def field(name, value):
            body.extend(f"--{boundary}\r\n".encode())
            body.extend(
                f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode()
            )
            body.extend(f"{value}\r\n".encode())

        field("model_type", model_type)
        field("model_id", model_id)
        field("comment", comment)

        body.extend(f"--{boundary}\r\n".encode())
        body.extend(
            f'Content-Disposition: form-data; name="attachment"; '
            f'filename="{filename}"\r\n'.encode()
        )
        body.extend(b"Content-Type: text/html\r\n\r\n")
        body.extend(payload)
        body.extend(f"\r\n--{boundary}--\r\n".encode())

        req = urllib.request.Request(
            f"{self.base}/api/attachment/",
            data=bytes(body),
            headers={
                "Authorization": f"Token {self.token}",
                "Content-Type": f"multipart/form-data; boundary={boundary}",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout, context=self._ssl) as resp:
                return json.loads(resp.read())
        except urllib.error.HTTPError as e:
            detail = e.read().decode(errors="replace")[:500]
            raise InvenTreeError(f"attachment upload -> {e.code}: {detail}") from None

    def get_locations(self):
        """pk -> pathstring for every stock location, fetched once."""
        return {
            loc["pk"]: loc.get("pathstring") or loc.get("name") or ""
            for loc in self.rows("/api/stock/location/")
        }
