---
title: Connectors
sidebar_label: Connectors
sidebar_position: 5
icon: "\U0001F50C"
---

# Data connectors

Connectors are declared in `sources.yaml` and loaded **lazily** the first time a
query asks for that type. Each backend's heavy dependencies are an optional `pip`
extra, so you only install what you use. A query's `connector=` chooses which one
runs it; omitted, the [default source](#the-default-source) answers.

```yaml
# sources.yaml
sales_data:
  type: csv
  directory: data

warehouse:
  type: postgres
  host: ${PG_HOST}        # ${ENV_VAR} expansion is supported everywhere
  database: analytics
  user: ${PG_USER}
  password: ${PG_PASSWORD}
```

## The default source

A query with no `connector=` runs on the project's **default source**:

1. the source marked `default: true` in `sources.yaml`;
2. otherwise, if exactly **one** source is configured, that one — a
   single-source project never needs `connector=` anywhere.

```yaml
# sources.yaml
warehouse:
  type: postgres
  default: true           # queries without connector= run here
  host: ${PG_HOST}
  database: analytics

archive:
  type: duckdb
  path: data/archive.duckdb
```

Source **names carry no meaning** — call them whatever reads well. Only one
source may set `default: true` (two fail at startup), and with several sources
and no flag there is *no* default: a query that omits `connector=` fails with a
message asking you to mark one.

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
