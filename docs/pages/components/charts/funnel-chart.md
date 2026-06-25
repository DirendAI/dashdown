---
title: FunnelChart
sidebar_label: FunnelChart
sidebar_position: 6
---

# FunnelChart

Stage-by-stage values, widest at the top — useful for conversion / drop-off.
`x` labels each stage, `y` is its value.

```markdown
<FunnelChart data={channel_totals} x="channel" y="downloads" title="Channels by volume" />
```

<FunnelChart data={channel_totals} x="channel" y="downloads" title="Channels by volume" />

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
