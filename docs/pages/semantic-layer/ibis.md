---
title: BSL / Ibis backend
sidebar_label: BSL / Ibis
sidebar_position: 1
---

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
