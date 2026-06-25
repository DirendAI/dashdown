"""Tests for the Grid layout component, col-span, and chart height (Task 3)."""
import pytest

import dashdown.components  # noqa: F401  (registers built-ins)
from dashdown.components.base import RenderContext, get_component
from dashdown.render.components import render_components


@pytest.fixture
def ctx():
    return RenderContext(queries={}, params={}, current_path="/")


def test_grid_registered():
    assert get_component("Grid") is not None


def test_grid_sets_column_count(ctx):
    html = render_components("<Grid cols=3></Grid>", ctx)
    assert "dashdown-grid" in html
    assert "dashdown-grid-responsive" in html
    assert "repeat(3,minmax(0,1fr))" in html
    assert 'data-cols="3"' in html


def test_grid_columns_alias_and_gap(ctx):
    html = render_components('<Grid columns=4 gap="2rem"></Grid>', ctx)
    assert "repeat(4,minmax(0,1fr))" in html
    assert "gap:2rem;" in html


def test_grid_default_gap_uses_design_token(ctx):
    html = render_components("<Grid></Grid>", ctx)
    assert "gap:var(--dashdown-grid-gap, 1rem);" in html


def test_grid_defaults_to_two_columns(ctx):
    html = render_components("<Grid></Grid>", ctx)
    assert "repeat(2,minmax(0,1fr))" in html


def test_grid_renders_children(ctx):
    html = render_components(
        '<Grid cols=2><LineChart data={sales} x="m" y="v" /></Grid>', ctx
    )
    assert 'data-async-component="chart"' in html


def test_chart_col_span_emits_grid_column(ctx):
    html = render_components('<LineChart data={sales} x="m" y="v" col-span=2 />', ctx)
    assert "grid-column:span 2;" in html


def test_chart_span_alias(ctx):
    html = render_components('<BarChart data={sales} x="m" y="v" span=3 />', ctx)
    assert "grid-column:span 3;" in html


def test_chart_no_span_by_default(ctx):
    html = render_components('<LineChart data={sales} x="m" y="v" />', ctx)
    assert "grid-column" not in html


def test_chart_default_height_is_compact(ctx):
    html = render_components('<LineChart data={sales} x="m" y="v" />', ctx)
    assert "height:300px;" in html


def test_chart_height_override(ctx):
    html = render_components('<LineChart data={sales} x="m" y="v" height=220 />', ctx)
    assert "height:220px;" in html


def test_counter_col_span(ctx):
    html = render_components(
        '<Counter data={kpis} column="total" label="Total" col-span=2 />', ctx
    )
    assert "grid-column:span 2;" in html


def test_table_col_span(ctx):
    html = render_components('<Table data={rows} col-span=2 />', ctx)
    assert "grid-column:span 2;" in html
