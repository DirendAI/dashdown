---
title: PieChart
sidebar_label: PieChart
sidebar_position: 4
---

# PieChart

Part-to-whole breakdown. `x` is the category, `y` the value. PieCharts default to
a **donut** with a center total; pass `donut=false` for a solid pie.

```markdown
<PieChart data={channel_totals} x="channel" y="downloads" title="Share by channel" />
```

<PieChart data={channel_totals} x="channel" y="downloads" title="Share by channel" />

Solid pie:

<PieChart data={channel_totals} x="channel" y="downloads" donut=false title="Solid pie" />

## Faceted (small multiples)

Add `series=` and the pie splits into **one pie per value** — a small-multiples
grid that shares a single slice legend, ideal for comparing the *same* breakdown
across a dimension. Here the channel mix, one pie per month:

```markdown
<PieChart data={by_channel_recent} x="channel" y="downloads" series="month"
          title="Channel mix by month" />
```

<PieChart data={by_channel_recent} x="channel" y="downloads" series="month" title="Channel mix by month" height=340 />

The pies are sized to fill the card from its live dimensions and re-fit on resize.
(Faceted pies are always solid — the `donut` center total applies to a single pie
only.)

## From the semantic layer

Like every chart, PieChart also takes [semantic metric refs](/semantic-layer)
instead of `data={query}`:

```markdown
<PieChart metric={sales.revenue} by={sales.region} />
```

As with a `data=` pie, a second dimension `series={model.dim}` renders the faceted
small-multiples grid — one pie per value.

## Attributes

| Attribute | Purpose |
| --------- | ------- |
| `data` | **Required.** The query to plot (`data={query}`). |
| `x` | **Required.** Category column (slice labels). |
| `y` | **Required.** Value column (slice sizes). |
| `series` | Facet column — renders one pie per value (small multiples). |
| `donut` | Donut with a center total (**default `true`**); `donut=false` for a solid pie. Ignored when faceted. |
| `title` | Chart title. |
| `color` | Single color or comma-separated palette override. |
| `height` | Pixel height (default `300`). |
| `col-span` | Columns to span inside a `<Grid>`. |
| `format` · `currency` · `decimals` · `locale` | Value & tooltip number formatting. |
| `empty_message` | Text shown when the query returns no rows. |

`donut` is specific to PieChart; the rest are the shared chart attributes — see [Charts](/components/charts).
