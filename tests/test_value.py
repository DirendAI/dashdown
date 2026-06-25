"""Tests for the Value component — single-value config + display formatting."""
import html
import json
import re

import pytest

import dashdown.components  # noqa: F401  (registers built-ins)
from dashdown.components.base import RenderContext
from dashdown.render.components import render_components


@pytest.fixture
def ctx():
    return RenderContext(queries={}, params={}, current_path="/")


def _config(rendered: str) -> dict:
    """Extract and decode the data-config JSON from a value's HTML."""
    m = re.search(r'data-config="([^"]*)"', rendered)
    assert m, f"no data-config in: {rendered}"
    return json.loads(html.unescape(m.group(1)))


def test_value_basic_config(ctx):
    cfg = _config(render_components('<Value data={kpis} column="price" />', ctx))
    assert cfg["query_name"] == "kpis"
    assert cfg["column"] == "price"
    # Prefix/suffix default to empty strings; no format keys without attrs.
    assert cfg["prefix"] == ""
    assert cfg["suffix"] == ""
    assert "format" not in cfg
    assert "decimals" not in cfg


def test_value_format_attrs(ctx):
    cfg = _config(
        render_components(
            '<Value data={kpis} column="price" format="currency" '
            'currency="$" decimals=2 prefix="≈" suffix=" USD" />',
            ctx,
        )
    )
    assert cfg["format"] == "currency"
    assert cfg["currency"] == "$"
    assert cfg["decimals"] == 2
    assert cfg["prefix"] == "≈"
    assert cfg["suffix"] == " USD"


def test_value_date_format(ctx):
    cfg = _config(
        render_components(
            '<Value data={events} column="day" format="date" '
            'date_format="MMM D, YYYY" />',
            ctx,
        )
    )
    assert cfg["format"] == "date"
    assert cfg["date_format"] == "MMM D, YYYY"


def test_value_requires_data(ctx):
    html_out = render_components("<Value />", ctx)
    assert "Value requires data" in html_out
