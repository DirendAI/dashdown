---
title: Charts
sidebar_label: Charts
sidebar_position: 1
icon: "\U0001F4C8"
---

# Charts

Every chart type shares one rendering path (ECharts, drawn client-side from the
query result), so they share most attributes. Pick a type below for a live
example; the common attributes are here.

## Shared attributes

| Attribute      | Purpose                                                         |
| -------------- | --------------------------------------------------------------- |
| `data`         | **Required.** The query to plot (`data={query}`).               |
| `x`            | Column for the category / x-axis.                               |
| `y`            | Column for the value / y-axis — or several, comma-separated, for multiple metrics. |
| `series`       | A second dimension — split one value column into a series per group. |
| `title`        | Chart title.                                                    |
| `sort_by`      | Column to sort the data by before plotting.                     |
| `color`        | A single color or comma-separated palette override.             |
| `height`       | Pixel height (default `300`).                                   |
| `col-span`     | Columns to span inside a `<Grid>`.                              |
| `format`, `currency`, `decimals`, `locale`, `date_format` | Value-axis & tooltip number/date formatting — see [Formatting](/formatting). |
| `empty_message`| Message shown (centered) when the query returns no rows, for every chart type. Default `"No data available"`. |
| `explain`      | A hover-revealed ✨ button that generates on-demand AI commentary below the plot (needs an `llm:` block); `explain="…"` asks your own question, `cache_ttl=` tunes the answer cache — see [Ask → Explain any chart](/ai/ask#explain-any-chart). |

A few types take their own attributes on top of the shared set — distribution
charts ([BoxPlot](/components/charts/box-plot),
[Violin](/components/charts/violin)), [MapChart](/components/maps#mapchart),
[HeatmapChart](/components/charts/heatmap-chart) (a `value` column),
[SankeyChart](/components/charts/sankey-chart) /
[GraphChart](/components/charts/graph-chart) (`source`/`target`/`value`),
[CandlestickChart](/components/charts/candlestick-chart)
(`open`/`high`/`low`/`close`), [GaugeChart](/components/charts/gauge-chart)
(`min`/`max`), [SunburstChart](/components/charts/sunburst-chart) /
[TreeChart](/components/charts/tree-chart) (`id`/`parent`/`value`/`label`), and
[ParallelChart](/components/charts/parallel-chart) (`dimensions`) — see their
pages.

[LineChart](/components/charts/line-chart) and [BarChart](/components/charts/bar-chart)
also take **`stacked`** — with a `series` column it stacks the groups on a shared
total (a stacked area / stacked bar).

## Multiple series

There are two ways to draw more than one coloured series — pick by the shape of
your data:

| You have… | Use | Result |
| --------- | --- | ------ |
| one value column + a category to split by | **`series="region"`** (a second dimension) | one series per category value |
| several value columns side by side | **`y="revenue,profit"`** (comma-separated) | one series per metric |

```markdown
<!-- second dimension: one metric, split by a category -->
<BarChart data={by_channel} x="month" y="downloads" series="channel" />

<!-- multiple metrics: several value columns at once -->
<BarChart data={downloads_by_channel_wide} x="month" y="pip,docker,source" />
```

<Grid cols=2>
  <BarChart data={by_channel} x="month" y="downloads" series="channel" title="series= (2nd dimension)" />
  <BarChart data={downloads_by_channel_wide} x="month" y="pip,docker,source" title="multi-metric y=" />
</Grid>

Add `stacked` to stack the groups on a shared total:

<BarChart data={by_channel} x="month" y="downloads" series="channel" stacked title="Stacked by channel" />

Both give a legend and a colour per series; they're **mutually exclusive** (if you
set both, `series` wins). On a [PieChart](/components/charts/pie-chart), `series=`
instead renders **faceted small multiples** — one pie per value, sharing a slice
legend. The same `series=` / multi-metric grammar works on
[semantic-layer](/semantic-layer) charts (`series={model.dim}` /
`metric="model.a,model.b"`).

## The chart types

| Type | Best for |
| ---- | -------- |
| [LineChart](/components/charts/line-chart) | Trends over time |
| [BarChart](/components/charts/bar-chart) | Comparing categories |
| [ComboChart](/components/charts/combo-chart) | Bars + lines together, with a second y-axis |
| [PieChart](/components/charts/pie-chart) | Part-to-whole |
| [ScatterChart](/components/charts/scatter-chart) | Correlation between two numbers |
| [FunnelChart](/components/charts/funnel-chart) | Stage-by-stage drop-off |
| [TreemapChart](/components/charts/treemap-chart) | Nested proportions |
| [CalendarHeatmap](/components/charts/calendar-heatmap) | Daily values over a year |
| [BoxPlot](/components/charts/box-plot) / [Violin](/components/charts/violin) | Distributions |
| [MapChart](/components/maps#mapchart) | Values by geography |
| [RadarChart](/components/charts/radar-chart) | Comparing many metrics at once |
| [GaugeChart](/components/charts/gauge-chart) | A single KPI against a target |
| [HeatmapChart](/components/charts/heatmap-chart) | Intensity across a category grid |
| [SankeyChart](/components/charts/sankey-chart) | Flows between stages |
| [CandlestickChart](/components/charts/candlestick-chart) | OHLC price / range data |
| [ThemeRiver](/components/charts/theme-river) | Category streams over time |
| [GraphChart](/components/charts/graph-chart) | Relationships in a network |
| [SunburstChart](/components/charts/sunburst-chart) | Hierarchy as proportional rings |
| [TreeChart](/components/charts/tree-chart) | Hierarchy as a node-link diagram |
| [ParallelChart](/components/charts/parallel-chart) | Many numeric dimensions at once |
| [Chart auto](/components/charts/auto-chart) | Let Dashdown infer the type |
