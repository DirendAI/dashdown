---
title: DuckDB
sidebar_label: DuckDB
sidebar_position: 6
---

# DuckDB connector

Query a DuckDB database file (or an in-memory DB) directly. CSV is a thin subclass
of this connector, so you get the same engine — plus DuckDB's ability to read
Parquet, JSON, and remote files via its extensions.

```yaml
# sources.yaml
sales_data:
  type: duckdb
  path: data/warehouse.duckdb    # omit for an in-memory database
```

| Key         | Purpose                                        |
| ----------- | ---------------------------------------------- |
| `path`      | DuckDB file (omit for in-memory).              |
| `csv_views` | Optional `{view: csv_path}` map to attach.     |

**Resilience:** if a query *invalidates* the connection (a fatal DuckDB error),
`query()` rebuilds the connection and retries once — so one bad query can't break
every later query on the long-lived connection.

## Querying JSON and nested data

DuckDB reads JSON (and Parquet) straight from a path or URL — no load step. Use an
**in-memory** connector (omit `path`) and do the reading inside the SQL:

```yaml
# sources.yaml
sales_data:
  type: duckdb          # no `path:` → in-memory; files are read in the query
```

```sql
SELECT unnest(matches) AS m
FROM read_json_auto('data/x.json', maximum_object_size=20000000)
```

Remote files work too — DuckDB autoloads the `httpfs` extension on first use:

```sql
SELECT unnest(matches) AS m
FROM read_json_auto('https://example.com/x.json', maximum_object_size=20000000)
```

Working with the parsed structure:

- **Flatten arrays** with `unnest()` — one row per element.
- **Access struct fields** with a dot: `m.score.ft`.
- **Lists are 1-indexed**: `m.score.ft[1]` is the first element (not `[0]`).
- **Quote reserved words** used as identifiers or aliases with double quotes:
  `m."group"`, `… AS "minute"`, `… AS "type"`, `… AS "time"`.

:::note
A `${param}` always substitutes a **quoted string literal** (injection-safe — see
[Queries](/queries#parameters--injection-safety)). To match it against a numeric
column or key, compare as text: `WHERE CAST(id AS VARCHAR) = '${id}'`.
:::

End to end — flatten a nested array, then filter one record by a route `${id}`:

```sql
WITH games AS (
  SELECT unnest(games) AS g
  FROM read_json_auto('data/games.json', maximum_object_size=20000000)
)
SELECT
  g."group"      AS "group",      -- reserved word → quoted identifier/alias
  g.minute       AS "minute",
  g.score.ft[1]  AS home_goals    -- struct field + 1-indexed list element
FROM games
WHERE CAST(g.id AS VARCHAR) = '${id}'
ORDER BY g.minute
```

**Extra:** none — in the core install.
