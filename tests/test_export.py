"""Tests for the per-table CSV export affordance.

The CSV serialization + download live in static/components/export.js (`toCsv` /
`exportQueryCsv`) and the settings dialog in export_modal.js; both run in the
browser. Here we lock the server-rendered table config the client JS reads to
decide whether to show the export button and how to name the file.

(The old standalone `<ExportButton>` component was removed in favor of this; see
the showcase demo and README.)
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


def test_export_button_component_removed():
    """The standalone <ExportButton> is gone — export now lives on <Table>."""
    assert get_component("ExportButton") is None


def test_table_export_on_by_default(ctx):
    html = render_components('<Table data={sales} title="Sales" />', ctx)
    assert _config_of(html)["export"] is True


def test_table_export_opt_out(ctx):
    html = render_components('<Table data={sales} export=false />', ctx)
    assert "export" not in _config_of(html)


def test_table_export_filename(ctx):
    html = render_components(
        '<Table data={sales} export_filename="orders.csv" />', ctx
    )
    assert _config_of(html)["export_filename"] == "orders.csv"


def test_table_export_filename_falls_back_to_filename_attr(ctx):
    html = render_components('<Table data={sales} filename="x.csv" />', ctx)
    assert _config_of(html)["export_filename"] == "x.csv"
