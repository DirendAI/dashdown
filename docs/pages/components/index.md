---
title: Components
sidebar_label: Components
sidebar_position: 10
icon: "\U0001F4CA"
---

# Components

A component is a PascalCase tag you drop into a page. Most visual components take
a `data={query}` reference and render in the browser from that query's result.
Unknown tags or render errors become an inline error card — never a 500.

## Charts

One rendering path drives every chart type. See **[Charts](/components/charts)**
for the shared attributes, then the per-type page:

- [LineChart](/components/charts/line-chart) · [BarChart](/components/charts/bar-chart) · [ComboChart](/components/charts/combo-chart) · [PieChart](/components/charts/pie-chart) · [ScatterChart](/components/charts/scatter-chart)
- [FunnelChart](/components/charts/funnel-chart) · [TreemapChart](/components/charts/treemap-chart) · [CalendarHeatmap](/components/charts/calendar-heatmap)
- [BoxPlot](/components/charts/box-plot) · [Violin](/components/charts/violin) · [MapChart](/components/charts/map-chart)
- [RadarChart](/components/charts/radar-chart) · [GaugeChart](/components/charts/gauge-chart) · [HeatmapChart](/components/charts/heatmap-chart)
- [SankeyChart](/components/charts/sankey-chart) · [CandlestickChart](/components/charts/candlestick-chart) · [ThemeRiver](/components/charts/theme-river)
- [GraphChart](/components/charts/graph-chart) · [SunburstChart](/components/charts/sunburst-chart) · [TreeChart](/components/charts/tree-chart) · [ParallelChart](/components/charts/parallel-chart)
- [Chart auto](/components/charts/auto-chart)

## Data display

- [Table](/components/table) — sortable, filterable grid with CSV export.
- [PivotTable](/components/pivot-table) — client-side cross-tab.
- [Counter](/components/counter) — a single KPI with an optional delta.
- [Value](/components/value) — an inline single value.

## Layout & content

- [Grid](/components/grid) — multi-column layout for widgets.
- [Ask](/ai/ask) — LLM commentary on a query result (documented under [AI](/ai)).

## Filters & search

- [Dropdown](/components/dropdown) · [Combobox](/components/combobox) · [Search](/components/search) · [DateRange](/components/date-range) · [RangeSlider](/components/range-slider) · [Slider](/components/slider) · [ButtonGroup](/components/button-group) · [Toggle](/components/toggle) · [TimeGrain](/components/time-grain) — see also the [Filters](/filters) concept page.
- [SiteSearch](/components/site-search) — full-text search across all pages.
