"""Turn resolved matches into an InvenTree assembly BOM.

Deletion is deliberately not automatic. A line in InvenTree that this design no
longer uses may be genuine drift worth removing, or may be something added
deliberately in InvenTree (hand-added hardware, a substitute) that no
schematic will ever mention. The plan is reported and the caller decides.
"""

from collections import OrderedDict


#: Why a BOM line exists that this design does not account for.
ORPHAN_NOT_FITTED = "not fitted in this variant"
ORPHAN_NO_SYMBOL = "no symbol in this design"


class BomChange:
    CREATE = "create"
    UPDATE = "update"
    UNCHANGED = "unchanged"
    ORPHAN = "orphan"
    #: Comes from a template above this assembly. Read-only from here.
    INHERITED = "inherited"

    def __init__(self, kind, part_pk, ipn, name, refs, quantity, existing=None,
                 reason="", source=""):
        self.kind = kind
        self.part_pk = part_pk
        self.ipn = ipn
        self.name = name
        self.refs = refs
        self.quantity = quantity
        self.existing = existing
        #: For an orphan, why the design does not account for it.
        self.reason = reason
        #: For an inherited line, the ancestor it comes from.
        self.source = source

    @property
    def reference(self):
        return ",".join(self.refs)

    def __repr__(self):
        return f"<{self.kind} {self.ipn or self.part_pk} x{self.quantity}>"


def _owner_name(client, line):
    """The part a BOM line actually belongs to -- a template, when inherited."""
    try:
        owner = client.get_part(line["part"])
    except Exception:
        return "a template"
    return owner.get("IPN") or owner.get("name") or f"part {line['part']}"


def group_matches(matches):
    """part pk -> designators, in schematic order.

    Several symbols share one InvenTree part (every 100nF cap), and InvenTree
    models that as a single BOM line with a quantity and a comma-joined
    reference string.
    """
    grouped = OrderedDict()
    for match in matches:
        if not match.matched:
            continue
        pk = match.part["pk"]
        grouped.setdefault(pk, {"part": match.part, "refs": []})
        grouped[pk]["refs"].append(match.ref)
    return grouped


def plan(client, assembly_pk, matches, excluded=None):
    """What syncing would do, without doing any of it.

    `excluded` are the design's DNP symbols -- not synced, but resolved -- so
    that a BOM line the design no longer accounts for can say whether it is
    there because you stopped fitting the part, or because it was added in
    InvenTree and no schematic will ever mention it.
    """
    grouped = group_matches(matches)

    # A variant's BOM listing includes lines inherited from its template, and
    # those carry the *template's* pk in `part`. They must not be edited from
    # here: PATCHing one would change the line on the template, and so on every
    # sibling variant at once. Split them out before anything else.
    existing, inherited = {}, {}
    for line in client.get_bom(assembly_pk):
        if line.get("part") == assembly_pk:
            existing[line["sub_part"]] = line
        else:
            inherited.setdefault(line["sub_part"], line)

    # Designators the design deliberately does not fit, by part.
    unfitted = {}
    for row in excluded or []:
        if row.get("part_pk"):
            unfitted.setdefault(row["part_pk"], []).append(row.get("ref", ""))

    changes = []
    for pk, entry in grouped.items():
        part = entry["part"]
        refs = entry["refs"]
        quantity = len(refs)
        reference = ",".join(refs)
        current = existing.get(pk)
        from_template = inherited.get(pk)

        if from_template is not None:
            # Already required via the template. Creating it here would double
            # what the build asks for, and the inherited line cannot be edited
            # from this assembly -- it is shared with every sibling variant.
            detail = from_template.get("sub_part_detail") or {}
            note = ""
            if float(from_template.get("quantity") or 0) != quantity:
                note = (f"gives x{float(from_template['quantity']):g} "
                        f"({from_template.get('reference') or '—'}), "
                        f"this design needs x{quantity}")
            changes.append(BomChange(
                BomChange.INHERITED, pk, part.get("IPN") or detail.get("IPN") or "",
                part.get("name") or detail.get("name") or "",
                refs, quantity, existing=from_template,
                source=_owner_name(client, from_template),
                reason=note,
            ))
            continue

        if current is None:
            kind = BomChange.CREATE
        elif (
            float(current.get("quantity") or 0) != quantity
            or (current.get("reference") or "") != reference
        ):
            kind = BomChange.UPDATE
        else:
            kind = BomChange.UNCHANGED

        changes.append(BomChange(
            kind, pk, part.get("IPN") or "", part.get("name") or "",
            refs, quantity, existing=current,
        ))

    # Lines InvenTree has that this design did not account for.
    for pk, line in existing.items():
        if pk in grouped:
            continue
        detail = line.get("sub_part_detail") or {}
        if pk in unfitted:
            reason = f"{ORPHAN_NOT_FITTED}: {','.join(sorted(unfitted[pk]))}"
        else:
            reason = ORPHAN_NO_SYMBOL
        changes.append(BomChange(
            BomChange.ORPHAN, pk, detail.get("IPN") or "", detail.get("name") or "",
            [r for r in (line.get("reference") or "").split(",") if r],
            float(line.get("quantity") or 0), existing=line, reason=reason,
        ))

    # Inherited lines the design does not use are reported, never removed:
    # deleting one would take it off every sibling variant too.
    for pk, line in inherited.items():
        if pk in grouped or pk in existing:
            continue
        detail = line.get("sub_part_detail") or {}
        changes.append(BomChange(
            BomChange.INHERITED, pk, detail.get("IPN") or "", detail.get("name") or "",
            [r for r in (line.get("reference") or "").split(",") if r],
            float(line.get("quantity") or 0), existing=line,
            source=_owner_name(client, line),
            reason="not in this design, and not removable from here",
        ))

    return changes


def apply(client, assembly_pk, changes, remove_orphans=False):
    """Apply a plan. Returns (applied, errors)."""
    applied, errors = [], []

    for change in changes:
        # INHERITED never appears here: it belongs to a template and editing it
        # from this assembly would change every sibling variant.
        try:
            if change.kind == BomChange.CREATE:
                client._request("POST", "/api/bom/", data={
                    "part": assembly_pk,
                    "sub_part": change.part_pk,
                    "quantity": change.quantity,
                    "reference": change.reference,
                })
                applied.append(change)
            elif change.kind == BomChange.UPDATE:
                client._request("PATCH", f"/api/bom/{change.existing['pk']}/", data={
                    "quantity": change.quantity,
                    "reference": change.reference,
                })
                applied.append(change)
            elif change.kind == BomChange.ORPHAN and remove_orphans:
                client.delete(f"/api/bom/{change.existing['pk']}/")
                applied.append(change)
        except Exception as e:
            errors.append((change, str(e)))

    return applied, errors


def summarise(changes):
    counts = {}
    for c in changes:
        counts[c.kind] = counts.get(c.kind, 0) + 1
    return counts
