"""Tests for the first-class semantic metric layer (Stage 18b POC, on BSL/Ibis).

The BSL-dependent tests skip when the optional `semantic` extra isn't installed
(mirroring the Polars skip in test_python_query). The pure-logic tests (filter
mapping, ref resolution, query naming) run a lightweight fake handle and need no
deps.
"""
from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from dashdown.semantic import (
    DATE_END_PARAM,
    DATE_START_PARAM,
    GRAIN_TOKENS,
    SemanticModelHandle,
    SemanticRef,
    build_filters,
    resolve_grain_token,
    resolve_ref,
    semantic_filter_params,
    semantic_query_name,
)

EXAMPLE = Path(__file__).parent / "fixtures" / "semantic_first_class"

bsl_installed = True
try:  # the semantic extra
    import boring_semantic_layer  # noqa: F401
    import ibis  # noqa: F401
except ImportError:  # pragma: no cover
    bsl_installed = False

needs_bsl = pytest.mark.skipif(not bsl_installed, reason="requires dashdown-md[semantic]")


@pytest.fixture
def example_project(tmp_path):
    """A runnable copy of the vendored ``tests/fixtures/semantic_first_class``
    project in tmp_path.

    ``sources.yaml`` is gitignored repo-wide, so it isn't part of the vendored
    fixture — copy the committed tree and write the credential-free CSV
    sources.yaml here. Keeps these tests self-contained (the pattern every other
    test follows) instead of depending on an untracked file.
    """
    dst = tmp_path / "semantic_proj"
    shutil.copytree(
        EXAMPLE, dst,
        ignore=shutil.ignore_patterns("__pycache__", "*.duckdb*", "sources.yaml"),
    )
    (dst / "sources.yaml").write_text("main:\n  type: csv\n  directory: data\n")
    return dst


def _fake_handle() -> SemanticModelHandle:
    """A handle with introspected sets/lookups filled in, no BSL build needed."""
    dims = {"region", "status", "month", "order_date"}
    measures = {"revenue", "orders", "avg_deal"}
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


# --------------------------------------------------------------------------- #
# Reference resolution (no BSL needed)
# --------------------------------------------------------------------------- #


def test_resolve_ref_ok():
    models = {"sales": _fake_handle()}
    ref = resolve_ref(models, "sales.revenue", "sales.region")
    assert ref.model == "sales"
    assert ref.metric == "revenue"
    assert ref.by == "region"
    assert ref.connector == "main"
    assert ref.query_name == "_sem.sales.revenue.by.region"


def test_resolve_ref_bare_by():
    ref = resolve_ref({"sales": _fake_handle()}, "sales.revenue", "region")
    assert ref.by == "region"


def test_resolve_ref_no_by():
    ref = resolve_ref({"sales": _fake_handle()}, "sales.revenue", None)
    assert ref.by is None
    assert ref.query_name == "_sem.sales.revenue"


@pytest.mark.parametrize(
    "metric_ref,by_ref",
    [
        ("revenue", "region"),          # missing model prefix
        ("sales.nope", "region"),       # unknown metric
        ("nope.revenue", "region"),     # unknown model
        ("sales.revenue", "nope"),      # unknown dimension
    ],
)
def test_resolve_ref_errors(metric_ref, by_ref):
    with pytest.raises(ValueError):
        resolve_ref({"sales": _fake_handle()}, metric_ref, by_ref)


def test_query_name_deterministic():
    assert semantic_query_name("s", "m", "d") == "_sem.s.m.by.d"
    assert semantic_query_name("s", "m", None) == "_sem.s.m"


def test_resolve_ref_resolves_joined_and_prefixed_names():
    """With joins, BSL prefixes names; a short `by={model.field}` still resolves."""
    h = SemanticModelHandle(
        name="sales",
        connector="main",
        file_config={},
        table_connectors={"orders": "main", "regions": "main"},
        profile=None,
        profile_path=None,
        # Canonical (prefixed) names as BSL reports them once joins exist:
        measures={"sales.revenue"},
        dimensions={"sales.region", "geo.manager"},
        measure_lookup={"sales.revenue": "sales.revenue", "revenue": "sales.revenue"},
        dim_lookup={
            "sales.region": "sales.region", "region": "sales.region",
            "geo.manager": "geo.manager", "manager": "geo.manager",
        },
    )
    models = {"sales": h}
    # short metric name + short joined-dimension name both resolve to canonical
    ref = resolve_ref(models, "sales.revenue", "sales.manager")
    assert ref.metric == "sales.revenue"
    assert ref.by == "geo.manager"
    # explicit canonical join path works too
    assert resolve_ref(models, "sales.revenue", "sales.geo.manager").by == "geo.manager"


def test_resolve_ref_multi_metric():
    """A comma-separated metric list on one model → an ordered `metrics` tuple."""
    models = {"sales": _fake_handle()}
    ref = resolve_ref(models, "sales.revenue, sales.orders", "sales.region")
    assert ref.metrics == ("revenue", "orders")
    assert ref.metric == "revenue"  # first, for single-value widgets
    assert ref.by == "region"
    # name carries every metric as its own segment, deterministic + unique
    assert ref.query_name == "_sem.sales.revenue.orders.by.region"


def test_resolve_ref_multi_metric_dedupes():
    ref = resolve_ref({"sales": _fake_handle()}, "sales.revenue,sales.revenue", None)
    assert ref.metrics == ("revenue",)


def test_resolve_ref_multi_metric_cross_model_errors():
    """Every metric in one chart must belong to the same model."""
    models = {"sales": _fake_handle()}
    with pytest.raises(ValueError):
        resolve_ref(models, "sales.revenue,other.cost", "sales.region")


def test_resolve_ref_series_second_dimension():
    """`series=` resolves a second dimension and adds a name segment."""
    models = {"sales": _fake_handle()}
    ref = resolve_ref(models, "sales.revenue", "sales.month", series_ref="sales.status")
    assert ref.by == "month"
    assert ref.series == "status"
    assert ref.query_name == "_sem.sales.revenue.by.month.series.status"
    # default: no series
    assert resolve_ref(models, "sales.revenue", "sales.month").series is None


def test_resolve_ref_series_unknown_dimension_errors():
    with pytest.raises(ValueError):
        resolve_ref({"sales": _fake_handle()}, "sales.revenue", "sales.month", series_ref="nope")


def test_resolve_ref_comma_by_points_at_series():
    """A multi-dimension `by="a,b"` (no single-chart form) errors with an actionable
    pointer to `series=` — not a confusing `unknown dimension 'a,b'`."""
    with pytest.raises(ValueError) as exc:
        resolve_ref({"sales": _fake_handle()}, "sales.revenue", "region,status")
    msg = str(exc.value)
    assert "series=" in msg
    assert "by={region} series={status}" in msg  # the concrete suggestion
    assert "unknown dimension" not in msg


