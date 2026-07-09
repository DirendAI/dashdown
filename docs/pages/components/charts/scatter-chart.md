---
title: ScatterChart
sidebar_label: ScatterChart
sidebar_position: 5
---

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
