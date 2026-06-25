from __future__ import annotations

from dashdown.components.base import Component, register_component
from dashdown.components.builtin._util import attr_bool
from dashdown.components.builtin.line_chart import _chart_html


@register_component("Chart")
class Chart(Component):
    """Auto-recommended chart: picks line/bar/scatter from the result shape.

    Usage: <Chart auto data={query} />  (opt-in via the `auto` flag)
    Column roles are inferred client-side: a temporal x → line, a categorical
    x → bar, numeric x and y → scatter. Explicit `x`/`y`/`series` attributes
    override the inference.
    """

    def render(self, attrs, ctx, inner: str | None = None):
        if not attr_bool(attrs, "auto"):
            raise ValueError(
                "Chart requires the `auto` flag, e.g. `Chart auto data={query}`; "
                "for an explicit chart type use LineChart, BarChart, etc."
            )
        return _chart_html("auto", attrs, ctx, require_x=False, require_y=False)
