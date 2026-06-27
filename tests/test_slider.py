"""Tests for the Slider (single-value threshold filter) component.

Locks the server-rendered placeholder/config the client JS reads, the single
store-key semantics, default clamping, the `is_filter` static-build stripping,
and that the value flows through the test-locked `_substitute_params` like every
other filter (no new injection path).
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
def test_slider_registered():
    assert get_component("Slider") is not None


def test_slider_is_filter():
    assert get_component("Slider").is_filter is True


def test_requires_name(ctx):
    out = render_components("<Slider />", ctx)
    assert "Error rendering" in out
    assert "requires a `name`" in out


# --------------------------------------------------------------------------- #
# default config + placeholder
# --------------------------------------------------------------------------- #
def test_default_config_and_markup(ctx):
    html = render_components(
        '<Slider name="min_rating" min={0} max={5} step={0.5} default={4} '
        'label="Min rating" />',
        ctx,
    )
    assert 'data-async-component="slider"' in html
    assert 'data-name="min_rating"' in html
    assert 'data-url-sync="true"' in html
    assert "sliderComponent('min_rating')" in html
    assert html.count('type="range"') == 1  # single handle

    cfg = _config_of(html)
    assert cfg["name"] == "min_rating"
    assert cfg["label"] == "Min rating"
    assert cfg["min"] == 0
    assert cfg["max"] == 5
    assert cfg["step"] == 0.5
    assert cfg["default"] == 4
    assert cfg["url_sync"] is True


def test_label_defaults_to_name(ctx):
    cfg = _config_of(render_components('<Slider name="threshold" />', ctx))
    assert cfg["label"] == "threshold"


def test_default_falls_back_to_min(ctx):
    cfg = _config_of(render_components('<Slider name="s" min={10} max={90} />', ctx))
    assert cfg["default"] == 10
    assert cfg["step"] == 1  # default step


def test_default_clamped_into_track(ctx):
    cfg = _config_of(
        render_components('<Slider name="s" min={0} max={100} default={500} />', ctx)
    )
    assert cfg["default"] == 100


# --------------------------------------------------------------------------- #
# validation + format + url_sync
# --------------------------------------------------------------------------- #
def test_max_must_exceed_min(ctx):
    out = render_components('<Slider name="s" min={5} max={5} />', ctx)
    assert "Error rendering" in out
    assert "must be greater than" in out


def test_format_config_passed_through(ctx):
    cfg = _config_of(
        render_components('<Slider name="s" format="currency" currency="$" />', ctx)
    )
    assert cfg["format"]["format"] == "currency"
    assert cfg["format"]["currency"] == "$"


def test_url_sync_false(ctx):
    html = render_components('<Slider name="s" url_sync=false />', ctx)
    assert 'data-url-sync="false"' in html
    assert _config_of(html)["url_sync"] is False


# --------------------------------------------------------------------------- #
# placement
# --------------------------------------------------------------------------- #
def test_inline_by_default(ctx):
    assert "data-filter-bar" not in render_components('<Slider name="s" />', ctx)


def test_bar_relocates_to_filter_bar(ctx):
    html = render_components('<Slider name="s" bar />', ctx)
    assert 'data-filter-bar="true"' in html


# --------------------------------------------------------------------------- #
# is_filter → stripped from a static build
# --------------------------------------------------------------------------- #
def test_omitted_in_static_build():
    sctx = RenderContext(queries={}, params={}, current_path="/", static_build=True)
    out = render_components('<Slider name="s" />', sctx)
    assert out.strip() == ""


# --------------------------------------------------------------------------- #
# the value reaches SQL only via the test-locked _substitute_params
# --------------------------------------------------------------------------- #
class TestSubstitutionSemantics:
    SQL = "WHERE '${min_rating}' = '' OR rating >= CAST(${min_rating} AS DOUBLE)"

    def test_value_substituted(self):
        out = _substitute_params(self.SQL, {"min_rating": "4"})
        assert out == "WHERE '4' = '' OR rating >= CAST('4' AS DOUBLE)"

    def test_empty_trips_all_guard(self):
        out = _substitute_params(self.SQL, {"min_rating": ""})
        assert out == "WHERE '' = '' OR rating >= CAST('' AS DOUBLE)"

    def test_crafted_value_is_inert_literal(self):
        out = _substitute_params(self.SQL, {"min_rating": "1 OR 1=1"})
        assert out == "WHERE '1 OR 1=1' = '' OR rating >= CAST('1 OR 1=1' AS DOUBLE)"
