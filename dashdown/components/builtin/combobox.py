from __future__ import annotations

from dashdown.components.base import Component, RenderContext, register_component
from dashdown.components.builtin._util import (
    attr_bool,
    attr_int,
    attr_str,
    esc,
    filter_bar_marker,
    new_id,
    safe_json,
)
from dashdown.render.attrs import DataRef


@register_component("Combobox")
class Combobox(Component):
    """Searchable single-select filter for a **high-cardinality** column —
    type-to-filter over thousands of customers / SKUs / users where a plain
    `<Dropdown>` (which loads *every* distinct value) would choke.

    Options are fetched **server-side as you type**: the browser hits
    ``/_dashdown/api/options/{query}?_column=…&_search=…`` and the backend runs a
    ``SELECT DISTINCT … WHERE col ILIKE '%term%' LIMIT N`` against the warehouse,
    so only a small matching page is ever shipped. The search term and column go
    through the same injection-safe rules as ``${param}`` substitution
    (``render/pipeline.py::build_options_sql``) — no new injection surface.

    The picked value is stored as a **string** under ``filters[name]`` like every
    other filter, so SQL reads it with ``${name}`` and the empty (nothing picked)
    value trips the author's all-guard:

        :::query name=orders connector=main
        SELECT * FROM orders
        WHERE '${customer}' = '' OR customer = '${customer}'
        :::

    **Multi-select** (``multi``): the chosen values are stored as a single
    comma-separated string and feed an ``IN (…)`` clause — identical to a
    multi-select ``<Dropdown>``, so ``_substitute_params`` expands them into a
    quoted, per-item-escaped literal list, capped and empty-safe:

        :::query name=orders connector=main
        SELECT * FROM orders
        WHERE '${customers}' = '' OR customer IN (${customers})
        :::

    Requires ``data={query}`` + ``column`` (the query/column the distinct values
    come from). **SQL connectors only** — the options endpoint wraps the query as
    a subquery, which a non-SQL backend (DAX) / Python query can't satisfy.

    Attributes:
    - name: Required. Filter key your SQL reads as ``${name}``.
    - data: Required. ``data={query}`` — the query whose column supplies options.
    - column: Required. The column to pull distinct values from.
    - multi: Optional. Multi-select → a comma-joined value for an ``IN (…)`` clause
      (default single-select).
    - label: Optional. Inline label (defaults to name).
    - placeholder: Optional. Input placeholder (default ``"Search…"``).
    - limit: Optional. Max options per fetch (default 50; server caps at 200).
    - min_chars: Optional. Only search once this many characters are typed
      (default 0 — show the first page of values on focus).
    - url_sync: Optional. Mirror the value to the URL (default ``true``).
    - bar: Optional. Relocate into the page's top filter bar (default: inline).

    Example:
        <Combobox name="customer" data={customers} column="name" label="Customer" />
        <Combobox name="customers" data={customers} column="name" multi />
    """

    is_filter = True

    def render(
        self, attrs, ctx: RenderContext, inner: str | None = None
    ) -> str:
        name = attr_str(attrs, "name")
        if not name:
            raise ValueError("Combobox requires a `name` attribute")

        data_val = attrs.get("data")
        query_name = data_val.name if isinstance(data_val, DataRef) else (
            data_val if isinstance(data_val, str) else None
        )
        if not query_name:
            raise ValueError(
                "Combobox requires a `data={query}` attribute referencing a query"
            )

        column = attr_str(attrs, "column")
        if not column:
            raise ValueError("Combobox requires a `column` attribute")

        label = attr_str(attrs, "label", name)
        multi = attr_bool(attrs, "multi", False)
        placeholder = attr_str(attrs, "placeholder", "Search…")
        limit = attr_int(attrs, "limit", 50)
        min_chars = attr_int(attrs, "min_chars", 0)
        url_sync = attrs.get("url_sync", True)
        # Inline by default; `bar` relocates into the top filter row. See
        # filter_bar_marker.
        filter_bar_attr = filter_bar_marker(attrs, ctx)

        cid = new_id("dashdown-combobox")
        config = {
            "name": name,
            "query_name": query_name,
            "column": column,
            "label": label,
            "multi": multi,
            "placeholder": placeholder,
            "limit": limit,
            "min_chars": min_chars,
            "url_sync": url_sync,
        }

        # Multi-select keeps a row of removable chips before the search input;
        # combobox.js populates it. (Always emitted but empty in single mode.)
        multi_class = " dashdown-combobox-multi" if multi else ""
        chips_html = (
            '<div class="dashdown-combobox-chips" aria-live="polite"></div>'
            if multi
            else ""
        )

        # combobox.js owns the input + floating results panel + the debounced
        # server fetch; the markup is just the pill shell, the text input, a clear
        # (×) button and the empty panel it populates. data-query-name lets
        # filter_bar.js group it; data-filter-name is the single store key.
        return (
            f'<div class="dashdown-combobox dashdown-filter-pill" id="{cid}" '
            f'data-async-component="combobox" '
            f'data-config="{esc(safe_json(config))}" '
            f'data-query-name="{esc(query_name)}" '
            f'data-filter-name="{esc(name)}" '
            f'data-url-sync="{str(url_sync).lower()}" '
            f"{filter_bar_attr}"
            f">"
            f'<span class="dashdown-filter-pill-label">{esc(label)}'
            f'<span class="dashdown-filter-pill-colon">:</span></span>'
            f'<div class="dashdown-combobox-control{multi_class}">'
            f"{chips_html}"
            f'<input type="text" class="dashdown-combobox-input input input-sm" '
            f'role="combobox" aria-autocomplete="list" aria-expanded="false" '
            f'aria-multiselectable="{str(multi).lower()}" '
            f'autocomplete="off" spellcheck="false" '
            f'placeholder="{esc(placeholder)}" aria-label="{esc(label)}" />'
            f'<button type="button" class="dashdown-combobox-clear" '
            f'aria-label="Clear {esc(label)}" hidden>&times;</button>'
            f'<div class="dashdown-combobox-panel" role="listbox" hidden></div>'
            f"</div>"
            f"</div>"
        )
