"""Resolving a KiCad symbol to an InvenTree part (P4).

Strategies are tried in order and are all *exact* matches. Nothing is matched
on a heuristic without a human confirming it: a false positive here silently
consumes the wrong part's stock during a build.

  1. IPN field on the symbol
     Supplier-agnostic and unambiguous. No symbol carries this initially --
     it gets there via the write-back below, which is what makes every
     subsequent sync independent of any supplier.

  2. MPN -> /api/company/part/manufacturer/?MPN=
     The component's real part number rather than a distributor's SKU, so
     also supplier-agnostic. Depends on the symbol actually carrying an MPN
     field, which spike S3 checks.

  3. Supplier SKU -> /api/company/part/?SKU=
     What the current kicad_bom_to_inventree.py does, kept as a fallback and
     parameterised by supplier rather than hardcoded to LCSC.

  4. Create from LCSC
     For a part that was designed in but never purchased, so InvenTree has
     never seen it. Delegates to the inventree-lcsc-import plugin's
     find-or-create endpoint (part, IPN, parameters, datasheet -- no PO, no
     stock). That plugin is an optional soft dependency: probe for the
     endpoint, and if it is absent, offer this path greyed out with an
     install hint rather than failing.

  5. Manual pick, in the review dialog.

Whatever resolves the match -- automatic or human-confirmed -- the resulting
IPN is written back onto the symbol so strategy 1 handles it next time.
Nothing is written anywhere until the review dialog is confirmed.
"""
