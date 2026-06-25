---
title: GaugeChart
sidebar_label: GaugeChart
sidebar_position: 13
---

# GaugeChart

A speedometer-style gauge for a single KPI — progress toward a target, a score,
a utilization percentage. `y` is the value column; the **first row** is plotted
on a `min`..`max` scale (defaults `0`..`100`). No `x` is needed.

```markdown
<GaugeChart data={goal_completion} y="pct" min=0 max=100 title="Monthly goal" />
```

<GaugeChart data={goal_completion} y="pct" min=0 max=100 title="Monthly goal" />

`color` repaints the progress arc:

<GaugeChart data={goal_completion} y="pct" min=0 max=100 color="#16a34a" title="Monthly goal (custom color)" />

## From the semantic layer

Like every chart, GaugeChart also takes a [semantic metric ref](/semantic-layer)
instead of `data={query}`. It's a single-value gauge, so pass a `metric=` with
**no** `by=` (one scalar); `min`/`max` stay literal:

```markdown
<GaugeChart metric={sales.revenue} min=0 max=1000000 />
```

## Attributes

| Attribute | Purpose |
| --------- | ------- |
| `data` | **Required.** The query to plot (`data={query}`). |
| `y` | **Required.** Value column — the first row is the needle position. |
| `min` | Scale minimum (default `0`). |
| `max` | Scale maximum (default `100`). |
| `title` | Chart title (also labels the dial). |
| `color` | Single color or comma-separated palette override for the progress arc. |
| `height` | Pixel height (default `300`). |
| `col-span` | Columns to span inside a `<Grid>`. |
| `format` · `currency` · `decimals` · `locale` | Formatting for the center read-out. |
| `empty_message` | Text shown when the query returns no rows. |

`min`/`max` are GaugeChart-specific; the rest are the shared chart attributes — see [Charts](/components/charts). For a bare KPI number use [Counter](/components/counter) or [Value](/components/value) instead.
