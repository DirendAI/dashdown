"""Attribute-value types used in component tags."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class DataRef:
    """Reference to one or more named queries, written as `data={query_name}`.

    The name may be **dotted** (`data={finance.mrr}`) to reference a namespaced
    shared-library query — `queries/finance/mrr.sql` (see the query library). The
    dot is just part of the name; it stays a single safe URL/cache-key segment.

    A **comma list** (`data={revenue,churn}`) references several queries with
    one attribute — the multi-query ``<Ask />`` grammar. ``name`` keeps the
    normalized comma-joined text (so a single-ref consumer reading ``.name``
    is unchanged); consumers that support several iterate ``names``.
    """

    name: str

    @property
    def names(self) -> tuple[str, ...]:
        """The referenced query names — one for the common single ref."""
        return tuple(n for n in self.name.split(",") if n)


_ATTR_RE = re.compile(
    r"""
    \s+
    (?P<key>[A-Za-z_][\w-]*)         # attribute name
    (?:
        \s*=\s*
        (?:
            "(?P<dq>[^"]*)"          | # double-quoted
            '(?P<sq>[^']*)'          | # single-quoted
            \{\s*(?P<ref>[A-Za-z_][\w.]*(?:\s*,\s*[A-Za-z_][\w.]*)*)\s*\} | # data ref: {name}, {ns.name}, or a comma list {a,b}
            \{\s*(?P<num>-?\d+(?:\.\d+)?)\s*\} | # numeric literal: {0} or {-2.5}
            \{\s*(?P<list>\[[^\]]*\])\s*\} | # array literal: {[0, 10000]} or {["a", "b"]}
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
            # Normalize whitespace around list commas ({a, b} ≡ {a,b}) so the
            # stored name is canonical for cache keys and splitting.
            out[key] = DataRef(re.sub(r"\s*,\s*", ",", m.group("ref")))
        elif m.group("num") is not None:
            out[key] = _coerce(m.group("num"))
        elif m.group("list") is not None:
            out[key] = _parse_list(m.group("list"))
        elif m.group("dq") is not None:
            out[key] = m.group("dq")
        elif m.group("sq") is not None:
            out[key] = m.group("sq")
        elif m.group("bare") is not None:
            out[key] = _coerce(m.group("bare"))
        else:
            out[key] = True  # bare attribute flag
    return out


def _parse_list(s: str) -> list[Any]:
    """Parse an array-literal attribute value (`{[0, 10000]}` → `[0, 10000]`).

    `s` is the bracketed text (`[...]`). JSON is tried first (so `[0, 10000]` and
    `["a", "b"]` keep their native types); on failure (e.g. single-quoted items
    `['a', 'b']`) it falls back to a comma split with the same scalar coercion
    barewords get, so the two attribute forms stay consistent.
    """
    try:
        parsed = json.loads(s)
        if isinstance(parsed, list):
            return parsed
    except (ValueError, TypeError):
        pass
    inner = s.strip()[1:-1]  # drop the surrounding [ ]
    if not inner.strip():
        return []
    return [_coerce(part.strip().strip("'\"")) for part in inner.split(",")]


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
