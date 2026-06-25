"""Query composition — ``ref('other')`` compiled to SQL CTEs.

A shared-library query can reference another library query by name with a
dbt-style ``ref('name')`` call. At project load the library is *compiled*: every
``ref(...)`` is replaced inline and its target hoisted into a leading ``WITH``
clause, so a composed query is a single self-contained SQL statement::

    -- queries/active.sql            -- queries/signups.sql
    SELECT * FROM ref('signups')     SELECT user_id, created_at FROM events
    WHERE active                     WHERE kind = 'signup'

compiles ``active`` to::

    WITH signups AS (
    SELECT user_id, created_at FROM events
    WHERE kind = 'signup'
    )
    SELECT * FROM signups
    WHERE active

Composition happens **before** ``_substitute_params`` (``render/pipeline.py``):
the compiled SQL still carries every ``${param}`` placeholder verbatim, so the
single, test-locked, context-aware substitution stays the *only* injection path —
a composed query is indistinguishable from a hand-written one downstream.

**SQL connectors only.** CTEs are SQL; DAX composes via ``DEFINE``/``VAR``
instead, so a ``ref()`` on a DAX connector (or a cross-connector reference — a
CTE can't span two connections) is a load-time error. Cycles and unknown
references are load-time errors too (fail-at-startup, like a malformed ``auth:``
block).
"""
from __future__ import annotations

import re
from dataclasses import replace

from dashdown.render.markdown import QuerySpec

# A `ref('name')` / `ref("name")` call: optional whitespace, a quoted query name
# (dotted namespaces allowed, mirroring the DataRef grammar). The whole call is
# replaced by the target's CTE alias. Matches are only honoured outside string
# literals and comments — see `_scan_refs`.
_REF_RE = re.compile(r"\bref\(\s*(['\"])([A-Za-z_][\w.]*)\1\s*\)")


def _code_mask(sql: str) -> list[bool]:
    """Per-character mask: ``True`` where ``sql[i]`` is executable code, ``False``
    inside a string literal (``'…'`` / ``"…"``), a ``--`` line comment, or a
    ``/* … */`` block comment.

    A bare ``ref(...)`` only composes when it's real SQL — one written in a
    comment (e.g. a docstring mentioning the feature) or a string literal must be
    left alone, so refs are detected against this mask.
    """
    mask = [True] * len(sql)
    i, n = 0, len(sql)
    state = None  # one of: "sq", "dq", "line", "block"
    while i < n:
        c = sql[i]
        two = sql[i : i + 2]
        if state is None:
            if two == "--":
                state, span = "line", 2
            elif two == "/*":
                state, span = "block", 2
            elif c == "'":
                state, span = "sq", 1
            elif c == '"':
                state, span = "dq", 1
            else:
                span = 1
            if state is not None:
                for j in range(i, min(i + span, n)):
                    mask[j] = False
            i += span
            continue
        # Inside a non-code region: mark and look for its terminator.
        mask[i] = False
        if state == "line":
            if c == "\n":
                mask[i] = True  # newline itself is code-side whitespace
                state = None
            i += 1
        elif state == "block":
            if two == "*/":
                mask[i] = mask[i + 1] = False
                state = None
                i += 2
            else:
                i += 1
        elif state == "sq":
            if two == "''":  # escaped quote stays inside the literal
                i += 2
            elif c == "'":
                state = None
                i += 1
            else:
                i += 1
        elif state == "dq":
            if two == '""':
                i += 2
            elif c == '"':
                state = None
                i += 1
            else:
                i += 1
    return mask


def _scan_refs(sql: str) -> list[re.Match[str]]:
    """``ref(...)`` matches that lie in executable code (not comments/strings)."""
    mask = _code_mask(sql)
    return [m for m in _REF_RE.finditer(sql) if mask[m.start()]]


def _extract_refs(sql: str) -> list[str]:
    """Ordered, de-duplicated list of query names referenced via ``ref()``."""
    seen: dict[str, None] = {}
    for m in _scan_refs(sql):
        seen.setdefault(m.group(2), None)
    return list(seen)


def _alias(name: str) -> str:
    """CTE identifier for a (possibly dotted) query name: dots → underscores.

    ``finance.mrr`` → ``finance_mrr``, ``signups`` → ``signups`` — a readable,
    valid SQL identifier that the inlined ``ref()`` site is rewritten to.
    """
    return name.replace(".", "_")