def test_resolve_ref_comma_series_errors_without_by_hint():
    """A comma in `series=` is also rejected (one series split), but without the
    `by=`/`series=` suggestion (that's for splitting an over-stuffed `by=`)."""
    with pytest.raises(ValueError) as exc:
        resolve_ref({"sales": _fake_handle()}, "sales.revenue", "sales.region", series_ref="status,month")
    msg = str(exc.value)
    assert "series=" in msg and "names ONE dimension" in msg
    assert "Use `by=" not in msg


def test_resolve_ref_series_with_multi_metric_errors():
    """A second dimension can't combine with multiple metrics (cross-product)."""
    models = {"sales": _fake_handle()}
    with pytest.raises(ValueError):
        resolve_ref(models, "sales.revenue,sales.orders", "sales.month", series_ref="sales.status")


def test_query_name_multi_metric():
    assert semantic_query_name("s", ["m1", "m2"], "d") == "_sem.s.m1.m2.by.d"
    assert semantic_query_name("s", ("m1", "m2"), None) == "_sem.s.m1.m2"


def test_semantic_series_reaches_chart_config():
    """A semantic `series={model.dim}` becomes the chart's `series_by` config."""
    import html as html_mod
    import json
    import re

    import dashdown.components  # noqa: F401  (registers built-ins)
    from dashdown.components.base import RenderContext
    from dashdown.render.components import render_components

    ctx = RenderContext(
        queries={}, params={}, current_path="/",
        semantic_models={"sales": _fake_handle()},
    )
    html = render_components(
        "<BarChart metric={sales.revenue} by={sales.month} series={sales.status} />", ctx
    )
    m = re.search(r'data-config="([^"]*)"', html)
    assert m, html
    config = json.loads(html_mod.unescape(m.group(1)))
    assert config["x"] == "month"        # by → x-axis
    assert config["series_by"] == "status"  # series → grouping column
    assert config["y"] == "revenue"


def test_resolve_semantic_multi_metric_for_charts():
    """`resolve_semantic` joins metrics for charts and drops the format hint."""
    from dashdown.components.base import RenderContext
    from dashdown.components.builtin._util import resolve_semantic

    ctx = RenderContext(
        queries={}, params={}, current_path="/",
        semantic_models={"sales": _fake_handle()},
    )
    sem = resolve_semantic({"metric": "sales.revenue,sales.orders", "by": "sales.region"}, ctx)
    assert sem["metric"] == "revenue"          # single column for Value/Counter/Table
    assert sem["metrics"] == "revenue,orders"  # chart `y` → one series per metric
    assert sem["format"] is None               # ambiguous across metrics → none

    # Single metric keeps its format hint (revenue is currency in the fake handle).
    one = resolve_semantic({"metric": "sales.revenue", "by": "sales.region"}, ctx)
    assert one["metrics"] == "revenue"
    assert one["format"] == {"format": "currency", "currency": "$"}


# --------------------------------------------------------------------------- #
# ComboChart (bar + line, optional second axis) — no BSL needed
# --------------------------------------------------------------------------- #


def _combo_config(markup: str, *, semantic: bool = False):
    """Render a `<ComboChart>` and return its parsed `data-config` + the ctx."""
    import html as html_mod
    import json
    import re

    import dashdown.components  # noqa: F401  (registers built-ins)
    from dashdown.components.base import RenderContext
    from dashdown.render.components import render_components

    ctx = RenderContext(
        queries={}, params={}, current_path="/",
        semantic_models={"sales": _fake_handle()} if semantic else {},
    )
    html = render_components(markup, ctx)
    m = re.search(r'data-config="([^"]*)"', html)
    assert m, html
    return json.loads(html_mod.unescape(m.group(1))), ctx, html


def test_combo_plain_query_mode():
    """`data={q}` mode: bars/lines/right_axis are column names; two format groups."""
    config, _ctx, _html = _combo_config(
        '<ComboChart data={summary} x="month" bars="revenue" lines="orders" '
        'right_axis="orders" format="currency" currency="$" right_format="number" />'
    )
    assert config["type"] == "combo"
    assert config["query_name"] == "summary"
    assert config["x"] == "month"
    assert config["bars"] == ["revenue"]
    assert config["lines"] == ["orders"]
    assert config["right_axis"] == ["orders"]
    assert config["format"] == "currency" and config["currency"] == "$"
    assert config["right"] == {"format": "number"}  # nested → JS right-axis formatter


def test_combo_plain_multi_column_lists():
    """`bars=`/`lines=` accept comma lists (several bars, several lines)."""
    config, _ctx, _html = _combo_config(
        '<ComboChart data={q} x="month" bars="a, b" lines="c" />'
    )
    assert config["bars"] == ["a", "b"]
    assert config["lines"] == ["c"]
    assert config["right_axis"] == []  # single axis when none flagged right


def test_combo_per_series_colors():
    """`bar_color`/`line_color` ride the config as per-series colour lists (chart.js
    applies them as itemStyle/lineStyle, falling back to the palette otherwise)."""
    config, _ctx, _html = _combo_config(
        '<ComboChart data={q} x="month" bars="rev" lines="ord" '
        'bar_color="#6366f1" line_color="#f59e0b,#10b981" />'
    )
    assert config["bar_colors"] == ["#6366f1"]
    assert config["line_colors"] == ["#f59e0b", "#10b981"]  # comma list cycled in JS


def test_combo_semantic_combines_metrics_into_one_query():
    """Semantic mode: bars+lines metric refs → ONE synthetic query (the 18b path),
    each ref mapped to its result column, the ref recorded on ctx for the pipeline."""
    config, ctx, _html = _combo_config(
        '<ComboChart by={sales.order_date} grain="month" '
        'bars={sales.revenue} lines={sales.orders} right_axis={sales.orders} />',
        semantic=True,
    )
    assert config["x"] == "order_date"          # by → x-axis
    assert config["bars"] == ["revenue"]        # ref → canonical column
    assert config["lines"] == ["orders"]
    assert config["right_axis"] == ["orders"]
    # One combined synthetic query holding BOTH measures, grain in the name.
    assert config["query_name"] == "_sem.sales.revenue.orders.by.order_date.grain.month"
    ref = list(ctx.semantic_refs.values())[0]
    assert ref.metrics == ("revenue", "orders")
    assert ref.grain == "month"


