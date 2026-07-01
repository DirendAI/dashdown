"""Tests for the project-wide filter debounce (`filters.debounce`).

Every interactive filter control (Search / Combobox / Slider / RangeSlider /
DateRange) waits a quiet period after the last keystroke or slider drag before
committing its value to the store — the single reactive path data components
re-fetch off — so a burst of input coalesces into one fetch. The delay comes
from `filters.debounce` in `dashdown.yaml` (default 300), overridable per control
with a `debounce=` attr. These tests lock:

  * `parse_filters_config` validation,
  * `resolve_debounce` precedence (per-control attr > project default),
  * that each control bakes the resolved value into what the client reads
    (Search into `x-model.debounce`, the rest into `data-config`),
  * that `render_page` threads the project default onto the RenderContext.
"""
from __future__ import annotations

import html as html_mod
import json
import re
import tempfile
from pathlib import Path

import pytest

import dashdown.components  # noqa: F401  (registers built-ins)
from dashdown.components.base import RenderContext, get_component
from dashdown.components.builtin._util import resolve_debounce
from dashdown.project import FiltersConfig, parse_filters_config
from dashdown.render.attrs import DataRef
from dashdown.render.components import render_components
from dashdown.render.pipeline import render_page


def _config_of(html: str) -> dict:
    m = re.search(r'data-config="([^"]*)"', html)
    assert m, f"no data-config in: {html}"
    return json.loads(html_mod.unescape(m.group(1)))


# --------------------------------------------------------------------------- #
# Config parsing
# --------------------------------------------------------------------------- #
def test_parse_absent_block_defaults_300():
    assert parse_filters_config(None).debounce == 300
    assert parse_filters_config({}).debounce == 300


def test_parse_sets_debounce():
    assert parse_filters_config({"debounce": 500}).debounce == 500
    assert parse_filters_config({"debounce": 0}).debounce == 0


@pytest.mark.parametrize(
    "bad",
    [
        [1, 2],            # not a mapping
        "500",            # not a mapping
        {"debounce": -5},  # negative
        {"debounce": "x"},  # not an int
        {"debounce": 1.5},  # float, not int
        {"debounce": True},  # bool is not a valid int here
    ],
)
def test_parse_rejects_malformed(bad):
    with pytest.raises(ValueError):
        parse_filters_config(bad)


# --------------------------------------------------------------------------- #
# resolve_debounce precedence
# --------------------------------------------------------------------------- #
def test_resolve_uses_project_default_when_no_attr():
    ctx = RenderContext(queries={}, filter_debounce=500)
    assert resolve_debounce({}, ctx) == 500


def test_resolve_attr_overrides_project_default():
    ctx = RenderContext(queries={}, filter_debounce=500)
    assert resolve_debounce({"debounce": 800}, ctx) == 800
    assert resolve_debounce({"debounce": 0}, ctx) == 0  # 0 = fire immediately


def test_resolve_bad_attr_falls_back_to_default():
    ctx = RenderContext(queries={}, filter_debounce=400)
    assert resolve_debounce({"debounce": "nope"}, ctx) == 400
    assert resolve_debounce({"debounce": -20}, ctx) == 400


def test_render_context_default_is_300():
    assert RenderContext(queries={}).filter_debounce == 300


# --------------------------------------------------------------------------- #
# Each control bakes the resolved value in
# --------------------------------------------------------------------------- #
def _ctx(debounce: int = 500) -> RenderContext:
    return RenderContext(queries={}, current_path="/", filter_debounce=debounce)


def test_search_uses_project_default_in_x_model():
    html = render_components('<Search name="q" />', _ctx(500))
    assert 'x-model.debounce.500ms="' in html
    assert _config_of(html)["debounce"] == 500


def test_search_attr_overrides():
    html = render_components('<Search name="q" debounce={800} />', _ctx(500))
    assert 'x-model.debounce.800ms="' in html


def test_combobox_config_carries_debounce():
    ctx = _ctx(500)
    html = get_component("Combobox").render(
        {"name": "c", "data": DataRef("cust"), "column": "name"}, ctx
    )
    assert _config_of(html)["debounce"] == 500


def test_slider_config_carries_debounce():
    html = render_components('<Slider name="m" min={0} max={10} />', _ctx(500))
    assert _config_of(html)["debounce"] == 500


def test_slider_attr_override_zero():
    # 0 is a real value (fire immediately), not "unset".
    html = render_components('<Slider name="m" min={0} max={10} debounce={0} />', _ctx(500))
    assert _config_of(html)["debounce"] == 0


def test_range_slider_config_carries_debounce():
    html = render_components('<RangeSlider name="p" min={0} max={10} />', _ctx(500))
    assert _config_of(html)["debounce"] == 500


def test_date_range_config_carries_debounce():
    html = render_components('<DateRange name="d" />', _ctx(500))
    assert _config_of(html)["debounce"] == 500


# --------------------------------------------------------------------------- #
# render_page threads the project default onto the context
# --------------------------------------------------------------------------- #
PAGE = '# P\n\n<Search name="q" bar />\n'


def test_render_page_threads_debounce_default():
    rendered = render_page(PAGE, {}, filter_debounce=450)
    assert "x-model.debounce.450ms" in rendered.body_html


def test_render_page_defaults_to_300():
    rendered = render_page(PAGE, {})
    assert "x-model.debounce.300ms" in rendered.body_html


def test_filters_config_default_dataclass():
    assert FiltersConfig().debounce == 300


# --------------------------------------------------------------------------- #
# load_project round-trip
# --------------------------------------------------------------------------- #
def _write_project(root: Path, yaml_body: str) -> Path:
    (root / "pages").mkdir()
    (root / "data").mkdir()
    (root / "data" / "t.csv").write_text("a\n1\n", encoding="utf-8")
    (root / "sources.yaml").write_text(
        "main:\n  type: csv\n  directory: data\n", encoding="utf-8"
    )
    (root / "dashdown.yaml").write_text("title: T\n" + yaml_body, encoding="utf-8")
    (root / "pages" / "index.md").write_text("# Home\n", encoding="utf-8")
    return root


def test_load_project_reads_filters_debounce():
    from dashdown.project import load_project

    with tempfile.TemporaryDirectory() as d:
        proj = _write_project(Path(d), "filters:\n  debounce: 500\n")
        assert load_project(proj).config.filters.debounce == 500


def test_load_project_defaults_debounce_when_absent():
    from dashdown.project import load_project

    with tempfile.TemporaryDirectory() as d:
        proj = _write_project(Path(d), "")
        assert load_project(proj).config.filters.debounce == 300


def test_load_project_refuses_malformed_filters_block():
    # A malformed config must fail at load, so the server never starts half-broken
    # (same policy as auth/branding/embed).
    from dashdown.project import load_project

    with tempfile.TemporaryDirectory() as d:
        proj = _write_project(Path(d), "filters:\n  debounce: -10\n")
        with pytest.raises(ValueError):
            load_project(proj)
