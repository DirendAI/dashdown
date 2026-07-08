from __future__ import annotations

from dashdown.components.base import Component, register_component
from dashdown.components.builtin._map_base import _map_html, parse_metrics
from dashdown.components.builtin._util import attr_int, attr_str


@register_component("BubbleMap")
class BubbleMap(Component):
    """Proportional-symbol map — a circle per country, area ∝ value.

    Usage: <BubbleMap data={pop} id="iso" metrics="population|Population|people" />
    Circles sit on each country's centroid over a muted basemap; with several
    `metrics` a toggle switches between them. Countries join on ISO 3166-1
    numeric codes (the `id=` column). With a `year=` column, `year_value=`
    picks the year (default: latest). `max_radius=` caps the largest circle.
    """

    def render(self, attrs, ctx, inner: str | None = None):
        metrics = parse_metrics(attr_str(attrs, "metrics"))
        if not metrics:
            raise ValueError('BubbleMap requires `metrics="column|Label|unit,…"`')
        config = {
            "id": attr_str(attrs, "id", "iso"),
            "year": attr_str(attrs, "year"),
            "year_value": attr_str(attrs, "year_value"),
            "metrics": metrics,
            "max_radius": attr_int(attrs, "max_radius", 40),
        }
        return _map_html("bubble-map", attrs, ctx, config=config)