def test_combo_semantic_axis_formats_default_from_measures():
    """Left axis defaults to its metric's declared format; the right axis to its
    own. `revenue` is currency in the fake handle, `orders` has no format."""
    config, _ctx, _html = _combo_config(
        '<ComboChart by={sales.region} bars={sales.revenue} lines={sales.orders} '
        'right_axis={sales.orders} />',
        semantic=True,
    )
    assert config["format"] == "currency" and config["currency"] == "$"
    assert not config.get("right")  # orders carries no declared format → no right fmt


def test_combo_semantic_rejects_series_split():
    """ComboChart has no `series=` (metrics ARE its series) — it errors, surfaced as
    the component's inline error card rather than a 500."""
    _config_unused = None
    import dashdown.components  # noqa: F401
    from dashdown.components.base import RenderContext
    from dashdown.render.components import render_components

    ctx = RenderContext(
        queries={}, params={}, current_path="/",
        semantic_models={"sales": _fake_handle()},
    )
    html = render_components(
        "<ComboChart by={sales.region} bars={sales.revenue} series={sales.status} />",
        ctx,
    )
    assert "error" in html.lower() and "series" in html.lower()


def test_combo_requires_bars_or_lines():
    """Neither bars nor lines is an authoring error (→ inline error card)."""
    import dashdown.components  # noqa: F401
    from dashdown.components.base import RenderContext
    from dashdown.render.components import render_components

    ctx = RenderContext(queries={}, params={}, current_path="/")
    html = render_components('<ComboChart data={q} x="month" />', ctx)
    assert "error" in html.lower()


# --------------------------------------------------------------------------- #
# Role-mapped charts — multi-measure semantic (Candlestick/Heatmap/Sankey/…)
#
# These map several measures of one model onto named *roles* (OHLC, value, axes)
# the way a BI tool binds an OHLC visual to a semantic model: N measures grouped
# by a dimension, each measure → a visual role. They ride the same single
# synthetic query (`resolve_semantic_query`) ComboChart uses. No BSL needed.
# --------------------------------------------------------------------------- #


def _ohlc_handle() -> SemanticModelHandle:
    """A price model with open/high/low/close measures + a `day` time dimension."""
    dims = {"day", "ticker"}
    measures = {"open", "high", "low", "close"}
    return SemanticModelHandle(
        name="prices",
        connector="main",
        file_config={},
        table_connectors={"prices": "main"},
        profile=None,
        profile_path=None,
        measures=measures,
        dimensions=dims,
        time_dimension="day",
        measure_formats={"close": {"format": "currency", "currency": "$"}},
        dim_lookup={d: d for d in dims},
        measure_lookup={m: m for m in measures},
    )


def _grid_handle() -> SemanticModelHandle:
    """A model with two category dims + two measures for heatmap/sankey/graph."""
    dims = {"month", "channel", "stage_from", "stage_to"}
    measures = {"downloads", "users"}
    return SemanticModelHandle(
        name="grid",
        connector="main",
        file_config={},
        table_connectors={"grid": "main"},
        profile=None,
        profile_path=None,
        measures=measures,
        dimensions=dims,
        time_dimension=None,
        measure_formats={},
        dim_lookup={d: d for d in dims},
        measure_lookup={m: m for m in measures},
    )


def _chart_config(markup: str, handle: SemanticModelHandle, model: str):
    """Render a chart with one semantic model; return (config|None, ctx, html)."""
    import html as html_mod
    import json
    import re

    import dashdown.components  # noqa: F401  (registers built-ins)
    from dashdown.components.base import RenderContext
    from dashdown.render.components import render_components

    ctx = RenderContext(
        queries={}, params={}, current_path="/",
        semantic_models={model: handle},
    )
    html = render_components(markup, ctx)
    m = re.search(r'data-config="([^"]*)"', html)
    config = json.loads(html_mod.unescape(m.group(1))) if m else None
    return config, ctx, html


def test_candlestick_semantic_maps_ohlc_measures_to_one_query():
    """OHLC roles are four measures of one model → ONE synthetic query, each role
    mapped to its result column, the value-axis format defaulting from `close`."""
    config, ctx, _ = _chart_config(
        "<CandlestickChart by={prices.day} open={prices.open} high={prices.high} "
        "low={prices.low} close={prices.close} />",
        _ohlc_handle(), "prices",
    )
    assert config["type"] == "candlestick"
    assert config["x"] == "day"  # by → axis
    assert (config["open"], config["high"], config["low"], config["close"]) == (
        "open", "high", "low", "close",
    )
    assert config["query_name"] == "_sem.prices.open.high.low.close.by.day"
    ref = list(ctx.semantic_refs.values())[0]
    assert ref.metrics == ("open", "high", "low", "close")
    assert config["format"] == "currency" and config["currency"] == "$"


def test_candlestick_data_mode_unchanged():
    """`data={q}` mode still reads OHLC as plain column names (no semantic model)."""
    config, _ctx, _ = _chart_config(
        '<CandlestickChart data={prices} x="day" open="o" high="h" low="l" close="c" />',
        _ohlc_handle(), "prices",
    )
    assert config["query_name"] == "prices"
    assert config["x"] == "day"
    assert config["open"] == "o" and config["close"] == "c"


def test_candlestick_semantic_missing_role_errors():
    """A missing OHLC measure is an authoring error (→ inline error card)."""
    config, ctx, html = _chart_config(
        "<CandlestickChart by={prices.day} open={prices.open} high={prices.high} "
        "low={prices.low} />",  # close missing
        _ohlc_handle(), "prices",
    )
    assert config is None
    assert "error" in html.lower() and "close" in html.lower()
    assert ctx.semantic_refs == {}  # nothing registered when it can't resolve


def test_heatmap_semantic_x_y_dims_value_measure():
    """x/y are two dimensions (by + series), value is a measure → grouped grid."""
    config, ctx, _ = _chart_config(
        "<HeatmapChart x={grid.month} y={grid.channel} value={grid.downloads} />",
        _grid_handle(), "grid",
    )
    assert config["type"] == "heatmap"
    assert config["x"] == "month"  # by
    assert config["y"] == "channel"  # series
    assert config["value"] == "downloads"
    ref = list(ctx.semantic_refs.values())[0]
    assert ref.by == "month" and ref.series == "channel" and ref.metrics == ("downloads",)


def test_sankey_semantic_source_target_dims_value_measure():
    """source/target are two dimensions, value the link-weight measure."""
    config, _ctx, _ = _chart_config(
        "<SankeyChart source={grid.stage_from} target={grid.stage_to} "
        "value={grid.users} />",
        _grid_handle(), "grid",
    )
    assert config["type"] == "sankey"
    assert config["x"] == "stage_from"  # source → by
    assert config["y"] == "stage_to"  # target → series
    assert config["value"] == "users"


