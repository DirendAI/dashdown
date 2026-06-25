---
title: Google Sheets
sidebar_label: Google Sheets
sidebar_position: 5
---

# Google Sheets connector

Query a Google Sheet with SQL. Worksheets load into an in-memory DuckDB as views.
Values arrive as text, so `CAST` numeric columns in your SQL.

```yaml
# sources.yaml
sheet:
  type: sheets
  spreadsheet_id: 1AbC...xyz          # or `url:` / `key:`
  credentials_file: service-account.json
  worksheets:                         # optional
    - Data
  header: true                        # first row is the header (default true)
```

| Key                | Purpose                                        |
| ------------------ | ---------------------------------------------- |
| `spreadsheet_id`   | The sheet id (`url` or `key` also accepted).   |
| `credentials_file` | Service-account JSON (or `credentials_path`).  |
| `worksheets`       | Which tabs to load (default: all).             |
| `header`           | Boolean: is the first row the column header? (default `true`). Set `false` to auto-name columns `col0…colN`. |

```sql
SELECT name, CAST(amount AS DOUBLE) AS amount FROM Data
```

**Install:** `uv add 'dashdown-md[sheets]'` (gspread). Share the sheet with the
service account's email.
