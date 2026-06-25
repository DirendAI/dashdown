---
title: HeatmapChart
sidebar_label: HeatmapChart
sidebar_position: 14
---

# HeatmapChart

A matrix of cells shaded by magnitude — great for category-by-category
intensity (hour × weekday, month × channel). `x` and `y` are **both category
axes** and `value` is the per-cell magnitude column.

```markdown
<HeatmapChart data={by_channel} x="month" y="channel" value="downloads" title="Downloads by month & channel" />
```

<HeatmapChart data={by_channel} x="month" y="channel" value="downloads" title="Downloads by month & channel" />

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
