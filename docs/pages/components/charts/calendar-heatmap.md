---
title: CalendarHeatmap
sidebar_label: CalendarHeatmap
sidebar_position: 8
---

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
