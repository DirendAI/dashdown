---
title: ThemeRiver
sidebar_label: ThemeRiver
sidebar_position: 17
---

# ThemeRiver

A streamgraph — stacked categories flowing over time, each band's thickness
its value. `x` is the time column (ISO dates parse best), `y` the value, and
`series` the category each stream represents.

```markdown
<ThemeRiver data={daily_streams} x="date" y="value" series="metric" title="Activity streams" />
```

<ThemeRiver data={daily_streams} x="date" y="value" series="metric" title="Activity streams" />

## From the semantic layer

Like every chart, ThemeRiver also takes [semantic metric refs](/semantic-layer)
instead of `data={query}`. `series=` is required (it splits the streams), so pair
a `metric` with a `by` time dimension and a `series` category:

```markdown
<ThemeRiver metric={sales.revenue} by={sales.order_date} series={sales.region} grain="month" />
```

## Attributes

| Attribute | Purpose |
| --------- | ------- |
| `data` | **Required.** The query to plot (`data={query}`). |
| `x` | **Required.** Time column (ISO dates like `2026-06-01` parse cleanly). |
| `y` | **Required.** Value column — the band thickness. |
| `series` | **Required.** Category column — one stream per distinct value. |
| `title` | Chart title. |
| `sort_by` | Column to sort by before plotting. |
| `color` | Single color or comma-separated palette override. |
| `height` | Pixel height (default `300`). |
| `col-span` | Columns to span inside a `<Grid>`. |
| `format` · `currency` · `decimals` · `locale` | Tooltip value formatting. |
| `empty_message` | Text shown when the query returns no rows. |

ThemeRiver reuses the shared `x`/`y`/`series` attributes — `series` is required (it's what splits the streams). See [Charts](/components/charts).
