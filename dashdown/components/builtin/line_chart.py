from __future__ import annotations

from typing import Any

from dashdown.components.base import Component, RenderContext, register_component
from dashdown.components.builtin._util import (
    attr_bool,
    attr_int,
    attr_str,
    esc,
    format_config,
    grid_span_style,
    new_id,
    ref_str,
    resolve_dataset,
    resolve_semantic,
    safe_json,
)
from dashdown.render.attrs import DataRef


# Common chart HTML generator
# All charts use async loading and show skeleton while loading

def _chart_placeholder(
    chart_type: str,
    attrs: dict[str, Any],
    ctx: RenderContext,
    *,
    name: str,
    x: str | None,
    y: str | None,
    series_by: str | None = None,
    extra: dict[str, Any] | None = None,
    sem_format: dict[str, Any] | None = None,
) -> str:
    """Emit the shared async-chart card from **already-resolved** name/x/y.

    Both the simple ``metric→y`` path (:func:`_chart_html`) and the multi-measure
    *role* charts (Candlestick, Heatmap, Sankey, Graph, Parallel — and ComboChart)
    build their config here, so the placeholder markup, palette/format handling and
    grid-span stay in one place. ``extra`` carries the chart-specific config keys
    (a role→column map, a `value` column, a `dimensions` list, scalar flags…);
    ``sem_format`` is a semantic measure's default display format, applied only for
    keys the author didn't set.
    """
    cid = new_id(f"dashdown-{chart_type}")
    config: dict[str, Any] = {
        "type": chart_type,
        "query_name": name,
        "x": x,
        "y": y,
        "title": attr_str(attrs, "title", ""),
        "series_by": series_by,
        "sort_by": attr_str(attrs, "sort_by"),
        "empty_message": attr_str(attrs, "empty_message", "No data available"),
    }
    # Optional per-chart palette override (single color or comma-separated list)
    color = attr_str(attrs, "color")
    if color:
        config["color"] = color
    # Pie charts default to a donut with a center total; `donut=false` opts out.
    if "donut" in attrs:
        config["donut"] = attr_bool(attrs, "donut", True)
    # Value-axis / tooltip number formatting (format/currency/decimals) — applied
    # to the value axis labels and tooltips in chart.js.
    config.update(format_config(attrs))
    # A semantic measure can carry a default display format (currency etc.); apply
    # any keys the author didn't already set on the component.
    if sem_format:
        for k, v in sem_format.items():
            config.setdefault(k, v)
    if extra:
        config.update(extra)
    # Escape config for HTML attribute
    config_json = esc(safe_json(config))

    # Compact default height; per-chart `height=` override; `col-span=` for grids.
    height = attr_int(attrs, "height", 300) or 300
    span = grid_span_style(attrs)

    # Bordered card, no shadow + p-4 body, matching the mockups' card style.
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


def _chart_html(
    chart_type: str,
    attrs: dict[str, Any],
    ctx: RenderContext,
    *,
    require_x: bool = True,
    require_y: bool = True,
    extra: dict[str, Any] | None = None,
    semantic_hint: str | None = None,
) -> str:
    # First-class semantic metric grammar:
    # `metric={model.metric} by={model.dim}` compiles to a synthetic query whose
    # name/x/y we derive here. Falls through to the normal `data={query}` path
    # when `metric` is absent.
    #
    # `semantic_hint` is set by charts whose shape the metric/dimension grammar
    # can't express (a distribution's raw rows, a parent/child hierarchy): they
    # refuse a semantic reference with an actionable message rather than rendering a
    # broken card. Checked on the bare `metric` attr *before* resolve_semantic so we
    # don't register a synthetic ref we're about to reject.
    if semantic_hint is not None and ref_str(attrs, "metric"):
        raise ValueError(semantic_hint)
    sem = resolve_semantic(attrs, ctx)
    if sem is not None:
        # `metrics` (comma-joined) carries every metric the author listed, so a
        # multi-metric reference (`metric="sales.revenue,sales.profit"`) becomes
        # one coloured series per metric in chart.js; single-metric is identical.
        name, x, y = sem["query_name"], sem["by"], sem["metrics"]
        # A `series=` split column: for a semantic chart it's the resolved second
        # dimension (`sem["series"]`, a `{model.dim}` DataRef attr_str can't read).
        series_by = sem.get("series") or attr_str(attrs, "series")
        sem_format = sem.get("format")
    else:
        # For async loading, we need the query name but don't resolve the dataset
        data_val = attrs.get("data")
        # Handle DataRef or string
        if isinstance(data_val, DataRef):
            name = data_val.name
        else:
            name = attr_str(attrs, "data")

        x = attr_str(attrs, "x")
        y = attr_str(attrs, "y")
        # For a plain `data={query}` chart the split is a bare column name.
        series_by = attr_str(attrs, "series")
        sem_format = None
        if not name:
            raise ValueError(f"{chart_type} requires `data={{query_name}}` attribute")
    if (require_x and not x) or (require_y and not y):
        missing = " and ".join(
            n for n, req, v in (("x", require_x, x), ("y", require_y, y)) if req and not v
        )
        raise ValueError(f"{chart_type} requires `{missing}` attribute(s)")

    return _chart_placeholder(
        chart_type, attrs, ctx, name=name, x=x, y=y,
        series_by=series_by, extra=extra, sem_format=sem_format,
    )


