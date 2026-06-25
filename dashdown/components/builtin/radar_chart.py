from __future__ import annotations

from dashdown.components.base import Component, register_component
from dashdown.components.builtin.line_chart import _chart_html


@register_component("RadarChart")
class RadarChart(Component):
    """Radar (spider) chart for comparing several metrics on one shape.

    Usage: <RadarChart data={scores} x="metric" y="score" series="product" />
    `x` is the indicator/axis column, `y` the value. An optional `series` column
    overlays one polygon per group; without it a single polygon is drawn. Each
    axis is scaled to the largest value seen for that indicator.
    """

    def render(self, attrs, ctx, inner: str | None = None):
        return _chart_html("radar", attrs, ctx)
