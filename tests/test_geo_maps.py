"""Tests for the SVG geo map components (ChoroplethTime, ChoroplethFacets,
BivariateMap, BubbleMap, DotDensityMap) and the ISO-enriched world geometry."""
import json
from pathlib import Path

import pytest

import dashdown.components  # noqa: F401  (registers built-ins)
from dashdown.components.base import RenderContext, get_component
from dashdown.components.builtin._map_base import parse_metrics
from dashdown.render.components import render_components

GEO_TAGS = [
    ("ChoroplethTime", "choropleth-time"),
    ("ChoroplethFacets", "choropleth-facets"),
    ("BivariateMap", "bivariate-map"),
    ("BubbleMap", "bubble-map"),
    ("DotDensityMap", "dot-density-map"),
]

#: A valid instance of each component (required attrs filled in).
VALID = {
    "ChoroplethTime": '<ChoroplethTime data={pop} metrics="population|Population|people" />',
    "ChoroplethFacets": '<ChoroplethFacets data={pop} value="population" />',
    "BivariateMap": '<BivariateMap data={dev} x="gdp" y="life_exp" />',
    "BubbleMap": '<BubbleMap data={pop} metrics="population|Population" />',
    "DotDensityMap": '<DotDensityMap data={pop} metrics="population|Population|people|1000000" />',
}


@pytest.fixture
def ctx():
    return RenderContext(queries={}, params={}, current_path="/")


def _config_of(html: str) -> dict:
    """Extract and parse the data-config JSON from a map placeholder."""
    import html as html_mod
    import re

    m = re.search(r'data-config="([^"]*)"', html)
    assert m, f"no data-config found in: {html}"
    return json.loads(html_mod.unescape(m.group(1)))


@pytest.mark.parametrize("tag,expected_type", GEO_TAGS)
def test_map_registered(tag, expected_type):
    assert get_component(tag) is not None


@pytest.mark.parametrize("tag,expected_type", GEO_TAGS)
def test_map_renders_async_placeholder(ctx, tag, expected_type):
    html = render_components(VALID[tag], ctx)
    assert f'data-async-component="{expected_type}"' in html
    config = _config_of(html)
    assert config["type"] == expected_type
    assert config["query_name"] in ("pop", "dev")
    assert config["id"] == "iso"  # the join-column default
    # The skeleton keeps the card's footprint until data lands.
    assert "skeleton" in html


@pytest.mark.parametrize("tag", [t for t, _ in GEO_TAGS])
def test_map_requires_data(ctx, tag):
    # Missing data → component error card, not a crash.
    html = render_components(VALID[tag].replace(" data={pop}", "").replace(" data={dev}", ""), ctx)
    assert "error" in html.lower()


@pytest.mark.parametrize(
    "markup",
    [
        "<ChoroplethTime data={pop} />",  # metrics missing
        "<ChoroplethFacets data={pop} />",  # value missing
        '<BivariateMap data={dev} x="gdp" />',  # y missing
        "<BubbleMap data={pop} />",  # metrics missing
        "<DotDensityMap data={pop} />",  # metrics missing
    ],
)
def test_map_requires_its_metric_attrs(ctx, markup):
    html = render_components(markup, ctx)
    assert "error" in html.lower()


def test_common_attrs_pass_through(ctx):
    html = render_components(
        '<ChoroplethTime data={pop} metrics="population" title="World" '
        'scheme="viridis" scale="log" id="code" year="yr" interval=500 '
        'geojson="/assets/regions.json" id_field="ISO_N3" />',
        ctx,
    )
    config = _config_of(html)
    assert config["title"] == "World"
    assert config["scheme"] == "viridis"
    assert config["scale"] == "log"
    assert config["id"] == "code"
    assert config["year"] == "yr"
    assert config["interval"] == 500
    assert config["geojson"] == "/assets/regions.json"
    assert config["id_field"] == "ISO_N3"


def test_choropleth_time_parses_metrics(ctx):
    html = render_components(
        '<ChoroplethTime data={pop} metrics="population|Population|people,gdp|GDP|$" />',
        ctx,
    )
    config = _config_of(html)
    assert config["metrics"] == [
        {"column": "population", "label": "Population", "unit": "people"},
        {"column": "gdp", "label": "GDP", "unit": "$"},
    ]


def test_choropleth_facets_config(ctx):
    html = render_components(
        '<ChoroplethFacets data={pop} value="population" years="1990, 2000,2010" '
        'label="Population" unit="people" columns=2 />',
        ctx,
    )
    config = _config_of(html)
    assert config["value"] == "population"
    assert config["years"] == ["1990", "2000", "2010"]
    assert config["label"] == "Population"
    assert config["unit"] == "people"
    assert config["columns"] == 2
    # Facet cards grow with their grid — no fixed height on the card style.
    assert "height:" not in html.split('style="')[1].split('"')[0]