def test_graph_semantic_requires_value_measure():
    """A semantic GraphChart needs a `value` measure (a measure aggregates the
    edge list) — omitting it is an actionable error, not a silent blank."""
    config, _ctx, html = _chart_config(
        "<GraphChart source={grid.stage_from} target={grid.stage_to} />",
        _grid_handle(), "grid",
    )
    assert config is None
    assert "error" in html.lower() and "value" in html.lower()


def test_parallel_semantic_dimensions_are_measures():
    """`dimensions` is a comma list of measure refs (one axis each); `by` groups
    them into one polyline per value."""
    config, ctx, _ = _chart_config(
        '<ParallelChart by={prices.ticker} dimensions="prices.open,prices.high,prices.close" />',
        _ohlc_handle(), "prices",
    )
    assert config["type"] == "parallel"
    assert config["dimensions"] == ["open", "high", "close"]
    assert config["x"] == "ticker"
    ref = list(ctx.semantic_refs.values())[0]
    assert ref.metrics == ("open", "high", "close")


@pytest.mark.parametrize(
    "markup, needle",
    [
        ("<BoxPlot metric={sales.revenue} by={sales.region} />", "raw"),
        ("<Violin metric={sales.revenue} by={sales.region} />", "raw"),
        ("<SunburstChart metric={sales.revenue} by={sales.region} />", "hierarchy"),
        ("<TreeChart metric={sales.revenue} by={sales.region} />", "hierarchy"),
    ],
)
def test_distribution_and_hierarchy_charts_reject_semantic(markup, needle):
    """Charts the metric/dimension grammar can't express (raw distributions,
    parent/child hierarchies) refuse a semantic ref with an actionable message
    instead of rendering a broken card — and register no synthetic ref."""
    config, ctx, html = _chart_config(markup, _fake_handle(), "sales")
    assert config is None
    assert "error" in html.lower() and needle in html.lower()
    assert ctx.semantic_refs == {}  # rejected before resolve_semantic registers


# --------------------------------------------------------------------------- #
# Time grain — `grain=` (Stage 18e), no BSL needed for the parse/identity tests
# --------------------------------------------------------------------------- #


def test_resolve_ref_literal_grain_parses_and_enters_name():
    """`grain="month"` → a validated token on the ref, baked into the query name."""
    ref = resolve_ref({"sales": _fake_handle()}, "sales.revenue", "sales.order_date", grain="Month")
    assert ref.grain == "month"           # lowercased
    assert ref.grain_param is None
    assert ref.query_name.endswith("by.order_date.grain.month")


def test_resolve_ref_referenced_grain_records_param_not_name():
    """`grain={control}` → a recorded param name, deliberately NOT in the query name
    (its value varies per fetch like a filter, without changing query identity)."""
    ref = resolve_ref(
        {"sales": _fake_handle()}, "sales.revenue", "sales.order_date",
        grain_param="trendGrain",
    )
    assert ref.grain is None
    assert ref.grain_param == "trendGrain"
    assert "grain" not in ref.query_name
    assert ref.query_name == "_sem.sales.revenue.by.order_date"


@pytest.mark.parametrize("bad", ["fortnight", "monthly", "days", "decade"])
def test_resolve_ref_bad_grain_token_raises(bad):
    with pytest.raises(ValueError):
        resolve_ref({"sales": _fake_handle()}, "sales.revenue", "sales.order_date", grain=bad)


def test_resolve_ref_empty_grain_is_no_grain():
    """An empty `grain=""` means *no* grain (not a bad token) — it's how an unset
    attribute looks, and must leave the query identity unchanged."""
    ref = resolve_ref({"sales": _fake_handle()}, "sales.revenue", "sales.order_date", grain="")
    assert ref.grain is None
    assert "grain" not in ref.query_name


def test_grain_vocab_is_the_canonical_eight():
    assert GRAIN_TOKENS == ("second", "minute", "hour", "day", "week", "month", "quarter", "year")


def test_semantic_query_name_includes_literal_grain_only():
    assert semantic_query_name("s", "m", "d", grain="month") == "_sem.s.m.by.d.grain.month"
    # no grain segment when unset (back-compat with existing names)
    assert semantic_query_name("s", "m", "d") == "_sem.s.m.by.d"


def test_per_chart_distinct_grains_are_distinct_cache_entries():
    """Two charts at different *literal* grains coexist as distinct synthetic queries."""
    models = {"sales": _fake_handle()}
    day = resolve_ref(models, "sales.revenue", "sales.order_date", grain="day")
    month = resolve_ref(models, "sales.revenue", "sales.order_date", grain="month")
    assert day.query_name != month.query_name
    assert day.query_name.endswith("grain.day")
    assert month.query_name.endswith("grain.month")


def test_resolve_grain_token_literal_control_and_empty():
    models = {"sales": _fake_handle()}
    lit = resolve_ref(models, "sales.revenue", "sales.order_date", grain="month")
    ctl = resolve_ref(models, "sales.revenue", "sales.order_date", grain_param="g")
    # literal: fixed regardless of params
    assert resolve_grain_token(lit, {}) == "month"
    assert resolve_grain_token(lit, {"g": "year"}) == "month"
    # control: read (and validated/lowercased) from the live params; empty → None
    assert resolve_grain_token(ctl, {"g": "Quarter"}) == "quarter"
    assert resolve_grain_token(ctl, {}) is None
    assert resolve_grain_token(ctl, {"g": ""}) is None
    # no grain at all → None
    assert resolve_grain_token(resolve_ref(models, "sales.revenue", "sales.order_date"), {}) is None


def test_resolve_grain_token_bad_control_value_raises():
    ctl = resolve_ref({"sales": _fake_handle()}, "sales.revenue", "sales.order_date", grain_param="g")
    with pytest.raises(ValueError):
        resolve_grain_token(ctl, {"g": "fortnight"})


def test_build_filters_ignores_a_grain_control_param():
    """A grain control is named for a non-dimension, so `build_filters` ignores it —
    grain is a grouping modifier, not a filter (no special-casing needed)."""
    # `trendGrain` is not a model dimension, so it never becomes a filter…
    assert build_filters(_fake_handle(), {"trendGrain": "month"}) == []
    # …and it doesn't disturb a real dimension filter sitting beside it.
    f = build_filters(_fake_handle(), {"trendGrain": "month", "region": "East"})
    assert f == [{"field": "region", "operator": "in", "values": ["East"]}]


