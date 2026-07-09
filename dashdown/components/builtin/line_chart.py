from __future__ import annotations

from typing import Any

from dashdown.chart_annotations import ChartContext, build_chart_context
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
from dashdown.components.builtin.ask import ask_surface_inner
from dashdown.llm import (
    DEFAULT_ANSWER_TTL,
    DEFAULT_EXPLAIN_MAX_ROWS,
    DEFAULT_MAX_ROWS,
    register_ask_def,
)
from dashdown.render.attrs import DataRef


# Common chart HTML generator
# All charts use async loading and show skeleton while loading

# The ⛶ fullscreen button injected on every chart card, beside the `explain`
# sparkle. Pure client-side (static/components/fullscreen.js): it reuses the
# chart's already-cached query result to show the chart — or the same data as a
# table — larger in a modal. A distinct class from `.dashdown-explain-btn`
# so ask.js's initAllExplains() never mistakes it for a commentary toggle.
# Unlike `explain`, it is *not* gated on the static build: it needs no live
# server (fetchQueryData reads the baked JSON in exports/embeds).
_EXPAND_BTN_HTML = (
    '<button type="button" class="dashdown-chart-expand-btn" '
    'aria-label="View fullscreen" title="View fullscreen">'
    '<svg fill="none" stroke="currentColor" stroke-width="2" '
    'viewBox="0 0 24 24" aria-hidden="true"><path stroke-linecap="round" '
    'stroke-linejoin="round" d="M8 3H4a1 1 0 00-1 1v4m0 8v4a1 1 0 001 1h4m8 0h4a1 '
    '1 0 001-1v-4m0-8V4a1 1 0 00-1-1h-4"/></svg></button>'
)


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

    # The resolved shape snapshot the `explain` affordance pins to its AskDef —
    # None for chart types without an annotation vocabulary (their explain
    # stays commentary-only with an unchanged ask id), and under
    # `annotations=false` (the per-chart opt-out: commentary without marks).
    # Dropping the context IS the id change the opt-out needs: the prompt
    # reverts to plain commentary, and the plain id matches it.
    chart_context = None
    if attr_bool(attrs, "annotations", True):
        chart_context = build_chart_context(
            chart_type,
            x=x,
            y=y,
            series_by=series_by,
            horizontal=bool(config.get("horizontal")),
            stacked=bool(config.get("stacked")),
        )

    return _chart_card(
        attrs, ctx, chart_type=chart_type, cid=cid, name=name,
        config_json=config_json, height=height, span=span,
        chart_context=chart_context,
    )


def _chart_card(
    attrs: dict[str, Any],
    ctx: RenderContext,
    *,
    chart_type: str,
    cid: str,
    name: str,
    config_json: str,
    height: int,
    span: str,
    chart_context: ChartContext | None = None,
) -> str:
    """The bordered async-chart card shell — no shadow, p-4 skeleton body,
    matching the mockups' card style. Shared by :func:`_chart_placeholder` and
    ComboChart's bespoke config path (combo_chart.py), so the `explain`
    affordance works on every chart type. (catalog.py follows this helper
    cross-module the way it follows ``_chart_html``.)
    """
    skeleton_body = (
        f'<div class="card-body p-4 h-full">'
        f'<div class="dashdown-chart-skeleton skeleton w-full h-full"></div>'
        f"</div>"
    )

    # `explain` — a hover-revealed ✨ button that opens on-demand AI commentary
    # in a footer below the plot. Sugar over the <Ask /> machinery: an ordinary
    # AskDef with a canned prompt (or the author's, via `explain="…"`) registers
    # at render time, so the opaque-id model is untouched — viewers still can't
    # send prompts. The footer is a regular ask surface that ask.js initializes
    # only on first open, so an idle chart costs nothing. Works in static
    # builds too: the AskDef joins ctx.ask_defs, `_export_ask` bakes the
    # answer (+ annotations) per id, and ask.js's static branch fetches that
    # JSON on first open — click → retrieve → show, same as serve mode. Only
    # the ↻ refresh affordance stays live-server-only (a snapshot is fixed).
    explain_html = _explain_affordance(
        chart_type, attrs, ctx, cid=cid, name=name, chart_context=chart_context
    )
    if explain_html:
        # The fixed height moves onto an inner region so the card itself can
        # grow when the commentary footer opens; the plot keeps its exact size.
        # Both corner affordances (⛶ + ✨) sit as direct children of the card.
        inner = (
            f'<div class="dashdown-chart-region" style="height:{height}px">'
            f"{skeleton_body}"
            f"</div>"
            f"{_EXPAND_BTN_HTML}"
            f"{explain_html}"
        )
        style = f"width:100%;{span}"
    else:
        inner = f"{skeleton_body}{_EXPAND_BTN_HTML}"
        style = f"width:100%;height:{height}px;{span}"

    return (
        f'<div class="dashdown-chart card bg-base-100 border border-base-300" '
        f'id="{cid}" '
        f'style="{style}" '
        f'data-async-component="chart" '
        f'data-config="{config_json}" '
        f'data-component-id="{cid}" '
        f'data-query-name="{esc(name)}">'
        f"{inner}"
        f'</div>'
    )


