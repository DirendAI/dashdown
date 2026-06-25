"""Tests for the PivotTable component (Stage 9)."""
import json

import pytest

import dashdown.components  # noqa: F401  (registers built-ins)
from dashdown.components.base import RenderContext, get_component
from dashdown.render.components import render_components


@pytest.fixture
def ctx():
    return RenderContext(queries={}, params={}, current_path="/")


def _config_of(html: str) -> dict:
    import html as html_mod
    import re

    m = re.search(r'data-config="([^"]*)"', html)
    assert m, f"no data-config found in: {html}"
    return json.loads(html_mod.unescape(m.group(1)))


def test_pivot_registered():
    assert get_component("PivotTable") is not None


def test_pivot_renders_async_placeholder(ctx):
    html = render_components(
        '<PivotTable data={orders} rows="region" cols="category" '
        'values="amount" title="Revenue" />',
        ctx,
    )
    assert 'data-async-component="pivot"' in html
    assert 'data-query-name="orders"' in html
    config = _config_of(html)
    assert config["query_name"] == "orders"
    assert config["rows"] == ["region"]
    assert config["cols"] == ["category"]
    assert config["values"] == "amount"
    assert config["agg"] == "sum"
    assert config["title"] == "Revenue"


def test_pivot_multi_field_axes(ctx):
    config = _config_of(
        render_components(
            '<PivotTable data={orders} rows="region, rep" cols="category" />', ctx
        )
    )
    assert config["rows"] == ["region", "rep"]


def test_pivot_axes_default_empty(ctx):
    # rows/cols/values are optional; the client seeds them from the data shape.
    config = _config_of(render_components("<PivotTable data={orders} />", ctx))
    assert config["rows"] == []
    assert config["cols"] == []
    assert config["values"] is None


@pytest.mark.parametrize("agg", ["sum", "avg", "count", "min", "max"])
def test_pivot_accepts_known_aggs(ctx, agg):
    config = _config_of(
        render_components(f'<PivotTable data={{orders}} agg="{agg}" />', ctx)
    )
    assert config["agg"] == agg


def test_pivot_rejects_unknown_agg(ctx):
    html = render_components('<PivotTable data={orders} agg="median" />', ctx)
    assert "error" in html.lower()


def test_pivot_requires_data(ctx):
    html = render_components('<PivotTable rows="a" />', ctx)
    assert "error" in html.lower()
