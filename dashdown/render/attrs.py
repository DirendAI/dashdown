"""Attribute-value types used in component tags."""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class DataRef:
    """Reference to a named query, written as `data={query_name}`.

    The name may be **dotted** (`data={finance.mrr}`) to reference a namespaced
    shared-library query — `queries/finance/mrr.sql` (see the query library). The
    dot is just part of the name; it stays a single safe URL/cache-key segment.
    """

    name: str


_ATTR_RE = re.compile(
    r"""
    \s+
    (?P<key>[A-Za-z_][\w-]*)         # attribute name
    (?:
        \s*=\s*
        (?:
            "(?P<dq>[^"]*)"          | # double-quoted
            '(?P<sq>[^']*)'          | # single-quoted
            \{\s*(?P<ref>[A-Za-z_][\w.]*)\s*\} | # data ref: {name} or {ns.name}
            (?P<bare>[^\s/>]+)        # bareword
        )
    )?
    """,
    re.VERBOSE,
)


def parse_attrs(raw: str) -> dict[str, Any]:
    """Parse the attribute portion of a tag (the text after the tag name).

    Supports `key="value"`, `key='value'`, `key={ref}`, `key=bare`, and bare flags.
    """
    out: dict[str, Any] = {}
    for m in _ATTR_RE.finditer(raw):
        key = m.group("key")
        if m.group("ref") is not None:
            out[key] = DataRef(m.group("ref"))
        elif m.group("dq") is not None:
            out[key] = m.group("dq")
        elif m.group("sq") is not None:
            out[key] = m.group("sq")
        elif m.group("bare") is not None:
            out[key] = _coerce(m.group("bare"))
        else:
            out[key] = True  # bare attribute flag
    return out


def _coerce(s: str) -> Any:
    if s.lower() == "true":
        return True
    if s.lower() == "false":
        return False
    try:
        if "." in s:
            return float(s)
        return int(s)
    except ValueError:
        return s
