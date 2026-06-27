"""Tests for the ButtonGroup (segmented single-select filter) component.

Locks the server-rendered placeholder/config the client JS reads, the inline
Alpine click/active bindings, the include_all / default semantics, the
`is_filter` static-build stripping, and that the value flows through the
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
def test_button_group_registered():
    assert get_component("ButtonGroup") is not None


def test_button_group_is_filter():
    assert get_component("ButtonGroup").is_filter is True


def test_requires_name(ctx):
    out = render_components('<ButtonGroup options="A,B" />', ctx)
    assert "Error rendering" in out
    assert "requires a `name`" in out


def test_requires_options(ctx):
    out = render_components('<ButtonGroup name="status" />', ctx)
    assert "Error rendering" in out
    assert "requires an `options`" in out


# --------------------------------------------------------------------------- #
# default config + placeholder
# --------------------------------------------------------------------------- #
def test_default_config_and_markup(ctx):
    html = render_components(
        '<ButtonGroup name="status" label="Status" options="Active,Churned" />', ctx
    )
    assert 'data-async-component="buttongroup"' in html
    assert 'data-name="status"' in html
    assert 'data-url-sync="true"' in html
    assert 'role="radiogroup"' in html
    # include_all (default) adds an "All" segment + the two options = 3 buttons.
    assert html.count('role="radio"') == 3
    assert ">All</button>" in html
    assert ">Active</button>" in html
    assert ">Churned</button>" in html

    cfg = _config_of(html)
    assert cfg["name"] == "status"
    assert cfg["label"] == "Status"
    assert cfg["options"] == ["Active", "Churned"]
    assert cfg["include_all"] is True
    assert cfg["default"] == ""
    assert cfg["url_sync"] is True


def test_label_defaults_to_name(ctx):
    cfg = _config_of(render_components('<ButtonGroup name="seg" options="A,B" />', ctx))
    assert cfg["label"] == "seg"


def test_options_array_literal(ctx):
    cfg = _config_of(
        render_components('<ButtonGroup name="s" options={[Active, Churned]} />', ctx)
    )
    assert cfg["options"] == ["Active", "Churned"]


# --------------------------------------------------------------------------- #
# inline Alpine bindings (store is the source of truth)
# --------------------------------------------------------------------------- #
def test_segment_click_and_active_bindings(ctx):
    html = render_components('<ButtonGroup name="status" options="Active" />', ctx)
    # A real option writes its value on click and is active iff store === value.
    assert "$store.filters['status'] = 'Active'" in html_mod.unescape(html)
    assert "$store.filters['status'] === 'Active'" in html_mod.unescape(html)
    # The "All" segment clears the value and is active when nothing is set.
    assert "$store.filters['status'] = ''" in html_mod.unescape(html)
    assert "!$store.filters['status']" in html_mod.unescape(html)


def test_no_all_segment_when_disabled(ctx):
    html = render_components(
        '<ButtonGroup name="s" options="A,B" include_all=false />', ctx
    )
    cfg = _config_of(html)
    assert cfg["include_all"] is False
    assert html.count('role="radio"') == 2  # no "All"
    assert ">All</button>" not in html


def test_custom_all_label(ctx):
    html = render_components(
        '<ButtonGroup name="s" options="A,B" all_label="Any" />', ctx
    )
    assert ">Any</button>" in html


def test_default_value(ctx):
    cfg = _config_of(
        render_components('<ButtonGroup name="s" options="A,B" default="B" />', ctx)
    )
    assert cfg["default"] == "B"


def test_url_sync_false(ctx):
    html = render_components('<ButtonGroup name="s" options="A,B" url_sync=false />', ctx)
    assert 'data-url-sync="false"' in html
    assert _config_of(html)["url_sync"] is False


# --------------------------------------------------------------------------- #
# special chars in an option stay attribute-safe
# --------------------------------------------------------------------------- #
def test_option_with_special_chars_is_attribute_safe(ctx):
    html = render_components('<ButtonGroup name="q" options=\'a"b&c\' />', ctx)
    # Unescaped, the inline expr is well-formed JS with the literal value.
    assert "$store.filters['q'] = 'a\"b&c'" in html_mod.unescape(html)
    # Raw HTML keeps the attribute delimiter intact (the " is entity-escaped).
    assert 'a"b&c' not in html


# --------------------------------------------------------------------------- #
# placement
# --------------------------------------------------------------------------- #
def test_inline_by_default(ctx):
    assert "data-filter-bar" not in render_components(
        '<ButtonGroup name="s" options="A,B" />', ctx
    )


def test_bar_relocates_to_filter_bar(ctx):
    html = render_components('<ButtonGroup name="s" options="A,B" bar />', ctx)
    assert 'data-filter-bar="true"' in html


# --------------------------------------------------------------------------- #
# is_filter → stripped from a static build
# --------------------------------------------------------------------------- #
def test_omitted_in_static_build():
    sctx = RenderContext(queries={}, params={}, current_path="/", static_build=True)
    out = render_components('<ButtonGroup name="s" options="A,B" />', sctx)
    assert out.strip() == ""


# --------------------------------------------------------------------------- #
# the value reaches SQL only via the test-locked _substitute_params
# --------------------------------------------------------------------------- #
class TestSubstitutionSemantics:
    GUARD_SQL = "WHERE '${status}' = '' OR status = '${status}'"

    def test_all_trips_the_guard(self):
        # "All" → empty value → '' = '' true → show every row.
        out = _substitute_params(self.GUARD_SQL, {"status": ""})
        assert out == "WHERE '' = '' OR status = ''"

    def test_selection_filters(self):
        out = _substitute_params(self.GUARD_SQL, {"status": "Active"})
        assert out == "WHERE 'Active' = '' OR status = 'Active'"

    def test_value_is_inert_string_literal(self):
        out = _substitute_params(self.GUARD_SQL, {"status": "x' OR '1'='1"})
        assert out == "WHERE 'x'' OR ''1''=''1' = '' OR status = 'x'' OR ''1''=''1'"
