"""Counter component for displaying KPI-style metrics in large fonts."""
from __future__ import annotations

from typing import Any

from dashdown.components.base import Component, RenderContext, register_component
from dashdown.components.builtin._util import (
    attr_bool,
    attr_str,
    esc,
    format_config,
    grid_span_style,
    new_id,
    resolve_semantic,
    safe_json,
)
from dashdown.render.attrs import DataRef


def _ref_name(attrs: dict[str, Any], key: str) -> str | None:
    """Resolve a `key={query}` (DataRef) or `key="name"` attr to a query name."""
    v = attrs.get(key)
    if isinstance(v, DataRef):
        return v.name
    if v:
        return str(v)
    return None


@register_component("Counter")
class Counter(Component):
    """Display a single value as a large counter/KPI.

    Usage:
        <Counter data={query_name} row=0 column="field_name" label="Label" color="primary" />

    Optional KPI extras:
        compare={prev_query}   compute a ▲/▼ delta badge vs. a comparison value
        delta="12.4"           or pass an explicit delta percentage instead
        invert-delta           treat a decrease as good (e.g. cost / wait time)
        sparkline={series}     render an inline trend sparkline from a series query

    Semantic sparkline (drive the trend from a metric, like the headline):
        <Counter metric={sales.revenue}
                 sparkline={sales.revenue} sparkline-by={sales.order_date}
                 grain="month" />
        `sparkline={model.metric} sparkline-by={model.time_dim}` (+ optional
        `grain=`, literal or {control}) resolves a second semantic ref into its
        own synthetic query — no hand-written series query needed. Falls back to
        the named-query path above when `sparkline-by=` is absent.

    Number formatting (the headline value):
        format="currency"      currency | number | compact | percent
                               (`compact` abbreviates: 3,338,316,067 → "3.34B",
                               with the exact value shown on hover)
        currency="$"           symbol ("$"/"€") prepended, OR an ISO 4217 code
                               ("EUR") for full locale currency formatting
        locale="de-DE"         BCP-47 tag → European separators (1.157.252,33)
        decimals=2             pin the fraction-digit count
        prefix="$" suffix=…    literal strings wrapped around the formatted value

    Colors: primary, secondary, accent, success, warning, error, info.
    With a sparkline, `color` paints the trend and the value stays neutral
    (mockup KPI style); without one it colors the value itself.
    """

    def render(
        self, attrs, ctx: RenderContext, inner: str | None = None
    ) -> str:
        # Semantic metric: `<Counter metric={sales.revenue} />` is a scalar KPI
        # (no `by`); the metric name is the column, its format hint the default.
        sem = resolve_semantic(attrs, ctx)
        if sem is not None:
            name = sem["query_name"]
        else:
            name = _ref_name(attrs, "data")

        if not name:
            return '<div class="text-error">Counter requires data={query_name}</div>'

        row = int(attr_str(attrs, "row", "0"))
        column = sem["metric"] if sem is not None else attr_str(attrs, "column")
        index = attr_str(attrs, "index")
        label = attr_str(attrs, "label", "")
        color = attr_str(attrs, "color", "primary")
        prefix = attr_str(attrs, "prefix", "")
        suffix = attr_str(attrs, "suffix", "")

        cid = new_id("dashdown-counter")

        config: dict[str, Any] = {
            "query_name": name,
            "row": row,
            "prefix": prefix,
            "suffix": suffix,
        }
        if column:
            config["column"] = column
        if index:
            config["index"] = int(index)
        # Display formatting (format/currency/decimals) — consumed by counter.js.
        # The headline number is formatted before prefix/suffix are concatenated.
        config.update(format_config(attrs))
        # A semantic metric's format hint fills any key the author didn't set.
        if sem is not None and sem.get("format"):
            for k, v in sem["format"].items():
                config.setdefault(k, v)

        # Delta badge: an explicit `delta=` value, or a `compare={query}` to derive it from.
        delta = attrs.get("delta")
        if delta is not None and not isinstance(delta, bool):
            config["delta"] = str(delta)
        compare_name = _ref_name(attrs, "compare")
        if compare_name:
            config["compare_query"] = compare_name
            config["compare_row"] = int(attr_str(attrs, "compare-row", "0"))
            compare_column = attr_str(attrs, "compare-column")
            if compare_column:
                config["compare_column"] = compare_column
            compare_index = attr_str(attrs, "compare-index")
            if compare_index:
                config["compare_index"] = int(compare_index)
        if attr_bool(attrs, "invert-delta"):
            config["invert_delta"] = True

        # Sparkline: an inline trend line. Two ways to feed it:
        #   - semantic: `sparkline={model.metric} sparkline-by={model.time_dim}`
        #     (+ optional `grain=`) resolves a *second* semantic ref into its own
        #     synthetic query (metric bucketed by the time dimension), riding the
        #     same `_python_def_cache` seam as the headline — so a semantic-first
        #     dashboard drives the trend from a metric, no hand-written series query.
        #   - named query: `sparkline={series_query}` (the original path) — used
        #     when `sparkline-by=` is absent, so existing usage is unchanged.
        spark_sem = (
            resolve_semantic(
                attrs,
                ctx,
                metric_key="sparkline",
                by_key="sparkline-by",
                series_key=None,
            )
            if "sparkline-by" in attrs
            else None
        )
        if spark_sem is not None:
            config["sparkline_query"] = spark_sem["query_name"]
            # The metric's canonical name is its result column — the value series.
            config["sparkline_column"] = spark_sem["metric"]
            spark_name = spark_sem["query_name"]
        else:
            spark_name = _ref_name(attrs, "sparkline")
            if spark_name:
                config["sparkline_query"] = spark_name
                spark_column = attr_str(attrs, "sparkline-column")
                if spark_column:
                    config["sparkline_column"] = spark_column

        config_json = esc(safe_json(config))

        color_classes = {
            "primary": "text-primary",
            "secondary": "text-secondary",
            "accent": "text-accent",
            "success": "text-success",
            "warning": "text-warning",
            "error": "text-error",
            "info": "text-info",
        }
        color_class = color_classes.get(color, "text-base-content")
        span = grid_span_style(attrs)
        style_attr = f' style="{span}"' if span else ""

        # Every tile top-aligns (label, then value) so a KPI row that mixes
        # sparkline and plain counters keeps its labels and values on one line —
        # a centered plain tile next to a top-aligned sparkline tile reads
        # ragged. The sparkline is a full-bleed *background layer* pinned to the
        # card's bottom edge BEHIND the text — no extra card height; the text
        # floats above with a surface-colored text-shadow halo dimming the
        # trend around the glyphs (dashdown.css) — and the *sparkline* carries the `color`
        # while the value stays neutral, per the mockup KPI cards (the big
        # number reads as data, the trend provides the hue). Without a
        # sparkline the value is colored only when the author explicitly set
        # `color=` (the "primary" default exists for the sparkline); the
        # mockup's plain stats are neutral.
        if spark_name:
            card_modifier = " dashdown-counter--spark"
            value_class = "text-base-content"
            spark_html = f'<div class="dashdown-counter-spark {color_class}"></div>'
        else:
            card_modifier = ""
            value_class = color_class if "color" in attrs else "text-base-content"
            spark_html = ""

        return (
            f'<div id="{cid}"{style_attr} '
            f'data-async-component="counter" '
            f'data-config="{config_json}" '
            f'data-query-name="{esc(name)}" '
            f'class="dashdown-counter{card_modifier} card bg-base-100 border border-base-300 p-4 flex flex-col">'
            f'<div class="dashdown-counter-head flex items-center justify-between gap-2">'
            f'<div class="dashdown-counter-label text-xs font-medium uppercase tracking-wide text-base-content/60">{esc(label)}</div>'
            f'<span class="dashdown-counter-delta"></span>'
            f'</div>'
            f'<div class="dashdown-counter-value {value_class} text-2xl font-semibold mt-1">'
            f'<span class="dashdown-counter-skeleton skeleton inline-block w-24 h-8"></span>'
            f'</div>'
            f'{spark_html}'
            f'</div>'
        )
