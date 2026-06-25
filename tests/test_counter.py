"""Tests for the Counter KPI component — delta + sparkline config (Task 4)."""
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
    """Extract and decode the data-config JSON from a counter's HTML."""
    m = re.search(r'data-config="([^"]*)"', rendered)
    assert m, f"no data-config in: {rendered}"
    return json.loads(html.unescape(m.group(1)))


def test_counter_basic_config(ctx):
    html_out = render_components(
        '<Counter data={kpis} column="total" label="Total" />', ctx
    )
    cfg = _config(html_out)
    assert cfg["query_name"] == "kpis"
    assert cfg["column"] == "total"
    # No KPI extras requested → keys absent.
    assert "compare_query" not in cfg
    assert "sparkline_query" not in cfg
    assert "delta" not in cfg


def test_counter_structure(ctx):
    html_out = render_components('<Counter data={kpis} column="total" />', ctx)
    assert "dashdown-counter-label" in html_out
    assert "dashdown-counter-delta" in html_out
    assert "dashdown-counter-value" in html_out
    # No sparkline → no spark container.
    assert "dashdown-counter-spark" not in html_out


def test_counter_compare_query(ctx):
    html_out = render_components(
        '<Counter data={revenue} column="amt" compare={revenue_prior} />', ctx
    )
    cfg = _config(html_out)
    assert cfg["compare_query"] == "revenue_prior"
    assert cfg["compare_row"] == 0


def test_counter_compare_overrides(ctx):
    html_out = render_components(
        '<Counter data={revenue} column="amt" compare={prev} '
        'compare-column="old" compare-row=2 invert-delta />',
        ctx,
    )
    cfg = _config(html_out)
    assert cfg["compare_query"] == "prev"
    assert cfg["compare_column"] == "old"
    assert cfg["compare_row"] == 2
    assert cfg["invert_delta"] is True


def test_counter_explicit_delta(ctx):
    html_out = render_components(
        '<Counter data={kpis} column="total" delta="12.4" />', ctx
    )
    cfg = _config(html_out)
    assert cfg["delta"] == "12.4"


def test_counter_sparkline(ctx):
    html_out = render_components(
        '<Counter data={kpis} column="total" sparkline={trend} '
        'sparkline-column="revenue" />',
        ctx,
    )
    cfg = _config(html_out)
    assert cfg["sparkline_query"] == "trend"
    assert cfg["sparkline_column"] == "revenue"
    assert "dashdown-counter-spark" in html_out


def test_counter_format_attrs(ctx):
    html_out = render_components(
        '<Counter data={kpis} column="price" format="currency" '
        'currency="€" decimals=2 prefix="~" suffix=" each" />',
        ctx,
    )
    cfg = _config(html_out)
    assert cfg["format"] == "currency"
    assert cfg["currency"] == "€"
    assert cfg["decimals"] == 2
    assert cfg["prefix"] == "~"
    assert cfg["suffix"] == " each"


def test_counter_locale_and_iso_currency(ctx):
    html_out = render_components(
        '<Counter data={kpis} column="revenue" format="currency" '
        'currency="EUR" locale="de-DE" />',
        ctx,
    )
    cfg = _config(html_out)
    assert cfg["currency"] == "EUR"
    assert cfg["locale"] == "de-DE"


def test_counter_no_format_attrs_absent(ctx):
    """Unset format attrs stay out of the config so JS defaults win."""
    html_out = render_components('<Counter data={kpis} column="total" />', ctx)
    cfg = _config(html_out)
    assert "format" not in cfg
    assert "currency" not in cfg
    assert "decimals" not in cfg
    assert "locale" not in cfg


def test_counter_requires_data(ctx):
    html_out = render_components("<Counter label=\"x\" />", ctx)
    assert "Counter requires data" in html_out


# --------------------------------------------------------------------------- #
# Semantic-driven sparkline (Stage 18f): a metric + time-dim trend, resolved as
# its own synthetic query — the same `_python_def_cache` seam as the headline.
# --------------------------------------------------------------------------- #


def _semantic_handle():
    from dashdown.semantic import SemanticModelHandle

    dims = {"region", "order_date"}
    measures = {"revenue"}
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
        measure_formats={"revenue": {"format": "currency", "currency": "$"}},
        dim_lookup={d: d for d in dims},
        measure_lookup={m: m for m in measures},
    )


@pytest.fixture
def sem_ctx():
    return RenderContext(
        queries={}, params={}, current_path="/",
        semantic_models={"sales": _semantic_handle()},
    )


def test_counter_semantic_sparkline(sem_ctx):
    """`sparkline={metric} sparkline-by={time_dim}` → a second synthetic query."""
    html_out = render_components(
        "<Counter metric={sales.revenue} "
        "sparkline={sales.revenue} sparkline-by={sales.order_date} grain=\"month\" />",
        sem_ctx,
    )
    cfg = _config(html_out)
    # Headline is the scalar metric (no `by`); the sparkline rides its own query
    # bucketed by the time dimension at the chosen grain.
    assert cfg["query_name"] == "_sem.sales.revenue"
    assert cfg["sparkline_query"] == "_sem.sales.revenue.by.order_date.grain.month"
    # The metric's canonical name is its result column = the value series.
    assert cfg["sparkline_column"] == "revenue"
    assert "dashdown-counter-spark" in html_out
    # Both refs recorded so the pipeline compiles each into a synthetic spec.
    assert set(sem_ctx.semantic_refs) == {
        "_sem.sales.revenue",
        "_sem.sales.revenue.by.order_date.grain.month",
    }


def test_counter_semantic_sparkline_grain_ref(sem_ctx):
    """A `grain={control}` reference stays out of the query identity (read per fetch)."""
    html_out = render_components(
        "<Counter metric={sales.revenue} "
        "sparkline={sales.revenue} sparkline-by={sales.order_date} grain={trendGrain} />",
        sem_ctx,
    )
    cfg = _config(html_out)
    # Interactive grain → no `grain.<token>` segment in the name (one def, shape
    # varies on the existing filter re-fetch path).
    assert cfg["sparkline_query"] == "_sem.sales.revenue.by.order_date"
    ref = sem_ctx.semantic_refs["_sem.sales.revenue.by.order_date"]
    assert ref.grain is None
    assert ref.grain_param == "trendGrain"


def test_counter_named_sparkline_unchanged_under_semantic(sem_ctx):
    """Without `sparkline-by=`, the original named-query path is used verbatim —
    even when the headline is semantic."""
    html_out = render_components(
        "<Counter metric={sales.revenue} sparkline={trend} sparkline-column=\"amt\" />",
        sem_ctx,
    )
    cfg = _config(html_out)
    assert cfg["sparkline_query"] == "trend"
    assert cfg["sparkline_column"] == "amt"
    # Only the headline metric registered a semantic ref — the sparkline didn't.
    assert set(sem_ctx.semantic_refs) == {"_sem.sales.revenue"}
