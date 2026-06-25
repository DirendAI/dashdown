<!-- AUTO-GENERATED from the component/connector registries by tooling/gen-agent-docs.py — do not edit. -->
<!-- Topic: catalog. Regenerate with: python tooling/gen-agent-docs.py -->

# Component & connector catalog

Introspected straight from the registries — the **same data `dashdown components` prints**, in file-readable form. A new component attribute or connector config key appears here automatically (it is recovered from the source, not hand-written), so this can't drift. Prefer running `dashdown components` when you have a shell; read this shard when you don't.

## Components (34)

`*` marks a **filter** component (it writes a `${param}` and is stripped from static builds). Charts share a common attribute set via the shared chart helper.

| Component | Attributes | Summary |
|---|---|---|
| `Ask` | `ask`, `by`, `cache_ttl`, `col-span`, `data`, `grain`, `label`, `max_rows`, `metric`, `series`, `span` | Pin a question to a query's result and render the LLM's answer as a prose block alongside the charts. |
| `BarChart` | `by`, `col-span`, `color`, `currency`, `data`, `date_format`, `decimals`, `donut`, `empty_message`, `format`, `grain`, `height`, `horizontal`, `locale`, `metric`, `series`, `sort_by`, `span`, `stacked`, `title`, `x`, `y` | Bar chart — a metric across categories. |
| `BoxPlot` | `by`, `col-span`, `color`, `currency`, `data`, `date_format`, `decimals`, `donut`, `empty_message`, `format`, `grain`, `height`, `locale`, `metric`, `series`, `sort_by`, `span`, `title`, `x`, `y` | Box-and-whisker distribution chart. |
| `CalendarHeatmap` | `by`, `col-span`, `color`, `currency`, `data`, `date`, `date_format`, `decimals`, `donut`, `empty_message`, `format`, `grain`, `height`, `locale`, `metric`, `series`, `sort_by`, `span`, `title`, `value`, `x`, `y` | GitHub-style calendar heatmap for daily activity data. |
| `CandlestickChart` | `by`, `col-span`, `color`, `currency`, `data`, `date_format`, `decimals`, `donut`, `empty_message`, `format`, `grain`, `height`, `locale`, `metric`, `series`, `sort_by`, `span`, `title`, `x`, `y` | Candlestick (OHLC) chart for price/range series. |
| `Chart` | `auto`, `by`, `col-span`, `color`, `currency`, `data`, `date_format`, `decimals`, `donut`, `empty_message`, `format`, `grain`, `height`, `locale`, `metric`, `series`, `sort_by`, `span`, `title`, `x`, `y` | Auto-recommended chart: picks line/bar/scatter from the result shape. |
| `ComboChart` | `bar_color`, `bars`, `by`, `col-span`, `color`, `currency`, `data`, `date_format`, `decimals`, `empty_message`, `format`, `grain`, `height`, `line_color`, `lines`, `locale`, `right_axis`, `right_currency`, `right_decimals`, `right_format`, `right_locale`, `series`, `sort_by`, `span`, `title`, `x` | Combo chart — bars and lines together, with an optional second y-axis. |
| `Counter` | `by`, `col-span`, `color`, `column`, `compare`, `compare-column`, `compare-index`, `compare-row`, `currency`, `data`, `date_format`, `decimals`, `delta`, `format`, `grain`, `index`, `invert-delta`, `label`, `locale`, `metric`, `prefix`, `row`, `series`, `span`, `sparkline`, `sparkline-by`, `sparkline-column`, `suffix` | Display a single value as a large counter/KPI. |
| `DateRange` \* | `bar`, `default`, `end_param`, `filter_bar`, `label`, `name`, `persist`, `presets`, `start_param`, `url_sync` | Date range picker component with preset ranges and custom selection. |
| `Dropdown` \* | `bar`, `column`, `data`, `filter_bar`, `label`, `multi`, `name`, `options`, `url_sync` | Dropdown filter. Populates options from the distinct values of a column. |
| `FunnelChart` | `by`, `col-span`, `color`, `currency`, `data`, `date_format`, `decimals`, `donut`, `empty_message`, `format`, `grain`, `height`, `locale`, `metric`, `series`, `sort_by`, `span`, `title`, `x`, `y` | Funnel chart — stage labels (`x`) with descending values (`y`). |
| `GaugeChart` | `by`, `col-span`, `color`, `currency`, `data`, `date_format`, `decimals`, `donut`, `empty_message`, `format`, `grain`, `height`, `locale`, `max`, `metric`, `min`, `series`, `sort_by`, `span`, `title`, `x`, `y` | Speedometer-style gauge for a single KPI value. |
| `GraphChart` | `by`, `col-span`, `color`, `currency`, `data`, `date_format`, `decimals`, `donut`, `empty_message`, `format`, `grain`, `height`, `locale`, `metric`, `series`, `sort_by`, `source`, `span`, `target`, `title`, `value`, `x`, `y` | Force-directed network graph from an edge-list query. |
| `Grid` | `cols`, `columns`, `gap` | Lay children out on a fixed-column CSS grid. |
| `HeatmapChart` | `by`, `col-span`, `color`, `currency`, `data`, `date_format`, `decimals`, `donut`, `empty_message`, `format`, `grain`, `height`, `locale`, `metric`, `series`, `sort_by`, `span`, `title`, `value`, `x`, `y` | Matrix heatmap — a grid of cells shaded by magnitude. |
| `LineChart` | `by`, `col-span`, `color`, `currency`, `data`, `date_format`, `decimals`, `donut`, `empty_message`, `format`, `grain`, `height`, `locale`, `metric`, `series`, `sort_by`, `span`, `stacked`, `title`, `x`, `y` | Line chart — a metric over an ordered `x` (usually time). |
| `MapChart` | `by`, `col-span`, `color`, `currency`, `data`, `date_format`, `decimals`, `donut`, `empty_message`, `format`, `geojson`, `grain`, `height`, `locale`, `location`, `map`, `metric`, `series`, `sort_by`, `span`, `title`, `value`, `x`, `y` | Choropleth map for geospatial data (ECharts geo). |
| `ParallelChart` | `by`, `col-span`, `color`, `currency`, `data`, `date_format`, `decimals`, `dimensions`, `donut`, `empty_message`, `format`, `grain`, `height`, `locale`, `metric`, `series`, `sort_by`, `span`, `title`, `x`, `y` | Parallel-coordinates plot over several numeric columns. |
| `PieChart` | `by`, `col-span`, `color`, `currency`, `data`, `date_format`, `decimals`, `donut`, `empty_message`, `format`, `grain`, `height`, `locale`, `metric`, `series`, `sort_by`, `span`, `title`, `x`, `y` | Pie/donut chart — parts of a whole (`x` = slice label, `y` = value). |
| `PivotTable` | `agg`, `data`, `empty_message`, `title`, `values` | Interactive cross-tabulation with drag-and-drop row/column axes. |
| `RadarChart` | `by`, `col-span`, `color`, `currency`, `data`, `date_format`, `decimals`, `donut`, `empty_message`, `format`, `grain`, `height`, `locale`, `metric`, `series`, `sort_by`, `span`, `title`, `x`, `y` | Radar (spider) chart for comparing several metrics on one shape. |
| `SankeyChart` | `by`, `col-span`, `color`, `currency`, `data`, `date_format`, `decimals`, `donut`, `empty_message`, `format`, `grain`, `height`, `locale`, `metric`, `series`, `sort_by`, `source`, `span`, `target`, `title`, `value`, `x`, `y` | Sankey flow diagram from an edge-list query. |
| `ScatterChart` | `by`, `col-span`, `color`, `currency`, `data`, `date_format`, `decimals`, `donut`, `empty_message`, `format`, `grain`, `height`, `locale`, `metric`, `series`, `sort_by`, `span`, `title`, `x`, `y` | Scatter plot — two numeric columns as points (`x` vs `y`). |
| `Search` \* | `bar`, `debounce`, `filter_bar`, `label`, `name`, `placeholder`, `url_sync` | Search input component for filtering. |
| `SiteSearch` | `label`, `max_results`, `placeholder` | Full-text search across every page of the project. |
| `SunburstChart` | `by`, `col-span`, `color`, `currency`, `data`, `date_format`, `decimals`, `donut`, `empty_message`, `format`, `grain`, `height`, `id`, `label`, `locale`, `metric`, `parent`, `series`, `sort_by`, `span`, `title`, `value`, `x`, `y` | Sunburst — a hierarchy as nested rings, area encoding the value. |
| `Table` | `by`, `col-span`, `currency`, `data`, `date_format`, `detail_slug`, `empty_message`, `export`, `export_filename`, `filename`, `format`, `grain`, `headers`, `heatmap`, `heatmap_scheme`, `limit`, `link_column`, `link_pattern`, `locale`, `metric`, `page-size`, `row_link`, `search`, `series`, `sort`, `span`, `title` | Data table — renders a query result as a sortable, searchable grid. |
| `ThemeRiver` | `by`, `col-span`, `color`, `currency`, `data`, `date_format`, `decimals`, `donut`, `empty_message`, `format`, `grain`, `height`, `locale`, `metric`, `series`, `sort_by`, `span`, `title`, `x`, `y` | ThemeRiver (streamgraph) — stacked categories flowing over time. |
| `TimeGrain` \* | `bar`, `default`, `filter_bar`, `grains`, `label`, `name`, `native`, `url_sync` | Time-grain picker — writes a `day`/`week`/`month`/… token for `grain={name}`. |
| `Toggle` \* | `bar`, `default`, `filter_bar`, `label`, `name`, `off_value`, `on_value`, `url_sync`, `variant` | Boolean / toggle filter — a one-click switch (or checkbox) for a two-valued facet ("show only active", "include archived", "paid only"). |
| `TreeChart` | `by`, `col-span`, `color`, `currency`, `data`, `date_format`, `decimals`, `donut`, `empty_message`, `format`, `grain`, `height`, `id`, `label`, `locale`, `metric`, `parent`, `series`, `sort_by`, `span`, `title`, `value`, `x`, `y` | Tree — a hierarchy as a node-link diagram (left-to-right, collapsible). |
| `TreemapChart` | `by`, `col-span`, `color`, `currency`, `data`, `date_format`, `decimals`, `donut`, `empty_message`, `format`, `grain`, `height`, `locale`, `metric`, `series`, `sort_by`, `span`, `title`, `x`, `y` | Treemap — categories as nested rectangles sized by value. |
| `Value` | `by`, `column`, `currency`, `data`, `date_format`, `decimals`, `format`, `grain`, `index`, `locale`, `metric`, `prefix`, `row`, `series`, `suffix` | Display a single value from a query result. |
| `Violin` | `by`, `col-span`, `color`, `currency`, `data`, `date_format`, `decimals`, `donut`, `empty_message`, `format`, `grain`, `height`, `locale`, `metric`, `series`, `sort_by`, `span`, `title`, `x`, `y` | Violin (kernel density) distribution chart; same attributes as BoxPlot. |

