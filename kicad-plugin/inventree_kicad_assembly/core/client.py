"""InvenTree REST client.

To be ported in P1 from the working implementations in
scripts/inventree/{inventree_to_ibom_xml,kicad_bom_to_inventree}.py -- same
token auth and pagination handling, minus the per-script duplication.

One open question this module has to settle first (spike S3): whether
`requests` is importable under KiCad's bundled Python. If it is not, this
drops to urllib from the standard library rather than asking every user to
pip-install into KiCad's interpreter.

Endpoints already confirmed against a live InvenTree 1.3.2 instance:

  GET  /api/bom/?part=<pk>&sub_part_detail=true
       BOM lines; `reference` is a comma-joined designator string,
       `sub_part_detail.IPN` carries the IPN.
  GET  /api/build/<pk>/
       The build order, including its assembly part.
  GET  /api/build/line/?build=<pk>
       Per-BOM-line rows for a build: `bom_item`, `quantity`, `allocated`,
       `consumed`.
  GET  /api/build/item/?build=<pk>  (or ?build_line=<pk>)
       Individual allocations: `build_line`, `stock_item`, `quantity`.
       These pks are what /consume/ takes.
  GET  /api/stock/?part=<pk>
       Stock items: `location`, `quantity`, `in_stock`.
  GET  /api/stock/location/?limit=1000
       pk -> pathstring, fetched once and cached.
  GET  /api/company/part/?SKU=<sku>
       Supplier parts, for SKU matching.
  GET  /api/company/part/manufacturer/?MPN=<mpn>
       Manufacturer parts, for supplier-agnostic MPN matching.
  POST /api/build/attachment/
       Uploads the generated ibom.html against a build order.
"""
