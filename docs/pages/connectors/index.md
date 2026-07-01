---
title: Connectors
sidebar_label: Connectors
sidebar_position: 5
icon: "\U0001F50C"
---

# Data connectors

Connectors are declared in `sources.yaml` and loaded **lazily** the first time a
query asks for that type. Each backend's heavy dependencies are an optional `pip`
extra, so you only install what you use. A query's `connector=` (default `main`)
chooses which one runs it.

```yaml
# sources.yaml
main:
  type: csv
  directory: data

warehouse:
  type: postgres
  host: ${PG_HOST}        # ${ENV_VAR} expansion is supported everywhere
  database: analytics
  user: ${PG_USER}
  password: ${PG_PASSWORD}
```

## The built-in connectors

| Type | Family | Extra | Page |
| ---- | ------ | ----- | ---- |
| `csv` | DuckDB-backed | (core) | [CSV](/connectors/csv) |
| `json` | DuckDB-backed | (core) | [JSON](/connectors/json) |
| `parquet` | DuckDB-backed | (core) | [Parquet](/connectors/parquet) |
| `duckdb` | DuckDB-backed | (core) | [DuckDB](/connectors/duckdb) |
| `motherduck` | DuckDB-backed | (core) | [MotherDuck](/connectors/motherduck) |
| `quack` | DuckDB-backed | (core) | [Quack](/connectors/quack) |
| `postgres` | SQL DB-API | `dashdown-md[postgres]` | [Postgres](/connectors/postgres) |
| `mysql` | SQL DB-API | `dashdown-md[mysql]` | [MySQL](/connectors/mysql) |
| `mssql` | SQL DB-API | `dashdown-md[mssql]` | [SQL Server](/connectors/mssql) |
| `snowflake` | SQL DB-API | `dashdown-md[snowflake]` | [Snowflake](/connectors/snowflake) |
| `bigquery` | SQL DB-API | `dashdown-md[bigquery]` | [BigQuery](/connectors/bigquery) |
| `clickhouse` | SQL DB-API | `dashdown-md[clickhouse]` | [ClickHouse](/connectors/clickhouse) |
| `excel` | Tabular | `dashdown-md[excel]` | [Excel](/connectors/excel) |
| `sheets` | Tabular | `dashdown-md[sheets]` | [Google Sheets](/connectors/sheets) |
| `dax` | REST (Fabric/PBI) | `dashdown-md[dax]` | [DAX / Fabric](/connectors/dax) |
| `cube` | Semantic (Cube) | `dashdown-md[cube]` | [Cube](/connectors/cube) |

The `csv`, `json`, `parquet`, `duckdb`, `excel`, and `sheets` connectors all run
SQL on an embedded DuckDB — so files and spreadsheets answer the same SQL as a
real database.

:::tip
Third-party connectors ship as separate PyPI packages declaring the
`dashdown.connectors` entry-point group — no change to the core is needed.
:::