## Connectors (15)

`type:` in `sources.yaml`. Install the listed extra before using a non-core connector; config keys support `${ENV_VAR}` expansion.

| Type | Install | Config keys | Summary |
|---|---|---|---|
| `bigquery` | `pip install 'dashdown-md[bigquery]'` | `connect_args`, `credentials_file`, `credentials_path`, `dataset`, `location`, `project` | Google BigQuery connector (via google-cloud-bigquery). |
| `csv` | core | `directory`, `files` | CSV source: an always-in-memory DuckDB with one table per file. |
| `cube` | `pip install 'dashdown-md[cube]'` | `algorithm`, `api_path`, `private_key`, `secret`, `security_context`, `timeout`, `token`, `token_ttl`, `url` | HTTP client for a Cube deployment — driven by the Cube semantic backend. |
| `dax` | `pip install 'dashdown-md[dax]'` | `client_id`, `client_secret`, `dataset_id`, `tenant_id`, `workspace_id` | Executes DAX queries against a Fabric / Power BI dataset. |
| `duckdb` | core | `csv_views`, `path` | Connects to a DuckDB database file (or in-memory if no path). |
| `excel` | `pip install 'dashdown-md[excel]'` | `header`, `path`, `sheets` | Excel connector (via openpyxl). Backed by an in-memory DuckDB for SQL. |
| `json` | core | `directory`, `files` | JSON source: an always-in-memory DuckDB with one table per file. |
| `motherduck` | core | `config`, `database`, `db`, `duckdb_config`, `motherduck_token`, `token` | Connects to a MotherDuck cloud database (``md:`` over the DuckDB driver). |
| `mssql` | `pip install 'dashdown-md[mssql]'` | `auth`, `authentication`, `client_id`, `client_secret`, `connect_args`, `connection_string`, `connection_timeout`, `database`, `dbname`, `driver`, `dsn`, `encrypt`, `host`, `odbc`, `password`, `port`, `pwd`, `server`, `tenant_id`, `trust_cert`, `trust_server_certificate`, `uid`, `url`, `user` | Microsoft SQL Server / Azure SQL connector (via pyodbc). |
| `mysql` | `pip install 'dashdown-md[mysql]'` | `connect_args`, `database`, `db`, `dsn`, `host`, `password`, `port`, `url`, `user` | MySQL / MariaDB connector (via PyMySQL). |
| `parquet` | core | `directory`, `files` | Parquet source: an always-in-memory DuckDB with one table per file. |
| `postgres` | `pip install 'dashdown-md[postgres]'` | `connect_args`, `database`, `dbname`, `dsn`, `host`, `password`, `port`, `url`, `user` | PostgreSQL connector (via psycopg2). |
| `quack` | core | `alias`, `config`, `database`, `db`, `duckdb_config`, `extension_repository`, `host`, `hostname`, `install_extension`, `port`, `quack_token`, `target`, `token`, `url` | Connects to a remote DuckDB over the Quack RPC protocol (``quack:`` target). |
| `sheets` | `pip install 'dashdown-md[sheets]'` | `credentials_file`, `credentials_path`, `header`, `key`, `spreadsheet_id`, `url`, `worksheets` | Google Sheets connector (via gspread). Backed by an in-memory DuckDB for SQL. |
| `snowflake` | `pip install 'dashdown-md[snowflake]'` | `account`, `authenticator`, `connect_args`, `database`, `host`, `password`, `port`, `role`, `schema`, `token`, `user`, `warehouse` | Snowflake connector (via snowflake-connector-python). |
