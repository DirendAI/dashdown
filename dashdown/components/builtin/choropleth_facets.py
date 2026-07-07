from __future__ import annotations

from dashdown.components.base import Component, register_component
from dashdown.components.builtin._map_base import _map_html
from dashdown.components.builtin._util import attr_int, attr_str


@register_component("ChoroplethFacets")
class ChoroplethFacets(Component):
    """Small-multiple choropleths — one mini world map per year, shared scale.

    Usage: <ChoroplethFacets data={pop} id="iso" year="year" value="population"
             years="1990,2000,2010,2020" label="Population" unit="people" />
    Countries join on ISO 3166-1 numeric codes (the `id=` column). `years=`
    picks which years to facet (default: every distinct year in the result);
    `columns=` the facet-grid width. One color scale spans all facets so the
    panels compare honestly. `scheme=`/`color=`/`scale=` as on ChoroplethTime.
    """

    def render(self, attrs, ctx, inner: str | None = None):
        value = attr_str(attrs, "value")
        if not value:
            raise ValueError("ChoroplethFacets requires `value=` (the metric column)")
        years_attr = attr_str(attrs, "years")
        years = [y.strip() for y in years_attr.split(",") if y.strip()] if years_attr else None
        config = {
            "id": attr_str(attrs, "id", "iso"),
            "year": attr_str(attrs, "year", "year"),
            "value": value,
            "years": years,
            "label": attr_str(attrs, "label", value),
            "unit": attr_str(attrs, "unit", ""),
            "columns": attr_int(attrs, "columns", 3),
        }
        return _map_html(
            "choropleth-facets", attrs, ctx, config=config,
            default_height=320, fixed_height=False,
        )
