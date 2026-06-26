---
title: Semantic layer
sidebar_label: Semantic layer
sidebar_position: 8
icon: "\U0001F4D0"
---

# Semantic layer

Define your metrics and dimensions **once**, then reference them straight from a
component — no per-chart SQL, no copy-pasted queries:

```markdown
<BarChart  metric={sales.revenue} by={sales.region} />
<LineChart metric={sales.revenue} by={sales.month} />
<PieChart  metric={sales.orders}  by={sales.region} />
```

One definition of `revenue` drives every chart. Change it in the model and every
chart on every page follows. Dashboard filters become **semantic filters**
automatically, and the query is **pushed down to your database** — the
aggregation runs in the engine, not in Python.

:::note Experimental (preview)
The first-class semantic layer is a **preview** feature. The grammar and model
format are stable enough to try, but the surface may still change.
:::

## Choose a backend

Dashdown doesn't ship its own semantic engine — it **delegates** to a pluggable
backend, chosen per model with `backend:` (or auto-detected from the connector).
Every backend sits behind the *same* `metric={…} by={…}` grammar and filter
mapping, so a chart looks identical whichever engine a model uses:

| Backend | Engine | Best for | Extra | Guide |
|---|---|---|---|---|
| **`ibis`** (default) | [BSL](https://github.com/boringdata/boring-semantic-layer) on [Ibis](https://ibis-project.org) | Models over your own SQL warehouse — DuckDB, Postgres, MySQL, Snowflake, BigQuery — compiled to SQL and **pushed down** | `dashdown-md[semantic]` | **[BSL / Ibis →](/semantic-layer/ibis)** |
| **`cube`** (preview) | [Cube](https://cube.dev) | Reaching an **existing Cube deployment** over its JSON API | `dashdown-md[cube]` | **[Cube →](/semantic-layer/cube)** |

The backends **define and connect their models differently**, so head to a guide
to define one — the rest of this page is the grammar that's shared across all of
them. A third party can add another backend as a separate package; the registry is
the public extension point, mirroring [data connectors](/connectors).

## Reference it from a component

The examples below assume a `sales` model — see
[BSL / Ibis → Define a model](/semantic-layer/ibis#define-a-model) (or
[Cube](/semantic-layer/cube)) for how to declare one. Every data display takes
`metric={model.metric}` (and, except a scalar Counter / Value,
`by={model.dimension}`) instead of `data={query}` — charts, `<Counter>`,
`<Value>`, and `<Table>`:

```markdown
<!-- Charts -->
<BarChart metric={sales.revenue} by={sales.region} title="Revenue by region" />
<PieChart metric={sales.orders}  by={sales.region} title="Orders by region" />

<!-- KPI tiles / inline value — a metric with no `by` is a single scalar -->
<Counter metric={sales.revenue} label="Revenue" />
<Value metric={sales.revenue} />

<!-- A KPI tile whose sparkline is also a metric, bucketed by the time dimension -->
<Counter metric={sales.revenue} label="Revenue"
         sparkline={sales.revenue} sparkline-by={sales.order_date} grain="month" />

<!-- A table — one row per group -->
<Table metric={sales.revenue} by={sales.region} />
```

A `<Counter>` sparkline can be driven by a metric too — `sparkline={model.metric}`
plus `sparkline-by={model.time_dimension}` (and an optional `grain=`) builds the
bucketed trend with no hand-written series query. See
[Counter → Sparklines from a metric](/components/counter).

Filters re-query **every** metric component, KPIs included — pick a region and the
counters, the value, the table, and the charts all update together.

The `model.metric` / `model.dimension` names are validated at render time — an
unknown metric or dimension shows an inline error card, not a 500.

:::note
Filter controls (`Dropdown`/`Search`/`DateRange`) and the cross-tab `PivotTable`
keep their own `data={query}` interface — they drive or pivot data rather than
display a single metric.
:::

## Multiple metrics & a second dimension

Charts take the same two grouping shapes as a `data={query}` chart — driven by
metrics and dimensions instead of columns:

```markdown
<!-- Several metrics of one model → one coloured series each -->
<BarChart metric="sales.revenue,sales.avg_deal" by={sales.region} title="Revenue vs avg deal" />

<!-- A second dimension (series=) → split one metric into a series per value -->
<BarChart metric={sales.revenue} by={sales.region} series={sales.status} title="Revenue by region, by status" />

<!-- Faceted pies: one pie per series= value, sharing a slice legend -->
<PieChart metric={sales.revenue} by={sales.region} series={sales.status} title="Region mix by status" />
```

Use **several metrics** (comma-separated, quoted) when you want different measures
side by side, or a **second dimension** (`series={model.dim}`) when you want one
measure split by a category. They're mutually exclusive — combining a `series=`
with multiple metrics raises an inline error.

## Charts with named roles

A few charts position several measures into **named roles** instead of a single
`y` — and they take metric refs there too, combined into one query the same way:

```markdown
<!-- OHLC: four measures grouped by a date dimension -->
<CandlestickChart by={prices.day}
                  open={prices.open} high={prices.high}
                  low={prices.low} close={prices.close} />

<!-- Heatmap: two dimensions (x/y) + a cell measure -->
<HeatmapChart x={sales.month} y={sales.channel} value={sales.downloads} />

<!-- Sankey / Graph: source + target dimensions + a link-weight measure -->
<SankeyChart source={flow.stage_from} target={flow.stage_to} value={flow.users} />

<!-- Parallel: one measure per axis, grouped into a polyline by `by` -->
<ParallelChart by={products.category}
               dimensions="products.price,products.weight,products.rating" />
```

This mirrors how a BI tool binds an OHLC or heatmap visual to a semantic model:
N measures grouped by up to two dimensions, each measure mapped to a visual role.
[ComboChart](/components/charts/combo-chart) (`bars=`/`lines=`) follows the same
pattern.

**Not every chart fits.** Distribution charts (`BoxPlot`/`Violin`) need raw rows
and hierarchy charts (`SunburstChart`/`TreeChart`) need an `id`/`parent` tree —
neither is a measure-by-dimension shape, so they stay `data={query}` only, and a
`metric=` on them shows an actionable error pointing back to `data={query}`.

## Time grain — `grain=`

Put a **date** on an axis and bucket it on demand with `grain=` — there's no need to
pre-declare a `month` / `quarter` / `year` dimension. The model has one real time
dimension; `grain=` chooses how to truncate it, *per chart*:

```markdown
<LineChart metric={sales.revenue} by={sales.order_date} grain="month" />
<BarChart  metric={sales.revenue} by={sales.order_date} grain="quarter" />
```

The vocabulary is one neutral token set — **`second`, `minute`, `hour`, `day`,
`week`, `month`, `quarter`, `year`** — and each backend translates it to its native
mechanism, so the grammar is identical everywhere:

| Backend | `grain="month"` becomes |
|---|---|
| `ibis` (BSL) | `model.query(…, time_grain="TIME_GRAIN_MONTH")` — Ibis `.truncate()`, pushed down; validated against the dimension's `smallest_time_grain` |
| `cube` | `timeDimensions: [{ dimension, granularity: "month" }]` — Cube's native granularity |

Two ways to set it, following the usual `key="lit"` vs `key={ref}` attribute rule:

```markdown
<!-- Literal: fixed per chart. Two grains on one page are independent queries. -->
<LineChart metric={sales.revenue} by={sales.order_date} grain="month" />

<!-- Reference: a control drives it, so a reader re-buckets the chart live. -->
<TimeGrain name="trendGrain" default="month" />
<LineChart metric={sales.revenue} by={sales.order_date} grain={trendGrain} />
```

`grain=` composes with `series=` (bucket by month *and* split by category). On a
`<Counter>` / `<Value>` *headline* it's a no-op (a scalar has no time grouping), but a
`<Counter>` **sparkline** uses it to bucket its `sparkline-by=` time dimension — see
[Counter → Sparklines from a metric](/components/counter).

:::note Grain is a grouping, not a filter
`grain=` changes the GROUP BY shape, never a WHERE clause — so a grain control is
**not** a semantic filter (its name isn't a model dimension, so the filter compiler
ignores it; it won't show in a widget's "filtered by" badge). The dedicated
[`<TimeGrain>`](/components/time-grain) control is the ergonomic switcher (nice
labels, validated tokens, a real default); a plain `<Dropdown>` whose option values
are the canonical tokens works too.
:::

## Filters become semantic filters

A [Dropdown](/components/dropdown) (or any filter) whose name matches a model
**dimension** automatically narrows every chart on that model — no per-chart
wiring:

```markdown
<Dropdown name="region" label="Region" multi options="East,West,North,South" />

<BarChart metric={sales.revenue} by={sales.region} />
```

Picking regions re-queries the chart with a `region IN (…)` filter run by the
backend. The project-wide [global date filter](/configuration) maps onto the
model's time dimension (`order_date` above) the same way. Filter values reach the
model as **typed filter values, never interpolated SQL** — there is no `${param}`
injection surface.

## Trust boundary

Loading a `semantic/*.yml` builds a model **in-process** — the same trust boundary
as [Python queries](/python-queries) and custom components, gated by the same
switch:

```yaml
# dashdown.yaml
python_queries:
  enabled: false   # default true — also disables the semantic layer
```

Model expressions stay server-side and never reach the browser.
