---
title: JSON
sidebar_label: JSON
sidebar_position: 2
---

# JSON connector

Runs SQL over JSON files on an embedded DuckDB — no database to stand up. Each
file in the directory becomes a queryable table named after the file
(`orders.json` → `orders`), via DuckDB's `read_json_auto`, which auto-detects both
a JSON **array of objects** and **newline-delimited** JSON. So `.json`, `.ndjson`,
and `.jsonl` files are all picked up.

```yaml
# sources.yaml
main:
  type: json
  directory: data        # folder of .json/.ndjson/.jsonl files, relative to the project
```

Then query the table by file name:

```sql
SELECT region, SUM(amount) AS revenue
FROM orders               -- data/orders.json
GROUP BY region
```

| Key         | Purpose                                            |
| ----------- | -------------------------------------------------- |
| `directory` | Folder of JSON files (each → a table by stem).     |
| `files`     | Or an explicit `{table_name: path}` map.           |

**Extra:** none — it's in the core install (the `json` reader ships in core
DuckDB). Inherits the DuckDB connector's reconnect-on-fatal resilience, and
`dashdown query --tables` / `--schema <table>` work out of the box.
