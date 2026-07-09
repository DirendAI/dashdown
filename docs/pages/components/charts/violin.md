---
title: Violin
sidebar_label: Violin
sidebar_position: 10
---

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
