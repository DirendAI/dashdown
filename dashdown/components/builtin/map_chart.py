from __future__ import annotations

from dashdown.components.base import Component, register_component
from dashdown.components.builtin._util import attr_str
from dashdown.components.builtin.line_chart import _chart_html


@register_component("MapChart")
class MapChart(Component):
    """Choropleth map for geospatial data (ECharts geo).

    Usage: <MapChart data={by_country} location="country" value="sales" />
    `location` values must match feature names in the map's GeoJSON (for the
    default world map: country names like "United States", "Germany").
    Optional: map="world" (registered map name), geojson="url-or-path" to load
    a custom GeoJSON (e.g. a file under your project's assets/ dir).
    """

    def render(self, attrs, ctx, inner: str | None = None):
        attrs = dict(attrs)
        # location/value are aliases for the generic x/y chart attributes.
        if attr_str(attrs, "location") and not attr_str(attrs, "x"):
            attrs["x"] = attrs["location"]
        if attr_str(attrs, "value") and not attr_str(attrs, "y"):
            attrs["y"] = attrs["value"]
        extra = {
            "map": attr_str(attrs, "map", "world"),
            "geojson": attr_str(attrs, "geojson"),
        }
        return _chart_html("map", attrs, ctx, extra=extra)