def test_resolve_semantic_extracts_literal_vs_referenced_grain():
    """`resolve_semantic` maps the `key="lit"` vs `key={ref}` attr convention onto
    grain (literal token) vs grain_param (control name)."""
    from dashdown.components.base import RenderContext
    from dashdown.components.builtin._util import resolve_semantic
    from dashdown.render.attrs import DataRef

    ctx = RenderContext(
        queries={}, params={}, current_path="/",
        semantic_models={"sales": _fake_handle()},
    )
    # literal grain → on the recorded ref as `.grain`
    resolve_semantic({"metric": "sales.revenue", "by": "sales.order_date", "grain": "month"}, ctx)
    lit = next(r for r in ctx.semantic_refs.values() if r.grain == "month")
    assert lit.grain_param is None and lit.query_name.endswith("grain.month")
    # referenced grain ({control}) → `.grain_param`, not in the name
    resolve_semantic(
        {"metric": "sales.revenue", "by": "sales.order_date", "grain": DataRef("trendGrain")}, ctx
    )
    refd = next(r for r in ctx.semantic_refs.values() if r.grain_param == "trendGrain")
    assert refd.grain is None and "grain" not in refd.query_name


# --------------------------------------------------------------------------- #
# Filter mapping → BSL JSON filters (no BSL needed)
# --------------------------------------------------------------------------- #


def test_build_filters_dimension_in():
    f = build_filters(_fake_handle(), {"region": "East,West"})
    assert f == [{"field": "region", "operator": "in", "values": ["East", "West"]}]


def test_build_filters_single_value():
    f = build_filters(_fake_handle(), {"region": "East"})
    assert f == [{"field": "region", "operator": "in", "values": ["East"]}]


def test_build_filters_ignores_empty_internal_unknown():
    f = build_filters(
        _fake_handle(), {"region": "", "_connector": "main", "bogus": "x"}
    )
    assert f == []


def test_build_filters_date_range_on_time_dimension():
    f = build_filters(
        _fake_handle(),
        {DATE_START_PARAM: "2024-02-01", DATE_END_PARAM: "2024-03-31"},
    )
    assert {"field": "order_date", "operator": ">=", "value": "2024-02-01"} in f
    assert {"field": "order_date", "operator": "<=", "value": "2024-03-31"} in f


def test_build_filters_no_date_without_time_dimension():
    h = _fake_handle()
    h.time_dimension = None
    f = build_filters(h, {DATE_START_PARAM: "2024-02-01"})
    assert f == []


def test_semantic_filter_params_dimensions_plus_date():
    """The "filtered by" badge params (Stage 19) are the dimensions a dropdown
    can target plus the global-date params (model has a time dimension)."""
    params = semantic_filter_params(_fake_handle())
    assert "region" in params
    assert DATE_START_PARAM in params and DATE_END_PARAM in params
    assert params == sorted(params)  # stable, sorted


def test_semantic_filter_params_no_date_without_time_dimension():
    h = _fake_handle()
    h.time_dimension = None
    params = semantic_filter_params(h)
    assert "region" in params
    assert DATE_START_PARAM not in params and DATE_END_PARAM not in params


# --------------------------------------------------------------------------- #
# Loader + execution (require BSL/Ibis)
# --------------------------------------------------------------------------- #


@needs_bsl
def test_load_example_model(example_project):
    from dashdown.project import load_project

    proj = load_project(example_project)
    assert {"sales", "geo"} <= set(proj.semantic_models)
    h = proj.semantic_models["sales"]
    # The example model has joins, so BSL prefixes names — assert via the lookups
    # (short names resolve regardless of prefixing).
    for m in ("revenue", "orders", "avg_deal"):
        assert m in h.measure_lookup
    # Stage 18e collapsed the pre-declared month/quarter/year buckets into one
    # `order_date` time dimension that charts bucket on demand with `grain=`.
    for d in ("region", "status", "order_date", "manager"):  # manager is joined
        assert d in h.dim_lookup
    assert "month" not in h.dim_lookup  # no workaround bucket dimensions any more
    assert h.time_dimension.endswith("order_date")
    revenue_canonical = h.measure_lookup["revenue"]
    assert h.measure_formats[revenue_canonical] == {"format": "currency", "currency": "$"}


@needs_bsl
def test_loader_missing_dir_empty():
    from dashdown.semantic import load_semantic_models

    assert load_semantic_models(EXAMPLE / "nope", {}) == {}


@needs_bsl
def test_loader_duplicate_model_raises(tmp_path):
    from dashdown.semantic import load_semantic_models

    (tmp_path / "a.yml").write_text("m:\n  table: t\n  dimensions: {x: _.x}\n  measures: {c: _.count()}\n")
    (tmp_path / "b.yml").write_text("m:\n  table: t\n  dimensions: {x: _.x}\n  measures: {c: _.count()}\n")
    with pytest.raises(ValueError):
        load_semantic_models(tmp_path, {})


@needs_bsl
def test_loader_skips_underscore_and_profiles(tmp_path):
    from dashdown.project import load_project

    # A real project so connectors exist for the bridge.
    (tmp_path / "dashdown.yaml").write_text("title: t\n")
    (tmp_path / "sources.yaml").write_text("main:\n  type: csv\n  directory: data\n")
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "orders.csv").write_text("region,amount\nEast,10\nWest,20\n")
    sem = tmp_path / "semantic"
    sem.mkdir()
    (sem / "_shared.yml").write_text("ignored: not a model\n")
    (sem / "sales.yml").write_text(
        "sales:\n  connector: main\n  table: orders\n"
        "  dimensions: {region: _.region}\n  measures: {revenue: _.amount.sum()}\n"
    )
    proj = load_project(tmp_path)
    assert set(proj.semantic_models) == {"sales"}


# --------------------------------------------------------------------------- #
# Warehouse Ibis bridges (dep-free — a fake ibis, no BSL/driver/DB needed)
# --------------------------------------------------------------------------- #

import dashdown.semantic as semantic_mod  # noqa: E402


class _FakeBackend:
    """A stand-in ibis backend recording connect()/from_connection() calls."""

    def __init__(self, name, calls):
        self.name = name
        self.calls = calls

    def connect(self, **kwargs):
        self.calls.append((self.name, kwargs))
        return f"<{self.name} backend>"

    def from_connection(self, con):
        self.calls.append((self.name, {"from_connection": con}))
        return f"<{self.name} from_conn>"


class _FakeIbis:
    """A fake `ibis` exposing each backend + a top-level connect(url)."""

    def __init__(self):
        self.calls = []
        for n in ("postgres", "mysql", "snowflake", "bigquery", "duckdb"):
            setattr(self, n, _FakeBackend(n, self.calls))

    def connect(self, url):
        self.calls.append(("connect", {"url": url}))
        return f"<connect {url}>"


class _IbisNoBackends:
    """A fake `ibis` whose every backend attribute is missing (extra not installed)."""

    def __getattr__(self, name):
        raise ImportError(f"no ibis backend {name!r}")


