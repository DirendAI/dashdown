"""Tests for the Combobox (searchable single-select filter) component.

Locks the server-rendered placeholder/config the client JS reads, the required
data/column attrs, the `is_filter` static-build stripping, and that the value
flows through the test-locked `_substitute_params` like every other filter. The
server-side options endpoint (the only new SQL surface) is locked separately by
`test_pipeline.py::TestBuildOptionsSql`.
"""
import html as html_mod
import json
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import dashdown.components  # noqa: F401  (registers built-ins)
from dashdown.components.base import RenderContext, get_component
from dashdown.render.components import render_components
from dashdown.render.pipeline import (
    _substitute_params,
    _query_def_cache,
    _result_cache,
)
from dashdown.server import create_app


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
def test_combobox_registered():
    assert get_component("Combobox") is not None


def test_combobox_is_filter():
    assert get_component("Combobox").is_filter is True


def test_requires_name(ctx):
    out = render_components('<Combobox data={customers} column="name" />', ctx)
    assert "Error rendering" in out
    assert "requires a `name`" in out


def test_requires_data(ctx):
    out = render_components('<Combobox name="customer" column="name" />', ctx)
    assert "Error rendering" in out
    assert "requires a `data=" in out


def test_requires_column(ctx):
    out = render_components('<Combobox name="customer" data={customers} />', ctx)
    assert "Error rendering" in out
    assert "requires a `column`" in out


# --------------------------------------------------------------------------- #
# default config + placeholder
# --------------------------------------------------------------------------- #
def test_default_config_and_markup(ctx):
    html = render_components(
        '<Combobox name="customer" data={customers} column="name" label="Customer" />',
        ctx,
    )
    assert 'data-async-component="combobox"' in html
    assert 'data-query-name="customers"' in html
    assert 'data-filter-name="customer"' in html
    assert 'data-url-sync="true"' in html
    assert 'role="combobox"' in html
    assert 'role="listbox"' in html

    cfg = _config_of(html)
    assert cfg["name"] == "customer"
    assert cfg["query_name"] == "customers"
    assert cfg["column"] == "name"
    assert cfg["label"] == "Customer"
    assert cfg["placeholder"] == "Search…"
    assert cfg["limit"] == 50
    assert cfg["min_chars"] == 0
    assert cfg["url_sync"] is True


def test_label_defaults_to_name(ctx):
    cfg = _config_of(
        render_components('<Combobox name="c" data={q} column="x" />', ctx)
    )
    assert cfg["label"] == "c"


def test_custom_placeholder_limit_minchars(ctx):
    cfg = _config_of(
        render_components(
            '<Combobox name="c" data={q} column="x" placeholder="Find a customer" '
            "limit={20} min_chars={2} />",
            ctx,
        )
    )
    assert cfg["placeholder"] == "Find a customer"
    assert cfg["limit"] == 20
    assert cfg["min_chars"] == 2


def test_url_sync_false(ctx):
    html = render_components(
        '<Combobox name="c" data={q} column="x" url_sync=false />', ctx
    )
    assert 'data-url-sync="false"' in html
    assert _config_of(html)["url_sync"] is False


# --------------------------------------------------------------------------- #
# multi-select markup + config
# --------------------------------------------------------------------------- #
def test_single_by_default_has_no_chips(ctx):
    html = render_components('<Combobox name="c" data={q} column="x" />', ctx)
    assert _config_of(html)["multi"] is False
    assert "dashdown-combobox-multi" not in html
    assert "dashdown-combobox-chips" not in html
    assert 'aria-multiselectable="false"' in html


def test_multi_config_and_markup(ctx):
    html = render_components('<Combobox name="c" data={q} column="x" multi />', ctx)
    assert _config_of(html)["multi"] is True
    assert "dashdown-combobox-multi" in html
    assert "dashdown-combobox-chips" in html
    assert 'aria-multiselectable="true"' in html


# --------------------------------------------------------------------------- #
# placement
# --------------------------------------------------------------------------- #
def test_inline_by_default(ctx):
    assert "data-filter-bar" not in render_components(
        '<Combobox name="c" data={q} column="x" />', ctx
    )


def test_bar_relocates_to_filter_bar(ctx):
    html = render_components('<Combobox name="c" data={q} column="x" bar />', ctx)
    assert 'data-filter-bar="true"' in html


# --------------------------------------------------------------------------- #
# is_filter → stripped from a static build
# --------------------------------------------------------------------------- #
def test_omitted_in_static_build():
    sctx = RenderContext(queries={}, params={}, current_path="/", static_build=True)
    out = render_components('<Combobox name="c" data={q} column="x" />', sctx)
    assert out.strip() == ""


