"""Tests for the Toggle (boolean filter) component (Stage 21).

Locks the server-rendered placeholder/config the client JS reads, the
on/off-value semantics (incl. arbitrary strings like Yes/No), the `is_filter`
static-build stripping, and that the value flows through the test-locked
`_substitute_params` exactly like every other filter (no new injection path).
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


def _attr(html: str, attr: str) -> str:
    m = re.search(attr + r'="([^"]*)"', html)
    assert m, f"no {attr} in: {html}"
    return html_mod.unescape(m.group(1))


# --------------------------------------------------------------------------- #
# registration + required attrs
# --------------------------------------------------------------------------- #
def test_toggle_registered():
    assert get_component("Toggle") is not None


def test_toggle_is_filter():
    assert get_component("Toggle").is_filter is True


def test_requires_name(ctx):
    # The error is caught by render_components and surfaced as an inline card.
    out = render_components("<Toggle />", ctx)
    assert "Error rendering" in out
    assert "requires a `name`" in out


# --------------------------------------------------------------------------- #
# default config + placeholder
# --------------------------------------------------------------------------- #
def test_default_config_and_markup(ctx):
    html = render_components('<Toggle name="paid" label="Paid only" />', ctx)
    assert 'data-async-component="toggle"' in html
    assert 'data-name="paid"' in html
    assert 'data-url-sync="true"' in html
    # DaisyUI switch class (already in the vendored bundle → no rebuild).
    assert "toggle" in html and "dashdown-toggle-input" in html
    cfg = _config_of(html)
    assert cfg["name"] == "paid"
    assert cfg["label"] == "Paid only"
    assert cfg["on_value"] == "true"      # default on value
    assert cfg["off_value"] == ""         # default off = empty (all-guard)
    assert cfg["default"] is False        # starts off
    assert cfg["variant"] == "switch"
    assert cfg["url_sync"] is True


def test_label_defaults_to_name(ctx):
    cfg = _config_of(render_components('<Toggle name="archived" />', ctx))
    assert cfg["label"] == "archived"


# --------------------------------------------------------------------------- #
# the :checked / @change binding reflects the on/off values
# --------------------------------------------------------------------------- #
def test_binding_reflects_on_off_values(ctx):
    html = render_components('<Toggle name="paid" />', ctx)
    checked = _attr(html, ":checked")
    change = _attr(html, "@change")
    # store -> checkbox: checked iff the store holds the on value.
    assert checked == "$store.filters['paid'] === 'true'"
    # checkbox -> store: write on/off value on change.
    assert change == (
        "$store.filters['paid'] = $event.target.checked ? 'true' : ''"
    )


# --------------------------------------------------------------------------- #
# arbitrary on/off strings (Yes/No), not just true/false
# --------------------------------------------------------------------------- #
def test_custom_on_off_values_yes_no(ctx):
    html = render_components(
        '<Toggle name="open" on_value="Yes" off_value="No" />', ctx
    )
    cfg = _config_of(html)
    assert cfg["on_value"] == "Yes"
    assert cfg["off_value"] == "No"
    # both directions emit a value (two-state), per the inline binding
    assert _attr(html, "@change") == (
        "$store.filters['open'] = $event.target.checked ? 'Yes' : 'No'"
    )
    assert _attr(html, ":checked") == "$store.filters['open'] === 'Yes'"


def test_off_value_false_two_state(ctx):
    cfg = _config_of(
        render_components('<Toggle name="paid" off_value="false" />', ctx)
    )
    assert cfg["on_value"] == "true"
    assert cfg["off_value"] == "false"


def test_on_off_values_with_special_chars_are_attribute_safe(ctx):
    # A value with a double-quote / ampersand must not break the attribute.
    html = render_components(
        '<Toggle name="q" on_value=\'a"b&c\' off_value="x" />', ctx
    )
    # Unescaped, the inline expr is well-formed JS with the literal value.
    assert _attr(html, "@change") == (
        "$store.filters['q'] = $event.target.checked ? 'a\"b&c' : 'x'"
    )
    # Raw HTML keeps the attribute delimiter intact (the " is entity-escaped).
    assert 'a"b&c' not in html  # the raw double-quote never appears unescaped


# --------------------------------------------------------------------------- #
# default + variant + url_sync attrs
# --------------------------------------------------------------------------- #
def test_default_true_seeds_on(ctx):
    cfg = _config_of(render_components('<Toggle name="paid" default />', ctx))
    assert cfg["default"] is True


def test_variant_checkbox(ctx):
    html = render_components('<Toggle name="paid" variant="checkbox" />', ctx)
    cfg = _config_of(html)
    assert cfg["variant"] == "checkbox"
    assert "checkbox dashdown-toggle-input" in html


def test_url_sync_false(ctx):
    html = render_components('<Toggle name="paid" url_sync=false />', ctx)
    assert 'data-url-sync="false"' in html
    assert _config_of(html)["url_sync"] is False


def test_inline_by_default(ctx):
    # Inline by default (and on legacy filter_bar=false): no routing marker.
    assert "data-filter-bar" not in render_components('<Toggle name="paid" />', ctx)
    assert "data-filter-bar" not in render_components(
        '<Toggle name="paid" filter_bar=false />', ctx
    )


def test_bar_relocates_to_filter_bar(ctx):
    # `bar` opts the toggle into the top filter bar (now honored consistently —
    # Toggle was previously absent from filter_bar.js's selector list).
    html = render_components('<Toggle name="paid" bar />', ctx)
    assert 'data-filter-bar="true"' in html


# --------------------------------------------------------------------------- #
# is_filter → stripped from a static build (can't re-query a snapshot)
# --------------------------------------------------------------------------- #
def test_omitted_in_static_build():
    sctx = RenderContext(queries={}, params={}, current_path="/", static_build=True)
    out = render_components('<Toggle name="paid" label="Paid only" />', sctx)
    assert out.strip() == ""


# --------------------------------------------------------------------------- #
# the value reaches SQL only via the test-locked _substitute_params
# --------------------------------------------------------------------------- #
class TestSubstitutionSemantics:
    # All-guard: the toggle value is only the on/off *sentinel*; the condition is
    # fixed (`is_paid = TRUE`), so only the guard clause carries a placeholder.
    GUARD_SQL = "WHERE '${paid}' = '' OR is_paid = TRUE"

    def test_on_disables_the_guard(self):
        # on → 'true' != '' → guard false → the fixed condition applies.
        out = _substitute_params(self.GUARD_SQL, {"paid": "true"})
        assert out == "WHERE 'true' = '' OR is_paid = TRUE"

    def test_off_empty_trips_all_guard(self):
        # off → empty value → '' = '' is true → show all.
        out = _substitute_params(self.GUARD_SQL, {"paid": ""})
        assert out == "WHERE '' = '' OR is_paid = TRUE"

    def test_missing_param_also_trips_all_guard(self):
        # No URL param at all behaves like the empty off value.
        out = _substitute_params(self.GUARD_SQL, {})
        assert out == "WHERE '' = '' OR is_paid = TRUE"

    def test_yes_no_two_state(self):
        # Arbitrary string column: both directions filter.
        sql = "WHERE status = ${open}"
        assert _substitute_params(sql, {"open": "Yes"}) == "WHERE status = 'Yes'"
        assert _substitute_params(sql, {"open": "No"}) == "WHERE status = 'No'"

    def test_value_is_inert_string_literal(self):
        # Even a crafted value is a quoted literal — no new injection path.
        sql = "WHERE is_paid = ${paid}"
        out = _substitute_params(sql, {"paid": "1 OR 1=1"})
        assert out == "WHERE is_paid = '1 OR 1=1'"
