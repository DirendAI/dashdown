"""Tests for the RangeSlider (numeric range filter) component.

Locks the server-rendered placeholder/config the client JS reads, the low/high
param-pair semantics, the `default` parsing (array literal + comma string), the
`is_filter` static-build stripping, and that both values flow through the
test-locked `_substitute_params` exactly like every other filter (no new
injection path).
"""
import html as html_mod
import json
import re

import pytest

import dashdown.components  # noqa: F401  (registers built-ins)
from dashdown.components.base import RenderContext, get_component
from dashdown.render.components import render_components
from dashdown.render.pipeline import _substitute_params


@pytest.fixture
def ctx():
    return RenderContext(queries={}, params={}, current_path="/")


def _config_of(html: str) -> dict:
    m = re.search(r'data-config="([^"]*)"', html)
    assert m, f"no data-config in: {html}"
    return json.loads(html_mod.unescape(m.group(1)))


# --------------------------------------------------------------------------- #
# registration + required attrs
# --------------------------------------------------------------------------- #
def test_rangeslider_registered():
    assert get_component("RangeSlider") is not None


def test_rangeslider_is_filter():
    assert get_component("RangeSlider").is_filter is True


def test_requires_name(ctx):
    out = render_components("<RangeSlider />", ctx)
    assert "Error rendering" in out
    assert "requires a `name`" in out


# --------------------------------------------------------------------------- #
# default config + placeholder
# --------------------------------------------------------------------------- #
def test_default_config_and_markup(ctx):
    html = render_components(
        '<RangeSlider name="price_range_filter" min={0} max={10000} step={50} '
        'default={[0, 10000]} label="Price Range ($)" />',
        ctx,
    )
    assert 'data-async-component="rangeslider"' in html
    assert 'data-name="price_range_filter"' in html
    assert 'data-min-param="price_range_filter_min"' in html
    assert 'data-max-param="price_range_filter_max"' in html
    assert 'data-url-sync="true"' in html
    assert "rangeSliderComponent('price_range_filter')" in html
    # two native range inputs (the dual handles)
    assert html.count('type="range"') == 2

    cfg = _config_of(html)
    assert cfg["name"] == "price_range_filter"
    assert cfg["label"] == "Price Range ($)"
    assert cfg["min"] == 0
    assert cfg["max"] == 10000
    assert cfg["step"] == 50
    assert cfg["default_lo"] == 0
    assert cfg["default_hi"] == 10000
    assert cfg["min_param"] == "price_range_filter_min"
    assert cfg["max_param"] == "price_range_filter_max"
    assert cfg["url_sync"] is True


def test_label_defaults_to_name(ctx):
    cfg = _config_of(render_components('<RangeSlider name="age" />', ctx))
    assert cfg["label"] == "age"


def test_defaults_to_full_range_when_no_default(ctx):
    cfg = _config_of(
        render_components('<RangeSlider name="age" min={10} max={90} />', ctx)
    )
    assert cfg["default_lo"] == 10
    assert cfg["default_hi"] == 90
    assert cfg["step"] == 1  # default step


# --------------------------------------------------------------------------- #
# default parsing: array literal + comma string, clamped to the track
# --------------------------------------------------------------------------- #
def test_default_array_literal(ctx):
    cfg = _config_of(
        render_components(
            '<RangeSlider name="p" min={0} max={1000} default={[100, 500]} />', ctx
        )
    )
    assert cfg["default_lo"] == 100
    assert cfg["default_hi"] == 500


def test_default_comma_string(ctx):
    cfg = _config_of(
        render_components(
            '<RangeSlider name="p" min={0} max={1000} default="100,500" />', ctx
        )
    )
    assert cfg["default_lo"] == 100
    assert cfg["default_hi"] == 500


def test_default_clamped_into_track(ctx):
    cfg = _config_of(
        render_components(
            '<RangeSlider name="p" min={0} max={1000} default={[-50, 5000]} />', ctx
        )
    )
    assert cfg["default_lo"] == 0
    assert cfg["default_hi"] == 1000


def test_default_swapped_if_inverted(ctx):
    cfg = _config_of(
        render_components(
            '<RangeSlider name="p" min={0} max={1000} default={[800, 200]} />', ctx
        )
    )
    assert cfg["default_lo"] == 200
    assert cfg["default_hi"] == 800


# --------------------------------------------------------------------------- #
# validation
# --------------------------------------------------------------------------- #
def test_max_must_exceed_min(ctx):
    out = render_components('<RangeSlider name="p" min={100} max={100} />', ctx)
    assert "Error rendering" in out
    assert "must be greater than" in out


# --------------------------------------------------------------------------- #
# custom params + url_sync + format
# --------------------------------------------------------------------------- #
def test_custom_param_names(ctx):
    html = render_components(
        '<RangeSlider name="p" min_param="lo" max_param="hi" />', ctx
    )
    cfg = _config_of(html)
    assert cfg["min_param"] == "lo"
    assert cfg["max_param"] == "hi"
    assert 'data-min-param="lo"' in html
    assert 'data-max-param="hi"' in html


def test_url_sync_false(ctx):
    html = render_components('<RangeSlider name="p" url_sync=false />', ctx)
    assert 'data-url-sync="false"' in html
    assert _config_of(html)["url_sync"] is False


def test_format_config_passed_through(ctx):
    cfg = _config_of(
        render_components(
            '<RangeSlider name="p" format="currency" currency="$" />', ctx
        )
    )
    assert cfg["format"]["format"] == "currency"
    assert cfg["format"]["currency"] == "$"


# --------------------------------------------------------------------------- #
# placement
# --------------------------------------------------------------------------- #
def test_inline_by_default(ctx):
    assert "data-filter-bar" not in render_components(
        '<RangeSlider name="p" />', ctx
    )


def test_bar_relocates_to_filter_bar(ctx):
    html = render_components('<RangeSlider name="p" bar />', ctx)
    assert 'data-filter-bar="true"' in html


# --------------------------------------------------------------------------- #
# is_filter → stripped from a static build
# --------------------------------------------------------------------------- #
def test_omitted_in_static_build():
    sctx = RenderContext(queries={}, params={}, current_path="/", static_build=True)
    out = render_components('<RangeSlider name="p" />', sctx)
    assert out.strip() == ""


# --------------------------------------------------------------------------- #
# both values reach SQL only via the test-locked _substitute_params
# --------------------------------------------------------------------------- #
class TestSubstitutionSemantics:
    SQL = (
        "WHERE price BETWEEN CAST(${price_range_filter_min} AS DOUBLE) "
        "AND CAST(${price_range_filter_max} AS DOUBLE)"
    )

    def test_both_bounds_substituted(self):
        out = _substitute_params(
            self.SQL,
            {"price_range_filter_min": "100", "price_range_filter_max": "500"},
        )
        assert out == (
            "WHERE price BETWEEN CAST('100' AS DOUBLE) AND CAST('500' AS DOUBLE)"
        )

    def test_crafted_value_is_inert_literal(self):
        out = _substitute_params(
            self.SQL,
            {"price_range_filter_min": "1 OR 1=1", "price_range_filter_max": "500"},
        )
        assert out == (
            "WHERE price BETWEEN CAST('1 OR 1=1' AS DOUBLE) AND CAST('500' AS DOUBLE)"
        )
