"""Shared query library — ``queries/**/*.{sql,dax}`` loaded as ``QuerySpec``s.

Authors define a query **once, outside any page**, in a ``queries/`` directory
and reference it by name from any page (``<Table data={sales} />``), instead of
copy-pasting a ``:::query`` block into every page that needs it. Each file is a
``---``-fenced YAML frontmatter block (``connector`` default ``main``, optional
``cache_ttl``/``live``/``interval``/``description``) over a query body — the same
frontmatter+body shape a page already has, so it reuses
:func:`dashdown.render.markdown.split_frontmatter`.

The body is **opaque text passed to the connector verbatim**, exactly like an
inline ``:::query`` block: SQL for the DuckDB/DB-API/tabular connectors, **DAX**
for ``dax`` (Fabric / Power BI). The ``.sql``/``.dax`` extension is editor
ergonomics only — ``connector`` is authoritative — and ``${param}`` substitution
is already connector-aware, so a library DAX query inherits the same injection
guarantees as an inline one.

**The path under ``queries/`` is the query name, separators mapped to dots**
(``queries/finance/mrr.sql`` → ``finance.mrr``), so a namespaced name stays a
single safe URL/cache-key segment. A flat ``queries/foo.sql`` is just ``foo``.
"""
from __future__ import annotations

from pathlib import Path

from dashdown.render.markdown import QuerySpec, split_frontmatter

# Recognised query-file extensions. The language is the connector's, not the
# file's; the extension only selects which editor tooling (SQL vs DAX) applies.
_QUERY_EXTENSIONS = (".sql", ".dax")


def _coerce_optional_int(value: object) -> int | None:
    """Coerce a frontmatter ``cache_ttl``/``interval`` value to ``int`` or ``None``.

    Mirrors the inline ``:::query`` coercion (``render/markdown.py``): a real
    int/float becomes an int; anything else (including ``bool``) becomes
    ``None``."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return int(value)
    return None


def derive_query_name(rel_path: Path) -> str:
    """Query name for a file path *relative to* ``queries/``.

    Drops the extension and joins the path parts with dots:
    ``finance/mrr.sql`` → ``finance.mrr``; ``foo.sql`` → ``foo``.
    """
    return ".".join(rel_path.with_suffix("").parts)


def parse_query_file(path: Path, name: str) -> QuerySpec:
    """Parse one ``queries/**/*.{sql,dax}`` file into a :class:`QuerySpec`.

    The frontmatter supplies ``connector`` (empty = unresolved; the project's
    default source is filled in by ``load_project``) plus the optional
    ``cache_ttl``/``live``/``interval``; the body (everything after the
    frontmatter) is the query text, opaque to its connector. ``description`` is
    accepted for the catalogue/introspection but doesn't affect execution.
    """
    source = path.read_text(encoding="utf-8")
    fm, body = split_frontmatter(source)

    connector = fm.get("connector") or ""
    description = fm.get("description")
    return QuerySpec(
        name=name,
        connector=str(connector),
        sql=body.strip(),
        cache_ttl=_coerce_optional_int(fm.get("cache_ttl")),
        live=bool(fm.get("live")),
        interval=_coerce_optional_int(fm.get("interval")),
        description=str(description) if description is not None else None,
    )


def load_queries(queries_dir: Path) -> dict[str, QuerySpec]:
    """Scan ``queries_dir`` recursively into ``{name: QuerySpec}``.

    - Files with a non-query extension are ignored; an empty/absent directory
      yields ``{}``.
    - Names are derived via :func:`derive_query_name` (path-as-dotted-name).
    - Path-traversal guard: a resolved file that escapes ``queries_dir`` (e.g. a
      symlink) raises ``ValueError`` — same posture as the ``pages/`` matcher.
    - Uniqueness is checked on the **derived name**, so ``queries/foo.sql`` and
      ``queries/foo.dax`` (both → ``foo``) collide and raise ``ValueError``
      (fail-at-startup, like a malformed ``auth:`` block).
    """
    if not queries_dir.is_dir():
        return {}

    root = queries_dir.resolve()
    out: dict[str, QuerySpec] = {}
    sources: dict[str, Path] = {}  # name -> file, for the collision message

    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in _QUERY_EXTENSIONS:
            continue
        resolved = path.resolve()
        try:
            rel = resolved.relative_to(root)
        except ValueError as exc:
            raise ValueError(
                f"query file escapes queries/ directory: {path}"
            ) from exc

        name = derive_query_name(rel)
        if name in out:
            raise ValueError(
                f"duplicate query name {name!r}: defined by both "
                f"{sources[name].name} and {path.name} under {queries_dir}"
            )
        out[name] = parse_query_file(resolved, name)
        sources[name] = path

    return out
