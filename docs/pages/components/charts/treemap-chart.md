---
title: TreemapChart
sidebar_label: TreemapChart
sidebar_position: 7
---

# TreemapChart

Proportions as nested rectangles — area encodes the value. `x` is the label, `y`
the value.

```markdown
<TreemapChart data={channel_totals} x="channel" y="downloads" title="Share by channel" />
```

<TreemapChart data={channel_totals} x="channel" y="downloads" title="Share by channel" />

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