# --------------------------------------------------------------------------- #
# the value reaches SQL only via the test-locked _substitute_params
# --------------------------------------------------------------------------- #
class TestSubstitutionSemantics:
    GUARD_SQL = "WHERE '${customer}' = '' OR customer = '${customer}'"

    def test_empty_trips_all_guard(self):
        out = _substitute_params(self.GUARD_SQL, {"customer": ""})
        assert out == "WHERE '' = '' OR customer = ''"

    def test_selection_filters(self):
        out = _substitute_params(self.GUARD_SQL, {"customer": "Acme"})
        assert out == "WHERE 'Acme' = '' OR customer = 'Acme'"

    def test_value_is_inert_string_literal(self):
        out = _substitute_params(self.GUARD_SQL, {"customer": "x' OR '1'='1"})
        assert out == "WHERE 'x'' OR ''1''=''1' = '' OR customer = 'x'' OR ''1''=''1'"


class TestMultiSubstitution:
    """A multi Combobox stores a comma-joined value feeding ``IN (…)`` — the
    same expansion a multi Dropdown uses, so the per-item escaping is identical."""

    IN_SQL = "WHERE '${customers}' = '' OR customer IN (${customers})"

    def test_multiple_values_expand_to_quoted_list(self):
        out = _substitute_params(self.IN_SQL, {"customers": "Acme,Beta"})
        assert out == "WHERE 'Acme,Beta' = '' OR customer IN ('Acme', 'Beta')"

    def test_empty_trips_all_guard_and_in_null(self):
        out = _substitute_params(self.IN_SQL, {"customers": ""})
        assert out == "WHERE '' = '' OR customer IN (NULL)"

    def test_each_item_is_escaped(self):
        out = _substitute_params(self.IN_SQL, {"customers": "O'Brien,Acme"})
        assert "IN ('O''Brien', 'Acme')" in out


# --------------------------------------------------------------------------- #
# end-to-end: the /api/options endpoint feeds the searchable filter
# --------------------------------------------------------------------------- #
def _make_project(tmp: Path) -> Path:
    (tmp / "pages").mkdir(parents=True)
    (tmp / "pages" / "index.md").write_text(
        "# Customers\n\n"
        ":::query name=customers connector=main\n"
        "SELECT name, region FROM customers\n"
        ":::\n\n"
        '<Combobox name="customer" data={customers} column="name" />\n\n'
        "<Table data={customers} />\n",
        encoding="utf-8",
    )
    (tmp / "data").mkdir()
    # Duplicates so DISTINCT matters; an apostrophe name so escaping is exercised.
    (tmp / "data" / "customers.csv").write_text(
        "name,region\n"
        "Acme,East\nAcme,West\nAcorn,East\nBeta,West\nO'Brien,East\n",
        encoding="utf-8",
    )
    (tmp / "sources.yaml").write_text(
        "main:\n  type: csv\n  directory: data\n", encoding="utf-8"
    )
    (tmp / "dashdown.yaml").write_text("title: Test\n", encoding="utf-8")
    return tmp


@pytest.fixture
def client(tmp_path):
    _query_def_cache.clear()
    _result_cache.clear()
    c = TestClient(create_app(_make_project(tmp_path)))
    c.get("/")  # render registers the query def into the global cache
    yield c
    _query_def_cache.clear()
    _result_cache.clear()


class TestOptionsEndpoint:
    def test_returns_distinct_sorted_values(self, client):
        r = client.get("/_dashdown/api/options/customers?_column=name&_connector=main")
        assert r.status_code == 200
        opts = r.json()["options"]
        # DISTINCT collapses the two Acme rows; ORDER BY value sorts.
        assert opts == ["Acme", "Acorn", "Beta", "O'Brien"]

    def test_search_narrows_case_insensitively(self, client):
        r = client.get(
            "/_dashdown/api/options/customers?_column=name&_search=ac&_connector=main"
        )
        assert r.status_code == 200
        assert r.json()["options"] == ["Acme", "Acorn"]

    def test_search_with_apostrophe_is_safe_and_matches(self, client):
        r = client.get(
            "/_dashdown/api/options/customers",
            params={"_column": "name", "_search": "O'Br", "_connector": "main"},
        )
        assert r.status_code == 200
        assert r.json()["options"] == ["O'Brien"]

    def test_injection_in_search_returns_no_rows_not_error(self, client):
        # A break-out attempt is escaped to a literal — matches nothing, no 500.
        r = client.get(
            "/_dashdown/api/options/customers",
            params={"_column": "name", "_search": "x' OR '1'='1", "_connector": "main"},
        )
        assert r.status_code == 200
        assert r.json()["options"] == []

    def test_bad_column_is_rejected(self, client):
        r = client.get(
            "/_dashdown/api/options/customers",
            params={"_column": "name); DROP TABLE customers; --", "_connector": "main"},
        )
        assert r.status_code == 400

    def test_unknown_query_404(self, client):
        r = client.get("/_dashdown/api/options/nope?_column=name&_connector=main")
        assert r.status_code == 404

    def test_limit_is_honored(self, client):
        r = client.get(
            "/_dashdown/api/options/customers?_column=name&_limit=2&_connector=main"
        )
        assert r.status_code == 200
        assert r.json()["options"] == ["Acme", "Acorn"]
