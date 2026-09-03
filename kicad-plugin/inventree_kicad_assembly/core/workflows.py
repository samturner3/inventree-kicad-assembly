"""What the menu actions actually do, kept free of wx so it can be run and
tested from a terminal."""

import os
import tempfile

from . import bom_sync, generate, ibom_xml, matching, schematic, schematic_bom

ATTACHMENT_SUFFIX = ".ibom.html"

# InvenTree build status codes.
CANCELLED = 30
COMPLETE = 40


def build_choices(client, limit_to_part=None):
    """Build orders worth offering, newest first.

    Completed and cancelled orders are dropped -- you do not assemble against
    those -- and when the board's assembly part is known, other parts' builds
    are dropped too, since picking one would silently generate a board for the
    wrong product.
    """
    rows = client.rows("/api/build/", {"part_detail": "true"})
    out = []
    for b in rows:
        # InvenTree build status: 10 pending, 20 production, 25 on hold,
        # 30 cancelled, 40 complete. Note 20 is *production*, not complete --
        # reading it the other way hides exactly the builds worth offering.
        if b.get("status") in (CANCELLED, COMPLETE):
            continue
        if limit_to_part and b.get("part") != limit_to_part:
            continue
        detail = b.get("part_detail") or {}
        out.append({
            "pk": b["pk"],
            "reference": b.get("reference"),
            "part": b.get("part"),
            "label": f"{b.get('reference')} — {detail.get('IPN') or detail.get('name')} "
                     f"(x{b.get('quantity')})",
        })
    out.sort(key=lambda b: -b["pk"])
    return out


def generate_and_upload(client, board, pcb_path, build_pk, progress=None):
    """Generate this build's iBOM and attach it to the build order.

    Returns (attachment, summary_lines).
    """
    def say(msg):
        if progress:
            progress(msg)

    build = client.get_build(build_pk)
    reference = build.get("reference") or f"build-{build_pk}"

    say("Reading allocations from InvenTree…")
    fields, notes = ibom_xml.fields_for_build(client, build_pk)
    if not fields:
        raise generate.GenerationError(
            f"{reference} has no BOM lines with reference designators. "
            "Run 'InvenTree: Sync BOM' first."
        )

    # The XML is a throwaway hand-off to iBOM, regenerated every run, so it
    # belongs in a temp dir rather than beside the design where it would show
    # up as an untracked file.
    workdir = tempfile.mkdtemp(prefix="inventree-kicad-assembly-")
    xml_path = os.path.join(workdir, "fields.xml")
    ibom_xml.write_xml(fields, xml_path)

    say("Rendering the interactive BOM…")
    html_path = generate.generate_ibom(
        board, pcb_path, extra_data_file=xml_path, dest_dir=workdir, name="ibom"
    )

    say(f"Uploading to {reference}…")
    base = os.path.splitext(os.path.basename(pcb_path))[0]
    attachment = client.upload_attachment(
        "build", build_pk, html_path,
        filename=f"{base}{ATTACHMENT_SUFFIX}",
        comment=f"Interactive BOM for {reference}, generated from KiCad",
        # Replace rather than accumulate: regenerating during a build would
        # otherwise leave a pile of near-identical files to pick between.
        replace_suffix=ATTACHMENT_SUFFIX,
    )

    summary = [
        f"{reference}: {len(fields)} designators",
        ibom_xml.format_notes(notes, fields),
    ]
    return attachment, summary


IPN_SYMBOL_FIELD = "InvenTree_IPN"


def prepare_sync(client, pcb_path, progress=None):
    """Read the schematic and resolve every symbol. Nothing is written.

    Returns (matches, sch_path). The caller reviews these -- in the dialog, or
    by printing them -- before anything is applied, so a sync is always seen
    before it happens.
    """
    def say(msg):
        if progress:
            progress(msg)

    sch_path = schematic_bom.schematic_for_board(pcb_path)
    say("Reading the schematic…")
    rows = schematic_bom.read_bom(sch_path)
    if not rows:
        raise schematic_bom.SchematicError("The schematic has no components.")

    say(f"Matching {len(rows)} symbols against InvenTree…")
    matches = matching.Matcher(client).match_rows(rows, progress=progress)
    return matches, sch_path


def apply_sync(client, assembly_pk, matches, sch_path, sheets=None,
               remove_orphans=False, write_back_ipns=True, dry_run=False,
               progress=None):
    """Write the BOM, then write resolved IPNs back onto the symbols.

    The write-back is what makes later syncs supplier-independent: once a
    symbol carries its IPN, matching it needs no LCSC code, no MPN and no
    manual pick.
    """
    def say(msg):
        if progress:
            progress(msg)

    say("Working out what changes…")
    changes = bom_sync.plan(client, assembly_pk, matches)

    applied, errors = [], []
    if not dry_run:
        say("Updating the BOM in InvenTree…")
        applied, errors = bom_sync.apply(
            client, assembly_pk, changes, remove_orphans=remove_orphans
        )

    written = []
    if write_back_ipns:
        updates = {
            m.ref: {IPN_SYMBOL_FIELD: m.ipn}
            for m in matches if m.needs_ipn_writeback
        }
        if updates:
            say(f"Writing {len(updates)} IPNs back to the schematic…")
            for sheet in sheets or _sheets_for(sch_path):
                written.extend(schematic.write_fields(sheet, updates, dry_run=dry_run))

    return {
        "changes": changes,
        "applied": applied,
        "errors": errors,
        "written_back": written,
    }


def _sheets_for(sch_path):
    """Every sheet in a design, following Sheetfile references.

    A hierarchy can span directories -- this project keeps three of its four
    sheets in ../base-schematic -- so globbing one folder would miss symbols.
    """
    import re

    seen, queue, out = set(), [os.path.abspath(sch_path)], []
    while queue:
        path = queue.pop()
        if path in seen or not os.path.isfile(path):
            continue
        seen.add(path)
        out.append(path)
        try:
            with open(path, encoding="utf-8") as f:
                text = f.read()
        except OSError:
            continue
        for ref in re.findall(r'\(property "Sheetfile" "([^"]+)"', text):
            queue.append(os.path.normpath(os.path.join(os.path.dirname(path), ref)))
    return out
