---
title: TimeGrain
sidebar_label: TimeGrain
sidebar_position: 12
icon: "\U000023F2"
---

# TimeGrain

A filter control that lets a reader **re-bucket a time series** — Day / Week /
Month / Quarter / Year — without you pre-declaring one dimension per grain. It
writes a canonical grain token into the filter store under `name`, exactly what a
[semantic-layer](/semantic-layer) chart's `grain={name}` reads at fetch time:

```markdown
<TimeGrain name="trendGrain" default="month" />

<LineChart metric={sales.revenue} by={sales.order_date} grain={trendGrain} />
```

Pick a grain and the chart re-queries at that bucket, on the same filter
re-fetch path as every other control — no new plumbing.

Here's the control itself (the chart binding above needs a semantic model, so it's
shown as source only):

<TimeGrain name="trendGrain" default="month" />

Add `native` to include an ungrouped "Native" choice:

<TimeGrain name="grain2" grains="day,month,year" native default="day" />

:::note Pairs with the semantic layer
`grain=` is a **grouping** modifier the [semantic backends](/semantic-layer)
translate to their native time-truncation (Ibis `.truncate()`, Cube
`granularity`). So `<TimeGrain>` drives `grain={…}` on a **`metric={…}
by={…}`** chart — it has nothing to bucket on a plain `data={query}` chart (write
the `DATE_TRUNC` yourself there). See [Time grain](/semantic-layer#time-grain--grain).
:::

## Why not a plain Dropdown?

`<TimeGrain>` is sugar over `<Dropdown options="day,week,month,quarter,year">`, with
three conveniences a bare dropdown can't give you:

- **Nice labels** — shows `Month`, stores `month`.
- **Validation** — the offered `grains=` are checked against the canonical token
  set (`second`, `minute`, `hour`, `day`, `week`, `month`, `quarter`, `year`) at
  render, so a typo fails fast instead of silently producing an empty chart.
- **A real default** — `default="month"` actually seeds the first-load selection
  (URL params still win), so the chart's shown grain matches its grouping on load.

A plain `<Dropdown>` whose option values are those tokens still works as a grain
switcher — `<TimeGrain>` is just the ergonomic, grain-aware version.

## Attributes

| Attribute | Purpose |
| --------- | ------- |
| `name` | **Required.** Filter key a chart reads as `grain={name}`. |
| `label` | Pill label (default `Grain`). |
| `grains` | Comma-separated subset to offer (default `day,week,month,quarter,year`). |
| `default` | First-load grain (must be one of `grains`; else `month`, or the first grain). |
| `native` | Add a "Native" (ungrouped) choice; selecting it removes the bucketing. |
| `bar` | Lift into the top [filter bar](/filters) (default: inline). |

Like every filter control, `<TimeGrain>` is `is_filter` — it's stripped from
[static builds](/exporting) (a fixed snapshot can't be re-grouped) and suppressed
from the "filtered by" badge (a grain is a grouping, not a filter).
