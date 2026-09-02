"""Read and write symbol fields in a .kicad_sch.

KiCad exposes no scripting API for schematics (pcbnew covers the board only),
so this edits the s-expression text directly. It is deliberately conservative:
it locates the exact byte span of a symbol's last property and splices one new
property in after it, leaving every other byte of the file untouched. Nothing
is reformatted or rewritten, so the resulting git diff is just the added
lines.

Reading the BOM is a different matter -- prefer `kicad-cli sch export bom`,
which resolves hierarchical sheets and field inheritance properly. This module
is only for the write-back.
"""

import re

# Instance symbols sit at one tab of indentation. Library definitions live
# inside (lib_symbols ...) and are indented deeper; matching on the tab depth
# is what keeps this from corrupting the library section.
_SYMBOL_RE = re.compile(r"^\t\(symbol\b", re.MULTILINE)
_PROPERTY_RE = re.compile(r'\(property\s+"([^"]*)"\s+"((?:[^"\\]|\\.)*)"', re.DOTALL)


def _match_paren(text, start):
    """End index (exclusive) of the s-expression opening at `start`."""
    depth = 0
    in_string = False
    i = start
    while i < len(text):
        c = text[i]
        if in_string:
            if c == "\\":
                i += 2
                continue
            if c == '"':
                in_string = False
        elif c == '"':
            in_string = True
        elif c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
            if depth == 0:
                return i + 1
        i += 1
    raise ValueError(f"unbalanced s-expression from offset {start}")


def iter_symbols(text):
    """Yield (start, end, properties) for each symbol instance in a sheet."""
    for m in _SYMBOL_RE.finditer(text):
        start = text.index("(", m.start())
        end = _match_paren(text, start)
        body = text[start:end]
        props = {pm.group(1): pm.group(2) for pm in _PROPERTY_RE.finditer(body)}
        yield start, end, props


def read_fields(path):
    """{reference: {field: value}} for every symbol instance in one sheet."""
    with open(path, encoding="utf-8") as f:
        text = f.read()
    out = {}
    for _start, _end, props in iter_symbols(text):
        ref = props.get("Reference")
        if ref:
            out[ref] = props
    return out


def set_field(text, reference, name, value):
    """Return `text` with symbol `reference`'s field `name` set to `value`.

    Updates the field in place when it already exists, otherwise appends a new
    hidden property after the symbol's last one. Returns the text unchanged if
    the reference is not found or already holds this value, so callers can
    detect a no-op.
    """
    for start, end, props in iter_symbols(text):
        if props.get("Reference") != reference:
            continue
        body = text[start:end]

        existing = None
        for pm in _PROPERTY_RE.finditer(body):
            if pm.group(1) == name:
                existing = pm
                break

        if existing:
            if existing.group(2) == value:
                return text  # already correct
            value_start = start + existing.start(2)
            value_end = start + existing.end(2)
            return text[:value_start] + _escape(value) + text[value_end:]

        # Append after the last existing property, reusing its indentation so
        # the file keeps KiCad's own formatting.
        last = None
        for pm in _PROPERTY_RE.finditer(body):
            last = pm
        if last is None:
            return text
        prop_start = start + last.start()
        prop_end = _match_paren(text, prop_start)

        line_start = text.rfind("\n", 0, prop_start) + 1
        indent = text[line_start:prop_start]

        block = (
            f'\n{indent}(property "{name}" "{_escape(value)}"'
            f"\n{indent}\t(at 0 0 0)"
            f"\n{indent}\t(effects"
            f"\n{indent}\t\t(font"
            f"\n{indent}\t\t\t(size 1.27 1.27)"
            f"\n{indent}\t\t)"
            f"\n{indent}\t\t(hide yes)"
            f"\n{indent}\t)"
            f"\n{indent})"
        )
        return text[:prop_end] + block + text[prop_end:]

    return text


def _escape(value):
    return value.replace("\\", "\\\\").replace('"', '\\"')


def write_fields(path, updates, dry_run=False):
    """Apply {reference: {field: value}} to a sheet.

    Returns the list of (reference, field, value) actually changed. With
    dry_run the file is left alone, so a caller can show exactly what it would
    do before touching a versioned design file.
    """
    with open(path, encoding="utf-8") as f:
        text = original = f.read()

    changed = []
    for reference, fields in updates.items():
        for name, value in fields.items():
            new_text = set_field(text, reference, name, value)
            if new_text != text:
                changed.append((reference, name, value))
                text = new_text

    if changed and not dry_run and text != original:
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
    return changed
