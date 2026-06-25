from __future__ import annotations

from typing import Any

from dashdown.components.base import Component, RenderContext, register_component
from dashdown.components.builtin._util import attr_str, esc, new_id, safe_json
from dashdown.render.attrs import DataRef

_AGGS = ("sum", "avg", "count", "min", "max")


def _field_list(attrs: dict[str, Any], key: str) -> list[str]:
    raw = attr_str(attrs, key, "") or ""
    return [f.strip() for f in raw.split(",") if f.strip()]


@register_component("PivotTable")
class PivotTable(Component):
    """Interactive cross-tabulation with drag-and-drop row/column axes.

    Usage:
        <PivotTable data={orders} rows="region" cols="category"
                    values="amount" agg="sum" title="Revenue" />

    `rows`/`cols` are comma-separated column lists seeding the axes; the
    rendered pivot lets the user drag field chips between the Rows/Columns
    zones and switch the value column/aggregation. Aggregations: sum (default),
    avg, count, min, max.
    """

    def render(self, attrs, ctx: RenderContext, inner: str | None = None) -> str:
        data_val = attrs.get("data")
        if isinstance(data_val, DataRef):
            name = data_val.name
        else:
            name = attr_str(attrs, "data")
        if not name:
            raise ValueError("PivotTable requires `data={query_name}` attribute")

        agg = (attr_str(attrs, "agg", "sum") or "sum").lower()
        if agg not in _AGGS:
            raise ValueError(
                f"PivotTable `agg` must be one of {', '.join(_AGGS)} (got {agg!r})"
            )

        cid = new_id("dashdown-pivot")
        config = {
            "query_name": name,
            "title": attr_str(attrs, "title", ""),
            "rows": _field_list(attrs, "rows"),
            "cols": _field_list(attrs, "cols"),
            "values": attr_str(attrs, "values"),
            "agg": agg,
            "empty_message": attr_str(attrs, "empty_message", "No data available"),
        }
        config_json = esc(safe_json(config))
        return (
            f'<div class="dashdown-pivot card bg-base-100 border border-base-300" id="{cid}" '
            f'data-async-component="pivot" '
            f'data-config="{config_json}" '
            f'data-component-id="{cid}" '
            f'data-query-name="{esc(name)}">'
            f'<div class="card-body p-4">'
            f'<div class="dashdown-pivot-skeleton skeleton w-full" style="height:200px"></div>'
            f'</div></div>'
        )
