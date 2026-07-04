<!-- AUTO-GENERATED from docs/pages/ by tooling/gen-agent-docs.py — do not edit. -->
<!-- Topic: semantic-layer. Regenerate with: python tooling/gen-agent-docs.py -->

<!-- source: docs/pages/semantic-layer/index.md -->

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


<!-- source: docs/pages/semantic-layer/ibis.md -->

# BSL / Ibis backend (`backend: ibis`)

The **default** semantic backend, and the recommended starting point. Dashdown delegates to
**boring-semantic-layer** (BSL) running on **Ibis**. BSL owns the hard parts —
**joins, fan-out correctness, and SQL dialect** — so a model "just works" across
DuckDB, Postgres, MySQL, Snowflake, BigQuery, and more. A `metric={…} by={…}`
reference compiles to a BSL query that Ibis **pushes down** to the connector's
engine, so the aggregation runs in your database, not in Python.

:::tip References
- **boring-semantic-layer (BSL)** — the semantic engine:
  <https://github.com/boringdata/boring-semantic-layer>
- **Ibis** — the portable dataframe/expression layer BSL compiles through:
  [ibis-project.org](https://ibis-project.org) ·
  <https://github.com/ibis-project/ibis>
:::

It's auto-detected for a SQL/DuckDB connector, or set it explicitly with
`backend: ibis`. Install it with `pip install 'dashdown-md[semantic]'` (see
[Install](#install) for warehouse extras), then reference models with the shared
[`metric={…} by={…}` grammar](/semantic-layer#reference-it-from-a-component).

## Define a model

Drop a YAML file in `semantic/`. It's a BSL model — dimensions and measures
declared once — plus a `connector:` telling Dashdown which data source to run it
against:

```yaml
# semantic/sales.yml
sales:
  connector: warehouse     # one of your sources.yaml connectors (omit → default source)
  table: orders
  description: Sales orders

  dimensions:
    region: _.region
    status: _.status
    month: _.month
    # A real DATE so the global date filter can range over it.
    order_date:
      expr: (_.month + '-01').cast('date')
      is_time_dimension: true
      smallest_time_grain: TIME_GRAIN_DAY

  measures:
    revenue:
      expr: _.amount.sum()
      metadata: { format: currency, currency: "$" }
    orders: _.count()
    avg_deal:
      expr: _.amount.mean()
      metadata: { format: currency, currency: "$" }
```

Expressions use Ibis's deferred syntax (`_.column`, `_.amount.sum()`). A measure's
`metadata.format` / `metadata.currency` are picked up as the chart's default
number formatting. Once defined, reference the model from any component with the
shared [grammar](/semantic-layer#reference-it-from-a-component).

## Joins — group by a column in another table

Declare a `join` to a sibling model and a chart can group a metric by a dimension
that lives in a **different table** — BSL/Ibis plans the join and pushes it down:

```yaml
# semantic/sales.yml
sales:
  connector: warehouse
  table: orders
  measures: { revenue: { expr: _.amount.sum() } }
  dimensions: { region: _.region }
  joins:
    geo:
      model: geo          # the sibling model below
      type: one           # one | many
      left_on: region
      right_on: region

geo:
  connector: warehouse
  table: regions
  dimensions: { region: _.region, manager: _.manager }
  measures: { n: _.count() }
```

```markdown
<BarChart metric={sales.revenue} by={sales.manager} />   <!-- manager is in `geo` -->
```

:::note
Models that join must share a connector (Ibis can't join across backends — the
same SQL-only constraint as query composition). Define the joined models together
in **one** `semantic/*.yml` file. Use a **real temporal column** for a time
dimension (not a computed cast) — BSL can't resolve a derived expression through a
join.
:::

## Pushdown

`metric={sales.revenue} by={sales.region}` with `region` narrowed to East/West
compiles to — and executes in — the connector's engine:

```sql
SELECT "region", SUM("amount") AS "revenue"
FROM orders
WHERE "region" IN ('East', 'West')
GROUP BY 1
ORDER BY "region" ASC
```

For a DuckDB/CSV connector the model shares the connector's live connection
(zero-copy). For a warehouse (Postgres/Snowflake/BigQuery) the same query pushes
down via that backend.

## Connecting to your data

Two ways, mix freely per model:

- **Bridge a connector (default).** `connector:` reuses one of your
  `sources.yaml` connectors — a single connection config, with pushdown. Bridged
  connector types:
  - **`csv` / `duckdb`** — share the live in-process DuckDB connection (zero-copy),
    in the box with `dashdown-md[semantic]`.
  - **`postgres` / `mysql` / `snowflake` / `bigquery`** — Dashdown opens a native
    Ibis connection from the connector's config and pushes the aggregation down to
    the warehouse. Each needs the matching Ibis backend extra alongside
    `dashdown-md[semantic]` — `pip install 'ibis-framework[postgres]'` (or `[mysql]` /
    `[snowflake]` / `[bigquery]`); a missing one raises a clear install hint.
- **A native BSL profile (escape hatch).** `profile: warehouse` lets BSL/Ibis own
  the connection directly — useful for a connector with no bridge yet (e.g. `mssql`,
  `excel`/`sheets`), or to drop in a model an existing BSL setup already defines.

## Install

```bash
pip install 'dashdown-md[semantic]'
```

This adds BSL + Ibis (with the DuckDB backend), enough for `csv`/`duckdb` models. A
warehouse model also needs its Ibis backend — `pip install 'ibis-framework[postgres]'`
(or `[mysql]`/`[snowflake]`/`[bigquery]`) — or a BSL profile.

:::tip Prefer code over config?
You can also build a semantic model **inside a [Python query](/python-queries)** —
return `model.group_by(…).aggregate(…).execute()` from a `queries/*.py`. That
needs no extra framework feature and is a good fit when one model feeds a single
chart. The first-class `metric={…} by={…}` grammar is for when one model
definition should drive *many* charts with shared filters.
:::


<!-- source: docs/pages/semantic-layer/cube.md -->

# Cube backend (`backend: cube`)

[Cube](https://cube.dev) is a standalone semantic-layer *server* — a team models
measures, dimensions and joins **in Cube**, and Cube serves them over a structured
JSON API. Dashdown reaches that model with the **same `metric={…} by={…}`
grammar** via this backend: a metric reference compiles to a Cube
[`POST /load`](https://cube.dev/docs/reference/rest-api#load) query
(`{measures, dimensions, timeDimensions, filters, order}`) that runs in Cube.

:::tip References
- **Cube** — the semantic-layer server: [cube.dev](https://cube.dev) ·
  [docs](https://cube.dev/docs) · <https://github.com/cube-js/cube>
- **REST `/load` API** — the query shape Dashdown compiles to:
  <https://cube.dev/docs/reference/rest-api#load>
:::

:::warning Experimental — preview
The Cube backend is **preview**: its query builder, `/meta` parser, JWT lifecycle
and column rename are fully unit-tested with fakes, but it is **not yet verified
against a live Cube deployment**, and it covers the
`measures`/`dimensions`/`timeDimensions`/`filters` subset of Cube's query shape (no
segments, no relative-date keywords, a single time dimension for the global date
range). Treat it as a preview to try, not a production guarantee.
:::

Two things make Cube the *most* config-free and the *safest* backend:

- **`/meta` auto-introspection.** Cube publishes its model, so Dashdown reads
  `GET /meta` at load and fills the catalogue (measures vs dimensions, types, display
  formats, the time dimension) itself — a model is as small as `orders: { connector:
  cube }`, with **nothing re-declared**.
- **No injection surface — JSON, not a query string.** The compiled query is a Python
  `dict` serialized as the request body; filter values are JSON **data**, never
  assembled into a string. There is no `${param}` substitution and no string-escaping
  to get wrong.

## 1. Add the Cube source

A `type: cube` source is a thin HTTP client — it isn't queried with SQL; see the
[`cube` connector](/connectors/cube) for every config key:

```yaml
# sources.yaml
cube:
  type: cube
  url: https://cube.example.com
  secret: ${CUBE_API_SECRET}     # HS256 signing secret (env-expanded)
  token_ttl: 300                 # seconds (default)
  security_context:              # optional — embedded in every JWT (RLS rail)
    tenant_id: acme
```

## 2. Define a model

Typically **just the connector**, since `/meta` does the rest — auto-detected as
`backend: cube` from the `type: cube` connector:

```yaml
# semantic/orders.yml
orders:
  connector: cube
  # backend: cube            # optional — inferred from the `type: cube` connector
```

Reference it exactly like any model — same single-/multi-metric, `series=`, scalar,
table, filter, and [`grain=`](/semantic-layer#time-grain--grain) behaviour. A time
dimension buckets via `grain=` (the canonical token maps straight onto Cube's native
granularity), so there's nothing to declare for a time series:

```markdown
<BarChart  metric={orders.count}   by={orders.status} />
<LineChart metric={orders.revenue} by={orders.createdAt} grain="month" />
<Value     metric={orders.revenue} label="Revenue" />
```

## Filters & time grain on Cube

Filters map ~1:1 onto Cube's query: a `<Dropdown name="status">` becomes a
`{member, operator: "equals", values}` filter, and the global date range collapses
into a single `timeDimensions[].dateRange`. A time-type `by`/`series` dimension routes
to `timeDimensions[].granularity` (from `grain=`, or a model-level `granularity:`
default of `day`), not `dimensions[]` — Cube's `/meta` types tell us which is which.

## JWT & security context — the RLS rail

Dashdown mints a short-lived HS256 token (RS256 via `private_key`, or a static
`token` escape hatch) embedding the `security_context`, re-minting before expiry and
once on a `401`. Cube applies that context **server-side**, so a per-tenant identity
scopes every query without changing the compiled JSON. An HTTP 4xx/5xx surfaces as
the component's error card, never a 500. Cube's **pre-aggregations** are used
transparently (the framework is unaware of them).

If Cube is unreachable at load, project startup fails loudly (parity with a malformed
`sources.yaml`); set `optional: true` on the model to downgrade that to a warning + an
empty catalogue so an outage doesn't wedge the rest of the dashboard.

## Install

```bash
pip install 'dashdown-md[cube]'
```

:::tip Just want SQL access to Cube?
Cube also exposes a **Postgres-wire-compatible SQL API**. You don't need this backend
for that — point a [`postgres` connector](/connectors/postgres) at Cube's SQL port and
write `queries/*.sql` (with Cube's `MEASURE(...)`). This backend is for the
first-class `metric={…} by={…}` grammar, which the structured JSON API serves best
(richer `/meta`, no query-string escaping).
:::
