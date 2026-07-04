<!-- AUTO-GENERATED from docs/pages/ by tooling/gen-agent-docs.py — do not edit. -->
<!-- Topic: connectors. Regenerate with: python tooling/gen-agent-docs.py -->

<!-- source: docs/pages/connectors/index.md -->

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

1. the source named by the top-level `default:` key in `sources.yaml`;
2. otherwise, if exactly **one** source is configured, that one — a
   single-source project never needs `connector=` anywhere.

```yaml
# sources.yaml
default: warehouse        # queries without connector= run here

warehouse:
  type: postgres
  host: ${PG_HOST}
  database: analytics

archive:
  type: duckdb
  path: data/archive.duckdb
```

Source **names carry no meaning** — call them whatever reads well (`default` is
the one reserved word). A `default:` naming an unknown source fails at startup,
and with several sources and no `default:` there is *no* default: a query that
omits `connector=` fails with a message asking you to set one.

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


<!-- source: docs/pages/connectors/csv.md -->

# CSV connector

Runs SQL over CSV files on an embedded DuckDB — no database to stand up. Each file
in the directory becomes a queryable view named after the file (`sales.csv` →
`sales`). This is what these docs use.

```yaml
# sources.yaml
sales_data:
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


<!-- source: docs/pages/connectors/json.md -->

# JSON connector

Runs SQL over JSON files on an embedded DuckDB — no database to stand up. Each
file in the directory becomes a queryable table named after the file
(`orders.json` → `orders`), via DuckDB's `read_json_auto`, which auto-detects both
a JSON **array of objects** and **newline-delimited** JSON. So `.json`, `.ndjson`,
and `.jsonl` files are all picked up.

```yaml
# sources.yaml
sales_data:
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


<!-- source: docs/pages/connectors/parquet.md -->

# Parquet connector

Runs SQL over Parquet files on an embedded DuckDB — no database to stand up. Each
`.parquet` (or `.pq`) file in the directory becomes a queryable table named after
the file (`sales.parquet` → `sales`), via DuckDB's `read_parquet`. Parquet is
columnar and already typed, so it's the fastest file source — no header sniffing
or type inference.

```yaml
# sources.yaml
sales_data:
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


<!-- source: docs/pages/connectors/excel.md -->

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


<!-- source: docs/pages/connectors/sheets.md -->

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


<!-- source: docs/pages/connectors/duckdb.md -->

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


<!-- source: docs/pages/connectors/motherduck.md -->

# MotherDuck connector

[MotherDuck](https://motherduck.com) is cloud DuckDB. It uses the same `duckdb`
driver and the same SQL — you just point at an `md:` database and authenticate
with a service token. So this connector is a thin subclass of the
[DuckDB connector](/connectors/duckdb): same engine, same resilience, plus your
cloud-hosted (and shared) databases.

```yaml
# sources.yaml
cloud:
  type: motherduck
  database: my_db                # optional — omit to attach all your databases
  token: ${MOTHERDUCK_TOKEN}     # optional — ${ENV_VAR} expansion supported
```

| Key            | Purpose                                                              |
| -------------- | ------------------------------------------------------------------- |
| `database`     | MotherDuck database name (becomes the `md:<database>` target). Omit for the bare `md:` connection, which attaches all your databases. |
| `token`        | MotherDuck service token. `${ENV_VAR}` is expanded. Omit to use the `motherduck_token` environment variable DuckDB reads on its own. |
| `duckdb_config`| Optional extra settings passed to `duckdb.connect` (e.g. `custom_user_agent`). |

Once connected, query it like any DuckDB source — `database`-qualify a table if
you've attached more than one:

```sql
SELECT region, sum(amount) AS revenue
FROM my_db.sales
GROUP BY region
ORDER BY revenue DESC
```

**Resilience:** inherited from the DuckDB connector — if a query *invalidates* the
connection, `query()` rebuilds it and retries once, so one bad query can't break
every later query on the long-lived connection.

:::note
A `${param}` always substitutes a **quoted string literal** (injection-safe — see
[Queries](/queries#parameters--injection-safety)), exactly as with the local
DuckDB connector.
:::

**Extra:** none — in the core install. The `duckdb` core dependency ships the
MotherDuck extension, which auto-loads on the first `md:` connect.


<!-- source: docs/pages/connectors/quack.md -->

# Quack connector

[Quack](https://duckdb.org/quack/) is a remote protocol for DuckDB — it turns
DuckDB from an embedded engine into a client-server one. A DuckDB instance runs
as a server (`CALL quack_serve('quack:host', …)`) and clients reach it over the
network by attaching a `quack:` target. It's still the same `duckdb` driver and
the same SQL — only *where the data lives* changes. So this connector is a thin
subclass of the [DuckDB connector](/connectors/duckdb): same engine, same
resilience, pointed at a remote server instead of a local file.

```yaml
# sources.yaml
remote:
  type: quack
  host: data.example.com         # the quack server host (target becomes quack:<host>)
  port: 9494                     # optional — omit for the server's default port
  token: ${QUACK_TOKEN}          # optional — ${ENV_VAR} expansion supported
  database: remote               # optional — ATTACH alias (default "remote")
