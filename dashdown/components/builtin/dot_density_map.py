from __future__ import annotations

from dashdown.components.base import Component, register_component
from dashdown.components.builtin._map_base import _map_html, parse_metrics
from dashdown.components.builtin._util import attr_float, attr_int, attr_str


@register_component("DotDensityMap")
class DotDensityMap(Component):
    """Dot-density map — one dot per fixed quantity, scattered inside borders.

    Usage: <DotDensityMap data={pop} id="iso"
             metrics="population|Population|people|10000000" />
    Each `metrics` entry is `column|Label|unit|per_dot` — one dot stands for
    `per_dot` of the metric (omitted: derived from the data so the map stays
    under `max_dots`). Dot placement is seeded per country+metric, so the same
    data draws the identical map on every load and in static exports.
    Countries join on ISO 3166-1 numeric codes (the `id=` column). With a
    `year=` column, `year_value=` picks the year (default: latest).
    """

    def render(self, attrs, ctx, inner: str | None = None):
        metrics = parse_metrics(attr_str(attrs, "metrics"), quantity_field="per_dot")
        if not metrics:
            raise ValueError(
                'DotDensityMap requires `metrics="column|Label|unit|per_dot,…"`'
            )
        config = {
            "id": attr_str(attrs, "id", "iso"),
            "year": attr_str(attrs, "year"),
            "year_value": attr_str(attrs, "year_value"),
            "metrics": metrics,
            "dot_radius": attr_float(attrs, "dot_radius", 1.2),
            "max_dots": attr_int(attrs, "max_dots", 20000),
        }
        return _map_html("dot-density-map", attrs, ctx, config=config)
