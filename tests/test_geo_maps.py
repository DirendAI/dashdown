"""Tests for the SVG geo map components (ChoroplethTime, ChoroplethFacets,
BivariateMap, BubbleMap, DotDensityMap) and the ISO-enriched world geometry."""
import json
from pathlib import Path

import pytest

import dashdown.components  # noqa: F401  (registers built-ins)
from dashdown.chart_annotations import (
    ChartContext,
    annotation_instructions,
    normalize_geo_id,
    validate_annotations,
)
from dashdown.components.base import RenderContext, get_component
from dashdown.components.builtin._map_base import parse_metrics
from dashdown.data.base import QueryResult
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


@pytest.mark.parametrize("tag,expected_type", GEO_TAGS)
def test_map_card_has_fullscreen_button(ctx, tag, expected_type):
    # Chart parity: every map card carries the ⛶ that opens the fullscreen
    # modal (fullscreen.js re-draws the map via the _geo.js renderer registry).
    html = render_components(VALID[tag], ctx)
    assert "dashdown-chart-expand-btn" in html


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
        # The explain affordance rides _map_html, so its knobs must be in the
        # hand-maintained _HELPER_ATTRS set or they silently vanish from
        # `dashdown components`.
        for knob in ("explain", "cache_ttl", "max_rows", "annotations"):
            assert knob in attrs, f"{tag} missing explain attr {knob}"
    assert "interval" in rows["ChoroplethTime"]["attrs"]
    assert "columns" in rows["ChoroplethFacets"]["attrs"]
    assert "xlabel" in rows["BivariateMap"]["attrs"]
    assert "max_radius" in rows["BubbleMap"]["attrs"]
    assert "per_dot" not in rows["DotDensityMap"]["attrs"]  # metric field, not an attr
    assert "max_dots" in rows["DotDensityMap"]["attrs"]


# --------------------------------------------------------------------------- #
# Explain on the SVG geo maps (mirrors test_ask.py::TestChartExplain)
# --------------------------------------------------------------------------- #
class TestGeoMapExplain:
    def test_bubble_map_explain_registers_ask_with_geo_context(self, ctx):
        html = render_components(
            '<BubbleMap data={pop} metrics="population|Population,gdp|GDP" '
            'year="yr" year_value="2020" title="World population" explain />',
            ctx,
        )
        assert "dashdown-explain-btn" in html
        assert "dashdown-explain-panel" in html
        # The fixed height moves onto the inner region so the card can grow
        # when the commentary footer opens (chart parity).
        assert "dashdown-chart-region" in html
        assert 'style="height:420px"' in html
        assert len(ctx.ask_defs) == 1
        ask = ctx.ask_defs[0]
        assert 'bubble-map chart titled "World population"' in ask.prompt
        assert ask.chart_context == ChartContext(
            chart_type="bubble-map",
            x="iso",
            y="population,gdp",
            extra=(("year", "yr"), ("year_value", "2020")),
        )

    def test_dot_density_map_explain_carries_context(self, ctx):
        render_components(
            '<DotDensityMap data={pop} metrics="population|Population|people|1000" '
            "explain />",
            ctx,
        )
        (ask,) = ctx.ask_defs
        assert ask.chart_context == ChartContext(
            chart_type="dot-density-map", x="iso", y="population"
        )

    @pytest.mark.parametrize(
        "markup",
        [
            '<ChoroplethTime data={pop} metrics="population" explain />',
            '<ChoroplethFacets data={pop} value="population" explain />',
            '<BivariateMap data={dev} x="gdp" y="life_exp" explain />',
        ],
    )
    def test_choropleths_explain_stays_commentary_only(self, ctx, markup):
        # No annotation vocabulary for these map types (no drawable halo layer
        # for facets / animation frames / two-metric encodings) — the ask
        # registers, but commentary-only.
        html = render_components(markup, ctx)
        assert "dashdown-explain-btn" in html
        (ask,) = ctx.ask_defs
        assert ask.chart_context is None

    def test_annotations_false_opts_down_to_commentary(self, ctx):
        render_components(
            '<BubbleMap data={pop} metrics="population" explain annotations=false />',
            ctx,
        )
        (ask,) = ctx.ask_defs
        assert ask.chart_context is None

    def test_live_query_map_is_commentary_only(self):
        ctx = RenderContext(
            queries={}, params={}, current_path="/", live_queries={"pop"}
        )
        render_components(
            '<BubbleMap data={pop} metrics="population" explain />', ctx
        )
        (ask,) = ctx.ask_defs
        assert ask.chart_context is None

    def test_no_explain_keeps_classic_markup(self, ctx):
        html = render_components('<BubbleMap data={pop} metrics="population" />', ctx)
        assert ctx.ask_defs == []
        assert "dashdown-explain" not in html
        # Height stays inline on the card root, exactly as before.
        assert "dashdown-chart-region" not in html
        assert "height:420px" in html

    def test_explain_id_is_deterministic_across_renders(self):
        markup = '<BubbleMap data={pop} metrics="population" explain />'
        ids = []
        for _ in range(2):
            ctx = RenderContext(queries={}, params={}, current_path="/")
            render_components(markup, ctx)
            ids.append(ctx.ask_defs[0].id)
        assert ids[0] == ids[1]  # answer cache absorbs repeat page loads

    def test_explain_ships_in_static_build(self):
        ctx = RenderContext(
            queries={}, params={}, current_path="/", static_build=True
        )
        html = render_components(
            '<BubbleMap data={pop} metrics="population" explain />', ctx
        )
        assert "dashdown-explain-btn" in html
        assert len(ctx.ask_defs) == 1  # _export_ask bakes it