def _replace_refs(sql: str, alias_of: dict[str, str]) -> str:
    """Rewrite every code-region ``ref('x')`` in ``sql`` to its CTE alias.

    A ``ref(...)`` written inside a comment or string literal is left verbatim
    (it never reached the dependency graph), so only real references are
    rewritten."""
    out: list[str] = []
    last = 0
    for m in _scan_refs(sql):
        out.append(sql[last : m.start()])
        # Every reachable target is in alias_of (the closure was validated);
        # an absent one would be a loader bug, so fail loudly rather than emit
        # a literal `ref(...)` the database can't parse.
        out.append(alias_of[m.group(2)])
        last = m.end()
    out.append(sql[last:])
    return "".join(out)


def _compose_one(
    root_name: str,
    specs: dict[str, QuerySpec],
    dax_connectors: frozenset[str],
) -> str:
    """Compile one query's body, inlining its ``ref()`` closure as CTEs.

    Returns the original SQL unchanged when the query has no references.
    Raises ``ValueError`` on a DAX connector, an unknown reference, a
    cross-connector reference, or a cycle.
    """
    root = specs[root_name]
    if not _extract_refs(root.sql):
        return root.sql

    if root.connector in dax_connectors:
        raise ValueError(
            f"query {root_name!r} uses ref() but its connector "
            f"{root.connector!r} is a DAX connector — composition compiles to SQL "
            "CTEs (SQL connectors only). DAX composes via DEFINE/VAR, not CTEs."
        )

    order: list[str] = []  # dependency names, topologically sorted (deps first)
    visiting: set[str] = set()
    done: set[str] = set()

    def visit(name: str, path: list[str]) -> None:
        if name == root_name or name in visiting:
            chain = " -> ".join(path + [name])
            raise ValueError(f"circular query reference: {chain}")
        if name in done:
            return
        if name not in specs:
            raise ValueError(
                f"query {path[-1]!r} references unknown query {name!r} via ref()"
            )
        dep = specs[name]
        if dep.connector != root.connector:
            raise ValueError(
                f"query {root_name!r} (connector {root.connector!r}) references "
                f"{name!r} on a different connector ({dep.connector!r}); a CTE "
                "cannot span connectors. Keep composed queries on one connector."
            )
        visiting.add(name)
        for child in _extract_refs(dep.sql):
            visit(child, path + [name])
        visiting.discard(name)
        done.add(name)
        order.append(name)

    for child in _extract_refs(root.sql):
        visit(child, [root_name])

    # Map each dependency to its CTE alias, rejecting a collision (two distinct
    # names that sanitize to the same identifier, e.g. `a.b` and `a_b`).
    alias_of: dict[str, str] = {}
    for name in order:
        a = _alias(name)
        if a in alias_of.values():
            clash = next(n for n, v in alias_of.items() if v == a)
            raise ValueError(
                f"query names {name!r} and {clash!r} both map to CTE alias {a!r}; "
                "rename one to avoid the collision."
            )
        alias_of[name] = a

    ctes = [f"{alias_of[name]} AS (\n{_replace_refs(specs[name].sql, alias_of)}\n)" for name in order]
    main = _replace_refs(root.sql, alias_of)
    return "WITH " + ",\n".join(ctes) + "\n" + main


def compose_library_queries(
    specs: dict[str, QuerySpec],
    dax_connectors: frozenset[str] = frozenset(),
) -> dict[str, QuerySpec]:
    """Return ``specs`` with every ``ref()`` compiled into inline CTEs.

    Each :class:`QuerySpec` is returned with its ``sql`` replaced by the composed
    statement (all other fields untouched); a query with no ``ref()`` is returned
    unchanged. Composition is pure and order-independent — each query is compiled
    against the full library set — so the result is registered downstream exactly
    like any other library query.

    ``dax_connectors`` is the set of connector *names* backed by the DAX
    connector; a ``ref()`` whose query is on one of them raises (CTEs are SQL).
    """
    return {
        name: replace(spec, sql=_compose_one(name, specs, dax_connectors))
        for name, spec in specs.items()
    }
