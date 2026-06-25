---
title: RadarChart
sidebar_label: RadarChart
sidebar_position: 12
---

# RadarChart

Compare several metrics on one shape — one axis per metric, one polygon per
group. `x` is the indicator (axis) column, `y` the value, and an optional
`series` overlays a polygon per group. Each axis is scaled to the largest value
seen for that indicator.

```markdown
<RadarChart data={feature_scores} x="metric" y="score" series="product" title="Feature scores" />
```

<RadarChart data={feature_scores} x="metric" y="score" series="product" title="Feature scores" />

Omit `series` for a single polygon:

:::query name=dashdown_scores connector=main
SELECT metric, score FROM feature_scores WHERE product = 'Dashdown'
:::

<RadarChart data={dashdown_scores} x="metric" y="score" title="Dashdown scores" />

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
