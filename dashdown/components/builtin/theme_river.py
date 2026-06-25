from __future__ import annotations

from dashdown.components.base import Component, register_component
from dashdown.components.builtin._util import attr_str
from dashdown.components.builtin.line_chart import _chart_html


@register_component("ThemeRiver")
class ThemeRiver(Component):
    """ThemeRiver (streamgraph) — stacked categories flowing over time.

    Usage: <ThemeRiver data={streams} x="date" y="value" series="metric" />
    `x` is the time column (ISO dates parse best), `y` the value, and `series`
    the category each stream represents. Reuses the shared `x`/`y`/`series`
    attributes; `series` is required (it's what splits the streams).
    """

    def render(self, attrs, ctx, inner: str | None = None):
        if not attr_str(attrs, "series"):
            raise ValueError(
                "ThemeRiver requires a `series` attribute (the category column)"
            )
        return _chart_html("themeriver", attrs, ctx)
