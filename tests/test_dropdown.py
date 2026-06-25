"""Tests for the Dropdown component, focused on multi-select rendering.

The runtime behaviour of multi-select (comma-joined store value, `IN (...)`
expansion) is covered by tests/test_pipeline.py::TestInListExpansion; here we
lock the server-rendered placeholder/config the client JS reads.
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


def test_dropdown_registered():
    assert get_component("Dropdown") is not None


def test_single_select_unchanged(ctx):
    """Default (no multi) keeps the single-line select with an All option."""
    html = render_components(
        '<Dropdown name="region" data={regions} column="region" label="Region" />',
        ctx,
    )
    assert "multiple" not in html
    assert "dashdown-filter-pill-multi" not in html
    cfg = _config_of(html)
    assert cfg["multi"] is False
    assert cfg["include_all"] is True


def test_async_multi_select(ctx):
    """Multi-select renders the button + checkmark-popover widget, not a
    native <select multiple>; options load client-side from the query."""
    html = render_components(
        '<Dropdown name="region" data={regions} column="region" label="Region" multi />',
        ctx,
    )
    assert "multiple" not in html
    assert "dashdown-multiselect" in html
    assert "dashdown-multiselect-trigger" in html
    assert "dashdown-multiselect-panel" in html
    assert 'data-async-component="dropdown"' in html
    cfg = _config_of(html)
    assert cfg["multi"] is True
    # No "All" option in multi mode — an empty selection already means all.
    assert cfg["include_all"] is False
    assert cfg["query_name"] == "regions"
    assert cfg["column"] == "region"
    # No options loaded server-side — they're fetched client-side.
    assert "options" not in cfg


def test_explicit_options_multi_select(ctx):
    """Explicit options + multi carries the options in the config (the JS
    popover renders them) — no server-side <option> tags or <select multiple>."""
    html = render_components(
        '<Dropdown name="tags" options="alpha,beta,gamma" multi />', ctx
    )
    assert "multiple" not in html
    assert "<option" not in html
    assert "dashdown-multiselect-panel" in html
    assert 'data-async-component="dropdown"' in html
    cfg = _config_of(html)
    assert cfg["multi"] is True
    assert cfg["options"] == ["alpha", "beta", "gamma"]


def test_multi_select_shows_all_placeholder(ctx):
    """The multi-select button starts on its "All" placeholder (no selection =
    all), and config carries no include_all 'All' entry."""
    html = render_components(
        '<Dropdown name="tags" options="a,b" multi />', ctx
    )
    assert 'data-placeholder="All"' in html
    assert "dashdown-multiselect-summary" in html
    cfg = _config_of(html)
    assert cfg["include_all"] is False


def test_multi_requires_name(ctx):
    # Missing name → component error card, not a crash.
    html = render_components("<Dropdown multi />", ctx)
    assert "error" in html.lower()
