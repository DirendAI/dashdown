from __future__ import annotations

from dashdown.components.base import Component, register_component
from dashdown.components.builtin.line_chart import _chart_html


def _distribution_hint(chart: str) -> str:
    return (
        f"{chart} can't be built from a semantic `metric=`/`by=` reference: it needs "
        f"raw per-row values, but a semantic measure is pre-aggregated (the "
        f"distribution would collapse to a single point). Use `data={{query}}` with a "
        f"`y=` value column — or expose quartile measures and draw them with a "
        f"CandlestickChart-style chart."
    )


@register_component("BoxPlot")
class BoxPlot(Component):
    """Box-and-whisker distribution chart.

    Usage: <BoxPlot data={orders} x="category" y="amount" />
    `y` is the value column; `x` is an optional grouping column (omit it for a
    single box over all rows). Quartiles are computed client-side from the raw
    rows, with 1.5×IQR whiskers and outliers overlaid as points.

    Distribution charts read **raw rows**, so they take `data={query}` only — a
    semantic `metric=` (pre-aggregated) can't feed them.
    """

    def render(self, attrs, ctx, inner: str | None = None):
        return _chart_html(
            "boxplot", attrs, ctx, require_x=False,
            semantic_hint=_distribution_hint("BoxPlot"),
        )


@register_component("Violin")
class Violin(Component):
    """Violin (kernel density) distribution chart; same attributes as BoxPlot."""

    def render(self, attrs, ctx, inner: str | None = None):
        return _chart_html(
            "violin", attrs, ctx, require_x=False,
            semantic_hint=_distribution_hint("Violin"),
        )
