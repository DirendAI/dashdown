<!-- AUTO-GENERATED from docs/pages/ by tooling/gen-agent-docs.py — do not edit. -->
<!-- Topic: components. Regenerate with: python tooling/gen-agent-docs.py -->

<!-- source: docs/pages/components/index.md -->

# Components

A component is a PascalCase tag you drop into a page. Most visual components take
a `data={query}` reference and render in the browser from that query's result.
Unknown tags or render errors become an inline error card — never a 500.

## Charts

One rendering path drives every chart type. See **[Charts](/components/charts)**
for the shared attributes, then the per-type page:

- [LineChart](/components/charts/line-chart) · [BarChart](/components/charts/bar-chart) · [ComboChart](/components/charts/combo-chart) · [PieChart](/components/charts/pie-chart) · [ScatterChart](/components/charts/scatter-chart)
- [FunnelChart](/components/charts/funnel-chart) · [TreemapChart](/components/charts/treemap-chart) · [CalendarHeatmap](/components/charts/calendar-heatmap)
- [BoxPlot](/components/charts/box-plot) · [Violin](/components/charts/violin) · [MapChart](/components/maps#mapchart)
- [RadarChart](/components/charts/radar-chart) · [GaugeChart](/components/charts/gauge-chart) · [HeatmapChart](/components/charts/heatmap-chart)
- [SankeyChart](/components/charts/sankey-chart) · [CandlestickChart](/components/charts/candlestick-chart) · [ThemeRiver](/components/charts/theme-river)
- [GraphChart](/components/charts/graph-chart) · [SunburstChart](/components/charts/sunburst-chart) · [TreeChart](/components/charts/tree-chart) · [ParallelChart](/components/charts/parallel-chart)
- [Chart auto](/components/charts/auto-chart)

## Maps

- [Maps](/components/maps) — five SVG geo components joining on ISO country
  codes: ChoroplethTime (animated), ChoroplethFacets (small multiples),
  BivariateMap, BubbleMap, DotDensityMap. All offline and static-export safe.

## Data display

- [Table](/components/table) — sortable, filterable grid with CSV export.
- [PivotTable](/components/pivot-table) — client-side cross-tab.
- [Counter](/components/counter) — a single KPI with an optional delta.
- [Value](/components/value) — an inline single value.

## Layout & content

- [Grid](/components/grid) — multi-column layout for widgets.
- [Tabs](/components/tabs) — switchable tabbed sections.
- [Ask](/ai/ask) — LLM commentary on a query result (documented under [AI](/ai)).

## Filters & search

- [Dropdown](/components/dropdown) · [Combobox](/components/combobox) · [Search](/components/search) · [DateRange](/components/date-range) · [RangeSlider](/components/range-slider) · [Slider](/components/slider) · [ButtonGroup](/components/button-group) · [Toggle](/components/toggle) · [TimeGrain](/components/time-grain) — see also the [Filters](/filters) concept page.
- [SiteSearch](/components/site-search) — full-text search across all pages.


<!-- source: docs/pages/components/charts/index.md -->

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
| `annotations`  | `false` keeps an explained chart **commentary-only** — the AI never proposes (or draws) [marks on the plot](/ai/ask#annotations-on-the-chart). Default `true`. |

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
  <BarChart data={by_channel} x="month" y="downloads" series="channel" title="series= (2nd dimension)" explain />
  <BarChart data={downloads_by_channel_wide} x="month" y="pip,docker,source" title="multi-metric y=" explain />
</Grid>

Add `stacked` to stack the groups on a shared total:

<BarChart data={by_channel} x="month" y="downloads" series="channel" stacked title="Stacked by channel" explain />

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


<!-- source: docs/pages/components/charts/line-chart.md -->

# LineChart

Trends over a continuous or time axis. Add `series` to draw one line per group.

```markdown
<LineChart data={by_channel} x="month" y="downloads" series="channel"
           title="Downloads by channel" format="number" />
```

<LineChart data={by_channel} x="month" y="downloads" series="channel" title="Downloads by channel" explain />

Without `series` you get a single line:

<LineChart data={downloads_by_month} x="month" y="downloads" title="Total downloads" explain />

Add `stacked` (with a `series`) for a stacked-area chart:

<LineChart data={by_channel} x="month" y="downloads" series="channel" stacked title="Downloads by channel (stacked)" explain />

Or pass a comma-separated `y` for one line per metric column (no `series` needed):

<LineChart data={downloads_by_channel_wide} x="month" y="pip,docker,source" title="Downloads per channel (multi-metric)" explain />

## From the semantic layer

Like every chart, LineChart also takes [semantic metric refs](/semantic-layer)
instead of `data={query}` — and a date `by` buckets on demand with `grain=`:

```markdown
<LineChart metric={sales.revenue} by={sales.order_date} grain="month" />
```

`series={model.dim}` splits one metric into a line per value; a comma-separated
`metric=` draws one line per measure.

## Attributes

| Attribute | Purpose |
| --------- | ------- |
| `data` | **Required.** The query to plot (`data={query}`). |
| `x` | **Required.** Column for the x-axis (category / time). |
| `y` | **Required.** Column for the value (y-axis). |
| `series` | Column to split into one line per group. |
| `stacked` | With `series`, stack the lines into a cumulative area. |
| `title` | Chart title. |
| `sort_by` | Column to sort by before plotting. |
| `color` | Single color or comma-separated palette override. |
| `height` | Pixel height (default `300`). |
| `col-span` | Columns to span inside a `<Grid>`. |
| `format` · `currency` · `decimals` · `locale` · `date_format` | Value & tooltip number/date formatting. |
| `empty_message` | Message shown when the query returns no rows (default `"No data available"`). |

`stacked` pairs with `series`; the rest are the shared chart attributes — common to every chart type — see [Charts](/components/charts).


<!-- source: docs/pages/components/charts/bar-chart.md -->

# BarChart

Compare values across categories. Add `horizontal` to swap the axes (category on
the Y axis), or `series` for grouped bars — and `stacked` to stack those groups.

```markdown
<BarChart data={channel_totals} x="channel" y="downloads" title="By channel" />
```

<BarChart data={channel_totals} x="channel" y="downloads" title="Total by channel" explain />

Horizontal:

<BarChart data={channel_totals} x="channel" y="downloads" horizontal title="By channel (horizontal)" explain />

Grouped by series and **stacked**:

<BarChart data={by_channel} x="month" y="downloads" series="channel" stacked title="Downloads by month (stacked)" explain />

## Multiple metrics

When your value columns are *already* side by side (one column per metric), list
them in `y`, comma-separated — each becomes its own coloured series with a legend.
No `series=` grouping needed:

```markdown
<BarChart data={downloads_by_channel_wide} x="month" y="pip,docker,source"
          title="Downloads by channel" />
```

<BarChart data={downloads_by_channel_wide} x="month" y="pip,docker,source" title="Downloads by channel" explain />

This is the complement of `series=`: use **`series=`** to split *one* value column
by a category, or **a comma-separated `y`** to plot *several* value columns. The
two are mutually exclusive — see [Multiple series](/components/charts#multiple-series).

## From the semantic layer

Like every chart, BarChart also takes [semantic metric refs](/semantic-layer)
instead of `data={query}`:

```markdown
<BarChart metric={sales.revenue} by={sales.region} />
```

A comma-separated `metric=` gives one series per measure, and `series={model.dim}`
splits a single metric by a second dimension — the same two shapes as a `data=`
chart. A time `by` buckets on demand with `grain=`.

## Attributes

| Attribute | Purpose |
| --------- | ------- |
| `data` | **Required.** The query to plot (`data={query}`). |
| `x` | **Required.** Category column. |
| `y` | **Required.** Value column — or several, comma-separated (`y="pip,docker"`), for one series per metric. |
| `horizontal` | Swap the axes — category on the Y axis, bars running along X. |
| `series` | Column to split into grouped bars (a second dimension). |
| `stacked` | With `series`, stack the groups on a shared total. |
| `title` | Chart title. |
| `sort_by` | Column to sort by before plotting. |
| `color` | Single color or comma-separated palette override. |
| `height` | Pixel height (default `300`). |
| `col-span` | Columns to span inside a `<Grid>`. |
| `format` · `currency` · `decimals` · `locale` · `date_format` | Value & tooltip number/date formatting. |
| `empty_message` | Text shown when the query returns no rows. |

`horizontal` and `stacked` are specific to BarChart; the rest are the shared chart attributes — see [Charts](/components/charts).


<!-- source: docs/pages/components/charts/combo-chart.md -->

# ComboChart

Draw **bars and lines on one chart**, with an optional **second (right-hand)
y-axis** — the classic "volume as bars, a rate or a much smaller number as a
line" pattern. It's the one cartesian type that mixes series *types* and carries
two value axes, so instead of a single `y` it takes `bars=` and `lines=` (column
lists) plus `right_axis=` (the subset plotted against the right axis).

```markdown
<ComboChart data={traffic_combo} x="date"
            bars="visits" lines="signups" right_axis="signups"
            title="Visits (bars) vs signups (line)" />
```

<ComboChart data={traffic_combo} x="date" bars="visits" lines="signups" right_axis="signups" title="Visits (bars) vs signups (line)" explain />

`visits` (in the hundreds) draws as bars on the **left** axis; `signups` (in the
tens) draws as a line on its **own right axis** via `right_axis="signups"`, so the
small series isn't flattened against the big one. Drop `right_axis` and both share
a single left axis.

## Multiple columns per role

`bars=` and `lines=` each take a **comma-separated list** — every column becomes
its own bar or line series, sharing the legend:

```markdown
<ComboChart data={q} x="month"
            bars="pip,docker" lines="source" right_axis="source" />
```

<ComboChart data={downloads_by_channel_wide} x="month" bars="pip,docker" lines="source" right_axis="source" title="pip + docker bars, source line" explain />

## Per-series colours

`bar_color` and `line_color` override just the bar or line colours (a single
colour, or a comma list cycled across multiple series) — the usual "indigo bars,
amber line":

```markdown
<ComboChart data={traffic_combo} x="date"
            bars="visits" lines="signups" right_axis="signups"
            bar_color="#6366f1" line_color="#f59e0b" />
```

<ComboChart data={traffic_combo} x="date" bars="visits" lines="signups" right_axis="signups" bar_color="#6366f1" line_color="#f59e0b" title="Indigo bars, amber line" explain />

## From the semantic layer

Like every chart, ComboChart also takes [semantic metric
refs](/semantic-layer) instead of `data={query}` — list metrics from **one model**
in `bars=`/`lines=`, group with `by=` (and optional `grain=`). Each axis defaults
to its metric's declared number format:

```markdown
<ComboChart by={sales.order_date} grain="month"
            bars={sales.revenue} lines={sales.orders} right_axis={sales.orders} />
```

There is no `series=` on a ComboChart — the metrics (or columns) **are** the
series.

## Attributes

| Attribute | Purpose |
| --------- | ------- |
| `data` | The query to plot (`data={query}`) — or omit it and use metric refs. |
| `x` | **Required (query mode).** Category / x-axis column. |
| `bars` | Columns (or metric refs) drawn as **bars**. One or more, comma-separated. |
| `lines` | Columns (or metric refs) drawn as **lines**. One or more, comma-separated. |
| `right_axis` | The subset of `bars`/`lines` plotted against a **right-hand** y-axis. |
| `by` | **Semantic mode.** Dimension to group the metrics by. |
| `grain` | **Semantic mode.** Bucket a time `by` — `day`/`week`/`month`/… or `grain={control}`. |
| `bar_color` / `line_color` | Colour override for just the bar / line series (single or comma list). |
| `title` | Chart title. |
| `sort_by` | Column to sort by before plotting. |
| `height` | Pixel height (default `320`). |
| `col-span` | Columns to span inside a `<Grid>`. |
| `format` · `currency` · `decimals` · `locale` | **Left**-axis number formatting. |
| `right_format` · `right_currency` · `right_decimals` · `right_locale` | **Right**-axis number formatting. |
| `empty_message` | Text shown when the query returns no rows. |

At least one of `bars=` / `lines=` is required. See [Charts](/components/charts)
for the shared attributes and [Formatting](/formatting) for the number/date keys.


<!-- source: docs/pages/components/charts/pie-chart.md -->

# PieChart

Part-to-whole breakdown. `x` is the category, `y` the value. PieCharts default to
a **donut** with a center total; pass `donut=false` for a solid pie.

```markdown
<PieChart data={channel_totals} x="channel" y="downloads" title="Share by channel" />
```

<PieChart data={channel_totals} x="channel" y="downloads" title="Share by channel" explain />

Solid pie:

<PieChart data={channel_totals} x="channel" y="downloads" donut=false title="Solid pie" explain />

## Faceted (small multiples)

Add `series=` and the pie splits into **one pie per value** — a small-multiples
grid that shares a single slice legend, ideal for comparing the *same* breakdown
across a dimension. Here the channel mix, one pie per month:

```markdown
<PieChart data={by_channel_recent} x="channel" y="downloads" series="month"
          title="Channel mix by month" />
```

<PieChart data={by_channel_recent} x="channel" y="downloads" series="month" title="Channel mix by month" height=340 explain />

The pies are sized to fill the card from its live dimensions and re-fit on resize.
(Faceted pies are always solid — the `donut` center total applies to a single pie
only.)

## From the semantic layer

Like every chart, PieChart also takes [semantic metric refs](/semantic-layer)
instead of `data={query}`:

```markdown
<PieChart metric={sales.revenue} by={sales.region} />
```

As with a `data=` pie, a second dimension `series={model.dim}` renders the faceted
small-multiples grid — one pie per value.

## Attributes

| Attribute | Purpose |
| --------- | ------- |
| `data` | **Required.** The query to plot (`data={query}`). |
| `x` | **Required.** Category column (slice labels). |
| `y` | **Required.** Value column (slice sizes). |
| `series` | Facet column — renders one pie per value (small multiples). |
| `donut` | Donut with a center total (**default `true`**); `donut=false` for a solid pie. Ignored when faceted. |
| `title` | Chart title. |
| `color` | Single color or comma-separated palette override. |
| `height` | Pixel height (default `300`). |
| `col-span` | Columns to span inside a `<Grid>`. |
| `format` · `currency` · `decimals` · `locale` | Value & tooltip number formatting. |
| `empty_message` | Text shown when the query returns no rows. |

`donut` is specific to PieChart; the rest are the shared chart attributes — see [Charts](/components/charts).


<!-- source: docs/pages/components/charts/scatter-chart.md -->

# ScatterChart

Correlation between two numeric columns — one point per row.

```markdown
<ScatterChart data={daily_metrics} x="visits" y="signups" title="Visits vs signups" />
```

<ScatterChart data={daily_metrics} x="visits" y="signups" title="Visits vs signups" explain />

Add `series=` to colour points by a category — here device specs grouped by tier:

<ScatterChart data={device_specs} x="price" y="speed" series="tier" title="Price vs speed, by tier" explain />

## From the semantic layer

Like every chart, ScatterChart also takes [semantic metric refs](/semantic-layer)
instead of `data={query}` — `by` is the x-axis, `metric` the y-axis, and
`series={model.dim}` colours the points:

```markdown
<ScatterChart metric={sales.revenue} by={sales.region} series={sales.status} />
```

## Attributes

| Attribute | Purpose |
| --------- | ------- |
| `data` | **Required.** The query to plot (`data={query}`). |
| `x` | **Required.** Numeric column for the x-axis. |
| `y` | **Required.** Numeric column for the y-axis. |
| `series` | Column to color points by group. |
| `title` | Chart title. |
| `sort_by` | Column to sort by before plotting. |
| `color` | Single color or comma-separated palette override. |
| `height` | Pixel height (default `300`). |
| `col-span` | Columns to span inside a `<Grid>`. |
| `format` · `currency` · `decimals` · `locale` | Value & tooltip number formatting. |
| `empty_message` | Text shown when the query returns no rows. |

These shared attributes are common to every chart type — see [Charts](/components/charts).


<!-- source: docs/pages/components/charts/funnel-chart.md -->

# FunnelChart

Stage-by-stage values, widest at the top — useful for conversion / drop-off.
`x` labels each stage, `y` is its value.

```markdown
<FunnelChart data={channel_totals} x="channel" y="downloads" title="Channels by volume" />
```

<FunnelChart data={channel_totals} x="channel" y="downloads" title="Channels by volume" explain />

## From the semantic layer

Like every chart, FunnelChart also takes [semantic metric
refs](/semantic-layer) instead of `data={query}` — `by={model.dimension}` labels
each stage and `metric={model.measure}` is its value:

```markdown
<FunnelChart metric={sales.orders} by={sales.stage} />
```

## Attributes

| Attribute | Purpose |
| --------- | ------- |
| `data` | **Required.** The query to plot (`data={query}`). |
| `x` | **Required.** Stage label column. |
| `y` | **Required.** Stage value column (sets the band width). |
| `title` | Chart title. |
| `sort_by` | Column to sort stages by. |
| `color` | Single color or comma-separated palette override. |
| `height` | Pixel height (default `300`). |
| `col-span` | Columns to span inside a `<Grid>`. |
| `format` · `currency` · `decimals` · `locale` | Value & tooltip number formatting. |
| `empty_message` | Text shown when the query returns no rows. |

These shared attributes are common to every chart type — see [Charts](/components/charts).


<!-- source: docs/pages/components/charts/treemap-chart.md -->

# TreemapChart

Proportions as nested rectangles — area encodes the value. `x` is the label, `y`
the value.

```markdown
<TreemapChart data={channel_totals} x="channel" y="downloads" title="Share by channel" />
```

<TreemapChart data={channel_totals} x="channel" y="downloads" title="Share by channel" explain />

## From the semantic layer

Like every chart, TreemapChart also takes [semantic metric
refs](/semantic-layer) instead of `data={query}` — `by={model.dimension}` labels
the rectangles and `metric={model.measure}` sizes them:

```markdown
<TreemapChart metric={sales.revenue} by={sales.region} />
```

## Attributes

| Attribute | Purpose |
| --------- | ------- |
| `data` | **Required.** The query to plot (`data={query}`). |
| `x` | **Required.** Label column (rectangle names). |
| `y` | **Required.** Value column (rectangle area). |
| `title` | Chart title. |
| `sort_by` | Column to sort by before plotting. |
| `color` | Single color or comma-separated palette override. |
| `height` | Pixel height (default `300`). |
| `col-span` | Columns to span inside a `<Grid>`. |
| `format` · `currency` · `decimals` · `locale` | Value & tooltip number formatting. |
| `empty_message` | Text shown when the query returns no rows. |

These shared attributes are common to every chart type — see [Charts](/components/charts).


<!-- source: docs/pages/components/charts/calendar-heatmap.md -->

# CalendarHeatmap

A GitHub-style calendar grid — one cell per day, color encoding the value. Give
it a `date` column and a `value` column.

```markdown
<CalendarHeatmap data={daily_metrics} date="date" value="visits" title="Daily visits" />
```

<CalendarHeatmap data={daily_metrics} date="date" value="visits" title="Daily visits" explain />

## From the semantic layer

Like every chart, CalendarHeatmap also takes [semantic metric
refs](/semantic-layer) instead of `data={query}`. Use a daily time dimension on
`by` (with `grain="day"`) and a `metric` for the cell value:

```markdown
<CalendarHeatmap metric={sales.revenue} by={sales.order_date} grain="day" />
```

## Attributes

| Attribute | Purpose |
| --------- | ------- |
| `data` | **Required.** The query to plot (`data={query}`). |
| `date` | **Required.** Date column (alias for the generic `x`). |
| `value` | **Required.** Value column shading each day (alias for `y`). |
| `title` | Chart title. |
| `color` | Single color or comma-separated palette for the scale. |
| `height` | Pixel height (default `300`). |
| `col-span` | Columns to span inside a `<Grid>`. |
| `format` · `currency` · `decimals` · `locale` | Tooltip value formatting. |
| `empty_message` | Text shown when the query returns no rows. |

`date`/`value` are CalendarHeatmap-friendly aliases for `x`/`y`; the rest are the shared chart attributes — see [Charts](/components/charts).


<!-- source: docs/pages/components/charts/box-plot.md -->

# BoxPlot

A box-and-whisker distribution. `y` is the value column; `x` is an **optional**
grouping column (omit it for a single box over all rows). Quartiles, 1.5×IQR
whiskers, and outliers are computed client-side from the raw rows.

```markdown
<BoxPlot data={daily_metrics} x="weekday" y="visits" title="Visits by weekday" />
```

<BoxPlot data={daily_metrics} x="weekday" y="visits" title="Visits by weekday" explain />

A single box over every row (no `x`):

<BoxPlot data={daily_metrics} y="visits" title="All daily visits" explain />

:::note
BoxPlot reads **raw rows** to compute the distribution, so it takes `data={query}`
only — it can't be driven by a [semantic metric](/semantic-layer) (`metric=` is
pre-aggregated, which would collapse the distribution to a single point).
:::

## Attributes

| Attribute | Purpose |
| --------- | ------- |
| `data` | **Required.** The query to plot (`data={query}`). |
| `y` | **Required.** The value column the distribution is computed over. |
| `x` | **Optional** grouping column — one box per group (omit for a single box). |
| `title` | Chart title. |
| `color` | Single color or comma-separated palette override. |
| `height` | Pixel height (default `300`). |
| `col-span` | Columns to span inside a `<Grid>`. |
| `format` · `currency` · `decimals` · `locale` | Value & tooltip number formatting. |
| `empty_message` | Text shown when the query returns no rows. |

Unlike most charts, `x` is optional and `y` is the value — otherwise these are the shared chart attributes ([Charts](/components/charts)). [Violin](/components/charts/violin) takes the same set.


<!-- source: docs/pages/components/charts/violin.md -->

# Violin

A violin (kernel-density) distribution — the same attributes as
[BoxPlot](/components/charts/box-plot), but the shape shows the full density
rather than just the quartiles. `y` is the value; `x` is an optional group.

```markdown
<Violin data={daily_metrics} x="weekday" y="visits" title="Visit density by weekday" />
```

<Violin data={daily_metrics} x="weekday" y="visits" title="Visit density by weekday" explain />

Omit `x` for a single combined density shape:

<Violin data={daily_metrics} y="visits" title="Overall visit density" explain />

:::note
Like [BoxPlot](/components/charts/box-plot), Violin reads **raw rows** for its
density, so it takes `data={query}` only — a [semantic metric](/semantic-layer)
(`metric=`) is pre-aggregated and can't feed a distribution.
:::

## Attributes

| Attribute | Purpose |
| --------- | ------- |
| `data` | **Required.** The query to plot (`data={query}`). |
| `y` | **Required.** The value column the density is computed over. |
| `x` | **Optional** grouping column — one violin per group (omit for a single shape). |
| `title` | Chart title. |
| `color` | Single color or comma-separated palette override. |
| `height` | Pixel height (default `300`). |
| `col-span` | Columns to span inside a `<Grid>`. |
| `format` · `currency` · `decimals` · `locale` | Value & tooltip number formatting. |
| `empty_message` | Text shown when the query returns no rows. |

Identical to [BoxPlot](/components/charts/box-plot); the rest are the shared chart attributes — see [Charts](/components/charts).


<!-- source: docs/pages/components/charts/radar-chart.md -->

# RadarChart

Compare several metrics on one shape — one axis per metric, one polygon per
group. `x` is the indicator (axis) column, `y` the value, and an optional
`series` overlays a polygon per group. Each axis is scaled to the largest value
seen for that indicator.

```markdown
<RadarChart data={feature_scores} x="metric" y="score" series="product" title="Feature scores" />
```

<RadarChart data={feature_scores} x="metric" y="score" series="product" title="Feature scores" explain />

Omit `series` for a single polygon:

<RadarChart data={dashdown_scores} x="metric" y="score" title="Dashdown scores" explain />

## From the semantic layer

Like every chart, RadarChart also takes [semantic metric refs](/semantic-layer)
instead of `data={query}` — `by` is the indicator (one axis per value), `metric`
the value, and `series={model.dim}` overlays a polygon per group:

```markdown
<RadarChart metric={sales.revenue} by={sales.region} series={sales.status} />
```

## Attributes

| Attribute | Purpose |
| --------- | ------- |
| `data` | **Required.** The query to plot (`data={query}`). |
| `x` | **Required.** Indicator/axis column (one radar axis per distinct value). |
| `y` | **Required.** Value column plotted on each axis. |
| `series` | Optional group column — one overlaid polygon per group. |
| `title` | Chart title. |
| `sort_by` | Column to sort by before plotting. |
| `color` | Single color or comma-separated palette override. |
| `height` | Pixel height (default `300`). |
| `col-span` | Columns to span inside a `<Grid>`. |
| `empty_message` | Text shown when the query returns no rows. |

These shared attributes are common to every chart type — see [Charts](/components/charts).


<!-- source: docs/pages/components/charts/gauge-chart.md -->

# GaugeChart

A speedometer-style gauge for a single KPI — progress toward a target, a score,
a utilization percentage. `y` is the value column; the **first row** is plotted
on a `min`..`max` scale (defaults `0`..`100`). No `x` is needed.

```markdown
<GaugeChart data={goal_completion} y="pct" min=0 max=100 title="Monthly goal" />
```

<GaugeChart data={goal_completion} y="pct" min=0 max=100 title="Monthly goal" explain />

`color` repaints the progress arc:

<GaugeChart data={goal_completion} y="pct" min=0 max=100 color="#16a34a" title="Monthly goal (custom color)" explain />

## From the semantic layer

Like every chart, GaugeChart also takes a [semantic metric ref](/semantic-layer)
instead of `data={query}`. It's a single-value gauge, so pass a `metric=` with
**no** `by=` (one scalar); `min`/`max` stay literal:

```markdown
<GaugeChart metric={sales.revenue} min=0 max=1000000 />
```

## Attributes

| Attribute | Purpose |
| --------- | ------- |
| `data` | **Required.** The query to plot (`data={query}`). |
| `y` | **Required.** Value column — the first row is the needle position. |
| `min` | Scale minimum (default `0`). |
| `max` | Scale maximum (default `100`). |
| `title` | Chart title (also labels the dial). |
| `color` | Single color or comma-separated palette override for the progress arc. |
| `height` | Pixel height (default `300`). |
| `col-span` | Columns to span inside a `<Grid>`. |
| `format` · `currency` · `decimals` · `locale` | Formatting for the center read-out. |
| `empty_message` | Text shown when the query returns no rows. |

`min`/`max` are GaugeChart-specific; the rest are the shared chart attributes — see [Charts](/components/charts). For a bare KPI number use [Counter](/components/counter) or [Value](/components/value) instead.


<!-- source: docs/pages/components/charts/heatmap-chart.md -->

# HeatmapChart

A matrix of cells shaded by magnitude — great for category-by-category
intensity (hour × weekday, month × channel). `x` and `y` are **both category
axes** and `value` is the per-cell magnitude column.

```markdown
<HeatmapChart data={by_channel} x="month" y="channel" value="downloads" title="Downloads by month & channel" />
```

<HeatmapChart data={by_channel} x="month" y="channel" value="downloads" title="Downloads by month & channel" explain />

## From the semantic layer

Like every chart, HeatmapChart also takes [semantic metric
refs](/semantic-layer) instead of `data={query}`. The two axes are **dimensions**
and the cell magnitude is a **measure** — one aggregated cell per `x`×`y` pair:

```markdown
<HeatmapChart x={sales.month} y={sales.channel} value={sales.downloads} />
```

`x` and `y` map to the primary and secondary grouping dimensions; `value` is the
measure aggregated within each cell.

## Attributes

| Attribute | Purpose |
| --------- | ------- |
| `data` | The query to plot (`data={query}`) — or omit it and use semantic refs. |
| `x` | **Required.** Horizontal category axis — a column, or a `{model.dim}` in semantic mode. |
| `y` | **Required.** Vertical category axis — a column, or a `{model.dim}` in semantic mode. |
| `value` | **Required.** Cell magnitude — a column, or a `{model.measure}` in semantic mode. |
| `grain` | **Semantic mode.** Bucket a time `x`/`y` — `day`/`week`/`month`/… or `grain={control}`. |
| `title` | Chart title. |
| `sort_by` | Column to sort by before plotting. |
| `color` | Single color or comma-separated palette override. |
| `height` | Pixel height (default `300`). |
| `col-span` | Columns to span inside a `<Grid>`. |
| `format` · `currency` · `decimals` · `locale` | Cell-label & tooltip formatting. |
| `empty_message` | Text shown when the query returns no rows. |

`value` is HeatmapChart-specific (both `x` and `y` are axes here); the rest are the shared chart attributes — see [Charts](/components/charts). For a day-of-year calendar grid use [CalendarHeatmap](/components/charts/calendar-heatmap) instead.


<!-- source: docs/pages/components/charts/sankey-chart.md -->

# SankeyChart

A flow diagram — width-weighted links between nodes. Feed it an **edge list**:
each row is one link with a `source`, a `target`, and a `value` (the link
width). Nodes are the union of the two columns.

```markdown
<SankeyChart data={user_flow} source="stage_from" target="stage_to" value="users" title="Lifecycle flow" />
```

<SankeyChart data={user_flow} source="stage_from" target="stage_to" value="users" title="Lifecycle flow" explain />

## From the semantic layer

Like every chart, SankeyChart also takes [semantic metric
refs](/semantic-layer) instead of `data={query}`. `source`/`target` are two
**dimensions** and `value` is the **measure** weighting each link — one link per
source×target pair:

```markdown
<SankeyChart source={flow.stage_from} target={flow.stage_to} value={flow.users} />
```

This needs a model that exposes the two endpoints as dimensions and a link-weight
measure; the link width is the aggregated `value` per pair.

## Attributes

| Attribute | Purpose |
| --------- | ------- |
| `data` | The query to plot (`data={query}`) — or omit it and use semantic refs. |
| `source` | **Required.** Source-node column (alias for `x`) — or a `{model.dim}` in semantic mode. |
| `target` | **Required.** Target-node column (alias for `y`) — or a `{model.dim}` in semantic mode. |
| `value` | **Required.** Link-width column — or a `{model.measure}` in semantic mode. |
| `title` | Chart title. |
| `sort_by` | Column to sort by before plotting. |
| `color` | Single color or comma-separated palette override for the nodes. |
| `height` | Pixel height (default `300`). |
| `col-span` | Columns to span inside a `<Grid>`. |
| `format` · `currency` · `decimals` · `locale` | Tooltip value formatting. |
| `empty_message` | Text shown when the query returns no rows. |

`source`/`target` are aliases for `x`/`y`; `value` is the link width. The flow must be **acyclic** — ECharts cannot lay out a Sankey that loops back on itself. Otherwise these are the shared chart attributes — see [Charts](/components/charts).


<!-- source: docs/pages/components/charts/candlestick-chart.md -->

# CandlestickChart

An OHLC candlestick chart for price or range data over time. `x` is the
date/category axis; `open`, `high`, `low`, and `close` name the four price
columns. Bullish candles (close ≥ open) render green, bearish red.

```markdown
<CandlestickChart data={daily_prices} x="day"
                  open="open" high="high" low="low" close="close"
                  title="Daily price" />
```

<CandlestickChart data={daily_prices} x="day" open="open" high="high" low="low" close="close" title="Daily price" explain />

## From the semantic layer

Like every chart, CandlestickChart also takes [semantic metric
refs](/semantic-layer) instead of `data={query}`. Each price role
(`open`/`high`/`low`/`close`) names a **measure** of one model and `by=` the date
dimension — exactly how a BI tool binds an OHLC visual to a semantic model: four
measures grouped by a date, each mapped to a candle role.

```markdown
<CandlestickChart by={prices.day}
                  open={prices.open} high={prices.high}
                  low={prices.low} close={prices.close} />
```

The model author defines `open`/`close` as first/last measures and `high`/`low`
as max/min; the four combine into **one** query grouped by `by` (add an optional
`grain=` to bucket the date). The value-axis number format defaults from the
`close` measure.

## Attributes

| Attribute | Purpose |
| --------- | ------- |
| `data` | The query to plot (`data={query}`) — or omit it and use measure refs. |
| `x` | **Required (query mode).** Date/category column for the x-axis. |
| `open` | **Required.** Opening-price column — or `{model.measure}` in semantic mode. |
| `high` | **Required.** High-price column — or `{model.measure}` in semantic mode. |
| `low` | **Required.** Low-price column — or `{model.measure}` in semantic mode. |
| `close` | **Required.** Closing-price column — or `{model.measure}` in semantic mode. |
| `by` | **Semantic mode.** Date dimension to group the measures by. |
| `grain` | **Semantic mode.** Bucket a time `by` — `day`/`week`/`month`/… or `grain={control}`. |
| `title` | Chart title. |
| `sort_by` | Column to sort by before plotting. |
| `height` | Pixel height (default `300`). |
| `col-span` | Columns to span inside a `<Grid>`. |
| `format` · `currency` · `decimals` · `locale` | Value-axis & tooltip formatting. |
| `empty_message` | Text shown when the query returns no rows. |

`open`/`high`/`low`/`close` are CandlestickChart-specific; the rest are the shared chart attributes — see [Charts](/components/charts).


<!-- source: docs/pages/components/charts/theme-river.md -->

# ThemeRiver

A streamgraph — stacked categories flowing over time, each band's thickness
its value. `x` is the time column (ISO dates parse best), `y` the value, and
`series` the category each stream represents.

```markdown
<ThemeRiver data={daily_streams} x="date" y="value" series="metric" title="Activity streams" />
```

<ThemeRiver data={daily_streams} x="date" y="value" series="metric" title="Activity streams" explain />

## From the semantic layer

Like every chart, ThemeRiver also takes [semantic metric refs](/semantic-layer)
instead of `data={query}`. `series=` is required (it splits the streams), so pair
a `metric` with a `by` time dimension and a `series` category:

```markdown
<ThemeRiver metric={sales.revenue} by={sales.order_date} series={sales.region} grain="month" />
```

## Attributes

| Attribute | Purpose |
| --------- | ------- |
| `data` | **Required.** The query to plot (`data={query}`). |
| `x` | **Required.** Time column (ISO dates like `2026-06-01` parse cleanly). |
| `y` | **Required.** Value column — the band thickness. |
| `series` | **Required.** Category column — one stream per distinct value. |
| `title` | Chart title. |
| `sort_by` | Column to sort by before plotting. |
| `color` | Single color or comma-separated palette override. |
| `height` | Pixel height (default `300`). |
| `col-span` | Columns to span inside a `<Grid>`. |
| `format` · `currency` · `decimals` · `locale` | Tooltip value formatting. |
| `empty_message` | Text shown when the query returns no rows. |

ThemeRiver reuses the shared `x`/`y`/`series` attributes — `series` is required (it's what splits the streams). See [Charts](/components/charts).


<!-- source: docs/pages/components/charts/graph-chart.md -->

# GraphChart

A force-directed network — nodes connected by weighted links. Feed it an **edge
list**: each row is one edge with a `source`, a `target`, and an optional
`value` (the edge weight). Nodes are the union of the two columns, sized by
their total incident weight. Drag to rearrange; scroll to zoom.

```markdown
<GraphChart data={user_flow} source="stage_from" target="stage_to" value="users" title="Stage network" />
```

<GraphChart data={user_flow} source="stage_from" target="stage_to" value="users" title="Stage network" explain />

## From the semantic layer

Like every chart, GraphChart also takes [semantic metric
refs](/semantic-layer) instead of `data={query}`. `source`/`target` are two
**dimensions** and `value` is the **measure** weighting each edge:

```markdown
<GraphChart source={flow.stage_from} target={flow.stage_to} value={flow.users} />
```

In semantic mode `value` is **required** — a measure aggregates the edge list (use
a `count` measure for unweighted edges); for raw unweighted edges use
`data={query}` with just `source`/`target`.

## Attributes

| Attribute | Purpose |
| --------- | ------- |
| `data` | The query to plot (`data={query}`) — or omit it and use semantic refs. |
| `source` | **Required.** Source-node column (alias for `x`) — or a `{model.dim}` in semantic mode. |
| `target` | **Required.** Target-node column (alias for `y`) — or a `{model.dim}` in semantic mode. |
| `value` | Edge weight (also sizes the nodes). Optional in query mode; **required** as a `{model.measure}` in semantic mode. |
| `title` | Chart title. |
| `sort_by` | Column to sort by before plotting. |
| `color` | Single color or comma-separated palette override for the nodes. |
| `height` | Pixel height (default `300`). |
| `col-span` | Columns to span inside a `<Grid>`. |
| `empty_message` | Text shown when the query returns no rows. |

`source`/`target` are aliases for `x`/`y`. For a strictly directional flow with proportional widths use [SankeyChart](/components/charts/sankey-chart); for a free network use GraphChart. The rest are the shared chart attributes — see [Charts](/components/charts).


<!-- source: docs/pages/components/charts/sunburst-chart.md -->

# SunburstChart

A hierarchy drawn as nested rings — each ring a level, each arc's sweep its
value. Feed it an **adjacency list**: `id` names each node, `parent` points at
its parent's id (blank or unknown ⇒ a root). `value` (optional) sizes a node
and `label` (optional) is its display name.

```markdown
<SunburstChart data={org_tree} id="id" parent="parent" value="headcount" label="name" title="Headcount" />
```

<SunburstChart data={org_tree} id="id" parent="parent" value="headcount" label="name" title="Headcount" explain />

:::note
SunburstChart needs an `id`/`parent` **hierarchy**, which the
[semantic](/semantic-layer) `metric=`/`by=` grammar can't express — so it takes
`data={query}` only. (For a metric split by one category, a
[PieChart](/components/charts/pie-chart) `metric=`/`by=` works.)
:::

## Attributes

| Attribute | Purpose |
| --------- | ------- |
| `data` | **Required.** The query to plot (`data={query}`). |
| `id` | **Required.** Unique node-id column. |
| `parent` | **Required.** Parent-id column (blank/unknown ⇒ a root ring). |
| `value` | Optional column sizing each arc (leaf values roll up to parents). |
| `label` | Optional display-name column (defaults to the id). |
| `title` | Chart title. |
| `color` | Single color or comma-separated palette override. |
| `height` | Pixel height (default `300`). |
| `col-span` | Columns to span inside a `<Grid>`. |
| `format` · `currency` · `decimals` · `locale` | Tooltip value formatting. |
| `empty_message` | Text shown when the query returns no rows. |

Same `id`/`parent`/`value`/`label` shape as [TreeChart](/components/charts/tree-chart) — Sunburst shows proportions, Tree shows structure. For a single-level part-to-whole use [PieChart](/components/charts/pie-chart) or [TreemapChart](/components/charts/treemap-chart). The rest are the shared chart attributes — see [Charts](/components/charts).


<!-- source: docs/pages/components/charts/tree-chart.md -->

# TreeChart

A hierarchy drawn as a node-link diagram — an org chart / file tree, laid out
left-to-right and collapsible. Same **adjacency list** as
[SunburstChart](/components/charts/sunburst-chart): `id` names each node,
`parent` points at its parent's id. `value`/`label` are optional. Multiple
roots are gathered under one synthetic root.

```markdown
<TreeChart data={org_tree} id="id" parent="parent" label="name" title="Org chart" />
```

<TreeChart data={org_tree} id="id" parent="parent" label="name" title="Org chart" explain />

:::note
Like [SunburstChart](/components/charts/sunburst-chart), TreeChart needs an
`id`/`parent` **hierarchy**, which the [semantic](/semantic-layer)
`metric=`/`by=` grammar can't express — so it takes `data={query}` only.
:::

## Attributes

| Attribute | Purpose |
| --------- | ------- |
| `data` | **Required.** The query to plot (`data={query}`). |
| `id` | **Required.** Unique node-id column. |
| `parent` | **Required.** Parent-id column (blank/unknown ⇒ a root). |
| `value` | Optional value column (shown in the tooltip). |
| `label` | Optional display-name column (defaults to the id). |
| `title` | Chart title. |
| `color` | Single color or comma-separated palette override. |
| `height` | Pixel height (default `300`). |
| `col-span` | Columns to span inside a `<Grid>`. |
| `empty_message` | Text shown when the query returns no rows. |

Tree shows *structure*; [SunburstChart](/components/charts/sunburst-chart) shows the same hierarchy as *proportions*. The rest are the shared chart attributes — see [Charts](/components/charts).


<!-- source: docs/pages/components/charts/parallel-chart.md -->

# ParallelChart

Parallel coordinates — compare rows across many numeric columns at once. Each
`dimensions` column becomes a vertical axis and every row a polyline crossing
them, so clusters and trade-offs jump out. An optional `series` column colors
the lines by group.

```markdown
<ParallelChart data={device_specs} dimensions="price, speed, battery, rating" series="tier" title="Device trade-offs" />
```

<ParallelChart data={device_specs} dimensions="price, speed, battery, rating" series="tier" title="Device trade-offs" explain />

## From the semantic layer

Like every chart, ParallelChart also takes [semantic metric
refs](/semantic-layer) instead of `data={query}`. List the axes as **measure**
refs in `dimensions=` and group with `by=` — one polyline per `by` value:

```markdown
<ParallelChart by={products.category}
               dimensions="products.price,products.weight,products.rating" />
```

Each measure becomes a vertical axis; `by` produces one polyline per group (omit
it for a single aggregate line). Semantic mode has no `series=` (the metrics ARE
the axes).

## Attributes

| Attribute | Purpose |
| --------- | ------- |
| `data` | The query to plot (`data={query}`) — or omit it and use semantic refs. |
| `dimensions` | **Required.** Comma-separated numeric columns — or `model.metric` refs — one axis each (≥ 2). |
| `series` | **(Query mode.)** Optional group column — colors the lines and adds a legend. |
| `by` | **Semantic mode.** Dimension grouping the measures into one polyline per value. |
| `title` | Chart title. |
| `sort_by` | Column to sort by before plotting. |
| `color` | Single color or comma-separated palette override. |
| `height` | Pixel height (default `300`). |
| `col-span` | Columns to span inside a `<Grid>`. |
| `empty_message` | Text shown when the query returns no rows. |

`dimensions` is ParallelChart-specific; the rest are the shared chart attributes — see [Charts](/components/charts).


<!-- source: docs/pages/components/charts/auto-chart.md -->

# Chart (`auto`)

Let Dashdown infer the chart type from the columns instead of naming one. Opt in
with the `auto` flag:

```markdown
<Chart auto data={downloads_by_month} />
```

<Chart auto data={downloads_by_month} explain />

Rough heuristics: a time/category `x` with a numeric `y` → line or bar; two
numeric columns → scatter. You can still pass explicit `x`/`y`/`series` to guide
it. The `auto` flag is required — a bare `<Chart data=… />` raises, so you never
get a surprise chart type.

## From the semantic layer

`<Chart auto>` also takes [semantic metric refs](/semantic-layer) instead of
`data={query}` — it infers the type from the resolved `metric`/`by`/`series`
shape just as it does from query columns:

```markdown
<Chart auto metric={sales.revenue} by={sales.region} />
```

## Attributes

| Attribute | Purpose |
| --------- | ------- |
| `auto` | **Required** flag — opts into type inference (`<Chart auto … />`). |
| `data` | **Required.** The query to plot (`data={query}`). |
| `x` · `y` · `series` | Optional — provide them to guide the inferred chart instead of letting columns decide. |
| `title` | Chart title. |
| `color` | Single color or comma-separated palette override. |
| `height` | Pixel height (default `300`). |
| `col-span` | Columns to span inside a `<Grid>`. |
| `format` · `currency` · `decimals` · `locale` · `date_format` | Value & tooltip number/date formatting. |
| `empty_message` | Text shown when the query returns no rows. |

`auto` is unique to `<Chart>`; everything else is shared with the typed charts — see [Charts](/components/charts).


<!-- source: docs/pages/components/maps.md -->

# Maps

Every map component on one page: the ECharts-based [MapChart](#mapchart)
choropleth first — the quickest way to put values on a world map — then five
self-drawn SVG geo maps: an animated choropleth, small-multiple choropleths, a
bivariate choropleth, a proportional-symbol map, and a dot-density map.

## MapChart

An ECharts choropleth that joins by country **name** instead of ISO code —
handy when a dataset carries names and no codes. It's a regular chart (shared
chart attributes, canvas-rendered), not one of the SVG geo maps below, but it
follows the same conventions: overlaid title and legend, **Ctrl/⌘ + scroll**
to zoom (plain scrolling stays with the page), and a **Reset view** pill once
zoomed. `location` names the region column, `value` the metric; the built-in
`world` map ships offline, and `geojson=` takes a custom map here too.

```markdown
<MapChart data={downloads_by_country} location="country" value="downloads"
          map="world" title="Downloads by country" explain />
```

<MapChart data={downloads_by_country} location="country" value="downloads" map="world" title="Downloads by country" explain />

The `explain` attribute works here like on any chart — and the [AI
commentary](/ai/ask#annotations-on-the-chart) can highlight regions on the
map, each one validated against the locations the query actually returned.
(The SVG geo maps below take `explain` too — see
[AI commentary on geo maps](#ai-commentary-on-geo-maps).)

Like every chart, MapChart also takes [semantic metric refs](/semantic-layer)
instead of `data={query}` — `by` is the region dimension (its values must match
the GeoJSON names) and `metric` shades each region; `map`/`geojson` stay
literal:

```markdown
<MapChart metric={sales.revenue} by={sales.country} map="world" />
```

### MapChart attributes

| Attribute | Purpose |
| --------- | ------- |
| `data` | **Required.** The query to plot (`data={query}`). |
| `location` | **Required.** Region-name column (must match the GeoJSON; alias for `x`). |
| `value` | **Required.** Metric that shades each region (alias for `y`). |
| `map` | Built-in map name (default `world`). |
| `geojson` | URL/path to a custom GeoJSON for non-world maps (resolved from `assets/`). |
| `title` | Chart title. |
| `color` | Single color or comma-separated palette for the scale. |
| `height` | Pixel height (default `300`). |
| `col-span` | Columns to span inside a `<Grid>`. |
| `format` · `currency` · `decimals` · `locale` | Tooltip value formatting. |
| `empty_message` | Text shown when the query returns no rows. |

`location`/`value` are aliases for `x`/`y`; `map`/`geojson` are
MapChart-specific. The rest are the shared chart attributes — see
[Charts](/components/charts).

## Geo maps

The five SVG geo maps share one design:

- **Countries join on ISO 3166-1 numeric codes** (`id=` names the code column,
  default `iso`) against the bundled world geometry — the join key analytics
  datasets actually carry. Values like `840`, `"840"` and `"076"` all match.
  ([MapChart](#mapchart) instead joins by country *name*.)
- **Self-drawn SVG, fully offline** — no mapping library, no CDN, an
  equirectangular projection with standard parallels ±35° and antimeridian
  handling. The frame auto-fits the loaded geometry's extent, so the world
  shows no empty polar bands and a [custom region](#custom-regions) fills the
  card.
- **Static-export safe.** Every frame ships in the one query result, and the
  year scrubber / metric toggles are the component's own controls (not page
  filters) — so `dashdown build` exports stay fully interactive.
- **Deterministic.** DotDensityMap seeds its dot placement per country+metric,
  so the same data draws the identical map on every load and in exports.
- **Chrome overlays the map.** The title (top-left), legend (bottom-left) and
  metric toggle (bottom-right) float over the map on translucent washes, so
  the geometry gets the whole card. Only the ChoroplethTime timeline is a
  footer row, and ChoroplethFacets keeps a flow header/footer — a facet grid
  has no spare corners.

The demos below query a demo dataset of decade-level world indicators
(`data/world_indicators.csv`).

## Data shape

One result shape feeds every map: **one row per country (and per year, for the
time-aware maps), one numeric column per metric**. The demo dataset is already
in that shape, so its query is just:

```sql
SELECT iso, country, year, population, gdp_per_capita, life_expectancy
FROM world_indicators
ORDER BY year, country
```

Getting a fact table there is a `GROUP BY` country and year with one aggregate
per metric — plus, usually, a join to translate whatever country key your data
carries (alpha-2 codes like `US`, names) into ISO numeric:

```sql
SELECT c.iso_numeric                        AS iso,
       EXTRACT(year FROM o.created_at)      AS year,
       SUM(o.amount)                        AS revenue,
       COUNT(DISTINCT o.customer_id)        AS customers
FROM orders o
JOIN country_codes c ON c.alpha2 = o.country_code
GROUP BY 1, 2
```

`<ChoroplethTime data={sales_by_country} id="iso" year="year"
metrics="revenue|Revenue|$,customers|Customers|customers" />` then works as-is
— and the same result drives the other maps (`year_value=` picks a snapshot;
BivariateMap reads two of the columns as `x`/`y`).

- **Sparse is fine.** A country missing from a year (or a `NULL` value) renders
  in the no-data wash; year gaps are fine too — each distinct year is one
  scrubber stop or facet, so decade-level data plays as five frames.
- **Keep it aggregated.** Every frame ships in the one query result (that's
  what keeps exports interactive), so return countries × years rows, not raw
  events.
- **Don't log-transform in SQL.** Use `scale="log"` instead, so tooltips and
  legends keep the real values.

## Zoom, pan & fullscreen

Every map card has the charts' hover-revealed ⛶ button, opening it in a
fullscreen modal with a **Map / Table** switcher (the table shows the same
query result). On the map itself — inline or fullscreen — **Ctrl + scroll**
(⌘ on macOS) or a trackpad pinch zooms around the pointer, dragging pans once
zoomed, double-click zooms in, and a **Reset view** pill (bottom-center, where
the zoom hint flashes) restores the full extent. Plain scrolling is
deliberately left to the page, so a map never traps the wheel.
(ChoroplethFacets panels stay un-zoomable small multiples — fullscreen is the
"see them bigger" affordance there.)

## AI commentary on geo maps

Every geo map takes the charts' `explain` attribute (needs an
[`llm:` block](/ai/ask#configuration)): a hover-revealed ✨ button that
generates commentary on demand into a footer under the map. On **BubbleMap**
and **DotDensityMap** the commentary can also [mark the map
itself](/ai/ask#annotations-on-the-chart): a cited country gets a dashed
**halo ring** with a leader-line label, referenced from the text by numbered
chips (hover a chip to bold its halo). Every proposal is validated
server-side against the join ids in the active year slice — a country the
frame doesn't draw can't earn a halo — and a halo scoped to one metric shows
only while that metric is toggled active. The choropleths
(ChoroplethTime/ChoroplethFacets/BivariateMap) stay commentary-only: facets,
animation frames, and two-metric encodings give one static mark nothing
stable to point at. `annotations=false` keeps any map commentary-only;
`explain="…"` and `cache_ttl=` work exactly as on charts.

```markdown
<BubbleMap data={world_indicators} id="iso" year="year" year_value="2020"
    metrics="population|Population|people" max_radius=35
    title="Population, 2020" explain />
```

## Shared attributes

Every map takes `data={query}` plus:

| Attribute | Purpose |
| --------- | ------- |
| `id` | Column holding the ISO 3166-1 numeric country code (default `iso`). |
| `title` | Card title (overlaid on the map's top-left). |
| `scheme` | Named color ramp: `blues`, `greens`, `oranges`, `purples`, `reds`, `viridis` (BivariateMap instead takes `blue-purple`, `green-blue`, `red-blue`). |
| `color` | Base color to derive a ramp from (defaults to `branding.palette`). |
| `scale` | Value→color mapping: `linear` (default), `log`, `quantile`. |
| `map` / `geojson` | Basemap: the bundled `world` (default), or a custom GeoJSON URL. |
| `id_field` | Feature property to join on in a custom GeoJSON (default `iso`). |
| `height` | Pixel height (default `420`). |
| `col-span` | Columns to span inside a `<Grid>`. |
| `empty_message` | Text shown when the query returns no rows. |
| `explain` | AI commentary footer, ✨ on hover (`explain="…"` pins your own question) — see [above](#ai-commentary-on-geo-maps). |
| `annotations` | `false` keeps an explained BubbleMap/DotDensityMap commentary-only (no halo marks). |
| `cache_ttl` / `max_rows` | Explain answer-cache TTL and row cap, as on charts. |

## ChoroplethTime

An animated choropleth: countries shaded by a metric, stepped across the
`year=` column by a play/scrub control. With several `metrics` entries
(`column|Label|unit`, comma-separated) a toggle switches between them; the
color scale stays fixed across all years so frames compare honestly.
`interval=` sets the frame duration in milliseconds.

```markdown
<ChoroplethTime data={world_indicators} id="iso" year="year"
    metrics="population|Population|people,gdp_per_capita|GDP per capita|$"
    scale="log" title="World development, 1960–2020" />
```

<ChoroplethTime data={world_indicators} id="iso" year="year" metrics="population|Population|people,gdp_per_capita|GDP per capita|$" scale="log" title="World development, 1960–2020" />

## ChoroplethFacets

Small multiples: one mini map per year on a **shared** color scale, for
comparing snapshots side by side. `years=` picks the facets (default: every
distinct year); `columns=` sets the grid width.

```markdown
<ChoroplethFacets data={world_indicators} id="iso" year="year"
    value="life_expectancy" years="1960,1980,2000,2020" columns=2
    label="Life expectancy" unit="years" scheme="greens"
    title="Life expectancy by decade" />
```

<ChoroplethFacets data={world_indicators} id="iso" year="year" value="life_expectancy" years="1960,1980,2000,2020" columns=2 label="Life expectancy" unit="years" scheme="greens" title="Life expectancy by decade" />

## BivariateMap

Two metrics on one map: each country's `x` and `y` values are classed into
terciles and colored from a 3×3 bivariate palette, with the classic square
legend overlaid in the map's bottom-left corner. With a `year=` column,
`year_value=` picks the snapshot (default: latest year).

```markdown
<BivariateMap data={world_indicators} id="iso" year="year" year_value="2020"
    x="gdp_per_capita" y="life_expectancy"
    xlabel="GDP per capita" ylabel="Life expectancy" xunit="$" yunit="years"
    title="Wealth vs. health, 2020" />
```

<BivariateMap data={world_indicators} id="iso" year="year" year_value="2020" x="gdp_per_capita" y="life_expectancy" xlabel="GDP per capita" ylabel="Life expectancy" xunit="$" yunit="years" title="Wealth vs. health, 2020" />

## BubbleMap

A proportional-symbol map: a circle on each country's centroid, **area** ∝
value, over a muted basemap. `max_radius=` caps the largest circle; several
`metrics` get a toggle.

```markdown
<BubbleMap data={world_indicators} id="iso" year="year" year_value="2020"
    metrics="population|Population|people" max_radius=35
    title="Population, 2020" explain />
```

<BubbleMap data={world_indicators} id="iso" year="year" year_value="2020" metrics="population|Population|people" max_radius=35 title="Population, 2020" explain />

## DotDensityMap

One dot per fixed quantity, scattered inside each country's borders. A metric
is `column|Label|unit|per_dot` — one dot stands for `per_dot` of the metric
(omit it to derive a value that keeps the map under `max_dots`). Placement is
seeded per country+metric, so the pattern is identical on every load.

```markdown
<DotDensityMap data={world_indicators} id="iso" year="year" year_value="2020"
    metrics="population|Population|people|10000000"
    title="Population, 2020 — 1 dot = 10M people" />
```

<DotDensityMap data={world_indicators} id="iso" year="year" year_value="2020" metrics="population|Population|people|10000000" title="Population, 2020 — 1 dot = 10M people" explain />

## Custom regions

The maps accept a custom basemap: point `geojson=` at a GeoJSON file (e.g.
under your project's `assets/`) and name the feature property that carries
your join key with `id_field=`. The frame **auto-fits** the geometry's extent,
so a regional map fills the card, and pan/zoom/reset stay bounded to it;
bubble and dot sizes stay card-relative. The bundled world geometry is Natural
Earth 110m (public domain), enriched with ISO numeric codes — this demo's
`europe.json` is a subset of it, so the default `id_field="iso"` join works
unchanged:

```markdown
<BubbleMap data={world_indicators} id="iso" year="year" year_value="2020"
    geojson="/assets/europe.json"
    metrics="population|Population|people"
    title="Population, 2020 — Europe" />
```

<BubbleMap data={world_indicators} id="iso" year="year" year_value="2020" geojson="/assets/europe.json" metrics="population|Population|people" title="Population, 2020 — Europe" explain />

## Per-component attributes

| Component | Attributes |
| --------- | ---------- |
| `ChoroplethTime` | `year` (default `year`), `metrics="col\|Label\|unit,…"` **(required)**, `interval` (ms, default `700`). |
| `ChoroplethFacets` | `year`, `value` **(required)**, `years="1990,2000,…"`, `label`, `unit`, `columns` (default `3`). |
| `BivariateMap` | `x`/`y` **(required)**, `xlabel`/`ylabel`, `xunit`/`yunit`, `year`, `year_value`. |
| `BubbleMap` | `metrics` **(required)**, `max_radius` (default `40`), `year`, `year_value`. |
| `DotDensityMap` | `metrics="col\|Label\|unit\|per_dot,…"` **(required)**, `dot_radius` (default `1.2`), `max_dots` (default `20000`), `year`, `year_value`. |


<!-- source: docs/pages/components/table.md -->

# Table

A sortable, filterable data grid. Every table has a built-in **CSV export** button
(the ↓ in its header) that downloads the *current, filtered* rows.

```markdown
<Table data={channel_totals} title="Channel totals" search sort />
```

<Table data={channel_totals} title="Channel totals" />

Tables sort, search, format, and paginate client-side. Seed an initial order with
`sort=` (or click a header), format cells per column, page long results, and turn
rows into drill-down links:

<Table data={channel_totals} title="Top channels (sorted, formatted)" sort="downloads desc" format="downloads=number" />

<Table data={device_specs} title="Device specs (price formatted)" format="price=currency" />

<Table data={by_channel} title="By month & channel (search + paging)" search="Filter rows…" page-size="5" />

<Table data={channel_totals} title="Channels (click a row →)" row_link="/detail-pages/{channel}" />

## Common attributes

| Attribute             | Purpose                                                    |
| --------------------- | ---------------------------------------------------------- |
| `data`                | **Required.** The query to display.                        |
| `title`               | Heading above the table.                                   |
| `search`              | Show a per-table search box.                               |
| `sort`                | Enable column sorting.                                     |
| `limit`               | Max rows to render.                                        |
| `format`              | Per-column formatting, e.g. `format="downloads=number"`.   |
| `heatmap`             | Shade numeric cells by value, e.g. `heatmap="amount,profit"` (bare `heatmap` = all numeric columns). |
| `heatmap_scheme`      | `sequential` (default, low→high) or `diverging` (red↔green, centered at zero). |
| `export`              | `export=false` removes the CSV button.                     |
| `export_filename`     | Rename the downloaded CSV.                                 |
| `row_link`            | Make the **whole row** clickable, e.g. `row_link="/customers/{id}"`. |
| `link_column` / `link_pattern` | Turn a single column into links.                  |

Formatting helpers (`currency`, `decimals`, `locale`, `date_format`) work like
they do on charts — see [Formatting](/formatting) for the full reference and the
project-wide `format:` defaults. CSV export is built client-side (RFC 4180) and
works in static exports for free.

## Heatmap cells

`heatmap` shades numeric cells by their value — spreadsheet-style conditional
formatting — so the high and low points in a column jump out at a glance. Pass a
column list, or bare `heatmap` to shade **every** numeric column. Here it shades
the monthly downloads of each channel (the `month` column is text, so it's left
alone):

```markdown
<Table data={downloads_by_channel_wide} title="Monthly downloads by channel"
       heatmap format="pip=number,docker=number,source=number" />
```

<Table data={downloads_by_channel_wide} title="Monthly downloads by channel" heatmap format="pip=number,docker=number,source=number" />

The color scale is computed per column from its own min/max (over the full
result, so it stays stable as you sort, search, and page). Colors are drawn from
your **theme** — they follow the project's primary color and any `custom.css`
override, so the heatmap matches the rest of the UI.

`heatmap_scheme` picks the ramp — `sequential` (the default; low→high in the
theme's primary color, above) or `diverging` for **signed** values like
profit/variance, where it runs from the theme's error color through to its success
color, centered on zero. Here every cell is a channel-month's deviation from that
channel's average, so below-average months read red and above-average read green:

```markdown
<Table data={downloads_vs_avg} title="Downloads vs. channel average"
       heatmap heatmap_scheme="diverging" format="pip=number,docker=number,source=number" />
```

<Table data={downloads_vs_avg} title="Downloads vs. channel average" heatmap heatmap_scheme="diverging" format="pip=number,docker=number,source=number" />

The shading is a translucent overlay, so cell text stays legible in both light
and dark themes.

`row_link` (and `link_column` / `link_pattern`) fill `{column}` placeholders from
each row, so a table becomes the entry point to a [detail page](/detail-pages).


<!-- source: docs/pages/components/pivot-table.md -->

# PivotTable

A client-side cross-tab with drag-and-drop axes. Choose `rows`, `cols`, and a
`values` column; pick the aggregation with `agg` (`sum`, `avg`, `count`, …).

```markdown
<PivotTable data={by_channel} rows="channel" cols="month" values="downloads" agg="sum" />
```

<PivotTable data={by_channel} rows="channel" cols="month" values="downloads" agg="sum" />

Switch the aggregation with `agg=` (here the **average** per cell), or stack
multiple fields on an axis with a comma-separated list:

<PivotTable data={by_channel} rows="channel" cols="month" values="downloads" agg="avg" title="Average downloads" />

<PivotTable data={device_specs} rows="tier,device" values="price" agg="avg" title="Avg price by tier & device" />

| Attribute | Purpose                                       |
| --------- | --------------------------------------------- |
| `data`    | **Required.** The query to cross-tab.         |
| `rows`    | Column(s) for the row axis.                   |
| `cols`    | Column(s) for the column axis.                |
| `values`  | The measure column.                           |
| `agg`     | Aggregation: `sum` (default), `avg`, `count`, `min`, `max`. |

The axes are draggable in the browser, so readers can re-pivot without editing
the page.


<!-- source: docs/pages/components/counter.md -->

# Counter

A single big-number KPI. Reads one cell — `column` from a given `row` (default the
first). Add `prefix`/`suffix` for units and an optional `delta` badge.

```markdown
<Counter data={downloads_total} column="downloads" label="Total downloads" />
```

<Counter data={downloads_total} column="downloads" label="Total downloads" />

<Counter data={channel_totals} column="downloads" row="0" label="Top channel" suffix=" dl" color="primary" />

A `delta=` badge shows a ▲/▼ change pill; or pass `compare={query}` (with
`compare-row` / `compare-column`) to derive the change from another row or query
instead of a literal percentage:

<Counter data={downloads_total} column="downloads" label="Total downloads" delta="12.4" />

<Counter data={channel_totals} column="downloads" row="0" compare={channel_totals} compare-row="1" label="Top vs next channel" />

| Attribute        | Purpose                                              |
| ---------------- | --------------------------------------------------- |
| `data`           | **Required.** The query to read.                    |
| `column`         | Which column to display.                            |
| `row`            | Row index (default `0`).                            |
| `label`          | Caption under the number.                           |
| `prefix`/`suffix`| Text around the value.                              |
| `format`         | [Number format](/formatting) — `compact` fits a billions-scale KPI (`3.34B`, exact value on hover). |
| `color`          | DaisyUI color name (`primary`, `success`, …).       |
| `delta` / `compare` | Show a change badge (static value or vs another query). |
| `sparkline` / `sparkline-column` | Draw an inline trend line from a series query (or a `metric` + `sparkline-by` time dimension on a semantic dashboard). |
| `breakdown` / `breakdown-label` / `breakdown-column` | Draw a proportional composition strip from a per-category query (or a `metric` + `breakdown-by` dimension on a semantic dashboard). `breakdown-legend=false` hides its legend line; `breakdown-values` picks what it prints (`percent`/`value`/`both`). |

## Sparklines

Pass a second, multi-row query to `sparkline={…}` to draw a trend line along the
card's bottom edge, behind the number — handy for showing *where* a KPI has been,
not just where it landed.
`sparkline-column` picks which column of that series to plot (the headline value
still comes from `data`/`column`). The card doesn't grow to fit the trend: a
spark tile stays exactly as tall as a plain one, and where the line passes under
the text a soft halo of the card's surface color keeps the number legible.

```markdown
<Counter data={downloads_total} column="downloads" label="Total downloads"
         sparkline={downloads_by_month} sparkline-column="downloads" />
```

<Counter data={downloads_total} column="downloads" label="Total downloads"
         sparkline={downloads_by_month} sparkline-column="downloads" />

With a sparkline, `color` paints the **trend line** and the headline number stays
neutral (the mockup KPI style); without one, `color` colors the number itself. It
pairs naturally with a `delta`/`compare` badge to show both the latest change and
the longer trend.

### Sparklines from a metric

On a [semantic](/semantic-layer) dashboard you don't even need a series query: point
`sparkline=` at a **metric** and `sparkline-by=` at the model's **time dimension**,
and the framework builds the bucketed trend for you — the same way `metric=` drives
the headline.

```markdown
<Counter metric={sales.revenue} label="Revenue"
         sparkline={sales.revenue} sparkline-by={sales.order_date} grain="month" />
```

`grain=` buckets the time dimension (`day`/`week`/`month`/`quarter`/`year`), literal
or pointed at a `{control}` like any other grain. The metric's value column is plotted
automatically, so there's no `sparkline-column` to set. (Omit `sparkline-by=` and the
classic series-query form above still applies, even for a semantic headline.)

## Breakdowns

Pass a per-category query to `breakdown={…}` to draw a proportional composition
strip along the card's bottom — a "one-row treemap" showing *how the KPI splits*,
one colored segment per row, widths proportional to each category's share.
`breakdown-label` / `breakdown-column` pick the category and value columns
(defaults: first non-numeric / first numeric). Hover a segment for its exact
value and share; a compact legend line spells out the categories
(`breakdown-legend=false` hides it). By default the legend prints each
category's **share** — `breakdown-values="value"` prints the value instead
(formatted like the headline, so `format="compact"` gives `pip 7.6K`), and
`breakdown-values="both"` prints `pip 7.6K · 72%`.

```markdown
<Counter data={downloads_total} column="downloads" label="Total downloads"
         breakdown={channel_totals} breakdown-label="channel" breakdown-column="downloads" />
```

<Counter data={downloads_total} column="downloads" label="Total downloads"
         breakdown={channel_totals} breakdown-label="channel" breakdown-column="downloads" />

<Counter data={downloads_total} column="downloads" label="Total downloads" format="compact"
         breakdown={channel_totals} breakdown-label="channel" breakdown-column="downloads" breakdown-values="both" />

Segment colors follow the same palette as the charts (your
`branding.palette` if set), so the strip matches a pie or bar chart of the same
dimension elsewhere on the page. Categories beyond the palette fold into a single
neutral **Other** segment; negative values don't compose and are skipped. A
breakdown and a `sparkline` are mutually exclusive — both draw along the card's
bottom edge.

On a [semantic](/semantic-layer) dashboard, point `breakdown=` at a **metric** and
`breakdown-by=` at a **dimension** instead of writing the per-category query:

```markdown
<Counter metric={sales.revenue} label="Revenue"
         breakdown={sales.revenue} breakdown-by={sales.region} />
```

For an inline single value inside prose, use [Value](/components/value) instead.


<!-- source: docs/pages/components/value.md -->

# Value

A single value rendered inline — handy for weaving a number into a sentence.
Reads `column` from a `row` (default `0`).

```markdown
We have shipped <Value data={downloads_total} column="downloads" suffix=" downloads" /> so far.
```

We have shipped <Value data={downloads_total} column="downloads" suffix=" downloads" /> across
<Value data={downloads_total} column="months" suffix=" months" />.

`format=` applies number/currency/percent formatting inline — e.g. thousands
separators: we have <Value data={downloads_total} column="downloads" format="number" /> downloads.

| Attribute        | Purpose                              |
| ---------------- | ------------------------------------ |
| `data`           | **Required.** The query to read.     |
| `column`         | Which column to display.             |
| `row`            | Row index (default `0`).             |
| `prefix`/`suffix`| Text around the value.               |
| `format` · `currency` · `decimals` · `locale` | Number/date formatting — see [Formatting](/formatting). |

For a large standalone KPI card, use [Counter](/components/counter).


<!-- source: docs/pages/components/grid.md -->

# Grid

Lay widgets out in equal-width columns. Wrap any components in `<Grid>`; set
`cols` (default `2`) and an optional `gap`.

```markdown
<Grid cols=2>
  <Counter data={downloads_total} column="downloads" label="Downloads" delta="12.4"
           sparkline={downloads_by_month} sparkline-column="downloads" color="primary" />
  <Counter data={downloads_total} column="months" label="Months tracked" />
</Grid>
```

<Grid cols=2>
  <Counter data={downloads_total} column="downloads" label="Downloads" delta="12.4"
           sparkline={downloads_by_month} sparkline-column="downloads" color="primary" />
  <Counter data={downloads_total} column="months" label="Months tracked" />
</Grid>

A child can span more than one column with `col-span=`:

<Grid cols=3>
  <LineChart data={downloads_by_month} x="month" y="downloads" col-span=2 title="Trend (spans 2 cols)" explain />
  <BarChart data={channel_totals} x="channel" y="downloads" title="By channel" explain />
</Grid>

| Attribute        | Purpose                                          |
| ---------------- | ------------------------------------------------ |
| `cols` / `columns` | Number of equal columns (default `2`).         |
| `gap`            | CSS gap between cells.                            |

Charts inside a grid honor `col-span=` to span multiple columns. In a printed/PDF
export the grid stacks to one widget per row automatically.


<!-- source: docs/pages/components/tabs.md -->

# Tabs

Section a page into switchable panels — "Overview · By region · Raw data" views
that share the same page, filters, and queries. Wrap each section in a
`<Tab title="…">` inside a `<Tabs>` container; one panel shows at a time behind
a tab bar.

```markdown
<Tabs name="view">
  <Tab title="Trend">
    <LineChart data={downloads_by_month} x="month" y="downloads" title="Downloads over time" />
  </Tab>
  <Tab title="By channel">
    <BarChart data={channel_totals} x="channel" y="downloads" title="Downloads by channel" />
  </Tab>
</Tabs>
```

<Tabs name="view">
  <Tab title="Trend">
    <LineChart data={downloads_by_month} x="month" y="downloads" title="Downloads over time" explain />
  </Tab>
  <Tab title="By channel">
    <BarChart data={channel_totals} x="channel" y="downloads" title="Downloads by channel" explain />
  </Tab>
</Tabs>

Tabs are pure **layout** — switching one shows different authored content but
never changes any query. To let the *reader's choice* filter the data instead,
reach for a [ButtonGroup](/components/button-group): it looks similar but writes
a filter value your SQL reads as `${name}`.

## Attributes

### `<Tabs>`

| Attribute  | Purpose                                                                                          |
| ---------- | ------------------------------------------------------------------------------------------------ |
| `name`     | Sync the active tab to the URL as `?name=<title-slug>` — deep-linkable, back/forward-aware. Omit for no URL sync. |
| `default`  | Title of the tab active on first load (a URL param wins). Defaults to the first tab.             |
| `url_sync` | Set `false` to keep a named Tabs out of the URL. Default `true`.                                  |
| `label`    | Accessible label for the tab bar (default `"Tabs"`).                                              |
| `col-span` / `span` | Columns to span when the Tabs sits inside a [Grid](/components/grid).                    |

### `<Tab>`

| Attribute | Purpose                              |
| --------- | ------------------------------------ |
| `title`   | Required. The tab's label in the bar. |

## Behavior notes

- A panel can hold anything a page can — markdown, charts, tables, nested
  `<Tabs>` — and every panel's queries load normally whether or not it's visible.
- In a **PDF/print export** the tab bar is hidden and every panel is printed
  stacked, each introduced by its title, so hidden content still makes it into
  the document. Static builds work unchanged.
- The tab bar is keyboard-accessible (arrow keys, Home/End) and follows the
  WAI-ARIA tabs pattern.


<!-- source: docs/pages/components/dropdown.md -->

# Dropdown

A select filter. Its value lands in the reactive `filters` store under `name`;
any query using `${name}` re-runs. Options come from a query column (`data` +
`column`) or a literal `options` list. Add `multi` for a multi-select that feeds
an `IN (…)` clause.

```sql
SELECT month, SUM(downloads) AS downloads
FROM downloads
WHERE '${channel}' = '' OR channel = '${channel}'
GROUP BY month
ORDER BY month
```

<Dropdown name="channel" data={all_channels} column="channel" label="Channel" />

<LineChart data={dl_by_channel} x="month" y="downloads" title="Downloads (filtered)" explain />

Pick a channel — the chart re-queries. The `'${channel}' = ''` guard makes "no
selection" mean "all".

Add `multi` for a multi-select whose chosen values expand into an `IN (…)` list
(the same empty-means-all guard applies):

```sql
SELECT month, SUM(downloads) AS downloads
FROM downloads
WHERE '${channels}' = '' OR channel IN (${channels})
GROUP BY month
ORDER BY month
```

<Dropdown name="channels" data={all_channels} column="channel" label="Channels" multi />

<LineChart data={dl_multi} x="month" y="downloads" title="Downloads (multi-select)" explain />

| Attribute   | Purpose                                                  |
| ----------- | -------------------------------------------------------- |
| `name`      | **Required.** Filter key (the `${name}` your SQL reads). |
| `data` + `column` | Populate options from a query column.              |
| `options`   | …or a literal comma-separated list.                      |
| `label`     | Control label.                                           |
| `multi`     | Multi-select → `IN (…)`.                                  |
| `url_sync`  | Mirror the value to the URL (default `true`).            |
| `bar`       | Lift into the top [filter bar](/filters) (default: inline). |

:::note
Filter controls drive server-side SQL substitution, so they're stripped from
`dashdown build` static exports (a fixed snapshot can't re-query). See
[Filters](/filters).
:::


<!-- source: docs/pages/components/search.md -->

# Search (filter)

A free-text filter. Its value lands in `filters[name]`; reference it in SQL with
`${name}`. This is a **query filter** — for searching across *pages*, use
[SiteSearch](/components/site-search) instead.

```sql
SELECT channel, SUM(downloads) AS downloads
FROM downloads
WHERE '${q}' = '' OR channel ILIKE '%' || '${q}' || '%'
GROUP BY channel
ORDER BY downloads DESC
```

<Search name="q" label="Filter channels" placeholder="Type a channel…" />

<Table data={channel_like} title="Matching channels" />

| Attribute     | Purpose                                          |
| ------------- | ------------------------------------------------ |
| `name`        | **Required.** Filter key (`${name}` in SQL).     |
| `label`       | Accessible label.                                |
| `placeholder` | Input placeholder.                               |
| `debounce`    | Debounce in ms (default `300`).                  |
| `url_sync`    | Mirror to the URL (default `true`).              |
| `bar`         | Lift into the top [filter bar](/filters) (default: inline). |

Like other filter controls, `<Search>` is stripped from static builds.


<!-- source: docs/pages/components/date-range.md -->

# DateRange

A start/end date control with presets (`last_7_days`, `this_month`, `custom`, …).
It writes two URL params — `start_param` / `end_param` (default `name_start` /
`name_end`) — that your SQL reads.

```sql
SELECT date, visits
FROM daily
WHERE ('${from}' = '' OR date >= '${from}')
  AND ('${to}' = '' OR date <= '${to}')
ORDER BY date
```

<DateRange name="period" label="Period" start_param="from" end_param="to" presets="last_7_days,last_30_days,custom" />

<LineChart data={daily_in_range} x="date" y="visits" title="Visits in range" explain />

`default=` seeds a preset on first load (URL params still win):

<DateRange name="period2" label="Period" start_param="from2" end_param="to2" presets="last_7_days,last_30_days,custom" default="last_30_days" />

| Attribute       | Purpose                                                   |
| --------------- | --------------------------------------------------------- |
| `name`          | **Required.** Base filter key.                            |
| `start_param` / `end_param` | URL/SQL param names (default `name_start` / `name_end`). |
| `presets`       | Comma-separated preset list, in display order.            |
| `default`       | A preset applied on first load.                           |
| `persist`       | Remember the selection in `localStorage` across pages.    |
| `bar`           | Lift into the top [filter bar](/filters) (default: inline). |

The project-wide [global date filter](/filters) is this same control configured
once in `dashdown.yaml`. Filter controls are stripped from static builds.


<!-- source: docs/pages/components/toggle.md -->

# Toggle

A one-click switch (or checkbox) for a **boolean / two-valued** facet — "show
only X", "include archived", "paid only". It writes a string into the filter
store under `name`, exactly like the other filters, so your SQL reads it with
`${name}`.

```sql
SELECT date, visits
FROM daily
WHERE '${busy}' = '' OR visits >= 500
ORDER BY date
```

<Toggle name="busy" label="Busy days only" />

<LineChart data={daily_traffic} x="date" y="visits" title="Daily visits" explain />

Flip the switch — when **off** it stores `""`, so the `'${busy}' = ''` guard
passes and every day shows; when **on** it stores `"true"`, the guard fails, and
only `visits >= 500` days remain. This is the same "empty means all" convention a
[`<Dropdown>`](/components/dropdown) uses.

| Attribute    | Purpose                                                              |
| ------------ | ------------------------------------------------------------------- |
| `name`       | **Required.** Filter key your SQL reads as `${name}`.               |
| `label`      | Inline label shown in the control pill (defaults to `name`).        |
| `on_value`   | Value stored when **checked** (default `"true"`; any string).       |
| `off_value`  | Value stored when **unchecked** (default `""`; any string).         |
| `default`    | Start checked on first load (URL params still win).                 |
| `variant`    | `switch` (default) or `checkbox` styling.                           |
| `bar`        | Lift into the top [filter bar](/filters) (default: inline).         |

## Value modes

The default `on_value="true"` / `off_value=""` is the **all-guard** above — off
shows everything. Because `on_value` / `off_value` are **arbitrary strings**, a
non-empty `off_value` turns it into a **two-state** filter where both directions
narrow the data — including a text column that stores something like `Yes`/`No`:

```sql
SELECT date, visits
FROM daily
WHERE CASE WHEN '${weekend}' = 'Yes' THEN weekday IN ('Sat','Sun')
           ELSE weekday NOT IN ('Sat','Sun') END
ORDER BY date
```

<Toggle name="weekend" label="Weekends only" on_value="Yes" off_value="No" />

<LineChart data={daily_weekend} x="date" y="visits" title="Visits (weekend vs weekday)" explain />

Here **checked** sends `weekend = 'Yes'` (weekend days) and **unchecked** sends
`'No'` (weekdays) — there's no "show all" state, so both directions narrow the
data. `variant="checkbox"` swaps the switch for a checkbox:

<Toggle name="weekend_cb" label="Weekends only" on_value="Yes" off_value="No" variant="checkbox" />

(For three states — All / Yes / No — use a
[`<Dropdown options="Yes,No">`](/components/dropdown) instead; `<Toggle>` is the
one-click binary affordance.)

:::note
The value reaches SQL only through the same context-aware `${param}`
substitution as every other filter — it's always a quoted string literal, so a
toggle adds no new injection surface. Like the other filter controls, `<Toggle>`
is stripped from [static builds](/exporting) (a fixed snapshot can't be
re-filtered).
:::


<!-- source: docs/pages/components/time-grain.md -->

# TimeGrain

A filter control that lets a reader **re-bucket a time series** — Day / Week /
Month / Quarter / Year — without you pre-declaring one dimension per grain. It
writes a canonical grain token into the filter store under `name`, exactly what a
[semantic-layer](/semantic-layer) chart's `grain={name}` reads at fetch time:

```markdown
<TimeGrain name="trendGrain" default="month" />

<LineChart metric={sales.revenue} by={sales.order_date} grain={trendGrain} />
```

Pick a grain and the chart re-queries at that bucket, on the same filter
re-fetch path as every other control — no new plumbing.

Here's the control itself (the chart binding above needs a semantic model, so it's
shown as source only):

<TimeGrain name="trendGrain" default="month" />

Add `native` to include an ungrouped "Native" choice:

<TimeGrain name="grain2" grains="day,month,year" native default="day" />

:::note Pairs with the semantic layer
`grain=` is a **grouping** modifier the [semantic backends](/semantic-layer)
translate to their native time-truncation (Ibis `.truncate()`, Cube
`granularity`). So `<TimeGrain>` drives `grain={…}` on a **`metric={…}
by={…}`** chart — it has nothing to bucket on a plain `data={query}` chart (write
the `DATE_TRUNC` yourself there). See [Time grain](/semantic-layer#time-grain--grain).
:::

## Why not a plain Dropdown?

`<TimeGrain>` is sugar over `<Dropdown options="day,week,month,quarter,year">`, with
three conveniences a bare dropdown can't give you:

- **Nice labels** — shows `Month`, stores `month`.
- **Validation** — the offered `grains=` are checked against the canonical token
  set (`second`, `minute`, `hour`, `day`, `week`, `month`, `quarter`, `year`) at
  render, so a typo fails fast instead of silently producing an empty chart.
- **A real default** — `default="month"` actually seeds the first-load selection
  (URL params still win), so the chart's shown grain matches its grouping on load.

A plain `<Dropdown>` whose option values are those tokens still works as a grain
switcher — `<TimeGrain>` is just the ergonomic, grain-aware version.

## Attributes

| Attribute | Purpose |
| --------- | ------- |
| `name` | **Required.** Filter key a chart reads as `grain={name}`. |
| `label` | Pill label (default `Grain`). |
| `grains` | Comma-separated subset to offer (default `day,week,month,quarter,year`). |
| `default` | First-load grain (must be one of `grains`; else `month`, or the first grain). |
| `native` | Add a "Native" (ungrouped) choice; selecting it removes the bucketing. |
| `bar` | Lift into the top [filter bar](/filters) (default: inline). |

Like every filter control, `<TimeGrain>` is `is_filter` — it's stripped from
[static builds](/exporting) (a fixed snapshot can't be re-grouped) and suppressed
from the "filtered by" badge (a grain is a grouping, not a filter).


<!-- source: docs/pages/components/range-slider.md -->

# RangeSlider

A dual-handle numeric slider for a *between* bound on a numeric column (price,
age, score, …). Like [`<DateRange>`](/components/date-range) it owns **two** URL
params — `min_param` / `max_param` (default `name_min` / `name_max`) — that your
SQL reads.

A handle resting on its track bound writes an **empty value** (the same
empty-means-all convention as every other filter), so **guard each bound** — the
empty case (a wide-open slider, or the first fetch before the control seeds)
then shows everything instead of erroring on `CAST('' AS DOUBLE)`:

```sql
SELECT device, tier, price
FROM device_specs
WHERE ('${price_min}' = '' OR price >= CAST(${price_min} AS DOUBLE))
  AND ('${price_max}' = '' OR price <= CAST(${price_max} AS DOUBLE))
ORDER BY price DESC
```

<RangeSlider name="price" min={500} max={1500} step={50} default={[700,1300]} label="Price ($)" format="currency" currency="$" />

<BarChart data={devices_in_range} x="device" y="price" title="Devices in price range" explain />

Drag either handle — the chart re-queries. The readout above the track shows the
live low/high, formatted with the same `format=`/`currency=` options the other
components use.

Each handle substitutes as a **quoted string literal** (the one injection-safe
path every filter shares), so the `CAST(… AS DOUBLE)` turns it back into a number
for the comparison. The `'${price_min}' = ''` half of each clause is the
empty-means-all guard — drag a handle back to its bound and that side stops
filtering.

## The `default` bounds

`default=` seeds the initial `[low, high]` on first load (URL params still win).
Write it as an **array literal** or a **comma string** — both clamp into
`[min, max]`:

```html
<RangeSlider name="price" min={0} max={10000} step={50} default={[2000,8000]} />
<RangeSlider name="price" min={0} max={10000} step={50} default="2000, 8000" />
```

:::note
Inside `{…}` keep the values **space-free** (`{[2000,8000]}`, not
`{[2000, 8000]}`) — an unquoted attribute value with a space isn't recognized as
a tag and renders as plain text. Use the quoted `default="2000, 8000"` form if
you want spaces. Omitting `default` starts the slider wide open at `[min, max]`.
:::

| Attribute       | Purpose                                                       |
| --------------- | ------------------------------------------------------------- |
| `name`          | **Required.** Base filter key.                                |
| `min` / `max`   | Track bounds (default `0` / `100`). `max` must exceed `min`.  |
| `step`          | Handle increment (default `1`).                               |
| `default`       | Initial `[low, high]` pair (default: the full `[min, max]`).  |
| `min_param` / `max_param` | URL/SQL param names (default `name_min` / `name_max`). |
| `format` / `currency` / `decimals` / `locale` | Format the readout values. |
| `bar`           | Lift into the top [filter bar](/filters) (default: inline).   |

Filter controls drive **server-side** SQL substitution, so they're stripped from
[static builds](/exporting) automatically.


<!-- source: docs/pages/components/button-group.md -->

# ButtonGroup

A single-select filter shown as an inline **segmented control** — a row of pill
buttons where exactly one is active (`All · High · Mid · Budget`). A
lower-friction alternative to a [`<Dropdown>`](/components/dropdown) for a small,
**fixed** set of choices: one click instead of open-then-pick.

The picked value is stored as a string under `name`, exactly like the other
filters, so your SQL reads it with `${name}`. The default **"All"** segment stores
`""`, so the `'${tier}' = ''` guard passes and every row shows — the same
empty-means-all convention a single-select Dropdown uses:

```sql
SELECT device, tier, price
FROM device_specs
WHERE '${tier}' = '' OR tier = '${tier}'
ORDER BY price DESC
```

<ButtonGroup name="tier" label="Tier" options="High,Mid,Budget" />

<BarChart data={devices_by_tier} x="device" y="price" title="Devices by tier" explain />

Click a segment — the chart re-queries. Click **All** to drop the filter again
(selecting a segment doesn't toggle off, so "All" is how you clear it).

| Attribute     | Purpose                                                            |
| ------------- | ----------------------------------------------------------------- |
| `name`        | **Required.** Filter key your SQL reads as `${name}`.             |
| `options`     | **Required.** Choices — `options="High,Mid,Budget"` or `options={[High,Mid,Budget]}`. Value == label. |
| `label`       | Inline label shown before the segments (defaults to `name`).      |
| `include_all` | Prepend an **All** segment that clears the filter (default `true`). |
| `all_label`   | Text for that segment (default `"All"`).                          |
| `default`     | Value selected on first load (URL params still win).              |
| `bar`         | Lift into the top [filter bar](/filters) (default: inline).       |

:::note
A ButtonGroup is for a handful of **fixed** options you'd lay out as buttons. For
a **dynamic** or high-cardinality column, use a [`<Dropdown>`](/components/dropdown)
— it populates its options from the data. The value reaches SQL through the same
context-aware `${param}` substitution as every other filter (always a quoted
string literal, no new injection surface), and like the other controls it's
stripped from [static builds](/exporting).
:::


<!-- source: docs/pages/components/combobox.md -->

# Combobox

A **searchable** single-select for a **high-cardinality** column — type to filter
over thousands of customers, SKUs, or users where a plain
[`<Dropdown>`](/components/dropdown) (which loads *every* distinct value) would
choke.

Options are fetched **server-side as you type**: the browser hits
`/_dashdown/api/options/{query}` and the backend runs a `SELECT DISTINCT … WHERE
col ILIKE '%term%' LIMIT N` against the warehouse, so only a small matching page
is ever shipped. Results rank **prefix matches first** (typing `num` surfaces
`numpy` above `abnum`), alphabetical within each band. The picked value lands in
`filters[name]` like every other filter, so your SQL reads it with `${name}` and
the empty (nothing picked) value trips the all-guard:

```sql
SELECT country, downloads
FROM by_country
WHERE '${country}' = '' OR country = '${country}'
ORDER BY downloads DESC
```

<Combobox name="country" data={countries} column="country" label="Country" placeholder="Search countries…" />

<BarChart data={country_rows} x="country" y="downloads" title="Downloads by country" explain />

Start typing — the panel lists matching values fetched from the server; pick one
and the chart re-queries. The **×** clears the selection.

The `data={query}` + `column` pair names where the distinct values come from. It
needn't be the same query the chart shows — point it at a lightweight lookup
(`SELECT country FROM by_country`) and let the heavier display query carry the
`${country}` guard.

## Multi-select

Add **`multi`** to pick several values. They're stored as one comma-joined string
that feeds an `IN (…)` clause — identical to a multi-select
[`<Dropdown>`](/components/dropdown), so the values expand into a quoted,
per-item-escaped literal list (empty selection → matches all):

```sql
SELECT country, downloads
FROM by_country
WHERE '${countries}' = '' OR country IN (${countries})
ORDER BY downloads DESC
```

<Combobox name="countries" data={countries} column="country" label="Countries" multi placeholder="Add a country…" />

<BarChart data={country_multi} x="country" y="downloads" title="Downloads (selected countries)" explain />

Picks show as removable chips before the search box; the panel marks chosen rows
with a ✓ and stays open so you can add several. **Backspace** on an empty input
removes the last chip.

| Attribute   | Purpose                                                            |
| ----------- | ----------------------------------------------------------------- |
| `name`      | **Required.** Filter key your SQL reads as `${name}`.             |
| `data` + `column` | **Required.** The query + column the distinct values come from. |
| `multi`     | Multi-select → a comma-joined value for an `IN (…)` clause.        |
| `label`     | Inline label (defaults to `name`).                                |
| `placeholder` | Input placeholder (default `"Search…"`).                        |
| `limit`     | Max options fetched per keystroke (default `50`; server caps at `200`). |
| `min_chars` | Only search once this many characters are typed (default `0`).    |
| `bar`       | Lift into the top [filter bar](/filters) (default: inline).       |

:::note
**SQL connectors only** — the options endpoint wraps your query as a subquery,
which a non-SQL backend (DAX) or a [Python query](/python-queries) can't satisfy.
The search term and column go through the **same injection-safe rules** as
`${param}` substitution (the column must be a bare identifier; the term is always
a quoted literal), so there's no new injection surface. Like the other filter
controls, `<Combobox>` is stripped from [static builds](/exporting) — a fixed
snapshot has no server to search.
:::


<!-- source: docs/pages/components/slider.md -->

# Slider

A single-value numeric **threshold** filter — one handle on a track for a
`min rating ≥`, `price ≤`, or `top N` style bound. The one-handled sibling of
[`<RangeSlider>`](/components/range-slider) (which carries a low/high pair).

The value is stored under `name`, so your SQL reads it with `${name}`. Guard the
comparison so a missing value (the brief moment before the control seeds) shows
everything rather than erroring on `CAST('' AS DOUBLE)`. The **operator you pick
decides which handle position means "all"**: `>=` → the minimum, `<=` → the
maximum.

```sql
SELECT device, rating
FROM device_specs
WHERE '${min_rating}' = '' OR rating >= CAST(${min_rating} AS DOUBLE)
ORDER BY rating DESC
```

<Slider name="min_rating" min={0} max={5} step={0.1} default={4} label="Min rating" />

<BarChart data={devices_rated} x="device" y="rating" title="Devices at or above the rating" explain />

Drag the handle — the chart re-queries. The readout above the track shows the
live value, formatted with the same `format=`/`currency=` options the other
components use.

| Attribute     | Purpose                                                          |
| ------------- | ---------------------------------------------------------------- |
| `name`        | **Required.** Filter key your SQL reads as `${name}`.           |
| `min` / `max` | Track bounds (default `0` / `100`). `max` must exceed `min`.    |
| `step`        | Handle increment (default `1`).                                 |
| `default`     | Initial value (default: `min`). URL params still win.           |
| `format` / `currency` / `decimals` / `locale` | Format the readout value.       |
| `bar`         | Lift into the top [filter bar](/filters) (default: inline).     |

:::tip
For a **between** bound (low *and* high), use [`<RangeSlider>`](/components/range-slider)
— it carries two handles and a `${name}_min` / `${name}_max` pair. `<Slider>` is
the single-threshold control.
:::

Filter controls drive **server-side** SQL substitution, so they're stripped from
[static builds](/exporting) automatically.


<!-- source: docs/pages/components/site-search.md -->

# SiteSearch

Full-text search across **every page** of the project, ranked client-side. Unlike
the [Search](/components/search) *filter*, it searches a static index of all pages
(not a query), so it survives static builds. Dashdown already puts one in the app
header and the mobile menu — add `<SiteSearch>` in a page for an extra in-context
box.

```markdown
<SiteSearch placeholder="Search the docs…" max_results="8" />
```

<SiteSearch placeholder="Search the docs…" />

| Attribute     | Default                 | Purpose                  |
| ------------- | ----------------------- | ------------------------ |
| `placeholder` | `Search documentation…` | Input placeholder.       |
| `label`       | `Search`                | Accessible label.        |
| `max_results` | `8`                     | How many results to show.|

For the full design — index, ranking, static vs live — see the
[Full-text search](/search) page.
