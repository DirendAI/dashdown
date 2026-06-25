from __future__ import annotations

from dashdown.components.base import Component, register_component
from dashdown.components.builtin._util import attr_float
from dashdown.components.builtin.line_chart import _chart_html


@register_component("GaugeChart")
class GaugeChart(Component):
    """Speedometer-style gauge for a single KPI value.

    Usage: <GaugeChart data={goal} y="pct" min=0 max=100 title="Goal" />
    `y` is the value column — the **first row** is plotted on a `min`..`max`
    scale (defaults `0`..`100`). No `x` is needed.
    """

    def render(self, attrs, ctx, inner: str | None = None):
        extra: dict[str, float] = {}
        lo = attr_float(attrs, "min")
        hi = attr_float(attrs, "max")
        if lo is not None:
            extra["min"] = lo
        if hi is not None:
            extra["max"] = hi
        return _chart_html("gauge", attrs, ctx, require_x=False, extra=extra)
