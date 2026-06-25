---
title: Excel
sidebar_label: Excel
sidebar_position: 4
---

# Excel connector

Query an `.xlsx` workbook with SQL. Each sheet is loaded into an in-memory DuckDB
(NaN → NULL) and becomes a view, so spreadsheets answer the same SQL as a real
database.

```yaml
# sources.yaml
book:
  type: excel
  path: data/report.xlsx
  header: true         # first row is the header (default true)
  sheets:              # optional: limit/rename which sheets load
    - Sheet1
    - Summary
```

| Key      | Purpose                                              |
| -------- | --------------------------------------------------- |
| `path`   | Path to the `.xlsx` file.                           |
| `sheets` | Which sheets to load (default: all).                |
| `header` | Boolean: is the first row the column header? (default `true`). Set `false` to auto-name columns `col0…colN`. |

Then `SELECT … FROM Sheet1`. **Install:** `uv add 'dashdown-md[excel]'` (openpyxl).
