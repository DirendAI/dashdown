---
title: Parquet
sidebar_label: Parquet
sidebar_position: 3
---

# Parquet connector

Runs SQL over Parquet files on an embedded DuckDB — no database to stand up. Each
`.parquet` (or `.pq`) file in the directory becomes a queryable table named after
the file (`sales.parquet` → `sales`), via DuckDB's `read_parquet`. Parquet is
columnar and already typed, so it's the fastest file source — no header sniffing
or type inference.

```yaml
# sources.yaml
main:
  type: parquet
  directory: data        # folder of .parquet/.pq files, relative to the project
```

Then query the table by file name:

```sql
SELECT region, SUM(amount) AS revenue
FROM sales                -- data/sales.parquet
GROUP BY region
```

| Key         | Purpose                                            |
| ----------- | -------------------------------------------------- |
| `directory` | Folder of Parquet files (each → a table by stem).  |
| `files`     | Or an explicit `{table_name: path}` map.           |

**Extra:** none — it's in the core install (the `parquet` reader ships in core
DuckDB). Inherits the DuckDB connector's reconnect-on-fatal resilience, and
`dashdown query --tables` / `--schema <table>` work out of the box.
