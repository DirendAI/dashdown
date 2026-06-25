---
title: Sales — Semantic Metric Layer
description: One model. Every chart references metrics + dimensions directly.
---

# Sales

This dashboard has **no `:::query` blocks and no `queries/` files.** Every chart
points straight at a metric and a dimension defined once in a
[boring-semantic-layer](https://github.com/boringdata/boring-semantic-layer)
model, `semantic/sales.yml`:

```html
<BarChart metric={sales.revenue} by={sales.region} />
```

The framework compiles `(metric, dimension, current filters)` into a BSL query
that **Ibis pushes down to the `main` connector** at request time — the
aggregation runs in the database, not in Python. Change the definition of
`revenue` in the model and every chart below follows.

<Dropdown name="region" label="Region" multi options="East,West,North,South" />

The same metric powers **KPI counters**, an inline **Value**, and a **table** —
not just charts. Every one re-queries when you change the Region filter above. A
`<Counter>` can also draw its **sparkline** straight from a metric + the time
dimension — `sparkline={sales.revenue} sparkline-by={sales.order_date} grain="month"`
— so the trend line needs no hand-written series query either:

<Grid cols="3">
  <Counter metric={sales.revenue} label="Revenue" color="primary"
           sparkline={sales.revenue} sparkline-by={sales.order_date} grain="month" />
  <Counter metric={sales.orders} label="Orders" color="accent"
           sparkline={sales.orders} sparkline-by={sales.order_date} grain="month" />
  <Counter metric={sales.avg_deal} label="Avg deal" color="success"
           sparkline={sales.avg_deal} sparkline-by={sales.order_date} grain="month" />
</Grid>

Total revenue inline: <Value metric={sales.revenue} />.

<Table metric={sales.revenue} by={sales.region} title="Revenue by region" />

## Revenue by region

The region dropdown above is a **semantic filter**: picking regions re-queries
every chart whose model has a `region` dimension — no per-chart wiring.

<Grid cols="2">
  <BarChart metric={sales.revenue} by={sales.region} title="Revenue by region" />
  <PieChart metric={sales.orders} by={sales.region} title="Order count by region" />
</Grid>

## Bucket time at any grain — `grain=`

The model declares **one** real date dimension, `order_date`. A chart buckets it
on demand with `grain=` — there are **no** pre-declared `month`/`quarter`/`year`
dimensions to maintain. BSL truncates the date *in the database* and validates the
grain against the dimension's `smallest_time_grain`.

A **literal** grain is fixed per chart, so different charts on one page can sit at
different grains — here a monthly line beside a quarterly bar, from the *same*
`revenue` measure and the *same* `order_date` column:

<Grid cols="2">
  <LineChart metric={sales.revenue} by={sales.order_date} grain="month" title="Revenue by month" />
  <BarChart metric={sales.revenue} by={sales.order_date} grain="quarter" title="Revenue by quarter" />
</Grid>

```html
<LineChart metric={sales.revenue} by={sales.order_date} grain="month" />
<BarChart  metric={sales.revenue} by={sales.order_date} grain="quarter" />
```

## Switch the grain live — `grain={control}`

Point `grain=` at a **control** instead of a literal and a reader re-buckets the
chart without a reload. Grain is a *grouping* modifier, not a filter — the control
just writes a canonical token (`day`/`week`/`month`/`quarter`/`year`) that the chart
reads at fetch time, riding the same re-fetch path filters use. `<TimeGrain>` is the
sugar control for exactly this: it labels the tokens nicely and seeds a `default`.

<TimeGrain name="trendGrain" default="month" />

<LineChart metric={sales.revenue} by={sales.order_date} grain={trendGrain} title="Revenue over time (live grain)" />

```html
<TimeGrain name="trendGrain" default="month" />
<LineChart metric={sales.revenue} by={sales.order_date} grain={trendGrain} />
```

## Grain + a second dimension

`grain=` composes with `series=`: bucket `order_date` by **quarter** and split each
bar into a coloured series per `status`. One `revenue` measure, no derived columns:

<BarChart metric={sales.revenue} by={sales.order_date} grain="quarter" series={sales.status} title="Revenue by quarter, by status" />

## Group by a joined column

`manager` lives in a **different table** (`data/regions.csv`), joined to orders in
the model. BSL/Ibis plans the join and pushes it down — the chart just asks for
`by={sales.manager}`:

<BarChart metric={sales.revenue} by={sales.manager} title="Revenue by manager" />

## Several metrics on one chart

List metrics of the same model (comma-separated, **quoted**) and each becomes its
own coloured series with a legend — `revenue` and `avg_deal` side by side, by region
and (with `grain="month"`) over time:

<Grid cols="2">
  <BarChart metric="sales.revenue,sales.avg_deal" by={sales.region} title="Revenue vs avg deal" />
  <LineChart metric="sales.revenue,sales.avg_deal" by={sales.order_date} grain="month" title="Revenue vs avg deal by month" />
</Grid>

A multi-metric chart and a `series=` split are mutually exclusive: list metrics in
`metric=` for *different measures*, or use one metric with `series=` for *one measure
split by a dimension* (as above).

## Bars and a line together — `<ComboChart>`

When two metrics live on **different scales** (revenue in dollars, orders as a count),
draw one as bars and the other as a line on a **secondary axis**. In semantic mode the
`bars=`/`lines=` lists carry `{model.metric}` refs — they're combined into one query and
each axis picks up its measure's declared format automatically (revenue → `$`):

<ComboChart by={sales.order_date} grain="month"
  bars={sales.revenue} lines={sales.orders} right_axis={sales.orders}
  title="Revenue (bars) vs orders (line)" />

The same second dimension turns a **pie into small multiples** — one pie per
`series=` value, sharing a slice legend. Revenue by region, one pie per status:

<PieChart metric={sales.revenue} by={sales.region} series={sales.status} title="Region mix by deal status" />

## Ask the data a question — `<Ask>`

`<Ask>` takes the **same `metric=`/`by=` a chart does** and sends that compiled
result to an LLM for a one-paragraph read. It binds to the *same* synthetic query
the charts above use — so commentary on semantic-layer data needs **no extra
`:::query` or `queries/` file**, and it honours the Region filter and date range
like every other widget.

<Ask metric={sales.revenue} by={sales.region}
     ask="Which region is driving revenue, and how concentrated is it across regions?" />

```html
<Ask metric={sales.revenue} by={sales.region} ask="Which region leads, and why?" />
```

:::note
This block needs an LLM provider. It's **off by default** — uncomment the `llm:`
block in `dashdown.yaml` and set the API key (e.g. `export MISTRAL_API_KEY=…`),
then reload. Until then the card shows "no LLM provider configured"; the rest of
the dashboard works without a key.
:::
