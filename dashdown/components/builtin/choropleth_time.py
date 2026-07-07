from __future__ import annotations

from dashdown.components.base import Component, register_component
from dashdown.components.builtin._map_base import _map_html, parse_metrics
from dashdown.components.builtin._util import attr_int, attr_str


@register_component("ChoroplethTime")
class ChoroplethTime(Component):
    """Animated choropleth — a metric shaded per country, played across time.

    Usage: <ChoroplethTime data={pop} id="iso" year="year"
             metrics="population|Population|people,gdp|GDP|$" />
    Countries join on ISO 3166-1 numeric codes (the `id=` column) against the
    bundled world map. A play/scrub control steps through the `year=` column;
    with several `metrics` a toggle switches between them. Every frame ships in
    the one query result, so the animation works fully in static exports.
    `interval=` is the frame duration in ms; `scheme=`/`color=` pick the ramp,
    `scale=` (linear|log|quantile) the value→color mapping.
    """

    def render(self, attrs, ctx, inner: str | None = None):
        metrics = parse_metrics(attr_str(attrs, "metrics"))
        if not metrics:
            raise ValueError(
                'ChoroplethTime requires `metrics="column|Label|unit,…"`'
            )
        config = {
            "id": attr_str(attrs, "id", "iso"),
            "year": attr_str(attrs, "year", "year"),
            "metrics": metrics,
            "interval": attr_int(attrs, "interval", 700),
        }
        return _map_html("choropleth-time", attrs, ctx, config=config)
