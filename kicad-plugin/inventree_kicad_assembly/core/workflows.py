"""What the menu actions actually do, kept free of wx so it can be run and
tested from a terminal."""

import os
import tempfile

from . import generate, ibom_xml

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
