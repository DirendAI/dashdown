"""The authored ``<List />`` component — pin a semantic *list* on a page.

This is the author-facing twin of the runtime ask engine's "list" rung (``show me
the last 10 orders``): a **projection** of one semantic model's dimensions (no
measures), ordered by one dimension and capped at ``limit``. It compiles to the
same synthetic, dims-only :class:`~dashdown.semantic.SemanticListRef` query the ask
engine runs, and renders through the shared ``<Table>`` placeholder, so it hydrates
with zero new JS and inherits the table's search / CSV export / "filtered by" badge
for free.
"""
from __future__ import annotations

from dashdown.components.base import Component, RenderContext, register_component
from dashdown.components.builtin._util import (
    attr_bool,
    attr_int,
    attr_str,
    esc,
    grid_span_style,
    new_id,
    safe_json,
)

# Mirror the ask engine's list bounds (dashdown/ask_engine.py) so an authored list
# and an asked list share one contract.
MAX_LIST_COLUMNS = 8
MIN_LIST_LIMIT = 1
MAX_LIST_LIMIT = 500
DEFAULT_LIST_LIMIT = 50


@register_component("List")
class List(Component):
    """Detail list — pins a semantic *list* (dimensions + order + limit) as a table.

    Usage: <List model="orders" columns="name, city, order_date" order_by="order_date"
    desc limit=10 title="Latest orders" />

    A projection of one semantic model's dimensions (no measures), ordered by one
    dimension and capped at ``limit`` — the authored twin of the ask engine's "list"
    rung. ``model`` and ``columns`` (1-8 comma-separated dimension names) are
    required; ``order_by`` defaults to the model's time dimension when it's selected,
    else the first column; ``desc`` (default true) sets the direction; ``limit``
    (default 50) is clamped to [1, 500]; ``title`` labels the card. It compiles to
    the same synthetic dims-only query the ask engine runs and renders through the
    shared ``<Table>`` placeholder, so it inherits search / CSV export / the
    "filtered by" badge with no new JS. An unknown model/column raises (surfaced as
    the component's inline error card).
    """

    def render(self, attrs, ctx: RenderContext, inner: str | None = None) -> str:
        from dashdown.semantic import SemanticListRef, semantic_list_query_name

        model = attr_str(attrs, "model")
        if not model:
            raise ValueError('List requires a `model="…"` attribute')
        handle = ctx.semantic_models.get(model)
        if handle is None:
            raise ValueError(
                f"unknown semantic model {model!r} "
                f"(known: {sorted(ctx.semantic_models)})"
            )

        raw_columns = attr_str(attrs, "columns") or ""
        requested = [c.strip() for c in raw_columns.split(",") if c.strip()]
        if not requested:
            raise ValueError('List requires a `columns="a, b, …"` attribute')

        # Resolve each column to its canonical dimension name (dedupe, cap at 8).
        # An unknown name is an authoring error → the inline error card, unlike the
        # ask engine's forgiving path (which drops off-catalog names).
        columns: list[str] = []
        seen: set[str] = set()
        for c in requested:
            canon = handle.dim_lookup.get(c)
            if canon is None:
                raise ValueError(
                    f"unknown dimension {c!r} on model {model!r} "
                    f"(known: {sorted(handle.dim_lookup)})"
                )
            if canon in seen:
                continue
            seen.add(canon)
            columns.append(canon)
            if len(columns) >= MAX_LIST_COLUMNS:
                break

        # order_by: an in-scope (selected) dimension; else the model's time
        # dimension when it's among the columns; else the first column. Mirrors the
        # ask engine's `_validate_list` fallback — an order_by that isn't a selected
        # column soft-falls-back rather than erroring (BSL orders by a projected dim).
        order_raw = attr_str(attrs, "order_by")
        order_by: str | None = None
        if order_raw:
            canon = handle.dim_lookup.get(order_raw)
            if canon in seen:
                order_by = canon
        if order_by is None:
            if handle.time_dimension and handle.time_dimension in seen:
                order_by = handle.time_dimension
            else:
                order_by = columns[0]

        desc = attr_bool(attrs, "desc", True)

        limit = attr_int(attrs, "limit", DEFAULT_LIST_LIMIT)
        if limit is None:
            limit = DEFAULT_LIST_LIMIT
        limit = max(MIN_LIST_LIMIT, min(MAX_LIST_LIMIT, limit))

        title = attr_str(attrs, "title", "")

        # Build + record the SemanticListRef; render_page compiles it into a
        # synthetic PythonQuerySpec on the shared _python_def_cache seam.
        query_name = semantic_list_query_name(
            model, tuple(columns), order_by, desc, limit
        )
        ctx.semantic_list_refs[query_name] = SemanticListRef(
            model=model,
            columns=tuple(columns),
            order_by=order_by,
            desc=desc,
            limit=limit,
            query_name=query_name,
        )

        # Emit the SAME placeholder <Table> emits, keyed on the synthetic query
        # name, so table.js hydrates it (data fetch, search, CSV export) verbatim.
        # BSL prefixes columns with the model name once a model has joins
        # (`sales.order_date`) — humanize those to clean headers (last segment,
        # title-cased).
        headers = {
            col: col.split(".")[-1].replace("_", " ").title() for col in columns
        }
        cid = new_id("dashdown-table")
        config = {
            "query_name": query_name,
            "title": title,
            "limit": limit,
            "empty_message": "No data available",
            "page_size": 10,
            "headers": headers,
            "search": True,
            "search_placeholder": "Search…",
            "search_auto": True,
            # CSV export rides the shared fetchQueryData path keyed on the query
            # name, so it works for the synthetic list query like any table.
            "export": True,
            "export_filename": f"{model}-list.csv",
        }
        config_json = esc(safe_json(config))
        span = grid_span_style(attrs)
        style_attr = f' style="{span}"' if span else ""
        return (
            f'<div class="dashdown-table card bg-base-100 border border-base-300" '
            f'id="{cid}"{style_attr} '
            f'data-async-component="table" '
            f'data-config="{config_json}" '
            f'data-component-id="{cid}" '
            f'data-query-name="{esc(query_name)}">'
            f'<div class="card-body p-4">'
            f'{_list_skeleton(title)}'
            f"</div></div>"
        )


def _list_skeleton(title: str) -> str:
    """A table-shaped loading placeholder (mirrors table.py's skeleton)."""
    title_bar = '<div class="skeleton h-5 w-40 mb-3"></div>' if title else ""
    row_bars = "".join(
        '<div class="skeleton h-4 w-full mb-2.5"></div>' for _ in range(6)
    )
    return (
        '<div class="dashdown-table-skeleton">'
        f"{title_bar}"
        '<div class="skeleton h-6 w-full mb-3"></div>'
        f"{row_bars}"
        "</div>"
    )