```

| Key                    | Purpose                                                                 |
| ---------------------- | ----------------------------------------------------------------------- |
| `host`                 | The Quack server host. Becomes the `quack:<host>` attach target. (Or set a full `target: quack:…` to pass it through verbatim.) |
| `port`                 | Optional server port, appended as `quack:<host>:<port>`. Omit for the default. |
| `token`                | Optional Quack auth token, registered as a `CREATE SECRET (TYPE quack …)`. `${ENV_VAR}` is expanded. |
| `database`             | The `ATTACH … AS <alias>` name you qualify remote tables with (default `remote`). |
| `install_extension`    | Whether to `INSTALL quack` before loading it (default `true`). Set `false` if the extension is already present in your DuckDB. |
| `extension_repository` | Where to install from (default `community`; a `https://…` URL is also accepted). |
| `duckdb_config`        | Optional extra settings passed to `duckdb.connect` (e.g. `allow_unsigned_extensions`). |

On connect the connector loads the Quack extension, registers the token secret
(if any), and `ATTACH`-es the remote. Then you query it like any DuckDB source,
qualifying remote tables with the attach alias:

```sql
SELECT region, sum(amount) AS revenue
FROM remote.sales
GROUP BY region
ORDER BY revenue DESC
```

**Resilience:** inherited from the DuckDB connector — if a query *invalidates* the
local connection, `query()` rebuilds it (re-loading the extension and re-attaching
the remote) and retries once.

