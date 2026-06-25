---
title: CSV
sidebar_label: CSV
sidebar_position: 1
---

# CSV connector

Runs SQL over CSV files on an embedded DuckDB — no database to stand up. Each file
in the directory becomes a queryable view named after the file (`sales.csv` →
`sales`). This is what these docs use.

```yaml
# sources.yaml
main:
  type: csv
  directory: data        # folder of .csv files, relative to the project
```

Then query the view by file name:

```sql
SELECT month, SUM(downloads) AS downloads
FROM downloads            -- data/downloads.csv
GROUP BY month
```

| Key         | Purpose                                            |
| ----------- | -------------------------------------------------- |
| `directory` | Folder of CSV files (each → a view).               |
| `files`     | Or an explicit `{view_name: path}` map.            |
| `path`      | A single CSV file.                                 |

**Extra:** none — it's in the core install. Inherits the DuckDB connector's
reconnect-on-fatal resilience.
