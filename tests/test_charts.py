"""Tests for chart components (Stage 5: scatter/treemap/funnel)."""
import json

import pytest

import dashdown.components  # noqa: F401  (registers built-ins)
from dashdown.components.base import RenderContext, get_component
from dashdown.render.components import render_components


@pytest.fixture
def ctx():
    return RenderContext(queries={}, params={}, current_path="/")


def _config_of(html: str) -> dict:
    """Extract and parse the data-config JSON from a chart placeholder."""
    import html as html_mod
    import re

    m = re.search(r'data-config="([^"]*)"', html)
    assert m, f"no data-config found in: {html}"
    return json.loads(html_mod.unescape(m.group(1)))


@pytest.mark.parametrize(
    "tag,expected_type",
    [
        ("ScatterChart", "scatter"),
        ("TreemapChart", "treemap"),
        ("FunnelChart", "funnel"),
    ],
)
def test_chart_registered(tag, expected_type):
    assert get_component(tag) is not None


@pytest.mark.parametrize(
    "tag,expected_type",
    [
        ("ScatterChart", "scatter"),
        ("TreemapChart", "treemap"),
        ("FunnelChart", "funnel"),
    ],
)
def test_chart_renders_async_placeholder(ctx, tag, expected_type):
    html = render_components(
        f'<{tag} data={{sales}} x="month" y="revenue" title="T" />', ctx
    )
    assert 'data-async-component="chart"' in html
    assert 'data-query-name="sales"' in html
    config = _config_of(html)
    assert config["type"] == expected_type
    assert config["query_name"] == "sales"
    assert config["x"] == "month"
    assert config["y"] == "revenue"
    assert config["title"] == "T"


@pytest.mark.parametrize("tag", ["ScatterChart", "TreemapChart", "FunnelChart"])
def test_chart_requires_data(ctx, tag):
    # Missing data → component error card, not a crash.
    html = render_components(f'<{tag} x="a" y="b" />', ctx)
    assert "error" in html.lower()


@pytest.mark.parametrize("tag", ["ScatterChart", "TreemapChart", "FunnelChart"])
def test_chart_requires_x_and_y(ctx, tag):
    html = render_components(f"<{tag} data={{sales}} />", ctx)
    assert "error" in html.lower()


def test_scatter_passes_series(ctx):
    html = render_components(
        '<ScatterChart data={pts} x="weight" y="height" series="group" />', ctx
    )
    config = _config_of(html)
    assert config["series_by"] == "group"


@pytest.mark.parametrize("tag", ["LineChart", "BarChart", "PieChart", "ScatterChart"])
def test_chart_has_fullscreen_button(ctx, tag):
    """Every chart card carries the hover-revealed ⛶ fullscreen button (a
    distinct class from the `explain` sparkle, so ask.js doesn't grab it)."""
    html = render_components(f'<{tag} data={{sales}} x="m" y="r" />', ctx)
    assert "dashdown-chart-expand-btn" in html
    assert "dashdown-explain-btn" not in html  # no `explain` attr → no sparkle


def test_chart_fullscreen_button_present_in_static_build():
    """Both corner affordances ship in an export: fullscreen is pure
    client-side, and `explain` reads its baked _ask/{id}.json snapshot on
    first open (the answer + annotations are generated at build time)."""
    ctx = RenderContext(queries={}, params={}, current_path="/", static_build=True)
    html = render_components('<LineChart data={s} x="m" y="r" explain />', ctx)
    assert "dashdown-chart-expand-btn" in html
    assert "dashdown-explain-btn" in html


def test_chart_fullscreen_and_explain_coexist(ctx):
    """With `explain` set, both corner affordances render on the card."""
    html = render_components('<LineChart data={s} x="m" y="r" explain />', ctx)
    assert "dashdown-chart-expand-btn" in html
    assert "dashdown-explain-btn" in html


def test_bar_multi_metric_y_passes_through(ctx):
    """`y="revenue,profit"` ships verbatim so chart.js builds one series per metric."""
    html = render_components(
        '<BarChart data={sales} x="month" y="revenue,profit" />', ctx
    )
    config = _config_of(html)
    assert config["y"] == "revenue,profit"


def test_pie_series_passes_through(ctx):
    """`series=` on a PieChart ships as `series_by` → faceted small-multiples in JS."""
    html = render_components(
        '<PieChart data={sales} x="category" y="revenue" series="region" />', ctx
    )
    config = _config_of(html)
    assert config["type"] == "pie"
    assert config["series_by"] == "region"


def test_pie_donut_default_unset(ctx):
    # No donut attr → key absent; the JS defaults to donut.
    html = render_components('<PieChart data={d} x="cat" y="n" />', ctx)
    config = _config_of(html)
    assert config["type"] == "pie"
    assert "donut" not in config