def _stub_connector(class_name, config):
    """A connector instance whose `type().__name__` + `.config` drive the bridge."""
    obj = type(class_name, (), {})()
    obj.config = config
    return obj


@pytest.fixture
def fake_ibis(monkeypatch):
    fi = _FakeIbis()
    monkeypatch.setattr(semantic_mod, "_require_bsl", lambda: (None, fi))
    return fi


def test_bridge_postgres_explicit_kwargs(fake_ibis):
    conn = _stub_connector("PostgresConnector", {
        "host": "db.example.com", "port": 5433, "database": "sales",
        "user": "u", "password": "p",
    })
    backend = semantic_mod.ibis_backend_for_connector(conn)
    assert backend == "<postgres backend>"
    name, kwargs = fake_ibis.calls[-1]
    assert name == "postgres"
    assert kwargs == {
        "host": "db.example.com", "port": 5433, "database": "sales",
        "user": "u", "password": "p",
    }


def test_bridge_postgres_dbname_alias_and_defaults(fake_ibis):
    conn = _stub_connector("PostgresConnector", {"dbname": "sales", "user": "u"})
    semantic_mod.ibis_backend_for_connector(conn)
    _, kwargs = fake_ibis.calls[-1]
    assert kwargs["database"] == "sales"          # `dbname` alias resolves
    assert kwargs["host"] == "localhost" and kwargs["port"] == 5432  # defaults


def test_bridge_postgres_url_uses_top_level_connect(fake_ibis):
    conn = _stub_connector("PostgresConnector", {"url": "postgresql://u:p@h/db"})
    semantic_mod.ibis_backend_for_connector(conn)
    assert ("connect", {"url": "postgresql://u:p@h/db"}) in fake_ibis.calls


def test_bridge_mysql(fake_ibis):
    conn = _stub_connector("MySQLConnector", {
        "host": "h", "port": "3307", "db": "shop", "user": "u", "password": "p",
    })
    semantic_mod.ibis_backend_for_connector(conn)
    name, kwargs = fake_ibis.calls[-1]
    assert name == "mysql"
    assert kwargs == {"host": "h", "port": 3307, "database": "shop",
                      "user": "u", "password": "p"}


def test_bridge_snowflake_strips_none(fake_ibis):
    conn = _stub_connector("SnowflakeConnector", {
        "account": "a", "user": "u", "password": "p",
        "warehouse": "wh", "database": "db", "schema": "sc",
    })
    semantic_mod.ibis_backend_for_connector(conn)
    name, kwargs = fake_ibis.calls[-1]
    assert name == "snowflake"
    assert kwargs == {"account": "a", "user": "u", "password": "p",
                      "warehouse": "wh", "database": "db", "schema": "sc"}
    assert "role" not in kwargs and "authenticator" not in kwargs  # None-filtered


def test_bridge_bigquery_maps_project(fake_ibis):
    conn = _stub_connector("BigQueryConnector", {"project": "my-proj", "location": "EU"})
    semantic_mod.ibis_backend_for_connector(conn)
    name, kwargs = fake_ibis.calls[-1]
    assert name == "bigquery"
    assert kwargs == {"project_id": "my-proj", "location": "EU"}


def test_bridge_caches_backend_on_connector(fake_ibis):
    conn = _stub_connector("PostgresConnector", {"host": "h"})
    b1 = semantic_mod.ibis_backend_for_connector(conn)
    b2 = semantic_mod.ibis_backend_for_connector(conn)
    assert b1 is b2
    assert conn._ibis_backend is b1
    # connect() ran exactly once — the second call reused the cache.
    assert sum(1 for n, _ in fake_ibis.calls if n == "postgres") == 1


def test_bridge_missing_ibis_extra_friendly_hint(monkeypatch):
    monkeypatch.setattr(semantic_mod, "_require_bsl", lambda: (None, _IbisNoBackends()))
    conn = _stub_connector("PostgresConnector", {"host": "h"})
    with pytest.raises(ImportError) as exc:
        semantic_mod.ibis_backend_for_connector(conn)
    msg = str(exc.value)
    assert "ibis-framework[postgres]" in msg


def test_unbridged_connector_points_at_profile(fake_ibis):
    conn = _stub_connector("MSSQLConnector", {"host": "h"})
    with pytest.raises(ValueError) as exc:
        semantic_mod.ibis_backend_for_connector(conn)
    assert "profile:" in str(exc.value)


def test_duckdb_path_still_from_connection(fake_ibis):
    conn = _stub_connector("DuckDBConnector", {})
    conn._con = object()
    conn.query = lambda sql: None
    backend = semantic_mod.ibis_backend_for_connector(conn, ensure_setup=False)
    assert backend == "<duckdb from_conn>"


@needs_bsl
def test_spec_runs_with_pushdown(example_project):
    from dashdown.project import load_project
    from dashdown.python_query import run_python_query
    from dashdown.semantic import build_semantic_spec, resolve_ref

    proj = load_project(example_project)
    ref = resolve_ref(proj.semantic_models, "sales.revenue", "sales.region")
    spec = build_semantic_spec(proj.semantic_models, ref, proj.connectors)
    qr = run_python_query(spec, {"region": "East,West"}, proj.connectors)
    # columns are the canonical (possibly model-prefixed) names
    assert qr.columns[0].endswith("region") and qr.columns[1].endswith("revenue")
    assert {row[0] for row in qr.rows} == {"East", "West"}


@needs_bsl
def test_multi_metric_spec_returns_all_measures(example_project):
    """`metric="sales.revenue,sales.orders"` pushes both measures down in one query."""
    from dashdown.project import load_project
    from dashdown.python_query import run_python_query
    from dashdown.semantic import build_semantic_spec, resolve_ref

    proj = load_project(example_project)
    ref = resolve_ref(proj.semantic_models, "sales.revenue,sales.orders", "sales.region")
    # canonical (model-prefixed once joins exist) names, in author order
    assert len(ref.metrics) == 2
    assert ref.metrics[0].endswith("revenue") and ref.metrics[1].endswith("orders")
    spec = build_semantic_spec(proj.semantic_models, ref, proj.connectors)
    qr = run_python_query(spec, {}, proj.connectors)
    # both measure columns come back alongside the dimension
    assert any(c.endswith("revenue") for c in qr.columns)
    assert any(c.endswith("orders") for c in qr.columns)


