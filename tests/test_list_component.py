"""Tests for the authored ``<List />`` component — a pinned semantic *list*
(dimensions + order + limit, the ask engine's list rung as a component).

Pure render/attr tests need no deps (a lightweight fake handle drives the semantic
model catalog). The end-to-end render → data-API test needs the ``semantic`` extra
(BSL/Ibis), gated like tests/test_semantic.py.
"""
from __future__ import annotations

import html as html_mod
import json
import re
import shutil
from pathlib import Path

import pytest

import dashdown.components  # noqa: F401  (registers built-ins, incl. <List>)
from dashdown.components.base import RenderContext
from dashdown.render.components import render_components
from dashdown.semantic import SemanticModelHandle

EXAMPLE = Path(__file__).parent / "fixtures" / "semantic_first_class"

_bsl_installed = True
try:  # the semantic extra
    import boring_semantic_layer  # noqa: F401
    import ibis  # noqa: F401
except ImportError:  # pragma: no cover
    _bsl_installed = False

needs_bsl = pytest.mark.skipif(not _bsl_installed, reason="requires dashdown-md[semantic]")


def _fake_handle() -> SemanticModelHandle:
    dims = {"region", "status", "order_date", "customer"}
    measures = {"revenue", "orders"}
    return SemanticModelHandle(
        name="sales",
        connector="main",
        file_config={},
        table_connectors={"orders": "main"},
        profile=None,
        profile_path=None,
        measures=measures,
        dimensions=dims,
        time_dimension="order_date",
        measure_formats={},
        dim_lookup={d: d for d in dims},
        measure_lookup={m: m for m in measures},
    )


def _render(markup: str, ctx: RenderContext | None = None):
    """Render a <List> and return (parsed data-config | None, ctx, html)."""
    ctx = ctx or RenderContext(
        queries={}, params={}, current_path="/",
        semantic_models={"sales": _fake_handle()},
    )
    html = render_components(markup, ctx)
    m = re.search(r'data-config="([^"]*)"', html)
    config = json.loads(html_mod.unescape(m.group(1))) if m else None
    return config, ctx, html


# --------------------------------------------------------------------------- #
# Placeholder + ref recording (no BSL)
# --------------------------------------------------------------------------- #
def test_emits_table_placeholder_and_records_ref():
    config, ctx, html = _render(
        '<List model="sales" columns="region, status, order_date" '
        'order_by="order_date" desc limit=10 title="Latest" />'
    )
    # Renders the shared <Table> placeholder so table.js hydrates it.
    assert 'data-async-component="table"' in html
    assert config is not None
    qname = config["query_name"]
    assert qname.startswith("_semlist.sales.")
    assert config["title"] == "Latest"
    assert config["limit"] == 10
    # A SemanticListRef was recorded on ctx for the pipeline to compile.
    assert set(ctx.semantic_list_refs) == {qname}
    ref = ctx.semantic_list_refs[qname]
    assert ref.model == "sales"
    assert ref.columns == ("region", "status", "order_date")  # canonical
    assert ref.order_by == "order_date"
    assert ref.desc is True
    assert ref.limit == 10
    # data-query-name matches so table.js/export target the synthetic query.
    assert f'data-query-name="{qname}"' in html


def test_query_name_is_deterministic_and_matches_helper():
    from dashdown.semantic import semantic_list_query_name

    config, _ctx, _html = _render(
        '<List model="sales" columns="region, order_date" order_by="order_date" limit=25 />'
    )
    expected = semantic_list_query_name(
        "sales", ("region", "order_date"), "order_date", True, 25
    )
    assert config["query_name"] == expected


# --------------------------------------------------------------------------- #
# Attr defaults + coercion
# --------------------------------------------------------------------------- #
def test_order_by_defaults_to_time_dimension_when_selected():
    _config, ctx, _html = _render(
        '<List model="sales" columns="region, order_date" limit=5 />'
    )
    ref = next(iter(ctx.semantic_list_refs.values()))
    assert ref.order_by == "order_date"  # time dimension, selected


def test_order_by_defaults_to_first_column_when_no_time_dim_selected():
    _config, ctx, _html = _render(
        '<List model="sales" columns="region, status" limit=5 />'
    )
    ref = next(iter(ctx.semantic_list_refs.values()))
    assert ref.order_by == "region"  # time dim not among columns → first column


def test_order_by_not_selected_falls_back():
    # order_by names a valid dim that isn't among columns → soft-fallback (not a
    # hard error), mirroring the ask engine's _validate_list.
    _config, ctx, _html = _render(
        '<List model="sales" columns="region, status" order_by="customer" limit=5 />'
    )
    ref = next(iter(ctx.semantic_list_refs.values()))
    assert ref.order_by == "region"


def test_desc_defaults_true_and_can_be_disabled():
    _c1, ctx1, _h1 = _render('<List model="sales" columns="region" />')
    assert next(iter(ctx1.semantic_list_refs.values())).desc is True
    _c2, ctx2, _h2 = _render('<List model="sales" columns="region" desc=false />')
    assert next(iter(ctx2.semantic_list_refs.values())).desc is False


def test_limit_defaults_and_clamps():
    _c1, ctx1, _h1 = _render('<List model="sales" columns="region" />')
    assert next(iter(ctx1.semantic_list_refs.values())).limit == 50  # default
    _c2, ctx2, _h2 = _render('<List model="sales" columns="region" limit=9999 />')
    assert next(iter(ctx2.semantic_list_refs.values())).limit == 500  # clamped high
    _c3, ctx3, _h3 = _render('<List model="sales" columns="region" limit=0 />')
    assert next(iter(ctx3.semantic_list_refs.values())).limit == 1  # clamped low


