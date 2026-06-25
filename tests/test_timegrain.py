"""Tests for the TimeGrain filter control.

Sugar over a grain-token `<Dropdown>`: it labels the canonical
:data:`~dashdown.semantic.GRAIN_TOKENS`, validates the offered list, seeds a real
`default`, and (being `is_filter`) is stripped from static builds. The token it
writes rides the same `$store.filters[name]` path a chart's `grain={name}` reads.
"""
import html as html_mod
import json
import re

import pytest

import dashdown.components  # noqa: F401  (registers built-ins)
from dashdown.components.base import RenderContext, get_component
from dashdown.render.components import render_components


@pytest.fixture
def ctx():
    return RenderContext(queries={}, params={}, current_path="/")


def _config_of(html: str) -> dict:
    m = re.search(r'data-config="([^"]*)"', html)
    assert m, f"no data-config in: {html}"
    return json.loads(html_mod.unescape(m.group(1)))


def test_timegrain_registered_and_is_filter():
    assert get_component("TimeGrain") is not None
    assert get_component("TimeGrain").is_filter is True


def test_requires_name(ctx):
    out = render_components("<TimeGrain />", ctx)
    assert "Error rendering" in out and "requires a `name`" in out


def test_default_config_and_options(ctx):
    html = render_components('<TimeGrain name="trendGrain" default="month" />', ctx)
    assert 'data-async-component="timegrain"' in html
    assert 'data-filter-name="trendGrain"' in html
    # store binding mirrors the explicit-options Dropdown.
    assert "x-model=\"$store.filters['trendGrain']\"" in html
    cfg = _config_of(html)
    assert cfg["name"] == "trendGrain"
    assert cfg["default"] == "month"
    assert cfg["grains"] == ["day", "week", "month", "quarter", "year"]
    assert cfg["native"] is False
    # Nicely-labelled options (value=token, label=Capitalized).
    assert '<option value="month">Month</option>' in html
    assert '<option value="quarter">Quarter</option>' in html


def test_default_falls_back_to_month_then_first(ctx):
    # No explicit default, month present → month.
    assert _config_of(render_components('<TimeGrain name="g" />', ctx))["default"] == "month"
    # month absent → first grain.
    cfg = _config_of(render_components('<TimeGrain name="g" grains="hour,day" />', ctx))
    assert cfg["default"] == "hour"


def test_custom_grains_and_native(ctx):
    html = render_components('<TimeGrain name="g" grains="week,quarter" native />', ctx)
    cfg = _config_of(html)
    assert cfg["grains"] == ["week", "quarter"]
    assert cfg["native"] is True
    assert cfg["default"] == ""  # native → start ungrouped
    assert '<option value="">Native</option>' in html


def test_grains_are_lowercased_and_trimmed(ctx):
    cfg = _config_of(render_components('<TimeGrain name="g" grains="Day, MONTH " />', ctx))
    assert cfg["grains"] == ["day", "month"]


@pytest.mark.parametrize("markup", [
    '<TimeGrain name="g" grains="fortnight" />',     # not a canonical token
    '<TimeGrain name="g" grains="day,decade" />',    # one bad token in the list
    '<TimeGrain name="g" default="week" grains="month,quarter" />',  # default off-list
])
def test_invalid_grains_error(ctx, markup):
    out = render_components(markup, ctx)
    assert "Error rendering" in out


def test_label_defaults_to_grain(ctx):
    html = render_components('<TimeGrain name="g" />', ctx)
    assert '>Grain<span class="dashdown-filter-pill-colon">' in html


def test_omitted_in_static_build():
    """is_filter → stripped from a static build (can't re-query a snapshot)."""
    sctx = RenderContext(queries={}, params={}, current_path="/", static_build=True)
    out = render_components('<TimeGrain name="trendGrain" default="month" />', sctx)
    assert out.strip() == ""