@needs_bsl
def test_series_second_dimension_groups_by_both(example_project):
    """`by={region} series={status}` pushes both dimensions + the measure down."""
    from dashdown.project import load_project
    from dashdown.python_query import run_python_query
    from dashdown.semantic import build_semantic_spec, resolve_ref

    proj = load_project(example_project)
    ref = resolve_ref(
        proj.semantic_models, "sales.revenue", "sales.region", series_ref="sales.status"
    )
    assert ref.by.endswith("region") and ref.series.endswith("status")
    spec = build_semantic_spec(proj.semantic_models, ref, proj.connectors)
    qr = run_python_query(spec, {}, proj.connectors)
    # x dim, series dim, and the measure all come back
    assert any(c.endswith("region") for c in qr.columns)
    assert any(c.endswith("status") for c in qr.columns)
    assert any(c.endswith("revenue") for c in qr.columns)
    si = next(i for i, c in enumerate(qr.columns) if c.endswith("status"))
    assert {row[si] for row in qr.rows} == {"Won", "Lost"}


@needs_bsl
def test_grain_composes_with_series(example_project):
    """`grain=` (a time-dimension `by`) composes with a `series=` split — the date
    buckets at the grain *and* each bucket fans into a series per dimension value."""
    from dashdown.project import load_project
    from dashdown.python_query import run_python_query
    from dashdown.semantic import build_semantic_spec, resolve_ref

    proj = load_project(example_project)
    ref = resolve_ref(
        proj.semantic_models, "sales.revenue", "sales.order_date",
        series_ref="sales.status", grain="quarter",
    )
    assert ref.grain == "quarter" and ref.query_name.endswith("grain.quarter")
    qr = run_python_query(build_semantic_spec(proj.semantic_models, ref, proj.connectors), {}, proj.connectors)
    # order_date (quarter-bucketed), status, revenue all present
    assert any(c.endswith("order_date") for c in qr.columns)
    si = next(i for i, c in enumerate(qr.columns) if c.endswith("status"))
    assert {row[si] for row in qr.rows} == {"Won", "Lost"}
    di = next(i for i, c in enumerate(qr.columns) if c.endswith("order_date"))
    # quarter truncation → first-of-quarter dates (Jan/Apr/…); here only Q1s exist
    assert all(str(row[di]).endswith("-01-01") for row in qr.rows)


@needs_bsl
def test_joined_model_groups_by_joined_dimension(example_project):
    """A `by={sales.manager}` (manager lives in the joined `geo` table) pushes
    down a JOIN and groups revenue by the joined column."""
    from dashdown.project import load_project
    from dashdown.python_query import run_python_query
    from dashdown.semantic import build_semantic_spec, resolve_ref

    proj = load_project(example_project)
    assert {"sales", "geo"} <= set(proj.semantic_models)
    ref = resolve_ref(proj.semantic_models, "sales.revenue", "sales.manager")
    assert ref.by == "geo.manager"
    spec = build_semantic_spec(proj.semantic_models, ref, proj.connectors)
    qr = run_python_query(spec, {}, proj.connectors)
    # one row per manager, with revenue summed across the join
    managers = {row[qr.columns.index("geo.manager")] for row in qr.rows}
    assert managers == {"Alice", "Bron", "Chetan", "Dara"}


@needs_bsl
def test_value_metric_single_scalar(example_project):
    """<Value metric={sales.revenue} /> (no `by`) → a one-row aggregate query."""
    from dashdown.project import load_project
    from dashdown.python_query import run_python_query
    from dashdown.semantic import build_semantic_spec, resolve_ref

    proj = load_project(example_project)
    ref = resolve_ref(proj.semantic_models, "sales.revenue", None)
    assert ref.by is None
    spec = build_semantic_spec(proj.semantic_models, ref, proj.connectors)
    qr = run_python_query(spec, {}, proj.connectors)
    assert len(qr.rows) == 1  # a single scalar


@needs_bsl
def test_value_and_table_emit_semantic_query(example_project):
    """Counter, Value and Table register a synthetic semantic query like a chart."""
    from dashdown.render.pipeline import render_page
    from dashdown.project import load_project

    proj = load_project(example_project)
    src = (
        "# t\n\n"
        "<Counter metric={sales.revenue} label='Rev' />\n\n"
        "<Value metric={sales.orders} />\n\n"
        "<Table metric={sales.revenue} by={sales.region} />\n"
    )
    rp = render_page(src, proj.connectors, semantic_models=proj.semantic_models)
    # Counter + Value (no-by) + Table (by-region) all surface a semantic query
    assert any(n.endswith("orders") for n in rp.query_defs)
    assert any(n.endswith("revenue") for n in rp.query_defs)  # Counter scalar
    assert any("by.sales.region" in n for n in rp.query_defs)
    # the model expressions never reach the client
    assert "amount.sum" not in rp.body_html


@needs_bsl
def test_counter_semantic_sparkline_resolves_on_python_def_cache(example_project):
    """A semantic-driven sparkline (Stage 18f) registers its bucketed series as a
    synthetic query on the same `_python_def_cache` seam as the headline scalar,
    and runs to a monthly trend."""
    from dashdown.python_query import run_python_query
    from dashdown.render.pipeline import get_python_query_def, render_page
    from dashdown.project import load_project

    proj = load_project(example_project)
    src = (
        "# t\n\n"
        "<Counter metric={sales.revenue} "
        "sparkline={sales.revenue} sparkline-by={sales.order_date} grain=\"month\" />\n"
    )
    rp = render_page(src, proj.connectors, semantic_models=proj.semantic_models)
    # Both the scalar headline and the bucketed sparkline reach the client defs.
    # (The BSL model prefixes joined field names, so canonical names carry the
    # model segment, e.g. `sales.revenue` → the query name doubles it.)
    headline = next(
        n for n in rp.query_defs if n.endswith("revenue") and ".by." not in n
    )
    spark = next(
        n for n in rp.query_defs if ".by." in n and n.endswith("grain.month")
    )
    # The scalar headline carries no grain segment (grain is for the trend only).
    assert "grain" not in headline
    # The sparkline rides the Python-query cache like any other synthetic spec.
    spec = get_python_query_def(spark, rp.query_defs[spark]["connector"])
    assert spec is not None
    qr = run_python_query(spec, {}, proj.connectors)
    months = sorted({str(row[0])[:7] for row in qr.rows})
    assert months == ["2023-01", "2023-02", "2023-03", "2024-01", "2024-02", "2024-03"]
    # the model expressions never reach the client
    assert "amount.sum" not in rp.body_html


