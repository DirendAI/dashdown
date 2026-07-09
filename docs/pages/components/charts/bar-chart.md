---
title: BarChart
sidebar_label: BarChart
sidebar_position: 2
---

# BarChart

Compare values across categories. Add `horizontal` to swap the axes (category on
the Y axis), or `series` for grouped bars — and `stacked` to stack those groups.

```markdown
<BarChart data={channel_totals} x="channel" y="downloads" title="By channel" />
```

<BarChart data={channel_totals} x="channel" y="downloads" title="Total by channel" explain />

Horizontal:

<BarChart data={channel_totals} x="channel" y="downloads" horizontal title="By channel (horizontal)" explain />

Grouped by series and **stacked**:

<BarChart data={by_channel} x="month" y="downloads" series="channel" stacked title="Downloads by month (stacked)" explain />

## Multiple metrics

When your value columns are *already* side by side (one column per metric), list
them in `y`, comma-separated — each becomes its own coloured series with a legend.
No `series=` grouping needed:

```markdown
<BarChart data={downloads_by_channel_wide} x="month" y="pip,docker,source"
          title="Downloads by channel" />
```

<BarChart data={downloads_by_channel_wide} x="month" y="pip,docker,source" title="Downloads by channel" explain />

This is the complement of `series=`: use **`series=`** to split *one* value column
by a category, or **a comma-separated `y`** to plot *several* value columns. The
two are mutually exclusive — see [Multiple series](/components/charts#multiple-series).

## From the semantic layer

Like every chart, BarChart also takes [semantic metric refs](/semantic-layer)
instead of `data={query}`:

```markdown
<BarChart metric={sales.revenue} by={sales.region} />
```

A comma-separated `metric=` gives one series per measure, and `series={model.dim}`
splits a single metric by a second dimension — the same two shapes as a `data=`
chart. A time `by` buckets on demand with `grain=`.

## Attributes

| Attribute | Purpose |
| --------- | ------- |
| `data` | **Required.** The query to plot (`data={query}`). |
| `x` | **Required.** Category column. |
| `y` | **Required.** Value column — or several, comma-separated (`y="pip,docker"`), for one series per metric. |
| `horizontal` | Swap the axes — category on the Y axis, bars running along X. |
| `series` | Column to split into grouped bars (a second dimension). |
| `stacked` | With `series`, stack the groups on a shared total. |
| `title` | Chart title. |
| `sort_by` | Column to sort by before plotting. |
| `color` | Single color or comma-separated palette override. |
| `height` | Pixel height (default `300`). |
| `col-span` | Columns to span inside a `<Grid>`. |
| `format` · `currency` · `decimals` · `locale` · `date_format` | Value & tooltip number/date formatting. |
| `empty_message` | Text shown when the query returns no rows. |

`horizontal` and `stacked` are specific to BarChart; the rest are the shared chart attributes — see [Charts](/components/charts).
