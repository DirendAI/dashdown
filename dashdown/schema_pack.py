"""One-shot project context pack: the whole analytical surface in one call.

``build_schema_pack()`` gathers everything an author — or, more often, a coding
agent — needs to know before writing a query or a page, without opening the
app: every connector's tables and columns (via the connector introspection
seams), the shared SQL/DAX query library (names + the ``${param}`` placeholders
each takes), the Python queries, the semantic models (metrics / dimensions /
time dimension), and, per page, which queries it reads.

This collapses schema discovery from O(tables) round-trips of ``dashdown query
--tables`` / ``--schema <t>`` into a single ``dashdown schema`` call, and gives
LLM consumers a purpose-sized Markdown rendering (``--format md``). Pure
function over a loaded :class:`~dashdown.project.Project` — the CLI (and any
future wrapper, e.g. an MCP tool) shares it, mirroring
``catalog.py::build_catalog``. Rendering pages to collect their query names
never executes queries (SQL is collected, not run — the render-pipeline
invariant), so the only I/O is connector introspection, which ``--no-columns``
/ ``--max-tables`` bound for slow warehouses.
"""
from __future__ import annotations

import logging
from typing import Any

import yaml

from dashdown.data.base import IntrospectionUnsupported
from dashdown.render.pipeline import sql_param_names

log = logging.getLogger(__name__)

# Bound the per-connector introspection work: a warehouse can expose thousands
# of tables, and describing each is one query. The pack notes truncation.
DEFAULT_MAX_TABLES = 100


def build_schema_pack(
    proj: Any,
    *,
    include_pages: bool = True,
    include_columns: bool = True,
    max_tables: int = DEFAULT_MAX_TABLES,
) -> dict[str, Any]:
    """Build the context pack for a loaded project. See the module docstring."""
    return {
        "title": proj.config.title,
        "default_connector": proj.default_connector,
        "connectors": _connectors_section(
            proj, include_columns=include_columns, max_tables=max_tables
        ),
        "queries": _queries_section(proj),
        "python_queries": _python_queries_section(proj),
        "semantic_models": _semantic_section(proj),
        "pages": _pages_section(proj) if include_pages else None,
    }


