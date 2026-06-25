---
title: Counter
sidebar_label: Counter
sidebar_position: 4
icon: "\U0001F522"
---

# Counter

A single big-number KPI. Reads one cell — `column` from a given `row` (default the
first). Add `prefix`/`suffix` for units and an optional `delta` badge.

```markdown
<Counter data={downloads_total} column="downloads" label="Total downloads" />
```

<Counter data={downloads_total} column="downloads" label="Total downloads" />

<Counter data={channel_totals} column="downloads" row="0" label="Top channel" suffix=" dl" color="primary" />

A `delta=` badge shows a ▲/▼ change pill; or pass `compare={query}` (with
`compare-row` / `compare-column`) to derive the change from another row or query
instead of a literal percentage:

<Counter data={downloads_total} column="downloads" label="Total downloads" delta="12.4" />

<Counter data={channel_totals} column="downloads" row="0" compare={channel_totals} compare-row="1" label="Top vs next channel" />

| Attribute        | Purpose                                              |
| ---------------- | --------------------------------------------------- |
| `data`           | **Required.** The query to read.                    |
| `column`         | Which column to display.                            |
| `row`            | Row index (default `0`).                            |
| `label`          | Caption under the number.                           |
| `prefix`/`suffix`| Text around the value.                              |
| `color`          | DaisyUI color name (`primary`, `success`, …).       |
| `delta` / `compare` | Show a change badge (static value or vs another query). |
| `sparkline` / `sparkline-column` | Draw an inline trend line from a series query (or a `metric` + `sparkline-by` time dimension on a semantic dashboard). |

## Sparklines

Pass a second, multi-row query to `sparkline={…}` to draw a small trend line under
the number — handy for showing *where* a KPI has been, not just where it landed.
`sparkline-column` picks which column of that series to plot (the headline value
still comes from `data`/`column`).

```markdown
<Counter data={downloads_total} column="downloads" label="Total downloads"
         sparkline={downloads_by_month} sparkline-column="downloads" />
```

<Counter data={downloads_total} column="downloads" label="Total downloads"
         sparkline={downloads_by_month} sparkline-column="downloads" />

With a sparkline, `color` paints the **trend line** and the headline number stays
neutral (the mockup KPI style); without one, `color` colors the number itself. It
pairs naturally with a `delta`/`compare` badge to show both the latest change and
the longer trend.

### Sparklines from a metric

On a [semantic](/semantic-layer) dashboard you don't even need a series query: point
`sparkline=` at a **metric** and `sparkline-by=` at the model's **time dimension**,
and the framework builds the bucketed trend for you — the same way `metric=` drives
the headline.

```markdown
<Counter metric={sales.revenue} label="Revenue"
         sparkline={sales.revenue} sparkline-by={sales.order_date} grain="month" />
```

`grain=` buckets the time dimension (`day`/`week`/`month`/`quarter`/`year`), literal
or pointed at a `{control}` like any other grain. The metric's value column is plotted
automatically, so there's no `sparkline-column` to set. (Omit `sparkline-by=` and the
classic series-query form above still applies, even for a semantic headline.)

For an inline single value inside prose, use [Value](/components/value) instead.
