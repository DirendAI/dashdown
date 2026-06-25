from __future__ import annotations

from dashdown.components.base import Component, RenderContext, register_component
from dashdown.components.builtin._util import (
    attr_bool,
    attr_str,
    esc,
    filter_bar_marker,
    new_id,
    safe_json,
)
from dashdown.render.attrs import DataRef


@register_component("Dropdown")
class Dropdown(Component):
    """Dropdown filter. Populates options from the distinct values of a column.

    Selected value is stored in the page-level Alpine store under `filters[name]`.
    Charts/tables subscribe and re-filter client-side.
    
    For async loading, if data={query} is specified, the options are fetched
    client-side. If options="a,b,c" is specified, uses explicit options.

    Multi-select: `multi` turns the control into a dropdown button that opens a
    checkmark popover (`static/components/dropdown.js`); the picked values are
    stored as a comma-separated string in `filters[name]` and intended for an
    `IN (...)` clause, e.g.
    `WHERE '${region}' = '' OR region IN (${region})` — the empty (nothing
    selected) case is the author's "all" guard, mirroring the single-select
    `= ${region}` convention. There is no "All" option in multi mode (an empty
    selection already means all, shown as the button's "All" placeholder);
    `_substitute_params` expands the value into a quoted, per-item-escaped
    literal list. The JS owns option rendering/selection for both async
    (`data={query}` + `column`) and explicit (`options="a,b,c"`) modes, so the
    options ride in the config rather than as `<option>` tags.

    URL Sync: By default (url_sync=True), dropdown selections sync to URL query
    parameters. Set url_sync=False to disable this behavior.

    Placement: renders **inline where authored** by default (e.g. directly above
    the chart it filters). Add `bar` (`<Dropdown … bar />`) to relocate it into
    the page's top filter bar instead.
    """

    is_filter = True

    def render(
        self, attrs, ctx: RenderContext, inner: str | None = None
    ) -> str:
        name = attr_str(attrs, "name")
        if not name:
            raise ValueError("Dropdown requires a `name` attribute")
        column = attr_str(attrs, "column")
        label = attr_str(attrs, "label", name)
        multi = attr_bool(attrs, "multi", False)
        # A multi-select has no "All" entry — an empty selection already means
        # "all" (handled by the query's IN-guard), and an "All" row in a
        # multiple listbox is ambiguous alongside real picks.
        include_all = not multi

        # URL sync enabled by default (P0 requirement)
        url_sync = attrs.get("url_sync", True)

        # Inline by default (renders where authored); `bar` relocates it into the
        # top filter row (read by filter_bar.js). See filter_bar_marker.
        filter_bar_attr = filter_bar_marker(attrs, ctx)

        # Check if we have explicit options or data reference
        data_val = attrs.get("data")
        explicit_options = attrs.get("options")
        
        # Handle DataRef or string for data
        query_name = None
        if isinstance(data_val, DataRef):
            query_name = data_val.name
        elif isinstance(data_val, str):
            query_name = data_val
        
        # Options can come from data={query} + column="..." OR explicit options="a,b,c".
        options: list[str] = []
        if query_name and column:
            # Async mode: don't resolve data, just store metadata
            # Options will be populated client-side
            pass
        elif explicit_options:
            # Explicit options can be used without query
            options = [s.strip() for s in str(explicit_options).split(",") if s.strip()]
        
        cid = new_id("dashdown-dropdown")

        if multi:
            # Multi-select is a dropdown button + checkmark popover (image-2
            # design). The button shows the picked values joined by ", " (or the
            # "All" placeholder when none); clicking opens a panel where each
            # option toggles with a checkmark. dropdown.js owns the panel for
            # both async (query+column → options fetched client-side) and
            # explicit (`options=` → carried in the config) modes, so the store
            # value stays a comma-separated string for URL sync / chips / IN().
            config = {
                "name": name,
                "label": label,
                "include_all": include_all,  # always False in multi
                "url_sync": url_sync,
                "multi": True,
            }
            data_query_attr = ""
            if query_name and column:
                config["query_name"] = query_name
                config["column"] = column
                data_query_attr = f'data-query-name="{esc(query_name)}" '
            else:
                # Explicit options ride in the config (no query to load from).
                config["options"] = options
                if column:
                    config["column"] = column

            chevron = (
                '<svg class="dashdown-multiselect-chevron" viewBox="0 0 16 16" '
                'fill="none" stroke="currentColor" stroke-width="1.5" '
                'aria-hidden="true">'
                '<path d="M4 6l4 4 4-4" stroke-linecap="round" '
                'stroke-linejoin="round"/></svg>'
            )
            return (
                f'<div class="dashdown-dropdown dashdown-filter-pill dashdown-multiselect" id="{cid}" '
                f'data-async-component="dropdown" '
                f'data-config="{esc(safe_json(config))}" '
                f"{data_query_attr}"
                f'data-filter-name="{esc(name)}" '
                f'data-url-sync="{str(url_sync).lower()}" '
                f"{filter_bar_attr}"
                f">"
                f'<span class="dashdown-filter-pill-label">{esc(label)}'
                f'<span class="dashdown-filter-pill-colon">:</span></span>'
                f'<div class="dashdown-multiselect-control">'
                f'<button type="button" class="dashdown-multiselect-trigger" '
                f'aria-haspopup="listbox" aria-expanded="false" '
                f'aria-label="{esc(label)}">'
                f'<span class="dashdown-multiselect-summary is-placeholder" '
                f'data-placeholder="All">All</span>'
                f"{chevron}"
                f"</button>"
                f'<div class="dashdown-multiselect-panel" role="listbox" '
                f'aria-multiselectable="true" hidden></div>'
                f"</div>"
                f"</div>"
            )

        # Single-select async: data={query} + column, options loaded client-side.
        if query_name and column:
            config = {
                "name": name,
                "query_name": query_name,
                "column": column,
                "label": label,
                "include_all": include_all,
                "url_sync": url_sync,
                "multi": False,
            }

            # Compact pill: muted inline label prefix + borderless select (the
            # pill wrapper carries the border). The label is repeated as an
            # aria-label since it's no longer a <label>.
            html = (
                f'<div class="dashdown-dropdown dashdown-filter-pill" id="{cid}" '
                f'data-async-component="dropdown" '
                f'data-config="{esc(safe_json(config))}" '
                f'data-query-name="{esc(query_name)}" '
                f'data-filter-name="{esc(name)}" '
                f'data-url-sync="{str(url_sync).lower()}" '
                f"{filter_bar_attr}"
                f">"
                f'<span class="dashdown-filter-pill-label">{esc(label)}'
f'<span class="dashdown-filter-pill-colon">:</span></span>'
                f'<select class="select select-sm" aria-label="{esc(label)}">'
                f'<option value="" disabled>Loading...</option>'
                f'</select>'
            )

            html += f"</div>"
            return html
        else:
            # Explicit options mode (no async needed)
            opt_html = ""
            if include_all:
                opt_html += '<option value="">All</option>'
            opt_html += "".join(f'<option value="{esc(o)}">{esc(o)}</option>' for o in options)

            meta = {
                "name": name,
                "column": column,
                "label": label,
                "url_sync": url_sync,
            }
            
            html = (
                f'<div class="dashdown-dropdown dashdown-filter-pill" id="{cid}" '
                f"data-dashdown-dropdown='{safe_json(meta)}' "
                f'data-url-sync="{str(url_sync).lower()}" '
                f"{filter_bar_attr}"
                f">"
                f'<span class="dashdown-filter-pill-label">{esc(label)}'
f'<span class="dashdown-filter-pill-colon">:</span></span>'
                f'<select class="select select-sm" aria-label="{esc(label)}" '
                f'x-model="$store.filters[\'{esc(name)}\']" '
                f'>{opt_html}</select>'
            )

            html += f"</div>"
            return html
