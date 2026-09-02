#!/usr/bin/env python3
"""Command-line entry point.

The KiCad actions call the same `core` functions, so this exists to exercise
them without launching KiCad -- which is also how the phase test gates are
verified.

    python3 -m inventree_kicad_assembly.cli --build-order 5
    python3 -m inventree_kicad_assembly.cli --assembly-id 644 --pcb board.kicad_pcb

HOST and TOKEN come from the environment, or from a .env beside this package.
"""

import argparse
import os
import sys

from .core import ibom_xml
from .core.client import InvenTreeClient, InvenTreeError


def load_dotenv(path=None):
    """Read HOST/TOKEN from a .env without adding a dependency. Existing
    environment variables win, so an explicit export still overrides the file."""
    path = path or os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    if not os.path.isfile(path):
        return
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            if key and key not in os.environ:
                os.environ[key] = value.strip().strip("'\"")


def output_path(args):
    if args.output:
        return args.output
    if args.pcb:
        # iBOM's KiCad plugin auto-detects a same-named .xml beside the board,
        # so writing here means the extra-data-file field is already filled in.
        directory = os.path.dirname(os.path.abspath(args.pcb))
        base = os.path.splitext(os.path.basename(args.pcb))[0]
        return os.path.join(directory, f"{base}.xml")
    tag = f"build{args.build_order}" if args.build_order else f"part{args.assembly_id}"
    return f"ibom_fields_{tag}.xml"


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Generate an iBOM extra-data file (IPN + Location) from InvenTree"
    )
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--build-order", type=int, metavar="PK",
                        help="Build order to scope IPN/Location to its real allocations")
    source.add_argument("--assembly-id", type=int, metavar="PK",
                        help="Assembly part, for a build-agnostic snapshot")
    parser.add_argument("--output", help="Output XML path")
    parser.add_argument("--pcb", help="Write <pcb-basename>.xml beside this .kicad_pcb, "
                                      "where iBOM's KiCad plugin auto-detects it")
    parser.add_argument("--host", default=None, help="InvenTree URL (default: $HOST)")
    parser.add_argument("--token", default=None, help="API token (default: $TOKEN)")
    args = parser.parse_args(argv)

    load_dotenv()
    host = args.host or os.environ.get("HOST")
    token = args.token or os.environ.get("TOKEN")
    if not host or not token:
        parser.error("--host/--token required (or set HOST and TOKEN)")

    client = InvenTreeClient(host, token)

    try:
        if args.build_order:
            build = client.get_build(args.build_order)
            print(f"Build {build.get('reference')} (pk={build['pk']}) "
                  f"-- {build.get('quantity')} x part {build.get('part')}")
            fields, notes = ibom_xml.fields_for_build(client, args.build_order)
        else:
            part = client.get_part(args.assembly_id)
            print(f"Assembly {part.get('name')} (pk={part['pk']}) -- no build order, "
                  f"Location is a current-stock snapshot")
            fields, notes = ibom_xml.fields_for_assembly(client, args.assembly_id)
    except InvenTreeError as e:
        sys.exit(str(e))

    if not fields:
        sys.exit("No designators found -- nothing to write.")

    print(ibom_xml.format_notes(notes, fields))
    path = ibom_xml.write_xml(fields, output_path(args))
    print(f"\nWrote {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
