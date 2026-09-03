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
  4. The designator's existing line in this assembly's BOM -- somebody already
     decided, by hand, that R10 is this part. Exact and supplier-agnostic, and
     it liquidates itself: the write-back stamps the IPN onto the symbol, so
     the next sync resolves it at step 1 and never looks here again.
  5. Create from LCSC -- for a part designed in but never purchased, so
     InvenTree has never heard of it. Offered only when the
     inventree-lcsc-import plugin is installed; see lcsc.py.
  6. Manual pick, in the review dialog.

Whatever resolves a match, automatic or human, the resulting IPN is written
back onto the symbol so strategy 1 handles it next time.
"""

# How a row was resolved, most to least certain.
BY_IPN = "ipn"
BY_MPN = "mpn"
BY_SKU = "sku"
BY_BOM = "bom"
BY_CREATE = "create-from-lcsc"
BY_MANUAL = "manual"
UNMATCHED = "unmatched"

STRATEGY_LABELS = {
    BY_IPN: "IPN on symbol",
    BY_MPN: "MPN",
    BY_SKU: "supplier SKU",
    BY_BOM: "this assembly's BOM",
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
    """Resolves rows against InvenTree.

    Every table a strategy consults -- parts, manufacturer parts, supplier
    parts -- is fetched once and indexed in memory rather than queried per
    symbol. That is what makes a sync quick enough to watch: done the other
    way, a 71-symbol board issued a query per distinct IPN, MPN and SKU, then
    up to three search queries for every row that failed to match, which is
    two hundred-odd round trips to a remote server before the review dialog
    could open. The whole tables cost four, and they are small -- hundreds of
    rows, not thousands.

    A side effect worth having: with every part in hand, an ambiguous key can
    be recognised as ambiguous. A per-key query only ever saw its first result.
    """

    def __init__(self, client):
        self.client = client
        self.parts = {}       # pk -> part
        self._by_ipn = {}     # KEY -> [part, ...]
        self._by_mpn = {}
        self._by_sku = {}
        self._by_designator = {}   # only when an assembly is named
        self._loaded = False

    # --- the tables ----------------------------------------------------

    def load(self, progress=None):
        """Fetch and index everything matching needs. Idempotent."""
        if self._loaded:
            return

        def say(msg):
            if progress:
                progress(msg)

        say("Loading InvenTree parts…")
        # Parameters come along for the ride because the package -- '0402',
        # 'SOT-23' -- is the one field that lets a human tell eight similar
        # candidates apart, and it is only reachable per-part otherwise.
        parts = self.client.rows("/api/part/", {"parameters": "true"})
        self.parts = {p["pk"]: p for p in parts}
        for part in parts:
            _index(self._by_ipn, part.get("IPN"), part)

        say(f"Indexed {len(parts)} parts. Loading manufacturer numbers…")
        for row in self.client.rows("/api/company/part/manufacturer/", {}):
            part = self.parts.get(row.get("part"))
            if part:
                _index(self._by_mpn, row.get("MPN"), part)

        say("Indexing supplier part numbers…")
        for row in self.client.rows("/api/company/part/", {}):
            part = self.parts.get(row.get("part"))
            if part:
                _index(self._by_sku, row.get("SKU"), part)

        self._loaded = True

    def load_assembly_bom(self, assembly_pk):
        """Index the target assembly's current BOM by reference designator.

        Read as a record of decisions a person already made: a BOM line saying
        `RES-0603-100R` covers `R10,R11,R12,R13` is somebody having identified
        those four symbols by hand. Consulting it costs one request and rescues
        exactly the symbols that carry no supplier data of their own.
        """
        self._by_designator = {}
        if not assembly_pk:
            return
        for line in self.client.get_bom(assembly_pk):
            part = self.parts.get(line.get("sub_part")) or line.get("sub_part_detail")
            if not part:
                continue
            for ref in (line.get("reference") or "").split(","):
                ref = ref.strip()
                if ref:
                    self._by_designator.setdefault(ref, part)

    # --- individual lookups -------------------------------------------

    def _lookup(self, index, value):
        """The part for a key, or (None, others) when the key is not unique.

        Two parts sharing an MPN is a data problem in InvenTree, but guessing
        between them here would consume the wrong part's stock during a build.
        The ambiguity is handed to the review dialog as candidates instead.
        """
        self.load()
        found = index.get((value or "").strip().upper(), [])
        if len(found) == 1:
            return found[0], []
        return None, found

    def find_by_ipn(self, ipn):
        return self._lookup(self._by_ipn, ipn)[0]

    def find_by_mpn(self, mpn):
        return self._lookup(self._by_mpn, mpn)[0]

    def find_by_sku(self, sku):
        return self._lookup(self._by_sku, sku)[0]

    # --- suggestions ---------------------------------------------------

    def suggest(self, row, limit=8):
        """Candidates for a row the automatic strategies could not place.

        Ranked by how many of the row's own terms appear in the part's text.
        These are shown for a human to choose from and are never applied
        automatically, which is what makes a loose match safe here where an
        automatic one would not be.
        """
        self.load()
        terms = [
            str(t).strip().upper()
            for t in (row.get("mpn"), row.get("value"), row.get("description"))
            if t and str(t).strip()
        ]
        if not terms:
            return []

        # A candidate in the same package as the symbol is far more likely to
        # be the right one, so it sorts above an equally-worded part in the
        # wrong size. A hint for ranking only -- never enough to match on.
        footprint = str(row.get("footprint") or "").upper()

        scored = []
        for part in self.parts.values():
            haystack = " ".join(
                str(part.get(f) or "")
                for f in ("name", "description", "keywords", "IPN")
            ).upper()
            score = sum(1 for term in terms if term in haystack)
            if not score:
                continue
            package = package_of(part).upper()
            if package and footprint and package in footprint:
                score += 2
            scored.append((score, str(part.get("name") or ""), part))

        scored.sort(key=lambda s: (-s[0], s[1]))
        return [part for _score, _name, part in scored[:limit]]

    # --- a pass over a whole board --------------------------------------

    def match_rows(self, rows, suggest_unmatched=True, progress=None,
                   assembly_pk=None):
        self.load(progress)
        if assembly_pk:
            self.load_assembly_bom(assembly_pk)

        matches = []
        for n, row in enumerate(rows, 1):
            if progress and (n == 1 or n % 10 == 0):
                progress(f"Matching symbol {n} of {len(rows)}…")
            match = Match(row)

            if row.get("ipn"):
                part, ambiguous = self._lookup(self._by_ipn, row["ipn"])
                if part:
                    match.resolve(part, BY_IPN)
                elif ambiguous:
                    match.note = f"IPN {row['ipn']} is on {len(ambiguous)} parts"
                    match.candidates = ambiguous
                else:
                    # A stale IPN is worth flagging rather than silently
                    # falling through: it usually means the part was renamed
                    # or removed in InvenTree.
                    match.note = f"IPN {row['ipn']} on the symbol is not in InvenTree"

            for field, index, strategy in (
                ("mpn", self._by_mpn, BY_MPN),
                ("sku", self._by_sku, BY_SKU),
            ):
                if match.matched or not row.get(field):
                    continue
                part, ambiguous = self._lookup(index, row[field])
                if part:
                    match.resolve(part, strategy)
                elif ambiguous:
                    match.note = (
                        f"{field.upper()} {row[field]} matches {len(ambiguous)} "
                        "parts — pick one"
                    )
                    match.candidates = ambiguous

            if not match.matched:
                # Last of the automatic strategies: whatever this assembly's
                # BOM already says this designator is.
                part = self._by_designator.get(match.ref)
                if part:
                    match.resolve(
                        part, BY_BOM,
                        note=f"{match.ref} is already this part on the BOM",
                    )

            if not match.matched and not match.candidates and suggest_unmatched:
                match.candidates = self.suggest(row)

            matches.append(match)
        return matches


#: Parameter names that describe a component's physical package.
_PACKAGE_PARAMETERS = ("package", "footprint", "case", "mounting type")


def package_of(part):
    """The part's package parameter, or '' -- e.g. '0402', 'SOT-23-3'."""
    for parameter in part.get("parameters") or []:
        name = ((parameter.get("template_detail") or {}).get("name") or "").lower()
        if name in _PACKAGE_PARAMETERS:
            return str(parameter.get("data") or "").strip()
    return ""


def _index(index, key, part):
    """Add a part under a normalised key, keeping every part that shares it."""
    key = (key or "").strip().upper()
    if not key:
        return
    index.setdefault(key, []).append(part)


def summarise(matches):
    """Counts by strategy, for the dialog header and the CLI."""
    counts = {}
    for m in matches:
        counts[m.strategy] = counts.get(m.strategy, 0) + 1
    return counts