@needs_bsl
def test_date_range_filters_via_time_dimension(example_project):
    from dashdown.project import load_project
    from dashdown.python_query import run_python_query
    from dashdown.semantic import build_semantic_spec, resolve_ref

    proj = load_project(example_project)
    # The date range (a filter) and grain="month" (a grouping) are orthogonal: the
    # range constrains which rows, the grain buckets them — both on `order_date`.
    ref = resolve_ref(proj.semantic_models, "sales.revenue", "sales.order_date", grain="month")
    spec = build_semantic_spec(proj.semantic_models, ref, proj.connectors)
    qr = run_python_query(
        spec, {DATE_START_PARAM: "2024-02-01", DATE_END_PARAM: "2024-03-31"}, proj.connectors
    )
    months = {str(row[0])[:7] for row in qr.rows}  # order_date truncated to month
    assert months == {"2024-02", "2024-03"}  # date range pushed down to order_date


@needs_bsl
def test_grain_truncates_time_dimension_pushdown(example_project):
    """`grain="month"` threads `time_grain` into the BSL query → order_date buckets
    by month (the `.truncate()` runs in DuckDB, not Python)."""
    from dashdown.project import load_project
    from dashdown.python_query import run_python_query
    from dashdown.semantic import build_semantic_spec, resolve_ref

    proj = load_project(example_project)
    ref = resolve_ref(proj.semantic_models, "sales.revenue", "sales.order_date", grain="month")
    assert ref.grain == "month" and ref.query_name.endswith("grain.month")
    qr = run_python_query(build_semantic_spec(proj.semantic_models, ref, proj.connectors), {}, proj.connectors)
    months = sorted({str(row[0])[:7] for row in qr.rows})
    # the example data spans Jan–Mar of 2023 and 2024 → six monthly buckets
    assert months == ["2023-01", "2023-02", "2023-03", "2024-01", "2024-02", "2024-03"]

    # A coarser grain on the same model is a *distinct* query that coexists.
    ref_y = resolve_ref(proj.semantic_models, "sales.revenue", "sales.order_date", grain="year")
    assert ref_y.query_name != ref.query_name
    qy = run_python_query(build_semantic_spec(proj.semantic_models, ref_y, proj.connectors), {}, proj.connectors)
    assert sorted({str(row[0])[:4] for row in qy.rows}) == ["2023", "2024"]


@needs_bsl
def test_interactive_grain_reads_control_param(example_project):
    """`grain={control}` is one synthetic query whose grain varies with the live param."""
    from dashdown.project import load_project
    from dashdown.python_query import run_python_query
    from dashdown.semantic import build_semantic_spec, resolve_ref

    proj = load_project(example_project)
    ref = resolve_ref(proj.semantic_models, "sales.revenue", "sales.order_date", grain_param="g")
    assert "grain" not in ref.query_name  # one def, regardless of the chosen grain
    spec = build_semantic_spec(proj.semantic_models, ref, proj.connectors)
    # year bucket
    qy = run_python_query(spec, {"g": "year"}, proj.connectors)
    assert sorted({str(row[0])[:4] for row in qy.rows}) == ["2023", "2024"]
    # month bucket from the *same* spec
    qm = run_python_query(spec, {"g": "month"}, proj.connectors)
    assert len({str(row[0])[:7] for row in qm.rows}) == 6
    # empty control → native granularity (no truncation), more rows than the buckets
    qn = run_python_query(spec, {}, proj.connectors)
    assert len(qn.rows) >= len(qm.rows)


@needs_bsl
def test_grain_finer_than_smallest_is_rejected(example_project):
    """A grain finer than the dimension can express surfaces as the component error
    card (BSL/Ibis reject the truncation; we don't pre-validate availability)."""
    from dashdown.project import load_project
    from dashdown.python_query import run_python_query
    from dashdown.semantic import build_semantic_spec, resolve_ref

    proj = load_project(example_project)
    # order_date is a DATE with smallest_time_grain TIME_GRAIN_DAY → sub-day is invalid
    ref = resolve_ref(proj.semantic_models, "sales.revenue", "sales.order_date", grain="second")
    spec = build_semantic_spec(proj.semantic_models, ref, proj.connectors)
    with pytest.raises(Exception):
        run_python_query(spec, {}, proj.connectors)


# --------------------------------------------------------------------------- #
# End-to-end render + data API + gate
# --------------------------------------------------------------------------- #


@needs_bsl
def test_render_registers_and_data_api_resolves(example_project):
    from fastapi.testclient import TestClient

    from dashdown.server import create_app

    app = create_app(example_project)
    client = TestClient(app)

    page = client.get("/")
    assert page.status_code == 200
    # a revenue-by-region semantic query reached the client query_defs
    assert "revenue.by" in page.text and "region" in page.text
    # The model's measure *expression* must never reach the client through the
    # query metadata the framework emits — scope the check to the `query_defs`
    # JSON blob (the page prose legitimately shows `<LineChart …/>` HTML examples,
    # but never the model's `_.amount.sum(...)` measure defs).
    import re
    defs_blob = re.search(
        r'<script id="dashdown-query-defs"[^>]*>(.*?)</script>', page.text, re.S
    )
    assert defs_blob, "query_defs script not found"
    assert "amount.sum" not in defs_blob.group(1)
    assert "_.amount" not in defs_blob.group(1)

    # find the single-metric revenue-by-month (grain=month) synthetic query and fetch
    # it (the page also has a multi-metric revenue+avg_deal-by-month chart, whose name
    # carries `revenue.avg_deal.by.…`; the literal grain lands a `.grain.month` segment)
    names = set(re.findall(r"_sem[\w.]+", page.text))
    month_q = next(n for n in names if n.endswith("revenue.by.sales.order_date.grain.month"))
    data = client.get(
        f"/_dashdown/api/data/{month_q}", params={"_connector": "main"}
    )
    assert data.status_code == 200
    body = data.json()
    assert len(body["columns"]) == 2 and body["columns"][0].endswith("order_date")
    assert len(body["rows"]) > 0


@needs_bsl
def test_global_date_shown_for_semantic_time_dimension(example_project):
    from fastapi.testclient import TestClient

    from dashdown.server import create_app

    client = TestClient(create_app(example_project))
    page = client.get("/")
    assert "date_start" in page.text


@needs_bsl
def test_python_queries_disabled_skips_semantic(tmp_path):
    from dashdown.project import load_project

    (tmp_path / "dashdown.yaml").write_text("title: t\npython_queries:\n  enabled: false\n")
    (tmp_path / "sources.yaml").write_text("main:\n  type: csv\n  directory: data\n")
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "orders.csv").write_text("region,amount\nEast,10\n")
    sem = tmp_path / "semantic"
    sem.mkdir()
    (sem / "sales.yml").write_text(
        "sales:\n  connector: main\n  table: orders\n"
        "  dimensions: {region: _.region}\n  measures: {revenue: _.amount.sum()}\n"
    )
    proj = load_project(tmp_path)
    assert proj.semantic_models == {}
