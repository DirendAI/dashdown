from __future__ import annotations

from dashdown.components.base import Component, register_component
from dashdown.components.builtin._util import attr_str
from dashdown.components.builtin.line_chart import _chart_html


@register_component("CalendarHeatmap")
class CalendarHeatmap(Component):
    """GitHub-style calendar heatmap for daily activity data.

    Usage: <CalendarHeatmap data={daily} date="day" value="count" title="Activity" />
    `date`/`value` are aliases for the generic `x`/`y` chart attributes.
    """

    def render(self, attrs, ctx, inner: str | None = None):
        attrs = dict(attrs)
        if attr_str(attrs, "date") and not attr_str(attrs, "x"):
            attrs["x"] = attrs["date"]
        if attr_str(attrs, "value") and not attr_str(attrs, "y"):
            attrs["y"] = attrs["value"]
        return _chart_html("calendar", attrs, ctx)
