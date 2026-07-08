---
title: Counter
sidebar_label: Counter
sidebar_position: 5
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
| `format`         | [Number format](/formatting) — `compact` fits a billions-scale KPI (`3.34B`, exact value on hover). |
| `color`          | DaisyUI color name (`primary`, `success`, …).       |
| `delta` / `compare` | Show a change badge (static value or vs another query). |
| `sparkline` / `sparkline-column` | Draw an inline trend line from a series query (or a `metric` + `sparkline-by` time dimension on a semantic dashboard). |
| `breakdown` / `breakdown-label` / `breakdown-column` | Draw a proportional composition strip from a per-category query (or a `metric` + `breakdown-by` dimension on a semantic dashboard). `breakdown-legend=false` hides its legend line; `breakdown-values` picks what it prints (`percent`/`value`/`both`). |

## Sparklines

Pass a second, multi-row query to `sparkline={…}` to draw a trend line along the
card's bottom edge, behind the number — handy for showing *where* a KPI has been,
not just where it landed.
`sparkline-column` picks which column of that series to plot (the headline value
still comes from `data`/`column`). The card doesn't grow to fit the trend: a
spark tile stays exactly as tall as a plain one, and where the line passes under
the text a soft halo of the card's surface color keeps the number legible.

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

## Breakdowns

Pass a per-category query to `breakdown={…}` to draw a proportional composition
strip along the card's bottom — a "one-row treemap" showing *how the KPI splits*,
one colored segment per row, widths proportional to each category's share.
`breakdown-label` / `breakdown-column` pick the category and value columns
(defaults: first non-numeric / first numeric). Hover a segment for its exact
value and share; a compact legend line spells out the categories
(`breakdown-legend=false` hides it). By default the legend prints each
category's **share** — `breakdown-values="value"` prints the value instead
(formatted like the headline, so `format="compact"` gives `pip 7.6K`), and
`breakdown-values="both"` prints `pip 7.6K · 72%`.

```markdown
<Counter data={downloads_total} column="downloads" label="Total downloads"
         breakdown={channel_totals} breakdown-label="channel" breakdown-column="downloads" />
```

<Counter data={downloads_total} column="downloads" label="Total downloads"
         breakdown={channel_totals} breakdown-label="channel" breakdown-column="downloads" />

<Counter data={downloads_total} column="downloads" label="Total downloads" format="compact"
         breakdown={channel_totals} breakdown-label="channel" breakdown-column="downloads" breakdown-values="both" />

Segment colors follow the same palette as the charts (your
`branding.palette` if set), so the strip matches a pie or bar chart of the same
dimension elsewhere on the page. Categories beyond the palette fold into a single
neutral **Other** segment; negative values don't compose and are skipped. A
breakdown and a `sparkline` are mutually exclusive — both draw along the card's
bottom edge.

On a [semantic](/semantic-layer) dashboard, point `breakdown=` at a **metric** and
`breakdown-by=` at a **dimension** instead of writing the per-category query:

```markdown
<Counter metric={sales.revenue} label="Revenue"
         breakdown={sales.revenue} breakdown-by={sales.region} />
```

For an inline single value inside prose, use [Value](/components/value) instead.
