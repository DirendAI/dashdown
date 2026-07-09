<!-- AUTO-GENERATED from docs/pages/ by tooling/gen-agent-docs.py — do not edit. -->
<!-- Topic: python-queries. Regenerate with: python tooling/gen-agent-docs.py -->

<!-- source: docs/pages/python-queries.md -->

# Python queries

Some questions are awkward — or impossible — in a single SQL statement: a
**forecast**, an ML score, a **cross-connector join**, an external-API pull, a
pandas/Polars reshape. A **Python query** lets you write the query as a function
and reference it from a page exactly like a `.sql` file. You pick the engine; the
framework normalizes whatever you return into the same data the wire already
speaks.

:::tip
A Python query is just another file in `queries/` — it resolves by name, caches,
streams (`live`), and snapshots in a static build identically to a SQL query. The
only difference is the body language.
:::

## Define it

Drop a `.py` file in `queries/` with **one** `@query`-decorated function. The file
path is its name (`queries/ml/churn.py` → `ml.churn`), just like `.sql`/`.dax`.

```python
# queries/downloads_forecast.py
from dashdown import query

@query(cache_ttl=300)
def downloads_forecast(params, connect):
    # connect(name, sql, params=…) runs SQL on ANY project connector and returns
    # a result with .to_pandas() / .to_arrow().
    df = connect("demo", "SELECT month, SUM(downloads) AS downloads "
                         "FROM downloads GROUP BY month ORDER BY month").to_pandas()
    # …a forecast is trivial in pandas, awkward in SQL…
    last = df["downloads"].tail(3).mean()
    return df            # DataFrame / Arrow table / QueryResult / list-of-dicts
```

Reference it by name — no different from a SQL query:

```markdown
<LineChart data={downloads_forecast} x="month" y="downloads" />
```

The chart below is driven by exactly that query — the last point is the appended
moving-average forecast:

<LineChart data={downloads_forecast} x="month" y="downloads" title="Downloads (with forecast)" explain />

## The contract

- **Signature `fn(params, connect)`.** `params` is the merged filter + route
  values as a `dict[str, str]`. `connect(name, sql, params=None)` runs SQL on a
  project connector and returns a `QueryResult` exposing `.to_pandas()` /
  `.to_arrow()` for your engine.
- **Return anything table-shaped.** A pandas/Polars `DataFrame`, a PyArrow
  `Table`, a `QueryResult`, or a list of dicts — all are normalized to the same
  `{columns, rows}` (Decimal / NaN / datetime / numpy cells handled for you). The
  framework has **no hard pyarrow/polars import** — you bring the engine.
- **The decorator carries the metadata**: `connector`, `cache_ttl`, `live`,
  `interval`, `description` — the **name comes from the file path, not the
  function name**.

## Cross-connector joins

A single SQL query can't span two connectors; a Python query can — read from each
and join in pandas:

```python
@query()
def revenue_vs_target(params, connect):
    revenue = connect("sales",   "SELECT month, SUM(amount) AS revenue "
                                 "FROM sales GROUP BY month").to_pandas()
    targets = connect("targets", "SELECT month, SUM(target) AS target "
                                 "FROM targets GROUP BY month").to_pandas()
    return revenue.merge(targets, on="month", how="left")
```

## Params are data, never code

This is the whole security story, and it's *stronger* than SQL:

- There is **no `${param}` body** to inject into for a Python query — values arrive
  as a runtime dict, so the injection surface SQL must defend against simply
  doesn't exist.
- Author-built SQL handed to `connect(name, sql, params=…)` runs through the
  framework's one blessed, context-aware escaping (the same one the `.sql` path
  uses). Raw `connect(name, sql)` with no `params` is "you wrote it, you own it",
  exactly like authoring a `.sql` file.

:::warning Trust boundary
A `queries/*.py` runs author code in-process — the **same trust boundary** as a
custom component (`components/*.py`). A managed / multi-tenant host that serves
semi-trusted projects can turn Python queries off:

```yaml
# dashdown.yaml
python_queries:
  enabled: false   # default true
```

When disabled, `queries/*.py` files are skipped and any reference 404s.
:::

## A semantic layer, for free

Because a Python query can return any table, a **semantic layer** drops in with no
framework change — define metrics and dimensions once (e.g. with
[boring-semantic-layer](https://github.com/boringdata/boring-semantic-layer) on
Ibis) and return `model.group_by(…).aggregate(…).execute()`. One definition of
"revenue" drives a chart.

If you want one model to drive **many** charts with shared, automatic filters —
`<BarChart metric={sales.revenue} by={sales.region} />` — see the first-class
[Semantic layer](/semantic-layer), which builds on exactly this Arrow boundary.

## Installing engines

pandas is already a core dependency. `pip install 'dashdown-md[python]'` adds pyarrow
(for `.to_arrow()` and Arrow-returning queries); bring Polars or any other engine
your query code imports.
