---
title: ParallelChart
sidebar_label: ParallelChart
sidebar_position: 21
---

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
