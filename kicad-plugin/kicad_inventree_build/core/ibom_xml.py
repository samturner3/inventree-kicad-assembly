"""Build-order-scoped extra-data file for InteractiveHtmlBom (P1).

iBOM's --extra-data-file accepts any XML of the form

    <export><components>
      <comp ref="R14">
        <field name="IPN">RES-0603-10K</field>
        <field name="Location">Gridfinity/Bin-A1</field>
      </comp>
    </components></export>

matched purely by reference designator, with no validation against the real
netlist -- so this stays completely decoupled from the KiCad source files.
Ported in P1 from scripts/inventree/inventree_to_ibom_xml.py, with two
changes:

  * Location resolves from the build order's actual allocations
    (/api/build/item/?build=<pk>) rather than "whichever stock item has the
    most of this part". The old rule was right for a generic, build-agnostic
    file; scoped to one build, the allocation is both more accurate and what
    the assembler will physically pick from.

  * BuildItem pks are emitted as an extra field per designator, so the
    InvenTree panel can map a placed designator straight to the allocation to
    consume without re-deriving it.

The panel still re-fetches allocations before each consume call -- these pks
are a starting point, not a source of truth about remaining quantity.
"""
