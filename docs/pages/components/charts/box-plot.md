---
title: BoxPlot
sidebar_label: BoxPlot
sidebar_position: 9
---

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
