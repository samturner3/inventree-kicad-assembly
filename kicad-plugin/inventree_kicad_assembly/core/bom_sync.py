"""Turn resolved matches into an InvenTree assembly BOM.

Deletion is deliberately not automatic. A line in InvenTree that this design no
longer uses may be genuine drift worth removing, or may be something added
deliberately in InvenTree (hand-added hardware, a substitute) that no
schematic will ever mention. The plan is reported and the caller decides.
"""

from collections import OrderedDict


class BomChange:
    CREATE = "create"
    UPDATE = "update"
    UNCHANGED = "unchanged"
    ORPHAN = "orphan"

    def __init__(self, kind, part_pk, ipn, name, refs, quantity, existing=None):
        self.kind = kind
        self.part_pk = part_pk
        self.ipn = ipn
        self.name = name
        self.refs = refs
        self.quantity = quantity
        self.existing = existing

    @property
    def reference(self):
        return ",".join(self.refs)

    def __repr__(self):
        return f"<{self.kind} {self.ipn or self.part_pk} x{self.quantity}>"


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


def plan(client, assembly_pk, matches):
    """What syncing would do, without doing any of it."""
    grouped = group_matches(matches)
    existing = {line["sub_part"]: line for line in client.get_bom(assembly_pk)}

    changes = []
    for pk, entry in grouped.items():
        part = entry["part"]
        refs = entry["refs"]
        quantity = len(refs)
        reference = ",".join(refs)
        current = existing.get(pk)

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
        changes.append(BomChange(
            BomChange.ORPHAN, pk, detail.get("IPN") or "", detail.get("name") or "",
            [r for r in (line.get("reference") or "").split(",") if r],
            float(line.get("quantity") or 0), existing=line,
        ))

    return changes


def apply(client, assembly_pk, changes, remove_orphans=False):
    """Apply a plan. Returns (applied, errors)."""
    applied, errors = [], []

    for change in changes:
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
