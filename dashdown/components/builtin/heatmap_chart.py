from __future__ import annotations

from dashdown.components.base import Component, register_component
from dashdown.components.builtin._util import attr_str, ref_str, resolve_semantic_query
from dashdown.components.builtin.line_chart import _chart_html, _chart_placeholder


@register_component("HeatmapChart")
class HeatmapChart(Component):
    """Matrix heatmap — a grid of cells shaded by magnitude.

    Two input modes:

    * ``data={by_channel} x="month" y="channel" value="downloads"`` — `x`/`y` are
      both category columns; `value` is the per-cell magnitude column.
    * **semantic** — ``x={s.month} y={s.channel} value={s.downloads}``: `x` and `y`
      are two **dimensions** (the primary `by` + secondary `series` grouping) and
      `value` is a **measure**, combined into one synthetic query grouped by both
      dimensions (one aggregated cell per `x`×`y` pair).

    (For a calendar-style day grid use
    [CalendarHeatmap](/components/charts/calendar-heatmap) instead.)
    """

    def render(self, attrs, ctx, inner: str | None = None):
        if attrs.get("data") is None:
            # --- semantic mode: x/y are dimensions, value is a measure ---
            value_ref = ref_str(attrs, "value")
            x_ref = ref_str(attrs, "x") or ref_str(attrs, "by")
            y_ref = ref_str(attrs, "y") or ref_str(attrs, "series")
            if not value_ref or not x_ref or not y_ref:
                raise ValueError(
                    "HeatmapChart requires `x` and `y` dimensions and a `value` "
                    "measure (or `data={query}` with column names)"
                )
            sem = resolve_semantic_query(
                attrs, ctx, measures=[value_ref], by_ref=x_ref, series_ref=y_ref
            )
            value_col = sem["columns"][value_ref]
            sem_format = sem["formats"].get(value_col) or None
            return _chart_placeholder(
                "heatmap", attrs, ctx,
                name=sem["query_name"], x=sem["by"], y=sem["series"],
                extra={"value": value_col}, sem_format=sem_format,
            )
        # --- data={query} mode: x/y/value are column names ---
        value = attr_str(attrs, "value")
        if not value:
            raise ValueError(
                "HeatmapChart requires a `value` attribute (the per-cell magnitude column)"
            )
        return _chart_html("heatmap", attrs, ctx, extra={"value": value})
