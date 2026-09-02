"""Build the InteractiveHtmlBom "extra data file" from InvenTree.

iBOM's --extra-data-file accepts any XML of the form

    <export><components>
      <comp ref="R14">
        <field name="IPN">RES-0603-10K</field>
        <field name="Location">Gridfinity/Bin-A1</field>
      </comp>
    </components></export>

matched purely by reference designator, with no validation against the real
netlist -- so this stays completely decoupled from the KiCad source files, and
nothing in the design has to be edited to carry inventory data.

Two modes:

* **build order** (preferred): IPN and Location come from that build's actual
  allocations, so Location is the bin the stock was really reserved from
  rather than a guess. Also emits the BuildItem pk per designator, which is
  what `/api/build/<pk>/consume/` takes.
* **assembly**: no build order in play, so Location falls back to wherever the
  most stock currently sits. Useful as a general "where do I find this"
  reference, but it is a snapshot reserved for nobody.
"""

import xml.etree.ElementTree as ET


def _designators(reference):
    """Split a BOM line's comma-joined reference string into designators."""
    return [r.strip() for r in (reference or "").split(",") if r.strip()]


def _empty_notes():
    return {"no_reference": [], "blank_ipn": [], "unallocated": [], "duplicate": []}


def fields_for_build(client, build_pk):
    """{designator: {IPN, Location, BuildItem}} for one build order.

    Where a line's stock is split across bins, designators are handed out to
    allocations in order and in proportion to each allocation's quantity -- so
    a line needing 7 with 5 in one bin and 2 in another tells the assembler
    which five come from where, instead of naming one bin for all seven.
    """
    lines = client.get_build_lines(build_pk)
    items = client.get_build_items(build_pk)

    allocations = {}
    for item in items:
        allocations.setdefault(item["build_line"], []).append(item)

    fields = {}
    notes = _empty_notes()
    seen = set()

    for line in lines:
        detail = line.get("part_detail") or {}
        name = detail.get("name") or f"part {line.get('part')}"
        ipn = detail.get("IPN") or ""

        refs = _designators(line.get("reference"))
        if not refs:
            notes["no_reference"].append(name)
            continue
        if not ipn:
            notes["blank_ipn"].append(name)

        # Walk the allocations, spending each one's quantity across successive
        # designators. Anything left over had nothing allocated to it.
        queue = sorted(
            allocations.get(line["pk"], []), key=lambda a: -float(a["quantity"])
        )
        assigned = {}
        idx = 0
        for alloc in queue:
            location = (alloc.get("location_detail") or {}).get("pathstring", "")
            for _ in range(int(float(alloc["quantity"]))):
                if idx >= len(refs):
                    break
                assigned[refs[idx]] = {
                    "Location": location,
                    "BuildItem": str(alloc["pk"]),
                }
                idx += 1

        for ref in refs:
            if ref in seen:
                notes["duplicate"].append(ref)
            seen.add(ref)
            entry = {"IPN": ipn}
            entry.update(assigned.get(ref, {"Location": "", "BuildItem": ""}))
            fields[ref] = entry

        if idx < len(refs):
            notes["unallocated"].append(f"{name} ({len(refs) - idx} of {len(refs)})")

    return fields, notes


def fields_for_assembly(client, assembly_pk):
    """{designator: {IPN, Location}} with no build order in play.

    Location is whichever in-stock item holds the most of that part. That is a
    reasonable "where do I usually find this" answer, but it is reserved for
    nobody and goes stale as soon as stock moves -- which is why the
    build-order mode above exists and should be preferred for a real build.
    """
    bom = client.get_bom(assembly_pk)
    locations = client.get_locations()

    fields = {}
    notes = _empty_notes()
    seen = set()
    stock_cache = {}

    for line in bom:
        detail = line.get("sub_part_detail") or {}
        name = detail.get("name") or f"part {line.get('sub_part')}"
        ipn = detail.get("IPN") or ""

        refs = _designators(line.get("reference"))
        if not refs:
            notes["no_reference"].append(name)
            continue
        if not ipn:
            notes["blank_ipn"].append(name)

        sub_part = line["sub_part"]
        if sub_part not in stock_cache:
            stock_cache[sub_part] = client.get_stock_for_part(sub_part)
        candidates = [
            s for s in stock_cache[sub_part]
            if s.get("in_stock") and float(s.get("quantity") or 0) > 0
        ]
        if candidates:
            best = max(candidates, key=lambda s: float(s["quantity"]))
            location = locations.get(best.get("location"), "")
        else:
            location = ""
            notes["unallocated"].append(name)

        for ref in refs:
            if ref in seen:
                notes["duplicate"].append(ref)
            seen.add(ref)
            fields[ref] = {"IPN": ipn, "Location": location}

    return fields, notes


def build_xml(fields):
    root = ET.Element("export")
    components = ET.SubElement(root, "components")
    for ref in sorted(fields):
        comp = ET.SubElement(components, "comp", ref=ref)
        for name, value in fields[ref].items():
            if value:
                ET.SubElement(comp, "field", name=name).text = value
    tree = ET.ElementTree(root)
    ET.indent(tree, space="  ")
    return tree


def write_xml(fields, path):
    build_xml(fields).write(path, encoding="unicode", xml_declaration=True)
    return path


def format_notes(notes, fields):
    """Human-readable summary. A blank Location usually means 'nothing
    allocated yet', which is information rather than an error."""
    out = [f"{len(fields)} designators"]
    if notes["no_reference"]:
        out.append("  no reference on BOM line: " + ", ".join(notes["no_reference"]))
    if notes["blank_ipn"]:
        out.append("  blank IPN: " + ", ".join(notes["blank_ipn"]))
    if notes["unallocated"]:
        out.append("  nothing allocated (blank Location): " + ", ".join(notes["unallocated"]))
    if notes["duplicate"]:
        out.append(
            "  WARNING duplicate designator, later line wins: "
            + ", ".join(sorted(set(notes["duplicate"])))
        )
    return "\n".join(out)