@register_component("LineChart")
class LineChart(Component):
    """Line chart — a metric over an ordered `x` (usually time).

    Usage: <LineChart data={q} x="month" y="sales" series="region" />
    `series=` splits one metric into a coloured line per value; a comma-separated
    `y` draws one line per metric. `stacked` turns grouped lines into a cumulative
    area.
    """

    def render(self, attrs, ctx, inner: str | None = None):
        # `stacked` stacks grouped (`series=`) lines into a cumulative area.
        extra = {"stacked": True} if attr_bool(attrs, "stacked", False) else None
        return _chart_html("line", attrs, ctx, extra=extra)


@register_component("BarChart")
class BarChart(Component):
    """Bar chart — a metric across categories.

    Usage: <BarChart data={q} x="region" y="sales" series="channel" />
    `horizontal` swaps the axes (category on Y); `stacked` stacks grouped
    (`series=`) bars. `series=`/multi-metric `y` give multiple coloured series.
    """

    def render(self, attrs, ctx, inner: str | None = None):
        # `horizontal` swaps the category/value axes (ECharts "bar-y-category"):
        # category on the Y axis, values running along X. `stacked` stacks the
        # grouped (`series=`) bars. Both scoped to BarChart, emitted only when
        # set (like PieChart's `donut`).
        extra: dict[str, bool] = {}
        if attr_bool(attrs, "horizontal", False):
            extra["horizontal"] = True
        if attr_bool(attrs, "stacked", False):
            extra["stacked"] = True
        return _chart_html("bar", attrs, ctx, extra=extra or None)


@register_component("PieChart")
class PieChart(Component):
    """Pie/donut chart — parts of a whole (`x` = slice label, `y` = value).

    Usage: <PieChart data={q} x="region" y="sales" />
    Defaults to a donut with a center total (`donut=false` opts out). A `series=`
    column renders faceted small-multiple pies instead.
    """

    def render(self, attrs, ctx, inner: str | None = None):
        return _chart_html("pie", attrs, ctx)


@register_component("ScatterChart")
class ScatterChart(Component):
    """Scatter plot — two numeric columns as points (`x` vs `y`).

    Usage: <ScatterChart data={q} x="spend" y="revenue" series="segment" />
    An optional `series=` colours points by group.
    """

    def render(self, attrs, ctx, inner: str | None = None):
        return _chart_html("scatter", attrs, ctx)


@register_component("TreemapChart")
class TreemapChart(Component):
    """Treemap — categories as nested rectangles sized by value.

    Usage: <TreemapChart data={q} x="category" y="sales" />
    `x` is the label column, `y` the area-encoding value.
    """

    def render(self, attrs, ctx, inner: str | None = None):
        return _chart_html("treemap", attrs, ctx)


@register_component("FunnelChart")
class FunnelChart(Component):
    """Funnel chart — stage labels (`x`) with descending values (`y`).

    Usage: <FunnelChart data={q} x="stage" y="count" />
    """

    def render(self, attrs, ctx, inner: str | None = None):
        return _chart_html("funnel", attrs, ctx)