:::note
A `${param}` always substitutes a **quoted string literal** (injection-safe — see
[Queries](/queries#parameters--injection-safety)), exactly as with the local
DuckDB connector.
:::

:::warning
**Experimental / preview.** Quack itself is in beta. This connector covers the
documented attach + token-secret flow and is not yet verified against a live Quack
server. The extension is a community extension, so a managed DuckDB build may need
`duckdb_config: { allow_unsigned_extensions: true }` to load it.
:::

**Extra:** none — in the core install. The Quack extension is downloaded at
runtime by DuckDB (`INSTALL quack FROM community`), so there is no extra `pip`
dependency.


<!-- source: docs/pages/connectors/postgres.md -->

# Postgres connector

Connects to PostgreSQL over the shared SQL DB-API base: lazy connect, JSON-safe
value coercion, and one reconnect-and-retry on a dropped connection.

```yaml
# sources.yaml
warehouse:
  type: postgres
  host: ${PG_HOST}
  port: 5432
  database: analytics
  user: ${PG_USER}
  password: ${PG_PASSWORD}
```

| Key            | Purpose                                            |
| -------------- | -------------------------------------------------- |
| `host` / `port`| Server address.                                    |
| `database`     | Database name (`dbname` also accepted).            |
| `user` / `password` | Credentials.                                  |
| `dsn` / `url`  | …or a single connection string instead of the above.|
| `connect_args` | Extra kwargs passed to the driver.                 |

**Install:** `uv add 'dashdown-md[postgres]'` (or `pip install 'dashdown-md[postgres]'`).
Secrets support `${ENV_VAR}` expansion — keep them out of the YAML.


<!-- source: docs/pages/connectors/mysql.md -->

# MySQL connector

MySQL/MariaDB over the same SQL DB-API base as Postgres (lazy connect, JSON-safe
coercion, reconnect-and-retry).

```yaml
# sources.yaml
warehouse:
  type: mysql
  host: ${MYSQL_HOST}
  port: 3306
  database: analytics
  user: ${MYSQL_USER}
  password: ${MYSQL_PASSWORD}
```

| Key            | Purpose                                              |
| -------------- | --------------------------------------------------- |
| `host` / `port`| Server address.                                     |
| `database`     | Database name (`db` also accepted).                 |
| `user` / `password` | Credentials.                                   |
| `dsn` / `url`  | …or a single connection string.                     |
| `connect_args` | Extra kwargs passed to the driver.                  |

**Install:** `uv add 'dashdown-md[mysql]'`.


<!-- source: docs/pages/connectors/mssql.md -->

# SQL Server / Azure SQL connector

Microsoft SQL Server and Azure SQL over the shared SQL DB-API base (lazy connect,
JSON-safe value coercion, reconnect-and-retry). Uses **pyodbc**, so it needs
Microsoft's ODBC driver on the host — install `msodbcsql18` (the Azure AD auth
modes below require ODBC Driver 18, or 17.x ≥ 17.3).

```yaml
# sources.yaml — SQL login
warehouse:
  type: mssql
  host: ${MSSQL_HOST}        # alias: server
  port: 1433                 # optional (default 1433)
  database: analytics        # alias: dbname
  user: ${MSSQL_USER}        # alias: uid
  password: ${MSSQL_PASSWORD}# alias: pwd
```

## Azure AD authentication

The connector supports the full Azure AD matrix. The mode is chosen by the
`authentication` key — or **inferred** for a service principal.

```yaml
# Service principal (client credentials) — Authentication is inferred from the pair
warehouse:
  type: mssql
  host: myserver.database.windows.net
  database: analytics
  client_id: ${AZURE_CLIENT_ID}
  client_secret: ${AZURE_CLIENT_SECRET}
  tenant_id: ${AZURE_TENANT_ID}        # optional
```

```yaml
# Managed identity (Azure VM / App Service)
warehouse:
  type: mssql
  host: myserver.database.windows.net
  database: analytics
  authentication: ActiveDirectoryMsi
  # client_id: <user-assigned-identity-id>   # omit for a system-assigned identity
```

Other `authentication` values: `ActiveDirectoryPassword` (Azure AD user + password),
`ActiveDirectoryDefault` (the default credential chain — env → managed identity →
CLI → …), `ActiveDirectoryInteractive`, `ActiveDirectoryIntegrated`.

| Key                          | Purpose                                                        |
| ---------------------------- | -------------------------------------------------------------- |
| `host` / `port`              | Server address (`server` also accepted; default port 1433).    |
| `database`                   | Database name (`dbname` also accepted).                        |
| `user` / `password`          | SQL login (`uid` / `pwd` also accepted).                       |
| `client_id` / `client_secret`| Azure AD service principal → inferred `ActiveDirectoryServicePrincipal`. |
| `tenant_id`                  | Optional; appended as `UID=client_id@tenant_id` when set.       |
| `authentication`             | Pick an Azure AD mode explicitly (see above).                  |
| `driver`                     | ODBC driver name (default `ODBC Driver 18 for SQL Server`).    |
| `encrypt` / `trust_server_certificate` | TLS toggles (`yes`/`no` or `true`/`false`).         |
| `connection_string` / `url`  | …or a full ODBC string / `mssql://…` URL instead of the above. |
| `odbc`                       | Map of extra raw ODBC keywords, merged last.                   |
| `connect_args`               | Extra kwargs passed straight to `pyodbc.connect()`.            |

**Install:** `uv add 'dashdown-md[mssql]'` (or `pip install 'dashdown-md[mssql]'`), plus
the `msodbcsql18` ODBC driver. Keep every secret in `${ENV_VAR}`, not the YAML.


<!-- source: docs/pages/connectors/snowflake.md -->

# Snowflake connector

Snowflake over the SQL DB-API base. Connection details go under `connect_args`,
which are passed straight to `snowflake.connector.connect`.

```yaml
# sources.yaml
warehouse:
  type: snowflake
  connect_args:
    account: ${SNOWFLAKE_ACCOUNT}
    user: ${SNOWFLAKE_USER}
    password: ${SNOWFLAKE_PASSWORD}
    warehouse: COMPUTE_WH
    database: ANALYTICS
    schema: PUBLIC
```

| Key            | Purpose                                              |
| -------------- | --------------------------------------------------- |
| `connect_args` | All connection kwargs (account, user, warehouse, …).|

**Install:** `uv add 'dashdown-md[snowflake]'`. Use `${ENV_VAR}` for every secret.


<!-- source: docs/pages/connectors/bigquery.md -->

# BigQuery connector

Google BigQuery, wrapped in its native client's PEP 249 (DB-API) adapter so it
shares the same base as the other SQL connectors.

```yaml
# sources.yaml
warehouse:
  type: bigquery
  project: my-gcp-project
  location: EU                       # optional
  credentials_file: service-account.json   # path relative to the project
```

| Key                | Purpose                                          |
| ------------------ | ------------------------------------------------ |
| `project`          | GCP project id.                                  |
| `location`         | Dataset location (optional).                     |
| `credentials_file` | Service-account JSON (or `credentials_path`).    |

If `credentials_file` is omitted, Application Default Credentials are used.
**Install:** `uv add 'dashdown-md[bigquery]'`.


<!-- source: docs/pages/connectors/clickhouse.md -->

# ClickHouse connector

Connects to ClickHouse over the shared SQL DB-API base (lazy connect, JSON-safe
value coercion, reconnect-and-retry) via `clickhouse-connect`, ClickHouse's
official HTTP client. Works with self-hosted ClickHouse and ClickHouse Cloud.

```yaml
# sources.yaml
events:
  type: clickhouse
  host: ${CH_HOST}
  port: 8443
  database: analytics
  user: ${CH_USER}
  password: ${CH_PASSWORD}
  secure: true          # TLS — required by ClickHouse Cloud
```

| Key            | Purpose                                                         |
| -------------- | --------------------------------------------------------------- |
| `host` / `port`| Server address. Port is optional — the driver defaults to `8123` (`8443` when `secure`). |
| `database`     | Database name (`db` also accepted).                             |
| `user` / `password` | Credentials (`username` also accepted).                    |
| `secure`       | Connect over TLS.                                               |
| `dsn` / `url`  | …or a single `clickhouse://user:pass@host:port/db` URL instead of the above. |
| `connect_args` | Extra kwargs passed to the driver.                              |

**Install:** `uv add 'dashdown-md[clickhouse]'` (or `pip install 'dashdown-md[clickhouse]'`).
Secrets support `${ENV_VAR}` expansion — keep them out of the YAML.


<!-- source: docs/pages/connectors/dax.md -->

# DAX connector (Microsoft Fabric / Power BI)

Queries a Microsoft Fabric / Power BI dataset via the REST API with MSAL
authentication. Unlike the SQL connectors, queries are written in **DAX**, not
SQL — and `${param}` substitution still applies (a DAX string literal `"…"` gets
its quotes escaped automatically).

```yaml
# sources.yaml
fabric:
  type: dax
  tenant_id: ${FABRIC_TENANT_ID}
  client_id: ${FABRIC_CLIENT_ID}
  client_secret: ${FABRIC_CLIENT_SECRET}
  workspace_id: ${FABRIC_WORKSPACE_ID}
  dataset_id: ${FABRIC_DATASET_ID}
```

| Key             | Purpose                                  |
| --------------- | ---------------------------------------- |
| `tenant_id`     | Azure AD tenant.                         |
| `client_id` / `client_secret` | Service-principal credentials. |
| `workspace_id`  | Fabric/Power BI workspace.               |
| `dataset_id`    | The dataset to query.                    |

## Writing a DAX query

DAX queries start with `EVALUATE` and return a table. The most useful building
block is `SUMMARIZECOLUMNS`, which groups by one or more columns and projects
your measures alongside them — the table-shaped result a chart needs. Column
names come back **shortened**: `'Date'[Month]` arrives as just `Month`, so that's
the name you reference in `x=`/`y=`.

Define it once in the query library (`queries/revenue_by_month.dax`):

```dax
EVALUATE
SUMMARIZECOLUMNS(
    'Date'[Month],
    "Revenue", [Total Revenue]
)
ORDER BY 'Date'[Month]
```

Then reference it by name on a page and feed it to a chart:

```markdown
<LineChart data={revenue_by_month} x="Month" y="Revenue" />
```

Or inline on a single page, choosing the connector explicitly:

````markdown
```dax revenue_by_region connector=fabric
EVALUATE
SUMMARIZECOLUMNS(
    'Store'[Region],
    "Revenue", [Total Revenue]
)
```

<BarChart data={revenue_by_region} x="Region" y="Revenue" />
````

### Parameters and filtering

`${param}` substitution works the same as the SQL connectors, but filter values
in DAX are **string literals wrapped in double quotes** (`"…"`) — and Dashdown
escapes the value's quotes (`"` → `""`) automatically for that context. A
`<Dropdown>` feeding `${region}` looks like:

```dax
EVALUATE
SUMMARIZECOLUMNS(
    'Date'[Month],
    FILTER(VALUES('Store'[Region]), 'Store'[Region] = "${region}"),
    "Revenue", [Total Revenue]
)
ORDER BY 'Date'[Month]
```

Library queries for this connector live in `queries/*.dax`. **Install:**
`uv add 'dashdown-md[dax]'`.


<!-- source: docs/pages/connectors/cube.md -->

# Cube connector

Connects to a [Cube](https://cube.dev) deployment — a standalone semantic-layer
*server* where a team models measures and dimensions, served over a structured
JSON API. Unlike every other connector, **Cube isn't queried with SQL**: the
connector is a thin HTTP+JWT client that powers the [`backend: cube` semantic
layer](/semantic-layer/cube), so you reference its model with
the `metric={…} by={…}` grammar rather than inline SQL query blocks.

:::warning Experimental — preview
The Cube integration is **preview** — fully unit-tested with fakes but not yet
verified against a live Cube deployment, and it covers the
`measures`/`dimensions`/`timeDimensions`/`filters` subset of Cube's query shape.
Treat it as a preview to try, not a production guarantee.
:::

```yaml
# sources.yaml
cube:
  type: cube
  url: https://cube.example.com
  secret: ${CUBE_API_SECRET}      # HS256 signing secret (env-expanded)
  token_ttl: 300                  # JWT lifetime in seconds (default)
  security_context:               # optional — embedded in every JWT (the RLS rail)
    tenant_id: acme
```

| Key | Purpose |
| --- | ------- |
| `url` | **Required.** Base URL of the Cube deployment. |
| `secret` | HS256 signing secret — Dashdown mints a short-lived JWT per request. |
| `private_key` | RS256 private key, as an alternative to `secret`. |
| `token` | A static pre-minted JWT (escape hatch — no minting). |
| `algorithm` | Signing algorithm (default `HS256`). |
| `security_context` | Claims embedded in every JWT — Cube applies them server-side for row-level security. |
| `api_path` | API path (default `/cubejs-api/v1`). |
| `token_ttl` | JWT lifetime in seconds (default `300`); re-minted before expiry and once on a `401`. |
| `timeout` | Request timeout in seconds (default `60`). |

One of `secret`, `private_key`, or `token` is required.

## How it's used

Because Cube speaks a structured JSON query (not SQL), this connector is paired
with a `semantic/` model — Cube's `GET /meta` auto-fills the catalogue, so a model
is as small as `orders: { connector: cube }`:

```yaml
# semantic/orders.yml
orders:
  connector: cube
```

```markdown
<LineChart metric={orders.revenue} by={orders.createdAt} grain="month" />
<BarChart  metric={orders.count}   by={orders.status} />
```

See the [semantic layer → Cube backend](/semantic-layer/cube)
for the full modeling, filter mapping, JWT lifecycle, and `grain=` behaviour.
There is **no `${param}` injection surface** — filter values are JSON data, never
assembled into a query string.

:::tip Just want SQL access to Cube?
Cube also exposes a **Postgres-wire-compatible SQL API**. You don't need this
connector for that — point a [`postgres` connector](/connectors/postgres) at
Cube's SQL port and write `queries/*.sql`. This connector is for the first-class
`metric={…} by={…}` grammar.
:::

**Install:** `uv add 'dashdown-md[cube]'`.
