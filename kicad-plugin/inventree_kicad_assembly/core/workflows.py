"""What the menu actions actually do, kept free of wx so it can be run and
tested from a terminal."""

import datetime
import os
import tempfile

from . import (bom_sync, generate, ibom_xml, matching, schematic, schematic_bom,
               variants)

ATTACHMENT_SUFFIX = ".ibom.html"

#: Metadata keys. Deliberately separate from the panel's own "kicad-assembly",
#: which it rewrites wholesale every time a checkbox moves.
BOARD_METADATA_KEY = "kicad-assembly:board"
SYNC_METADATA_KEY = "kicad-assembly:sync"

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


def generate_and_upload(client, board, pcb_path, build_pk, variant="",
                       progress=None):
    """Generate this build's iBOM and attach it to the build order.

    Returns (attachment, summary_lines).
    """
    def say(msg):
        if progress:
            progress(msg)

    build = client.get_build(build_pk)
    reference = build.get("reference") or f"build-{build_pk}"

    if variant:
        # Two halves, and both are needed: the stamped copy fixes the fitted
        # set (iBOM can only add DNP, not clear it), and generate_ibom sets
        # config.kicad_variant so the variant's field overrides apply too.
        say(f"Applying the {variant} variant…")
        board = variants.apply_to_board(pcb_path, variant)

    say("Reading allocations from InvenTree…")
    fields, notes = ibom_xml.fields_for_build(client, build_pk)

    if not fields:
        raise generate.GenerationError(
            f"{reference} has no BOM lines with reference designators. "
            "Run 'InvenTree: Sync BOM' first."
        )

    # Recorded for the panel's status line and so that ticking an unfitted
    # designator reads as "not fitted" rather than a failed lookup. No longer
    # written into the IPN column: generate.DNP_FIELD keeps these components
    # out of the table altogether, so there is no row to label.
    unfitted = variants.not_fitted(board, variant)

    # The XML is a throwaway hand-off to iBOM, regenerated every run, so it
    # belongs in a temp dir rather than beside the design where it would show
    # up as an untracked file.
    workdir = tempfile.mkdtemp(prefix="inventree-kicad-assembly-")
    xml_path = os.path.join(workdir, "fields.xml")
    ibom_xml.write_xml(fields, xml_path)

    say("Rendering the interactive BOM…")
    html_path = generate.generate_ibom(
        board, pcb_path, extra_data_file=xml_path, dest_dir=workdir, name="ibom",
        variant=variant,
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

    # Tell the panel which designators this board does not fit, so that
    # ticking one reads as "not fitted" rather than as a failed lookup.
    try:
        client.set_metadata("build", build_pk, BOARD_METADATA_KEY, {
            "variant": variant,
            "not_fitted": sorted(unfitted),
            "generated_at": datetime.datetime.now().isoformat(timespec="seconds"),
        })
    except Exception:
        # Cosmetic only -- never fail a generation over it.
        pass

    summary = [
        f"{reference}: {len(fields)} designators"
        + (f", {variant} variant" if variant else "")
        + (f", {len(unfitted)} not fitted" if unfitted else ""),
        ibom_xml.format_notes(notes, fields),
    ]
    return attachment, summary


IPN_SYMBOL_FIELD = "InvenTree_IPN"


def prepare_sync(client, pcb_path, variant="", assembly_pk=None, progress=None):
    """Read the schematic and resolve every symbol. Nothing is written.

    Returns (matches, sch_path, excluded, matcher). The caller reviews these --
    in the dialog, or by printing them -- before anything is applied, so a sync
    is always seen before it happens. The matcher comes back because it holds
    every InvenTree part already, which is what lets the dialog offer an
    unrestricted pick without another round trip.

    `excluded` holds the symbols this variant does not fit, which never reach
    InvenTree, and are returned only so the review can say so out loud. They
    are matched too, despite not being sent: knowing which part a DNP symbol
    would have been is what lets an orphaned BOM line be explained as "you
    stopped fitting this" rather than reported as unexplained drift.

    `assembly_pk` lets matching fall back to that assembly's existing BOM, so
    it has to be chosen before this runs.
    """
    def say(msg):
        if progress:
            progress(msg)

    sch_path = schematic_bom.schematic_for_board(pcb_path)
    if variant and not variants.is_declared(pcb_path, variant):
        # kicad-cli would accept this silently and hand back the default
        # variant, quietly writing the base product's BOM into a variant.
        raise schematic_bom.SchematicError(
            f"This design declares no variant called {variant!r}."
        )

    say("Reading the schematic…" if not variant
        else f"Reading the schematic ({variant})…")
    rows = schematic_bom.read_bom(sch_path, variant=variant)
    if not rows:
        raise schematic_bom.SchematicError("The schematic has no components.")

    rows, excluded = schematic_bom.split_excluded(rows)
    if not rows:
        raise schematic_bom.SchematicError(
            "Every symbol in this design is DNP or excluded from the BOM."
        )

    say(f"Matching {len(rows)} symbols against InvenTree…")
    matcher = matching.Matcher(client)
    matches = matcher.match_rows(rows, progress=progress, assembly_pk=assembly_pk)

    # Cheap now that the tables are in memory, and it is what makes an orphan
    # explainable.
    for row in excluded:
        resolved = matcher.match_rows([row], suggest_unmatched=False)[0]
        row["part_pk"] = resolved.part["pk"] if resolved.matched else None

    return matches, sch_path, excluded, matcher


def apply_sync(client, assembly_pk, matches, sch_path, sheets=None,
               remove_orphans=False, write_back_ipns=True, dry_run=False,
               excluded=None, progress=None):
    """Write the BOM, then write resolved IPNs back onto the symbols.

    The write-back is what makes later syncs supplier-independent: once a
    symbol carries its IPN, matching it needs no LCSC code, no MPN and no
    manual pick.
    """
    def say(msg):
        if progress:
            progress(msg)

    say("Working out what changes…")
    changes = bom_sync.plan(client, assembly_pk, matches, excluded=excluded)

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


def remember_pairing(client, assembly_pk, pcb_path, variant):
    """Record which board and variant this assembly is synced from.

    Pairing the wrong variant with the wrong assembly is the one mistake a
    per-variant workflow can still make, and it is silent -- the sync succeeds
    and writes a variant's parts into another product. Remembering the last
    pairing lets the chooser preselect it next time.
    """
    try:
        client.set_metadata("part", assembly_pk, SYNC_METADATA_KEY, {
            "board": os.path.basename(pcb_path or ""),
            "variant": variant,
            "synced_at": datetime.datetime.now().isoformat(timespec="seconds"),
        })
    except Exception:
        pass


def pairing_for(client, assembly_pk):
    return client.get_metadata("part", assembly_pk, SYNC_METADATA_KEY)


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
