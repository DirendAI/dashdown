"""ComboChart — bars and lines on one cartesian chart, with an optional second
(right-hand) y-axis.

It is the one chart type that mixes series *types* and carries a secondary value
axis, so it can't ride the shared ``_chart_html`` helper (which has a single
``y``). It builds its own ``data-async-component="chart"`` placeholder with a
``type:"combo"`` config that ``chart.js::comboChartOption`` turns into one
ECharts series per column — bar or line — over a shared category x-axis.

Two input modes, mirroring every other chart:

* ``data={query}`` — ``bars=``/``lines=`` name **columns** of that query.
* no ``data=`` (semantic) — ``bars=``/``lines=`` name **metric refs**
  (``{model.metric}`` or a comma list ``"model.a,model.b"``) on one model; they
  are combined into a single synthetic semantic query (the same path charts use),
  ``by=``/``grain=`` group it, and each axis defaults to its metric's declared
  display format.
"""
from __future__ import annotations

from typing import Any

from dashdown.components.base import Component, RenderContext, register_component
from dashdown.components.builtin._util import (
    attr_int,
    attr_str,
    esc,
    format_config,
    grid_span_style,
    new_id,
    ref_str,
    resolve_semantic_query,
    safe_json,
)
from dashdown.render.attrs import DataRef


def _split(raw: str | None) -> list[str]:
    """Split a comma-separated attr value (``"a, b"``) into a clean list."""
    if not raw:
        return []
    return [c.strip() for c in str(raw).split(",") if c.strip()]


def _right_format_config(attrs: dict[str, Any]) -> dict[str, Any]:
    """Display-format fragment for the **secondary** (right) y-axis.

    The ``right_*`` twins of :func:`format_config`'s keys, returned as a nested
    dict the JS feeds to its shared value formatter for the right axis only — so a
    ``revenue`` left axis can read ``$`` while a ``margin`` right axis reads ``%``.
    """
    cfg: dict[str, Any] = {}
    fmt = attr_str(attrs, "right_format")
    if fmt:
        cfg["format"] = fmt
    currency = attr_str(attrs, "right_currency")
    if currency:
        cfg["currency"] = currency
    if "right_decimals" in attrs:
        d = attr_int(attrs, "right_decimals")
        if d is not None:
            cfg["decimals"] = d
    locale = attr_str(attrs, "right_locale")
    if locale:
        cfg["locale"] = locale
    return cfg


