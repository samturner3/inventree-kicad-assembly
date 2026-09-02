"""Resolving a KiCad symbol to an InvenTree part.

Strategies are tried in order and every automatic one is an *exact* match.
Nothing is matched on a heuristic without a human confirming it: a false
positive here does not merely mislabel a row, it consumes the wrong part's
stock during a build.

  1. IPN written on the symbol -- supplier-agnostic and unambiguous. No symbol
     carries this initially; it gets there via the write-back, which is what
     makes every later sync independent of any supplier.
  2. MPN -- the component's real part number rather than a distributor's, so
     also supplier-agnostic.
  3. Supplier SKU -- what the original script did, kept as a fallback and
     parameterised by supplier rather than hardcoded to LCSC.
  4. Create from LCSC -- for a part designed in but never purchased, so
     InvenTree has never heard of it. Offered only when the
     inventree-lcsc-import plugin is installed; see lcsc.py.
  5. Manual pick, in the review dialog.

Whatever resolves a match, automatic or human, the resulting IPN is written
back onto the symbol so strategy 1 handles it next time.
"""

# How a row was resolved, most to least certain.
BY_IPN = "ipn"
BY_MPN = "mpn"
BY_SKU = "sku"
BY_CREATE = "create-from-lcsc"
BY_MANUAL = "manual"
UNMATCHED = "unmatched"

STRATEGY_LABELS = {
    BY_IPN: "IPN on symbol",
    BY_MPN: "MPN",
    BY_SKU: "supplier SKU",
    BY_CREATE: "created from LCSC",
    BY_MANUAL: "chosen by hand",
    UNMATCHED: "no match",
}


class Match:
    """One schematic symbol and what it resolved to."""

    def __init__(self, row):
        self.row = row
        self.ref = row["ref"]
        self.part = None          # InvenTree part dict once resolved
        self.strategy = UNMATCHED
        self.candidates = []      # suggestions for the review dialog
        self.note = ""

    @property
    def ipn(self):
        return (self.part or {}).get("IPN") or ""

    @property
    def matched(self):
        return self.part is not None

    @property
    def needs_ipn_writeback(self):
        """True when the symbol does not already carry the IPN we resolved."""
        return self.matched and bool(self.ipn) and self.row.get("ipn") != self.ipn

    def resolve(self, part, strategy, note=""):
        self.part = part
        self.strategy = strategy
        self.note = note

    def __repr__(self):
        return f"<Match {self.ref} {self.strategy} {self.ipn or '-'}>"


class Matcher:
    """Resolves rows against InvenTree, caching lookups across a whole board.

    A board has many identical passives, so the same IPN/MPN/SKU gets looked up
    repeatedly; without caching a 79-symbol sync would make hundreds of round
    trips.
    """

    def __init__(self, client):
        self.client = client
        self._by_ipn = {}
        self._by_mpn = {}
        self._by_sku = {}

    # --- individual lookups -------------------------------------------

    def find_by_ipn(self, ipn):
        if ipn not in self._by_ipn:
            rows = self.client.rows("/api/part/", {"IPN": ipn})
            exact = [p for p in rows if (p.get("IPN") or "").upper() == ipn.upper()]
            self._by_ipn[ipn] = exact[0] if exact else None
        return self._by_ipn[ipn]

    def find_by_mpn(self, mpn):
        if mpn not in self._by_mpn:
            rows = self.client.rows("/api/company/part/manufacturer/", {"MPN": mpn})
            exact = [m for m in rows if (m.get("MPN") or "").upper() == mpn.upper()]
            self._by_mpn[mpn] = self._part(exact[0].get("part")) if exact else None
        return self._by_mpn[mpn]

    def find_by_sku(self, sku):
        if sku not in self._by_sku:
            rows = self.client.rows("/api/company/part/", {"SKU": sku})
            exact = [s for s in rows if (s.get("SKU") or "").upper() == sku.upper()]
            self._by_sku[sku] = self._part(exact[0].get("part")) if exact else None
        return self._by_sku[sku]

    def _part(self, pk):
        if pk is None:
            return None
        try:
            return self.client.get_part(pk)
        except Exception:
            return None

    # --- suggestions ---------------------------------------------------

    def suggest(self, row, limit=8):
        """Candidates for a row the automatic strategies could not place.

        Search terms only. These are shown for a human to choose from and are
        never applied automatically, which is what makes a loose search safe
        here where an automatic fuzzy match would not be.
        """
        terms = [t for t in (row.get("mpn"), row.get("value"), row.get("description")) if t]
        seen, out = set(), []
        for term in terms:
            try:
                rows = self.client.rows("/api/part/", {"search": term, "limit": limit})
            except Exception:
                continue
            for part in rows:
                if part["pk"] in seen:
                    continue
                seen.add(part["pk"])
                out.append(part)
                if len(out) >= limit:
                    return out
        return out

    # --- a pass over a whole board --------------------------------------

    def match_rows(self, rows, suggest_unmatched=True):
        matches = []
        for row in rows:
            match = Match(row)

            if row.get("ipn"):
                part = self.find_by_ipn(row["ipn"])
                if part:
                    match.resolve(part, BY_IPN)
                else:
                    # A stale IPN is worth flagging rather than silently
                    # falling through: it usually means the part was renamed
                    # or removed in InvenTree.
                    match.note = f"IPN {row['ipn']} on the symbol is not in InvenTree"

            if not match.matched and row.get("mpn"):
                part = self.find_by_mpn(row["mpn"])
                if part:
                    match.resolve(part, BY_MPN)

            if not match.matched and row.get("sku"):
                part = self.find_by_sku(row["sku"])
                if part:
                    match.resolve(part, BY_SKU)

            if not match.matched and suggest_unmatched:
                match.candidates = self.suggest(row)

            matches.append(match)
        return matches


def summarise(matches):
    """Counts by strategy, for the dialog header and the CLI."""
    counts = {}
    for m in matches:
        counts[m.strategy] = counts.get(m.strategy, 0) + 1
    return counts