def test_pie_donut_opt_out(ctx):
    html = render_components('<PieChart data={d} x="cat" y="n" donut=false />', ctx)
    config = _config_of(html)
    assert config["donut"] is False


def test_pie_donut_explicit_true(ctx):
    html = render_components('<PieChart data={d} x="cat" y="n" donut />', ctx)
    config = _config_of(html)
    assert config["donut"] is True


def test_bar_horizontal_flag_absent_by_default(ctx):
    # No `horizontal` attr → key omitted; the JS treats it as a vertical bar.
    config = _config_of(render_components('<BarChart data={s} x="m" y="r" />', ctx))
    assert config["type"] == "bar"
    assert "horizontal" not in config


def test_bar_horizontal_flag_set(ctx):
    config = _config_of(
        render_components('<BarChart data={s} x="m" y="r" horizontal />', ctx)
    )
    assert config["type"] == "bar"
    assert config["horizontal"] is True


def test_bar_horizontal_explicit_false_omits_flag(ctx):
    config = _config_of(
        render_components('<BarChart data={s} x="m" y="r" horizontal=false />', ctx)
    )
    assert "horizontal" not in config


# ---------------------------------------------------------------------------
# Stage 9: CalendarHeatmap / BoxPlot / Violin / MapChart / Chart auto
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "tag,expected_type",
    [
        ("CalendarHeatmap", "calendar"),
        ("BoxPlot", "boxplot"),
        ("Violin", "violin"),
        ("MapChart", "map"),
        ("Chart", "auto"),
        ("PivotTable", None),
    ],
)
def test_stage9_components_registered(tag, expected_type):
    assert get_component(tag) is not None


def test_calendar_heatmap_date_value_aliases(ctx):
    html = render_components(
        '<CalendarHeatmap data={daily} date="day" value="count" title="Activity" />',
        ctx,
    )
    assert 'data-async-component="chart"' in html
    config = _config_of(html)
    assert config["type"] == "calendar"
    assert config["x"] == "day"
    assert config["y"] == "count"
    assert config["title"] == "Activity"


def test_calendar_heatmap_accepts_explicit_xy(ctx):
    config = _config_of(
        render_components('<CalendarHeatmap data={daily} x="d" y="v" />', ctx)
    )
    assert config["x"] == "d"
    assert config["y"] == "v"


def test_calendar_heatmap_requires_date(ctx):
    html = render_components('<CalendarHeatmap data={daily} value="v" />', ctx)
    assert "error" in html.lower()


def test_boxplot_x_is_optional(ctx):
    config = _config_of(render_components('<BoxPlot data={orders} y="amount" />', ctx))
    assert config["type"] == "boxplot"
    assert config["y"] == "amount"
    assert not config["x"]


def test_boxplot_requires_y(ctx):
    html = render_components('<BoxPlot data={orders} x="category" />', ctx)
    assert "error" in html.lower()


def test_violin_renders(ctx):
    config = _config_of(
        render_components('<Violin data={orders} x="region" y="amount" />', ctx)
    )
    assert config["type"] == "violin"


def test_map_chart_location_value_aliases(ctx):
    config = _config_of(
        render_components(
            '<MapChart data={geo} location="country" value="sales" />', ctx
        )
    )
    assert config["type"] == "map"
    assert config["x"] == "country"
    assert config["y"] == "sales"
    assert config["map"] == "world"
    assert config["geojson"] is None


def test_map_chart_custom_geojson(ctx):
    config = _config_of(
        render_components(
            '<MapChart data={geo} location="state" value="v" map="us" '
            'geojson="/assets/us.json" />',
            ctx,
        )
    )
    assert config["map"] == "us"
    assert config["geojson"] == "/assets/us.json"


def test_map_chart_requires_location_and_value(ctx):
    html = render_components("<MapChart data={geo} />", ctx)
    assert "error" in html.lower()


def test_chart_auto_needs_no_axes(ctx):
    config = _config_of(render_components("<Chart auto data={sales} />", ctx))
    assert config["type"] == "auto"
    assert config["query_name"] == "sales"


def test_chart_auto_passes_explicit_axes(ctx):
    config = _config_of(
        render_components('<Chart auto data={sales} x="month" series="region" />', ctx)
    )
    assert config["x"] == "month"
    assert config["series_by"] == "region"


def test_chart_without_auto_flag_errors(ctx):
    html = render_components("<Chart data={sales} />", ctx)
    assert "error" in html.lower()


def test_chart_format_attrs_in_config(ctx):
    """`format`/`currency`/`decimals` flow into a chart's data-config so the JS
    value-axis formatter can humanize axis labels."""
    config = _config_of(
        render_components(
            '<BarChart data={sales} x="month" y="revenue" '
            'format="currency" currency="$" decimals=0 />',
            ctx,
        )
    )
    assert config["format"] == "currency"
    assert config["currency"] == "$"
    assert config["decimals"] == 0