# --------------------------------------------------------------------------- #
# geo_item validation for the SVG geo maps (normalized ids + year slice)
# --------------------------------------------------------------------------- #
@pytest.fixture
def bubble_ctx():
    return ChartContext(
        chart_type="bubble-map",
        x="iso",
        y="population,gdp",
        extra=(("year", "yr"),),
    )


@pytest.fixture
def bubble_result():
    # "004" only exists in 2019; the active frame (latest year, 2020) carries
    # 840/076/276. gdp is zero for 840, population zero for 076.
    return QueryResult(
        columns=["iso", "yr", "population", "gdp"],
        rows=[
            ["004", "2019", 38.0, 20.1],
            ["840", "2020", 331.0, 0],
            ["076", "2020", 0, 1.4],
            ["276", "2020", 83.2, 4.2],
        ],
    )


class TestGeoItemValidation:
    def test_normalize_geo_id_matches_client(self):
        # Server twin of _geo.js::normalizeId — "004", 4 and "4" all match.
        assert normalize_geo_id("004") == "4"
        assert normalize_geo_id(4) == "4"
        assert normalize_geo_id(" 840 ") == "840"
        assert normalize_geo_id("DE") == "DE"
        assert normalize_geo_id("") is None
        assert normalize_geo_id(None) is None

    def test_id_spelling_and_zero_padding_accepted(self, bubble_ctx, bubble_result):
        out = validate_annotations(
            [{"type": "geo_item", "id": 840, "label": "US"}],
            bubble_result,
            bubble_ctx,
        )
        assert [a["name"] for a in out] == ["840"]

    def test_year_slice_excludes_stale_locations(self, bubble_ctx, bubble_result):
        # "004" has data only in 2019; the frame the viewer sees (latest year)
        # doesn't draw it, so it can't earn a halo.
        out = validate_annotations(
            [{"type": "geo_item", "id": "004", "label": "AF"}],
            bubble_result,
            bubble_ctx,
        )
        assert out == []

    def test_year_value_pins_the_frame(self, bubble_result):
        ctx = ChartContext(
            chart_type="bubble-map",
            x="iso",
            y="population,gdp",
            extra=(("year", "yr"), ("year_value", "2019")),
        )
        out = validate_annotations(
            [
                {"type": "geo_item", "id": "004", "label": "AF"},
                {"type": "geo_item", "id": "840", "label": "US"},
            ],
            bubble_result,
            ctx,
        )
        assert [a["name"] for a in out] == ["4"]

    def test_metric_scope_requires_positive_value(self, bubble_ctx, bubble_result):
        # 840 draws no gdp bubble (0) — a gdp-scoped halo has nothing to ring.
        out = validate_annotations(
            [
                {"type": "geo_item", "id": "840", "metric": "gdp"},
                {"type": "geo_item", "id": "076", "metric": "gdp"},
            ],
            bubble_result,
            bubble_ctx,
        )
        # Ships the *normalized* id ("076" → "76"), which is what the client's
        # feature._dashdownId matching expects.
        assert [(a["name"], a["metric"]) for a in out] == [("76", "gdp")]

    def test_unknown_metric_drops_the_field_not_the_mark(
        self, bubble_ctx, bubble_result
    ):
        out = validate_annotations(
            [{"type": "geo_item", "id": "276", "metric": "bogus", "label": "DE"}],
            bubble_result,
            bubble_ctx,
        )
        assert len(out) == 1
        assert "metric" not in out[0]

    def test_unknown_location_dropped(self, bubble_ctx, bubble_result):
        out = validate_annotations(
            [{"type": "geo_item", "id": "999", "label": "Nowhere"}],
            bubble_result,
            bubble_ctx,
        )
        assert out == []

    def test_prompt_fragment_grounds_in_year_slice(self, bubble_ctx, bubble_result):
        text = annotation_instructions(bubble_ctx, bubble_result)
        # The frame the viewer sees (2020) — 2019's "004" isn't offered.
        assert "Location ids: 840, 076, 276" in text
        assert "004" not in text.split("Location ids:")[1].splitlines()[0]
        # SVG geo maps teach the id spelling and the metric scope.
        assert '"id": "<location id from the data>"' in text
        assert '"metric": "<metric column>"' in text
        assert "Metric columns: gdp, population" in text
