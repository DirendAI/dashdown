---
title: LineChart
sidebar_label: LineChart
sidebar_position: 1
---

# LineChart

Trends over a continuous or time axis. Add `series` to draw one line per group.

```markdown
<LineChart data={by_channel} x="month" y="downloads" series="channel"
           title="Downloads by channel" format="number" />
```

<LineChart data={by_channel} x="month" y="downloads" series="channel" title="Downloads by channel" />

Without `series` you get a single line:

<LineChart data={downloads_by_month} x="month" y="downloads" title="Total downloads" />

Add `stacked` (with a `series`) for a stacked-area chart:

<LineChart data={by_channel} x="month" y="downloads" series="channel" stacked title="Downloads by channel (stacked)" />

Or pass a comma-separated `y` for one line per metric column (no `series` needed):

<LineChart data={downloads_by_channel_wide} x="month" y="pip,docker,source" title="Downloads per channel (multi-metric)" />

## From the semantic layer

Like every chart, LineChart also takes [semantic metric refs](/semantic-layer)
instead of `data={query}` — and a date `by` buckets on demand with `grain=`:

```markdown
<LineChart metric={sales.revenue} by={sales.order_date} grain="month" />
```

`series={model.dim}` splits one metric into a line per value; a comma-separated
`metric=` draws one line per measure.

## Attributes

| Attribute | Purpose |
| --------- | ------- |
| `data` | **Required.** The query to plot (`data={query}`). |
| `x` | **Required.** Column for the x-axis (category / time). |
| `y` | **Required.** Column for the value (y-axis). |
| `series` | Column to split into one line per group. |
| `stacked` | With `series`, stack the lines into a cumulative area. |
| `title` | Chart title. |
| `sort_by` | Column to sort by before plotting. |
| `color` | Single color or comma-separated palette override. |
| `height` | Pixel height (default `300`). |
| `col-span` | Columns to span inside a `<Grid>`. |
| `format` · `currency` · `decimals` · `locale` · `date_format` | Value & tooltip number/date formatting. |
| `empty_message` | Message shown when the query returns no rows (default `"No data available"`). |

`stacked` pairs with `series`; the rest are the shared chart attributes — common to every chart type — see [Charts](/components/charts).