def test_bivariate_map_config(ctx):
    html = render_components(
        '<BivariateMap data={dev} x="gdp" y="life_exp" xlabel="GDP" '
        'ylabel="Life expectancy" year="year" year_value="2020" />',
        ctx,
    )
    config = _config_of(html)
    assert config["x"] == "gdp"
    assert config["y"] == "life_exp"
    assert config["xlabel"] == "GDP"
    assert config["ylabel"] == "Life expectancy"
    assert config["year_value"] == "2020"
    # Labels default to the column names when unset.
    default = _config_of(render_components('<BivariateMap data={dev} x="a" y="b" />', ctx))
    assert default["xlabel"] == "a"
    assert default["ylabel"] == "b"


def test_bubble_map_config(ctx):
    html = render_components(
        '<BubbleMap data={pop} metrics="population|Population" max_radius=25 />', ctx
    )
    config = _config_of(html)
    assert config["max_radius"] == 25
    assert config["metrics"][0]["column"] == "population"


def test_dot_density_config(ctx):
    html = render_components(
        '<DotDensityMap data={pop} '
        'metrics="population|Population|people|10000000,cars|Cars||500000" '
        'dot_radius=1.5 max_dots=5000 />',
        ctx,
    )
    config = _config_of(html)
    assert config["dot_radius"] == 1.5
    assert config["max_dots"] == 5000
    assert config["metrics"][0]["per_dot"] == 10000000
    assert config["metrics"][1] == {
        "column": "cars",
        "label": "Cars",
        "unit": "",
        "per_dot": 500000,
    }


class TestParseMetrics:
    def test_label_defaults_to_column(self):
        assert parse_metrics("population") == [
            {"column": "population", "label": "population", "unit": ""}
        ]

    def test_empty_and_blank(self):
        assert parse_metrics(None) == []
        assert parse_metrics("") == []
        assert parse_metrics(" , ") == []

    def test_per_dot_absent_is_none(self):
        (m,) = parse_metrics("population|Population|people", quantity_field="per_dot")
        assert m["per_dot"] is None

    def test_per_dot_invalid_is_none(self):
        (m,) = parse_metrics("population|P|u|lots", quantity_field="per_dot")
        assert m["per_dot"] is None


class TestWorldIsoEnrichment:
    """The bundled world.json carries ISO 3166-1 numeric codes (added by
    tooling/enrich-world-iso.py) so the geo components can join on the code
    analytics datasets actually carry."""

    @pytest.fixture(scope="class")
    def world(self):
        path = (
            Path(__file__).parent.parent
            / "dashdown" / "static" / "vendor" / "world.json"
        )
        return json.loads(path.read_text(encoding="utf-8"))

    def test_all_features_have_iso_except_known_gaps(self, world):
        # Entities with no ISO 3166-1 assignment (documented in the tooling).
        no_iso = {"N. Cyprus", "Siachen Glacier", ""}
        missing = {
            f["properties"]["name"]
            for f in world["features"]
            if "iso" not in f["properties"]
        }
        assert missing == no_iso

    def test_iso_codes_are_unique_and_zero_padded(self, world):
        codes = [
            f["properties"]["iso"]
            for f in world["features"]
            if "iso" in f["properties"]
        ]
        assert len(codes) == len(set(codes))
        assert all(len(c) == 3 and c.isdigit() for c in codes)

    def test_spot_checks(self, world):
        by_name = {f["properties"]["name"]: f["properties"].get("iso") for f in world["features"]}
        assert by_name["United States"] == "840"
        assert by_name["Germany"] == "276"
        assert by_name["China"] == "156"
        assert by_name["Brazil"] == "076"
        assert by_name["Korea"] == "410"  # South Korea
        assert by_name["Dem. Rep. Korea"] == "408"  # North Korea

    def test_names_untouched_for_mapchart(self, world):
        # MapChart still joins by feature name — the enrichment is additive.
        assert all("name" in f["properties"] for f in world["features"])


def test_maps_in_component_catalog():
    """The introspected catalog surfaces the shared _map_html attrs on every
    geo component (via catalog._HELPER_ATTRS) plus each one's own."""
    from dashdown.catalog import build_component_catalog

    rows = {r["name"]: r for r in build_component_catalog()}
    for tag, _ in GEO_TAGS:
        attrs = rows[tag]["attrs"]
        for common in ("data", "title", "scheme", "geojson", "id_field", "height"):
            assert common in attrs, f"{tag} missing common attr {common}"
    assert "interval" in rows["ChoroplethTime"]["attrs"]
    assert "columns" in rows["ChoroplethFacets"]["attrs"]
    assert "xlabel" in rows["BivariateMap"]["attrs"]
    assert "max_radius" in rows["BubbleMap"]["attrs"]
    assert "per_dot" not in rows["DotDensityMap"]["attrs"]  # metric field, not an attr
    assert "max_dots" in rows["DotDensityMap"]["attrs"]
