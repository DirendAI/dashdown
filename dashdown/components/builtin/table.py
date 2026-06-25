from __future__ import annotations

import re
from typing import Any

from dashdown.components.base import Component, RenderContext, register_component
from dashdown.components.builtin._util import (
    attr_int,
    attr_str,
    esc,
    grid_span_style,
    new_id,
    resolve_dataset,
    resolve_semantic,
    safe_json,
)
from dashdown.render.attrs import DataRef

_LINK_PLACEHOLDER = re.compile(r"{(\w+)}")


def _parse_map(spec: str | None) -> dict[str, str]:
    """Parse a `col=Value, col2=Value 2` attribute into a dict.

    Used by the `headers` (column → label override) and `format`
    (column → currency/number/percent/date/datetime) attributes.
    """
    out: dict[str, str] = {}
    if not spec:
        return out
    for part in spec.split(","):
        if "=" not in part:
            continue
        key, value = part.split("=", 1)
        key = key.strip()
        if key:
            out[key] = value.strip()
    return out


@register_component("Table")
class Table(Component):
    """Data table — renders a query result as a sortable, searchable grid.

    Usage: <Table data={q} title="Detail" sort="amount desc" />
    Optional: `headers=`/`format=` per-column label & number formatting,
    `page-size=`, `search=`, `export=` (CSV, on by default), `link_column`/
    `link_pattern` or `row_link`/`detail_slug` for drill-down links,
    `heatmap=`/`heatmap_scheme=` to shade numeric cells by value. Also accepts
    a semantic `metric={model.metric} by={model.dim}` reference.
    """

    def render(
        self, attrs, ctx: RenderContext, inner: str | None = None
    ) -> str:
        # Semantic metric: `<Table metric={sales.revenue} by={sales.region} />`
        # renders the metric-by-dimension result (one row per group). `sem` is
        # None for the normal data={query} path.
        sem = resolve_semantic(attrs, ctx)
        if sem is not None:
            name = sem["query_name"]
        else:
            # For async loading, get query name but don't resolve dataset
            data_val = attrs.get("data")
            # Handle DataRef or string
            if isinstance(data_val, DataRef):
                name = data_val.name
            else:
                name = attr_str(attrs, "data")

        title = attr_str(attrs, "title", "")
        link_column = attr_str(attrs, "link_column")
        link_pattern = attr_str(attrs, "link_pattern")
        detail_slug = attr_str(attrs, "detail_slug")
        # `row_link` makes the *whole row* clickable (a drill-down to a detail
        # page), not just one column — a URL pattern with `{column}` placeholders
        # filled per-row, e.g. row_link="/customers/{customer_id}". See table.js
        # for the click handling + the accessible per-row anchor.
        row_link = attr_str(attrs, "row_link")
        empty_message = attr_str(attrs, "empty_message", "No data available")
        try:
            limit = int(attrs.get("limit") or 100)
        except (TypeError, ValueError):
            limit = 100

        # KPI/grid polish attrs: humanized/overridable headers, per-column value
        # formatting, an initial sort, and client-side pagination.
        header_labels = _parse_map(attr_str(attrs, "headers"))
        formats = _parse_map(attr_str(attrs, "format"))
        # No default here: emit `currency` only when the author set it, so the
        # project-wide `format.currency` (or the JS "$" fallback) can apply.
        currency = attr_str(attrs, "currency")
        # A semantic metric carries a default format hint — apply it to the metric
        # column unless the author already set a format for it.
        if sem is not None:
            fmt = sem.get("format") or {}
            if fmt.get("format"):
                formats.setdefault(sem["metric"], fmt["format"])
            if fmt.get("currency") and not currency:
                currency = fmt["currency"]
            # BSL prefixes columns with the model name once a model has joins
            # (`sales.revenue`, `geo.manager`); humanize those to a clean header
            # (last segment, title-cased) unless the author set one.
            for col in (sem.get("by"), sem.get("metric")):
                if col:
                    header_labels.setdefault(
                        col, col.split(".")[-1].replace("_", " ").title()
                    )
        locale = attr_str(attrs, "locale")
        date_format = attr_str(attrs, "date_format")
        page_size = attr_int(attrs, "page-size", 10)
        if page_size is None or page_size < 0:
            page_size = 10

        # A client-side quick-filter box in the card header. On by default; the
        # JS only shows it once a table has enough rows to be worth searching
        # (so single-row detail tables stay clean). `search=false` opts out;
        # `search="placeholder"` forces it on with a custom placeholder.
        search_placeholder = "Search…"
        if "search" in attrs:
            search_val = attrs.get("search")
            is_false = search_val is False or (
                isinstance(search_val, str) and search_val.lower() == "false"
            )
            search_enabled = not is_false
            search_auto = False  # explicit → always show
            if isinstance(search_val, str) and search_val.lower() not in ("true", "false"):
                search_placeholder = search_val
        else:
            search_enabled = True
            search_auto = True  # default → show only when the table has rows to search

        # `sort="column"` or `sort="column desc"` seeds the initial ordering.
        sort_col = None
        sort_dir = "asc"
        sort_spec = attr_str(attrs, "sort")
        if sort_spec:
            parts = sort_spec.split()
            sort_col = parts[0]
            if len(parts) > 1 and parts[1].lower() in ("asc", "desc"):
                sort_dir = parts[1].lower()

        # CSV export affordance in the card header — on by default for every
        # table; `export=false` opts out. The download happens client-side from
        # the table's current filtered data (see table.js / export.js).
        export_enabled = True
        if "export" in attrs:
            ev = attrs.get("export")
            export_enabled = not (
                ev is False or (isinstance(ev, str) and ev.lower() == "false")
            )
        export_filename = attr_str(attrs, "export_filename") or attr_str(attrs, "filename")

        # Heatmap cells: shade numeric cells by value magnitude (spreadsheet-style
        # conditional formatting). `heatmap` (bare) / `heatmap="all"` shades every
        # numeric column; `heatmap="amount,profit"` shades just those. The per-column
        # min/max and the cell colors are computed client-side (see table.js) so the
        # scale stays correct as the user sorts/searches/paginates. Colors come
        # from the active theme (DaisyUI `--p`/`--su`/`--er`), so they follow the
        # project's palette. `heatmap_scheme` picks the ramp: `sequential` (low→high
        # in the primary color, the default) or `diverging` (error→success, centered
        # at zero when the column spans sign).
        heatmap_cols: bool | list[str] | None = None
        hv = attrs.get("heatmap")
        if hv is True or (isinstance(hv, str) and hv.strip().lower() in ("all", "true")):
            heatmap_cols = True
        elif isinstance(hv, str) and hv.strip():
            parts = [c.strip() for c in hv.split(",") if c.strip()]
            if parts and parts != ["false"]:
                heatmap_cols = parts
        heatmap_scheme = (attr_str(attrs, "heatmap_scheme") or "sequential").lower()
        if heatmap_scheme not in ("sequential", "diverging"):
            heatmap_scheme = "sequential"

        if not name:
            raise ValueError("Table requires `data={{query_name}}` attribute")

        # detail_slug is a shorthand: makes the named column clickable,
        # linking to {current_path}/{value}.  It supersedes link_column/link_pattern.
        if detail_slug:
            link_column = detail_slug
            base = ctx.current_path.rstrip("/")
            link_pattern = base + "/{" + detail_slug + "}"

        cid = new_id("dashdown-table")
        
        config = {
            "query_name": name,
            "title": title,
            "limit": limit,
            "link_column": link_column,
            "link_pattern": link_pattern,
            "empty_message": empty_message,
            "page_size": page_size,
        }
        if row_link:
            config["row_link"] = row_link
        if currency:
            config["currency"] = currency
        if locale:
            config["locale"] = locale
        if date_format:
            config["date_format"] = date_format
        if search_enabled:
            config["search"] = True
            config["search_placeholder"] = search_placeholder
            if search_auto:
                config["search_auto"] = True
        if header_labels:
            config["headers"] = header_labels
        if formats:
            config["formats"] = formats
        if sort_col:
            config["sort"] = sort_col
            config["sort_dir"] = sort_dir
        if export_enabled:
            config["export"] = True
            if export_filename:
                config["export_filename"] = export_filename
        if heatmap_cols:
            config["heatmap"] = heatmap_cols  # True (all numeric) or a column list
            config["heatmap_scheme"] = heatmap_scheme

        link_attrs = ""
        if link_column and link_pattern:
            link_attrs = (
                f' data-link-column="{esc(link_column)}"'
                f' data-link-pattern="{esc(link_pattern)}"'
            )
        
        config_json = esc(safe_json(config))
        span = grid_span_style(attrs)
        style_attr = f' style="{span}"' if span else ""
        return (
            f'<div class="dashdown-table card bg-base-100 border border-base-300" id="{cid}"{style_attr} '
            f'data-async-component="table" '
            f'data-config="{config_json}" '
            f'data-component-id="{cid}" '
            f'data-query-name="{esc(name)}"{link_attrs}>'
            f'<div class="card-body p-4">'
            f'{_table_skeleton(title, page_size)}'
            f'</div></div>'
        )


def _table_skeleton(title: str, page_size: int) -> str:
    """A table-shaped loading placeholder, sized to roughly the final card so the
    real table swaps in without a layout jump. The chart skeleton gets this for
    free from the card's fixed height; a table has no fixed height, so we
    approximate it with one header bar + ~page_size row bars.
    """
    # A full page is the table's tall case; clamp so a huge `page-size` doesn't
    # render an absurd placeholder and a tiny one still reads as a table.
    rows = page_size if page_size and page_size > 0 else 10
    rows = max(3, min(rows, 10))
    title_bar = '<div class="skeleton h-5 w-40 mb-3"></div>' if title else ""
    row_bars = "".join(
        '<div class="skeleton h-4 w-full mb-2.5"></div>' for _ in range(rows)
    )
    return (
        '<div class="dashdown-table-skeleton">'
        f"{title_bar}"
        '<div class="skeleton h-6 w-full mb-3"></div>'  # header row
        f"{row_bars}"
        "</div>"
    )