def test_columns_dedupe_and_cap():
    _config, ctx, _html = _render(
        '<List model="sales" columns="region, region, status" limit=5 />'
    )
    ref = next(iter(ctx.semantic_list_refs.values()))
    assert ref.columns == ("region", "status")  # deduped


# --------------------------------------------------------------------------- #
# Error cards (invalid names → the page still renders)
# --------------------------------------------------------------------------- #
def test_unknown_model_is_error_card():
    config, ctx, html = _render('<List model="ghost" columns="region" />')
    assert config is None
    assert "error" in html.lower() and "ghost" in html.lower()
    assert ctx.semantic_list_refs == {}


def test_unknown_column_is_error_card():
    config, ctx, html = _render('<List model="sales" columns="region, phantom" />')
    assert config is None
    assert "error" in html.lower() and "phantom" in html.lower()
    assert ctx.semantic_list_refs == {}


def test_missing_model_and_columns_are_error_cards():
    _c1, _ctx1, h1 = _render('<List columns="region" />')
    assert "error" in h1.lower()
    _c2, _ctx2, h2 = _render('<List model="sales" />')
    assert "error" in h2.lower()


# --------------------------------------------------------------------------- #
# Catalog introspection — the attrs surface in `dashdown components`
# --------------------------------------------------------------------------- #
def test_component_attrs_are_introspectable():
    from dashdown.catalog import build_component_catalog

    row = next(r for r in build_component_catalog() if r["name"] == "List")
    for attr in ("model", "columns", "order_by", "desc", "limit", "title"):
        assert attr in row["attrs"], row["attrs"]
    assert row["summary"].startswith("Detail list")


# --------------------------------------------------------------------------- #
# End-to-end: render a page → spec registered → data API serves ordered rows
# --------------------------------------------------------------------------- #
@pytest.fixture
def example_project(tmp_path):
    dst = tmp_path / "semantic_proj"
    shutil.copytree(
        EXAMPLE, dst,
        ignore=shutil.ignore_patterns("__pycache__", "*.duckdb*", "sources.yaml"),
    )
    (dst / "sources.yaml").write_text("main:\n  type: csv\n  directory: data\n")
    return dst


@pytest.fixture(autouse=True)
def _clear_caches():
    from dashdown.render import pipeline

    def _clear():
        pipeline._query_def_cache.clear()
        pipeline._result_cache.clear()
        pipeline._python_def_cache.clear()
        pipeline._stream_def_cache.clear()
        pipeline._library_keys.clear()
        pipeline._python_library_keys.clear()

    _clear()
    yield
    _clear()


@needs_bsl
def test_render_registers_list_spec_and_client_defs(example_project):
    from dashdown.project import load_project
    from dashdown.render.pipeline import get_python_query_def, render_page

    proj = load_project(example_project)
    src = (
        "# t\n\n"
        '<List model="sales" columns="region, status, order_date" '
        'order_by="order_date" desc limit=5 title="Recent" />\n'
    )
    rp = render_page(src, proj.connectors, semantic_models=proj.semantic_models)
    # The synthetic list query reached the client defs…
    name = next(n for n in rp.query_defs if n.startswith("_semlist.sales."))
    connector = rp.query_defs[name]["connector"]
    assert connector == "main"
    # …with the model's filterable params for the "filtered by" badge.
    assert "region" in rp.query_defs[name]["params"]
    # …and the spec is registered on the shared python-def cache (data API / build).
    assert get_python_query_def(name, connector) is not None
    # The model expressions never reach the client.
    assert "amount.sum" not in rp.body_html


@needs_bsl
def test_data_api_serves_ordered_limited_list(example_project):
    from fastapi.testclient import TestClient

    from dashdown.server import create_app

    project_root = example_project
    page = project_root / "pages" / "list_page.md"
    page.write_text(
        '# List page\n\n'
        '<List model="sales" columns="region, order_date" '
        'order_by="order_date" desc limit=3 />\n',
        encoding="utf-8",
    )
    client = TestClient(create_app(project_root))
    html = client.get("/list_page")
    assert html.status_code == 200
    name = next(iter(set(re.findall(r"_semlist\.sales\.[0-9a-f]+", html.text))))

    data = client.get(f"/_dashdown/api/data/{name}", params={"_connector": "main"})
    assert data.status_code == 200, data.text
    body = data.json()
    assert 0 < len(body["rows"]) <= 3  # limit applied
    date_col = next(c for c in body["columns"] if c.split(".")[-1] == "order_date")
    dates = [row[body["columns"].index(date_col)] for row in body["rows"]]
    assert dates == sorted(dates, reverse=True)  # ordered desc by the time dimension


@needs_bsl
def test_invalid_list_renders_error_card_but_page_survives(example_project):
    from dashdown.project import load_project
    from dashdown.render.pipeline import render_page

    proj = load_project(example_project)
    src = (
        "# t\n\n"
        '<List model="sales" columns="ghost" />\n\n'
        "Body text still here.\n"
    )
    rp = render_page(src, proj.connectors, semantic_models=proj.semantic_models)
    assert "error" in rp.body_html.lower()
    assert "Body text still here." in rp.body_html
    assert not any(n.startswith("_semlist.") for n in rp.query_defs)