def _connector_types(proj: Any) -> dict[str, str]:
    """Connector name -> configured ``type`` string, read from sources.yaml
    (the constructed Connector object doesn't carry its registry type name)."""
    types: dict[str, str] = {}
    sources_path = proj.root / "sources.yaml"
    if sources_path.is_file():
        try:
            raw = yaml.safe_load(sources_path.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError:
            return types
        for name, cfg in raw.items():
            if isinstance(cfg, dict):
                types[name] = str(cfg.get("type", "?"))
    return types


def _column_of(result: Any, name: str) -> int | None:
    try:
        return result.columns.index(name)
    except (ValueError, AttributeError):
        return None


def _connectors_section(
    proj: Any, *, include_columns: bool, max_tables: int
) -> dict[str, Any]:
    types = _connector_types(proj)
    out: dict[str, Any] = {}
    for name in sorted(proj.connectors):
        conn = proj.connectors[name]
        entry: dict[str, Any] = {"type": types.get(name, "?")}
        try:
            listed = conn.list_tables()
        except IntrospectionUnsupported as exc:
            entry["introspection"] = f"unsupported: {exc}"
            out[name] = entry
            continue
        except Exception as exc:  # noqa: BLE001 — a dead connector shouldn't kill the pack
            entry["error"] = f"{type(exc).__name__}: {exc}"
            out[name] = entry
            continue

        table_col = _column_of(listed, "table")
        table_names = [
            str(row[table_col]) for row in listed.rows if table_col is not None
        ]
        if len(table_names) > max_tables:
            entry["tables_truncated"] = len(table_names) - max_tables
            table_names = table_names[:max_tables]

        tables: dict[str, Any] = {}
        for table in table_names:
            if not include_columns:
                tables[table] = None
                continue
            try:
                desc = conn.describe_table(table)
            except Exception as exc:  # noqa: BLE001
                tables[table] = {"error": f"{type(exc).__name__}: {exc}"}
                continue
            col_i, type_i = _column_of(desc, "column"), _column_of(desc, "type")
            tables[table] = [
                {
                    "name": str(row[col_i]) if col_i is not None else "?",
                    "type": str(row[type_i]) if type_i is not None else "?",
                }
                for row in desc.rows
            ]
        entry["tables"] = tables
        out[name] = entry
    return out


def _queries_section(proj: Any) -> dict[str, Any]:
    return {
        name: {
            "connector": spec.connector,
            "params": sorted(sql_param_names(spec.sql)),
            **({"live": True} if spec.live else {}),
        }
        for name, spec in sorted(proj.queries.items())
    }


def _python_queries_section(proj: Any) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for name, spec in sorted(proj.python_queries.items()):
        entry: dict[str, Any] = {"connector": spec.connector}
        description = getattr(spec, "description", None)
        if description:
            entry["description"] = description
        if getattr(spec, "live", False):
            entry["live"] = True
        out[name] = entry
    return out


def _semantic_section(proj: Any) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for name in sorted(proj.semantic_models):
        handle = proj.semantic_models[name]
        time_dim = handle.time_dimension
        out[name] = {
            "connector": handle.connector,
            "backend": handle.backend,
            "metrics": sorted(handle.measure_lookup),
            "dimensions": sorted(handle.dim_lookup),
            **({"time_dimension": time_dim} if time_dim else {}),
        }
    return out


def _pages_section(proj: Any) -> dict[str, Any]:
    """Page url -> the query names its components read. Render-only (queries
    are collected, never executed); a page that fails to render is reported
    inline rather than failing the pack."""
    from dashdown.render.pipeline import render_page

    out: dict[str, Any] = {}
    pages_dir = proj.pages_dir
    if not pages_dir.is_dir():
        return out
    for md_path in sorted(pages_dir.rglob("*.md")):
        rel = md_path.relative_to(pages_dir).with_suffix("")
        url = "/" + str(rel).replace("\\", "/")
        if url.endswith("/index"):
            url = url[: -len("index")] or "/"
        try:
            rendered = render_page(
                md_path.read_text(encoding="utf-8"),
                proj.connectors,
                params={},
                current_path=url,
                include_base=proj.root,
                library=proj.queries,
                python_library=proj.python_queries,
                semantic_models=proj.semantic_models,
            )
        except Exception as exc:  # noqa: BLE001
            out[url] = {"error": f"{type(exc).__name__}: {exc}"}
            continue
        out[url] = {"queries": sorted(rendered.query_defs)}
    return out


# --------------------------------------------------------------------------- #
# Markdown rendering (LLM-sized)
# --------------------------------------------------------------------------- #
def schema_pack_markdown(pack: dict[str, Any]) -> str:
    """Render a pack as compact Markdown — the shape an LLM context wants:
    one line per table/query, columns inline, no prose."""
    lines: list[str] = [f"# {pack['title']} — project schema", ""]
    if pack.get("default_connector"):
        lines.append(f"Default connector: `{pack['default_connector']}`")
        lines.append("")

    lines.append("## Connectors")
    for name, entry in pack["connectors"].items():
        lines.append(f"### {name} ({entry.get('type', '?')})")
        if "error" in entry:
            lines.append(f"- error: {entry['error']}")
        elif "introspection" in entry:
            lines.append(f"- {entry['introspection']}")
        else:
            for table, cols in entry.get("tables", {}).items():
                if isinstance(cols, list):
                    col_text = ", ".join(f"{c['name']} {c['type']}" for c in cols)
                    lines.append(f"- **{table}**: {col_text}")
                elif isinstance(cols, dict):
                    lines.append(f"- **{table}**: (describe failed: {cols['error']})")
                else:
                    lines.append(f"- **{table}**")
            if entry.get("tables_truncated"):
                lines.append(f"- … {entry['tables_truncated']} more table(s) omitted")
        lines.append("")

    if pack["queries"]:
        lines.append("## Shared queries (queries/*.sql|dax — reference by name)")
        for name, q in pack["queries"].items():
            params = f" params: {', '.join(q['params'])}" if q["params"] else ""
            live = " [live]" if q.get("live") else ""
            lines.append(f"- `{name}` ({q['connector']}){live}{params}")
        lines.append("")

    if pack["python_queries"]:
        lines.append("## Python queries (queries/*.py)")
        for name, q in pack["python_queries"].items():
            desc = f" — {q['description']}" if q.get("description") else ""
            lines.append(f"- `{name}` ({q['connector']}){desc}")
        lines.append("")

    if pack["semantic_models"]:
        lines.append("## Semantic models (metric={model.metric} by={model.dim})")
        for name, m in pack["semantic_models"].items():
            lines.append(f"### {name} ({m['connector']}, backend: {m['backend']})")
            lines.append(f"- metrics: {', '.join(m['metrics']) or '(none)'}")
            dims = ", ".join(
                f"{d} [time]" if d == m.get("time_dimension") else d
                for d in m["dimensions"]
            )
            lines.append(f"- dimensions: {dims or '(none)'}")
        lines.append("")

    if pack.get("pages"):
        lines.append("## Pages → queries")
        for url, p in pack["pages"].items():
            if "error" in p:
                lines.append(f"- `{url}`: (render failed: {p['error']})")
            else:
                lines.append(f"- `{url}`: {', '.join(p['queries']) or '(no queries)'}")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"