def _combo_html(attrs: dict[str, Any], ctx: RenderContext) -> str:
    title = attr_str(attrs, "title", "")
    empty_message = attr_str(attrs, "empty_message", "No data available")

    # `bars`/`lines`/`right_axis` accept a single `{ref}` (DataRef) or a quoted
    # comma list ("a,b") — `ref_str` collapses a DataRef to its name, then we split.
    bar_in = _split(ref_str(attrs, "bars"))
    line_in = _split(ref_str(attrs, "lines"))
    right_in = _split(ref_str(attrs, "right_axis"))
    if not bar_in and not line_in:
        raise ValueError(
            "ComboChart requires `bars=` and/or `lines=` "
            "(one or more columns, or `model.metric` refs)"
        )

    left_fmt = format_config(attrs)
    right_fmt = _right_format_config(attrs)

    data_val = attrs.get("data")
    if data_val is not None:
        # --- plain `data={query}` mode: bars/lines/right_axis are column names ---
        name = data_val.name if isinstance(data_val, DataRef) else attr_str(attrs, "data")
        x = attr_str(attrs, "x")
        if not name:
            raise ValueError("ComboChart requires `data={query_name}` (or metric refs)")
        if not x:
            raise ValueError("ComboChart requires an `x` attribute")
        bar_cols, line_cols, right_cols = bar_in, line_in, right_in
    else:
        # --- semantic mode: bars/lines are metric refs on one model ---
        if "series" in attrs:
            raise ValueError(
                "ComboChart has no `series=` split — list the metrics in "
                "bars=/lines= instead (each becomes its own bar/line)"
            )
        # All bars + lines are measures of one model, combined into ONE synthetic
        # query grouped by `by=` (the shared multi-measure semantic path); each ref
        # maps back to its result column for its bar/line/right-axis role.
        sem = resolve_semantic_query(
            attrs, ctx, measures=bar_in + line_in, by_ref=ref_str(attrs, "by")
        )
        canon = sem["canon"]
        name, x = sem["query_name"], sem["by"]
        bar_cols = [canon(r) for r in bar_in]
        line_cols = [canon(r) for r in line_in]
        right_cols = [canon(r) for r in right_in]
        # Each axis defaults to its first column's declared measure format (revenue
        # → currency, etc.) unless the author set `format=`/`right_format=`. The left
        # axis carries the columns *not* on the right.
        right_set = set(right_cols)
        left_cols = [c for c in (bar_cols + line_cols) if c not in right_set]
        if not left_fmt and left_cols:
            left_fmt = dict(sem["formats"].get(left_cols[0], {}))
        if not right_fmt and right_cols:
            right_fmt = dict(sem["formats"].get(right_cols[0], {}))

    cid = new_id("dashdown-combo")
    config: dict[str, Any] = {
        "type": "combo",
        "query_name": name,
        "x": x,
        "title": title,
        "empty_message": empty_message,
        "bars": bar_cols,
        "lines": line_cols,
        "right_axis": right_cols,
        "sort_by": attr_str(attrs, "sort_by"),
    }
    color = attr_str(attrs, "color")
    if color:
        config["color"] = color
    # Per-series colours: `bar_color`/`line_color` (each a single colour or a
    # comma list cycled across multiple bars/lines) override the shared palette
    # for just the bar or line series — the usual "indigo bars, amber line". A
    # series with no explicit colour still falls back to `color=`/the theme palette.
    bar_colors = _split(attr_str(attrs, "bar_color"))
    if bar_colors:
        config["bar_colors"] = bar_colors
    line_colors = _split(attr_str(attrs, "line_color"))
    if line_colors:
        config["line_colors"] = line_colors
    config.update(left_fmt)  # left-axis format/currency/decimals/… keys
    if right_fmt:
        config["right"] = right_fmt  # nested → chart.js feeds it to valueFormatter

    config_json = esc(safe_json(config))
    height = attr_int(attrs, "height", 320) or 320
    span = grid_span_style(attrs)
    return (
        f'<div class="dashdown-chart card bg-base-100 border border-base-300" '
        f'id="{cid}" '
        f'style="width:100%;height:{height}px;{span}" '
        f'data-async-component="chart" '
        f'data-config="{config_json}" '
        f'data-component-id="{cid}" '
        f'data-query-name="{esc(name)}">'
        f'<div class="card-body p-4 h-full">'
        f'<div class="dashdown-chart-skeleton skeleton w-full h-full"></div>'
        f'</div>'
        f'</div>'
    )


@register_component("ComboChart")
class ComboChart(Component):
    """Combo chart — bars and lines together, with an optional second y-axis.

    Usage (query):  ``<ComboChart data={q} x="month" bars="revenue" lines="orders"
    right_axis="orders" />``. Usage (metric): ``<ComboChart by={sales.order_date}
    grain="month" bars={sales.revenue} lines={sales.orders} right_axis={sales.orders}
    />``. ``bars=``/``lines=`` list the columns (or ``model.metric`` refs) drawn as
    bars vs lines; ``right_axis=`` lists the subset plotted against a right-hand
    y-axis (its own ``right_format``/``right_currency``/``right_decimals``).
    Left-axis number format uses the usual ``format``/``currency``/``decimals``.
    """

    def render(self, attrs, ctx, inner: str | None = None):
        return _combo_html(attrs, ctx)