def test_chart_no_format_attrs_absent(ctx):
    """Unset format attrs stay out of config so ECharts keeps its raw default."""
    config = _config_of(
        render_components('<LineChart data={sales} x="month" y="revenue" />', ctx)
    )
    assert "format" not in config
    assert "currency" not in config
    assert "decimals" not in config


# ---------------------------------------------------------------------------
# Stage 18: RadarChart / GaugeChart / HeatmapChart / SankeyChart /
# CandlestickChart / ThemeRiver
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "tag,expected_type",
    [
        ("RadarChart", "radar"),
        ("GaugeChart", "gauge"),
        ("HeatmapChart", "heatmap"),
        ("SankeyChart", "sankey"),
        ("CandlestickChart", "candlestick"),
        ("ThemeRiver", "themeriver"),
    ],
)
def test_stage18_components_registered(tag, expected_type):
    assert get_component(tag) is not None


def test_radar_passes_indicator_value_series(ctx):
    config = _config_of(
        render_components(
            '<RadarChart data={scores} x="metric" y="score" series="product" />', ctx
        )
    )
    assert config["type"] == "radar"
    assert config["x"] == "metric"
    assert config["y"] == "score"
    assert config["series_by"] == "product"


def test_radar_series_optional(ctx):
    config = _config_of(
        render_components('<RadarChart data={scores} x="metric" y="score" />', ctx)
    )
    assert config["type"] == "radar"
    assert not config["series_by"]


def test_gauge_needs_no_x(ctx):
    config = _config_of(
        render_components('<GaugeChart data={goal} y="pct" title="Goal" />', ctx)
    )
    assert config["type"] == "gauge"
    assert config["y"] == "pct"
    assert not config["x"]


def test_gauge_min_max_in_config(ctx):
    config = _config_of(
        render_components('<GaugeChart data={goal} y="pct" min=10 max=200 />', ctx)
    )
    assert config["min"] == 10
    assert config["max"] == 200


def test_gauge_min_max_absent_by_default(ctx):
    config = _config_of(render_components('<GaugeChart data={goal} y="pct" />', ctx))
    assert "min" not in config
    assert "max" not in config


def test_gauge_requires_y(ctx):
    html = render_components("<GaugeChart data={goal} />", ctx)
    assert "error" in html.lower()


def test_heatmap_value_in_config(ctx):
    config = _config_of(
        render_components(
            '<HeatmapChart data={grid} x="month" y="channel" value="downloads" />', ctx
        )
    )
    assert config["type"] == "heatmap"
    assert config["x"] == "month"
    assert config["y"] == "channel"
    assert config["value"] == "downloads"


def test_heatmap_requires_value(ctx):
    html = render_components('<HeatmapChart data={grid} x="month" y="channel" />', ctx)
    assert "error" in html.lower()


def test_heatmap_requires_x_and_y(ctx):
    html = render_components('<HeatmapChart data={grid} value="v" />', ctx)
    assert "error" in html.lower()


def test_sankey_source_target_value_aliases(ctx):
    config = _config_of(
        render_components(
            '<SankeyChart data={flow} source="a" target="b" value="n" />', ctx
        )
    )
    assert config["type"] == "sankey"
    assert config["x"] == "a"  # source aliases x
    assert config["y"] == "b"  # target aliases y
    assert config["value"] == "n"


def test_sankey_requires_source_target_value(ctx):
    for src in (
        '<SankeyChart data={flow} target="b" value="n" />',
        '<SankeyChart data={flow} source="a" value="n" />',
        '<SankeyChart data={flow} source="a" target="b" />',
    ):
        assert "error" in render_components(src, ctx).lower()


def test_candlestick_ohlc_in_config(ctx):
    config = _config_of(
        render_components(
            '<CandlestickChart data={p} x="day" open="o" high="h" low="l" close="c" />',
            ctx,
        )
    )
    assert config["type"] == "candlestick"
    assert config["x"] == "day"
    assert config["open"] == "o"
    assert config["high"] == "h"
    assert config["low"] == "l"
    assert config["close"] == "c"


def test_candlestick_requires_all_ohlc(ctx):
    html = render_components(
        '<CandlestickChart data={p} x="day" open="o" high="h" />', ctx
    )
    assert "error" in html.lower()


def test_themeriver_requires_series(ctx):
    html = render_components('<ThemeRiver data={s} x="date" y="value" />', ctx)
    assert "error" in html.lower()


def test_themeriver_passes_axes(ctx):
    config = _config_of(
        render_components(
            '<ThemeRiver data={s} x="date" y="value" series="metric" />', ctx
        )
    )
    assert config["type"] == "themeriver"
    assert config["x"] == "date"
    assert config["y"] == "value"
    assert config["series_by"] == "metric"


