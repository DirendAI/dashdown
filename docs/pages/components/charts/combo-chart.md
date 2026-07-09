---
title: ComboChart
sidebar_label: ComboChart
sidebar_position: 3
---

# ComboChart

Draw **bars and lines on one chart**, with an optional **second (right-hand)
y-axis** — the classic "volume as bars, a rate or a much smaller number as a
line" pattern. It's the one cartesian type that mixes series *types* and carries
two value axes, so instead of a single `y` it takes `bars=` and `lines=` (column
lists) plus `right_axis=` (the subset plotted against the right axis).

```markdown
<ComboChart data={traffic_combo} x="date"
            bars="visits" lines="signups" right_axis="signups"
            title="Visits (bars) vs signups (line)" />
```

```sql traffic_combo
SELECT date, visits, signups
FROM daily
ORDER BY date
```

<ComboChart data={traffic_combo} x="date" bars="visits" lines="signups" right_axis="signups" title="Visits (bars) vs signups (line)" explain />

`visits` (in the hundreds) draws as bars on the **left** axis; `signups` (in the
tens) draws as a line on its **own right axis** via `right_axis="signups"`, so the
small series isn't flattened against the big one. Drop `right_axis` and both share
a single left axis.

## Multiple columns per role

`bars=` and `lines=` each take a **comma-separated list** — every column becomes
its own bar or line series, sharing the legend:

```markdown
<ComboChart data={q} x="month"
            bars="pip,docker" lines="source" right_axis="source" />
```

<ComboChart data={downloads_by_channel_wide} x="month" bars="pip,docker" lines="source" right_axis="source" title="pip + docker bars, source line" explain />

## Per-series colours

`bar_color` and `line_color` override just the bar or line colours (a single
colour, or a comma list cycled across multiple series) — the usual "indigo bars,
amber line":

```markdown
<ComboChart data={traffic_combo} x="date"
            bars="visits" lines="signups" right_axis="signups"
            bar_color="#6366f1" line_color="#f59e0b" />
```

<ComboChart data={traffic_combo} x="date" bars="visits" lines="signups" right_axis="signups" bar_color="#6366f1" line_color="#f59e0b" title="Indigo bars, amber line" explain />

## From the semantic layer

Like every chart, ComboChart also takes [semantic metric
refs](/semantic-layer) instead of `data={query}` — list metrics from **one model**
in `bars=`/`lines=`, group with `by=` (and optional `grain=`). Each axis defaults
to its metric's declared number format:

```markdown
<ComboChart by={sales.order_date} grain="month"
            bars={sales.revenue} lines={sales.orders} right_axis={sales.orders} />
```

There is no `series=` on a ComboChart — the metrics (or columns) **are** the
series.

## Attributes

| Attribute | Purpose |
| --------- | ------- |
| `data` | The query to plot (`data={query}`) — or omit it and use metric refs. |
| `x` | **Required (query mode).** Category / x-axis column. |
| `bars` | Columns (or metric refs) drawn as **bars**. One or more, comma-separated. |
| `lines` | Columns (or metric refs) drawn as **lines**. One or more, comma-separated. |
| `right_axis` | The subset of `bars`/`lines` plotted against a **right-hand** y-axis. |
| `by` | **Semantic mode.** Dimension to group the metrics by. |
| `grain` | **Semantic mode.** Bucket a time `by` — `day`/`week`/`month`/… or `grain={control}`. |
| `bar_color` / `line_color` | Colour override for just the bar / line series (single or comma list). |
| `title` | Chart title. |
| `sort_by` | Column to sort by before plotting. |
| `height` | Pixel height (default `320`). |
| `col-span` | Columns to span inside a `<Grid>`. |
| `format` · `currency` · `decimals` · `locale` | **Left**-axis number formatting. |
| `right_format` · `right_currency` · `right_decimals` · `right_locale` | **Right**-axis number formatting. |
| `empty_message` | Text shown when the query returns no rows. |

At least one of `bars=` / `lines=` is required. See [Charts](/components/charts)
for the shared attributes and [Formatting](/formatting) for the number/date keys.
