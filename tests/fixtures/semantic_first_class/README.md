# Semantic Metric Layer (on BSL)

A worked demo of the **first-class semantic metric layer**: define metrics +
dimensions once in a [boring-semantic-layer](https://github.com/boringdata/boring-semantic-layer)
(BSL) model, reference them straight from a component, and let **Ibis push the
query down to the database**.

```bash
pip install 'dashdown-md[semantic]'
dashdown serve .
```

## What's here

- `semantic/sales.yml` — two BSL models: `sales` (over `data/orders.csv`) and
  `geo` (over `data/regions.csv`), with a **join** from sales→geo. `connector:
  main` bridges each table to the project's CSV/DuckDB connector for pushdown.
- `pages/index.md` — charts, a `<Value>` KPI, and a `<Table>` that point at
  metrics directly: `<BarChart metric={sales.revenue} by={sales.region} />`. One
  chart groups by `sales.manager` — a column that lives in the **joined** `geo`
  table. No `:::query` blocks, no `queries/` files.

## How it works

A `metric={model.metric} by={model.dim}` reference compiles, at render time, into
a **synthetic Python query** (`dashdown/semantic.py`) registered in the same
`_python_def_cache`. The data API / poller / static build /
server-side cache all resolve it with no special-casing. The synthetic query runs
`model.query(...).to_pyarrow()` — BSL+Ibis compile that to SQL and execute it **in
the connector's engine** (here DuckDB; Postgres/Snowflake/BigQuery via their Ibis
backends).

- **Filters become semantic filters automatically.** The `region` dropdown writes
  `$store.filters.region`; the compiler maps any filter whose key is a model
  dimension to a BSL `{"field","operator":"in","values":[…]}` filter.
- **The global date filter** maps onto the model's time dimension (`order_date`).
- **Joins / fan-out correctness / SQL dialect** are BSL's job — we don't ship a
  semantic engine.

## Connection (hybrid)

`connector: main` (this demo) bridges a `sources.yaml` connector to Ibis — one
connection config, real pushdown. Alternatively a model can declare a native BSL
`profile:` (escape hatch) for backends not yet bridged or to drop in an existing
BSL setup.
