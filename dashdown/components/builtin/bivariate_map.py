from __future__ import annotations

from dashdown.components.base import Component, register_component
from dashdown.components.builtin._map_base import _map_html
from dashdown.components.builtin._util import attr_str


@register_component("BivariateMap")
class BivariateMap(Component):
    """Bivariate choropleth — two metrics on one map via a 3×3 color matrix.

    Usage: <BivariateMap data={dev} id="iso" x="gdp_per_capita" y="life_exp"
             xlabel="GDP per capita" ylabel="Life expectancy" />
    Each country's `x` and `y` values are classed into terciles and colored by
    the 3×3 bivariate palette (`scheme=` blue-purple|green-blue|red-blue).
    Countries join on ISO 3166-1 numeric codes (the `id=` column). With a
    `year=` column, `year_value=` picks the year to show (default: latest).
    """

    def render(self, attrs, ctx, inner: str | None = None):
        x = attr_str(attrs, "x")
        y = attr_str(attrs, "y")
        if not x or not y:
            raise ValueError("BivariateMap requires `x=` and `y=` metric columns")
        config = {
            "id": attr_str(attrs, "id", "iso"),
            "year": attr_str(attrs, "year"),
            "year_value": attr_str(attrs, "year_value"),
            "x": x,
            "y": y,
            "xlabel": attr_str(attrs, "xlabel", x),
            "ylabel": attr_str(attrs, "ylabel", y),
            "xunit": attr_str(attrs, "xunit", ""),
            "yunit": attr_str(attrs, "yunit", ""),
        }
        return _map_html("bivariate-map", attrs, ctx, config=config)