def _explain_affordance(
    chart_type: str,
    attrs: dict[str, Any],
    ctx: RenderContext,
    *,
    cid: str,
    name: str,
    chart_context: ChartContext | None = None,
) -> str:
    """The ✨ button + hidden commentary footer for a chart with `explain`,
    or "" when the attr is unset.

    Registers the AskDef (deterministic id — same chart, same id across
    renders) and records it on ``ctx.ask_defs`` so an embed token signed for
    the page covers the ask endpoint too. `explain` bare uses the canned
    prompt; `explain="…"` pins the author's own question — author-supplied at
    render time either way, exactly the <Ask /> trust model. `cache_ttl=`
    controls how long the answer stays cached, same spelling and default as
    on <Ask /> (and, like there, it only affects expiry — it stays out of the
    id hash, so tuning it never busts existing answers).

    ``chart_context`` (the resolved shape, from the placeholder builders) asks
    the model for visual chart annotations too (chart_annotations.py). It's
    dropped for `live` queries — the data changes under the marks every poll
    interval, so those asks stay commentary-only — and is None already for
    chart types without an annotation vocabulary.
    """
    explain_val = attrs.get("explain")
    if not explain_val:
        return ""

    if name in ctx.live_queries:
        chart_context = None

    cache_ttl = max(0, attr_int(attrs, "cache_ttl", DEFAULT_ANSWER_TTL))
    # Annotation-bearing asks default to a larger data payload: candidates are
    # validated against the full result, and a model grounded in a truncated
    # view proposes marks that fail. Commentary-only explains keep the <Ask />
    # default (and their pre-annotation ask ids). `max_rows=` overrides either.
    max_rows = max(
        1,
        attr_int(
            attrs,
            "max_rows",
            DEFAULT_EXPLAIN_MAX_ROWS if chart_context is not None else DEFAULT_MAX_ROWS,
        ),
    )

    if isinstance(explain_val, str):
        prompt = explain_val
    else:
        title = attr_str(attrs, "title", "")
        shown = (
            f'a {chart_type} chart titled "{title}"'
            if title
            else f"a {chart_type} chart"
        )
        prompt = (
            f"Explain what is notable in this data, shown as {shown}: "
            "the overall pattern, any standouts or anomalies, and what a "
            "viewer should take away."
        )

    # Same connector resolution as <Ask />: a semantic chart's synthetic query
    # carries its connector on the recorded ref; a `data={query}` chart binds
    # by name with the project default as fallback.
    if name in ctx.semantic_refs:
        connector = ctx.semantic_refs[name].connector
    else:
        connector = ctx.query_connectors.get(name, ctx.default_connector)

    ask = register_ask_def(
        ((name, connector),),
        prompt,
        max_rows=max_rows,
        cache_ttl=cache_ttl,
        page_title=ctx.page_title,
        page_description=ctx.page_description,
        chart_context=chart_context,
    )
    ctx.ask_defs.append(ask)

    # highlight_queries stays empty: the commentary lives inside the chart it
    # explains, so glowing the parent card on hover would be noise. lazy=false
    # because the click is the gate — once open, generate immediately.
    ask_config = esc(
        safe_json(
            {
                "ask_id": ask.id,
                "query_names": [name],
                "replay": "once",
                "highlight_queries": [],
                "lazy": False,
            }
        )
    )
    panel_id = f"{cid}-explain"
    # The button reuses the AI-badge sparkle (ask.py) so the affordance and the
    # provenance mark speak one icon language.
    return (
        f'<button type="button" class="dashdown-explain-btn" '
        f'aria-expanded="false" aria-controls="{panel_id}" '
        f'aria-label="Explain this data" title="Explain this data">'
        '<svg fill="none" stroke="currentColor" stroke-width="1.5" '
        'viewBox="0 0 24 24" aria-hidden="true">'
        '<path stroke-linejoin="round" '
        'd="M12 4.5l1.9 5.1 5.1 1.9-5.1 1.9-1.9 5.1-1.9-5.1-5.1-1.9 5.1-1.9 1.9-5.1z"/>'
        '<path stroke-linejoin="round" '
        'd="M18.8 3.2l.7 1.8 1.8.7-1.8.7-.7 1.8-.7-1.8-1.8-.7 1.8-.7.7-1.8z"/>'
        "</svg></button>"
        f'<div id="{panel_id}" class="dashdown-ask dashdown-explain-panel" hidden '
        f'data-config="{ask_config}">' + ask_surface_inner() + "</div>"
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