# ---------------------------------------------------------------------------
# Stage 18b: GraphChart / SunburstChart / TreeChart / ParallelChart + stacked
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "tag,expected_type",
    [
        ("GraphChart", "graph"),
        ("SunburstChart", "sunburst"),
        ("TreeChart", "tree"),
        ("ParallelChart", "parallel"),
    ],
)
def test_stage18b_components_registered(tag, expected_type):
    assert get_component(tag) is not None


def test_graph_source_target_value_aliases(ctx):
    config = _config_of(
        render_components(
            '<GraphChart data={e} source="a" target="b" value="w" />', ctx
        )
    )
    assert config["type"] == "graph"
    assert config["x"] == "a"
    assert config["y"] == "b"
    assert config["value"] == "w"


def test_graph_value_optional(ctx):
    config = _config_of(
        render_components('<GraphChart data={e} source="a" target="b" />', ctx)
    )
    assert config["type"] == "graph"
    assert "value" not in config


def test_graph_requires_source_and_target(ctx):
    assert "error" in render_components('<GraphChart data={e} source="a" />', ctx).lower()
    assert "error" in render_components('<GraphChart data={e} target="b" />', ctx).lower()


@pytest.mark.parametrize("tag,expected_type", [("SunburstChart", "sunburst"), ("TreeChart", "tree")])
def test_hierarchy_id_parent_value_label(ctx, tag, expected_type):
    config = _config_of(
        render_components(
            f'<{tag} data={{org}} id="id" parent="parent" value="hc" label="name" />',
            ctx,
        )
    )
    assert config["type"] == expected_type
    assert config["node_id"] == "id"
    assert config["parent"] == "parent"
    assert config["value"] == "hc"
    assert config["label"] == "name"


@pytest.mark.parametrize("tag", ["SunburstChart", "TreeChart"])
def test_hierarchy_value_label_optional(ctx, tag):
    config = _config_of(
        render_components(f'<{tag} data={{org}} id="id" parent="parent" />', ctx)
    )
    assert config["node_id"] == "id"
    assert "value" not in config
    assert "label" not in config


@pytest.mark.parametrize("tag", ["SunburstChart", "TreeChart"])
def test_hierarchy_requires_id_and_parent(ctx, tag):
    assert "error" in render_components(f'<{tag} data={{org}} id="id" />', ctx).lower()
    assert "error" in render_components(f'<{tag} data={{org}} parent="p" />', ctx).lower()


def test_parallel_dimensions_split(ctx):
    config = _config_of(
        render_components(
            '<ParallelChart data={s} dimensions="price, speed, battery" series="tier" />',
            ctx,
        )
    )
    assert config["type"] == "parallel"
    assert config["dimensions"] == ["price", "speed", "battery"]
    assert config["series_by"] == "tier"


def test_parallel_requires_two_dimensions(ctx):
    assert "error" in render_components('<ParallelChart data={s} dimensions="price" />', ctx).lower()
    assert "error" in render_components("<ParallelChart data={s} />", ctx).lower()


def test_bar_stacked_flag(ctx):
    config = _config_of(
        render_components('<BarChart data={s} x="m" y="v" series="c" stacked />', ctx)
    )
    assert config["stacked"] is True
    assert "horizontal" not in config


def test_bar_stacked_with_horizontal(ctx):
    config = _config_of(
        render_components(
            '<BarChart data={s} x="m" y="v" series="c" stacked horizontal />', ctx
        )
    )
    assert config["stacked"] is True
    assert config["horizontal"] is True


def test_line_stacked_flag(ctx):
    config = _config_of(
        render_components('<LineChart data={s} x="m" y="v" series="c" stacked />', ctx)
    )
    assert config["stacked"] is True


def test_stacked_absent_by_default(ctx):
    assert "stacked" not in _config_of(
        render_components('<BarChart data={s} x="m" y="v" />', ctx)
    )
    assert "stacked" not in _config_of(
        render_components('<LineChart data={s} x="m" y="v" />', ctx)
    )


# --- chart drill-down link ---------------------------------------------------


def test_chart_link_in_config(ctx):
    """`link="/detail/{col}"` flows into data-config so chart.js can navigate
    on data-point click (same {column} grammar as a table row_link)."""
    config = _config_of(
        render_components(
            '<BarChart data={s} x="region" y="v" link="/regions/{region}" />', ctx
        )
    )
    assert config["link"] == "/regions/{region}"


def test_chart_link_absent_by_default(ctx):
    assert "link" not in _config_of(
        render_components('<BarChart data={s} x="region" y="v" />', ctx)
    )


def test_chart_card_carries_png_button(ctx):
    html = render_components('<BarChart data={s} x="region" y="v" />', ctx)
    assert "dashdown-chart-png-btn" in html
    assert "dashdown-chart-expand-btn" in html
