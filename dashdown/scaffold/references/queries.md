<!-- AUTO-GENERATED from docs/pages/ by tooling/gen-agent-docs.py — do not edit. -->
<!-- Topic: queries. Regenerate with: python tooling/gen-agent-docs.py -->

<!-- source: docs/pages/queries.md -->

# Queries

SQL lives either in the shared **query library** under `queries/` (the
recommended default) or, for a quick one-off, inline in a named ` ```sql `
block on a page.

:::tip
**Prefer the `queries/` library.** Defining each query once in its own file —
rather than inline on a page — keeps SQL out of your prose, lets you reuse and
[compose](#composition) queries across pages, and makes them easy to find and
edit. Reach for an inline query only for a throwaway query used on exactly
one page.
:::

## The shared query library

Drop a `.sql` (or `.dax`) file in `queries/`; reference it by name from any page.
The file name becomes the query name (`finance/mrr.sql` → `finance.mrr`). So
`queries/downloads_by_month.sql`:

```sql
-- queries/downloads_by_month.sql
SELECT month, SUM(downloads) AS downloads
FROM downloads GROUP BY month ORDER BY month
```

is referenced from any page by that name — this page's chart uses it:

```markdown
<LineChart data={downloads_by_month} x="month" y="downloads" />
```

<LineChart data={downloads_by_month} x="month" y="downloads" title="Downloads (shared query)" explain />

Per-query options (`connector`, `cache_ttl`, `live`, …) go in the file's YAML
frontmatter; omit `connector` to use the project's [default
source](/connectors#the-default-source).

:::note Need more than SQL?
A query in `queries/` can also be a **`.py` file** — a function returning a table
(pandas / Polars / Arrow / list-of-dicts) — for forecasts, ML scoring,
cross-connector joins, or anything awkward in SQL. See [Python
queries](/python-queries).
:::

### Composition

Library queries can compose with a dbt-style `ref('other')`, compiled into inline
CTEs at load time — so you build complex queries from small, named building
blocks instead of repeating subqueries.

## Inline queries

For a one-off used on a single page, define the SQL inline as a fenced code
block whose info string names the query — the first word after the language is
the **name**, the rest are options:

````markdown
```sql downloads_by_month ttl=300
SELECT month, SUM(downloads) AS downloads
FROM downloads GROUP BY month ORDER BY month
```

<LineChart data={downloads_by_month} x="month" y="downloads" />
````

Because it's a standard fenced block, every Markdown editor (and GitHub)
syntax-highlights the SQL. Options are the same as everywhere else:
`connector=` (omit it to use the [default source](/connectors)), `ttl=` /
`cache_ttl=` for [result caching](#caching-results), and `live` +
`interval=` for [real-time data](/realtime). A plain ` ```sql ` fence with
*nothing* after the language stays an ordinary display-only code sample — like
the ones on this page. Add the bare `show` option to a query definition to
*also* render its SQL on the page.

Either way — library file or inline block — the SQL is collected and **never executed server-side during page
render** — the page ships instantly with empty datasets, and the browser fetches
each query's data from the data API. This keeps first paint fast and the render
path pure.

## Parameters & injection safety

`${param}` placeholders are filled by a single **context-aware** substitution
function. It is the one and only injection defense — there is no bind-parameter
mechanism:

- `'${x}'` (inside single quotes) → the value is `'`-escaped in place.
- `IN (${x})` → a multi-select expands to a quoted, per-item list, capped.
- a bare `${x}` → wrapped in quotes.

Every value becomes a quoted string literal, so `${id}` with value `1 OR 1=1` is
inert.

:::warning
Don't try to interpolate identifiers (table/column names) through `${...}` — the
substitution always produces a *string literal*. Parameterize **values**, not
SQL structure.
:::

## Caching results

Set `cache_ttl` to cache a query's result so repeat requests (and repeat page
loads) don't re-run it until the TTL expires. On a library query, put it in the
file's frontmatter:

```sql
-- queries/daily_metrics.sql
---
cache_ttl: 300
---
SELECT day, SUM(downloads) AS downloads FROM downloads GROUP BY day
```

On an inline query it's an option instead (` ```sql name ttl=300 `; `ttl` and
`cache_ttl` are synonyms). With no TTL, results aren't cached — each request
runs the query fresh.
(For data that should repaint as it changes, see [Real-time data](/realtime)
instead.)

## SQL stays server-side

Query SQL is never written into the page source — it stays on the server. The
page references each query by name, and the data API
(`/_dashdown/api/data/<query>`) looks the SQL back up server-side and runs it.
View source on a rendered page and you won't find the `SELECT` behind a chart.

No configuration is needed; this is always the case.
