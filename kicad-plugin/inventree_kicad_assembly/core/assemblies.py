"""Picking -- or creating -- the InvenTree assembly a design belongs to.

The first sync of a new design has nowhere to go: InvenTree has never heard of
the board, so there is no assembly part to pick and the sync dead-ends before
it starts. Creating one is a handful of fields, so it is offered here rather
than sending the user off to the web UI and back.

Kept free of wx so the whole flow can be exercised from a terminal.
"""

import collections
import os


def list_assemblies(client):
    """Active assembly parts, ordered the way they will be read."""
    rows = client.rows("/api/part/", {"assembly": "true", "active": "true"})
    rows.sort(key=lambda p: ((p.get("IPN") or p.get("name") or "").upper()))
    return rows


def label_for(part):
    """One line describing an assembly, IPN first since that is its identity."""
    ipn = (part.get("IPN") or "").strip()
    name = (part.get("name") or "").strip()
    category = (part.get("category_name") or "").strip()
    head = f"{ipn} — {name}" if ipn and ipn != name else (ipn or name or "(unnamed)")
    return f"{head}   [{category}]" if category else head


def list_categories(client):
    """Categories a part can actually live in, by full path.

    Structural categories exist only to hold other categories; InvenTree
    refuses to put a part in one, so offering them would only produce an
    error at the end of the form.
    """
    rows = [c for c in client.rows("/api/part/category/", {}) if not c.get("structural")]
    rows.sort(key=lambda c: ((c.get("pathstring") or c.get("name") or "").upper()))
    return rows


def category_label(category):
    return category.get("pathstring") or category.get("name") or str(category.get("pk"))


def default_category(assemblies, categories):
    """Wherever this instance already keeps its assemblies.

    Read from the data rather than hardcoded: on someone else's InvenTree
    there may be no category called "Assemblies" at all, but whatever their
    existing assemblies use is a good guess for the next one.
    """
    counts = collections.Counter(
        a.get("category") for a in assemblies if a.get("category")
    )
    by_pk = {c.get("pk"): c for c in categories}
    for pk, _count in counts.most_common():
        if pk in by_pk:
            return by_pk[pk]
    return categories[0] if categories else None


def suggested_name(pcb_path):
    """The board's filename -- the closest thing a design has to a product name."""
    return os.path.splitext(os.path.basename(pcb_path or ""))[0]


def create_assembly(client, name, description="", ipn="", category=None):
    """Create an assembly part and return it.

    Deliberately minimal: name, description, IPN and category. Everything else
    about a part is better set in InvenTree, and guessing here would produce
    records nobody asked for.
    """
    if not (name or "").strip():
        raise ValueError("An assembly needs a name.")

    payload = {
        "name": name.strip(),
        # InvenTree wants a description; the name is a truthful default.
        "description": (description or name).strip(),
        "assembly": True,
        "active": True,
    }
    if (ipn or "").strip():
        payload["IPN"] = ipn.strip()
    if category:
        payload["category"] = category

    return client._request("POST", "/api/part/", data=payload)
