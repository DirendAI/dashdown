# CLAUDE.md

This file orients coding agents (and humans) working on the **Dashdown framework** itself. For
*authoring a dashboard* with Dashdown, read the user-facing docs in `docs/` (run them with
`dashdown serve docs`) — this file is about the internals of the package.

## What this is

Dashdown renders Markdown files (with embedded SQL and component tags) as interactive analytics
dashboards. No build step, no JS framework, no npm — the frontend is hand-written ES modules served
as static files; the backend is a FastAPI app. Distributed as the `dashdown` pip package with a CLI
entry point.

## Commands

```bash
uv run pytest tests/ -v              # run the full suite
uv run pytest tests/test_attrs.py    # single file
uv run pytest tests/test_pipeline.py::TestSubstituteParams -v   # single class
uv run pytest -k "injection"         # match by test name

pip install -e .                     # editable install (or: uv sync)
dashdown serve docs                  # run the docs project as a live dashboard at http://127.0.0.1:8000
dashdown serve . --port 8001 --no-watch
dashdown query "SELECT * FROM sales LIMIT 5" -p docs -c main   # probe a connector / inspect data (table|json|csv)
dashdown components                  # introspected catalog: component attrs + connector config keys (table|json; --connectors)
dashdown new my-dashboard            # scaffold a new project
dashdown build docs --out dist       # static export (pre-rendered HTML + data JSON)
```

There is no configured linter/formatter and no frontend build/test tooling — JS is shipped as-is.
The `docs/` project doubles as an end-to-end integration fixture; serve it to verify rendering
changes by hand.

## Two distinct domains

Keep clear about which side of the wire you're on:

1. **The framework** (`dashdown/`) — the Python package + static JS that *we* ship.
2. **A user's project** — a directory with `dashdown.yaml`, `sources.yaml`, `pages/*.md`, `data/`,
   `components/`, `assets/`. The CLI points the framework at one of these. (The project's served
   asset folder is `assets/` — images, downloads, `custom.css`, branding logo/favicon — mounted at
   `/assets/`; pages may also reference files co-located next to their `.md`. See
   `render/pipeline.py::_rewrite_asset_urls`.)

## Render pipeline (the core flow)

A page request flows through `server.py` → `render/pipeline.py` → `render/markdown.py` +
`render/components.py`. Read these four files together before changing rendering behavior.

1. `parse_markdown()` (`render/markdown.py`) splits the `.md` into: YAML frontmatter, HTML body, and
   a list of `QuerySpec` (from `:::query name=… connector=…` container directives). The SQL inside a
   query block is **collected, not executed**, and stripped from the HTML output. `build_md()` is
   CommonMark + tables + GitHub-flavored extensions (strikethrough, task lists, footnotes, deflists,
   `h2/h3` heading anchors) + `:::note`/`:::tip`/`:::info`/`:::warning`/`:::danger` callout containers
   (same `:::` machinery as `:::query`, distinct first words so they don't collide). Fenced code is
   highlighted **server-side** via Pygments through markdown-it's `highlight` option (`highlight_code`
   emits a `<pre class="dashdown-code" data-lang=…>` shell — unknown langs fall back to a plain escaped
   block in the same shell; `mermaid` is special-cased to an explicit `dashdown-mermaid` marker block,
   never highlighted, which the client upgrades to a diagram). Highlighting is static HTML, so it ships
   in `dashdown build` exports/embeds with no client JS. Styling is plain CSS under `.dashdown-prose`
   in `static/dashdown.css`. A copy-to-clipboard button is layered on **client-side**
   (`static/components/copy_code.js` wraps each non-mermaid `<pre class="dashdown-code">` and injects
   the button) — a pure progressive enhancement, so this server-side shell stays untouched. The
   separate `render_markdown_text()` (untrusted `<Ask />` LLM answers)
   stays minimal and `html: False` — these page extensions are deliberately **not** applied there.
2. `render_components()` (`render/components.py`) scans the HTML for PascalCase tags
   (`<LineChart .../>`, `<Table>…</Table>`) with a stack-based parser and replaces each with the
   registered component's `render()` output. Unknown tags / render errors become inline `_error_card`
   divs rather than 500s.
3. `render_page()` (`render/pipeline.py`) registers each query def into a **module-global cache**
   keyed by `(name, connector)` and emits HTML with empty datasets. The page's *effective* query set
   is its inline `:::query` specs **plus** any shared-library queries its components reference by name
   (precedence local → library → unresolved).

**Key architectural decision: queries never run server-side during page render.** The page ships
instantly with no data; the browser then calls `GET /_dashdown/api/data/{query_name}?…&_connector=…`
per query. This is why query defs live in a process-global cache (`_query_def_cache` in
`render/pipeline.py`) — the data API is a *separate* request from the page render and needs to look
the SQL back up. Don't "fix" this into a request-local registry: a prior race-condition bug came from
exactly that.

## SQL parameter substitution & injection

`${param}` placeholders in query SQL are filled by `_substitute_params()` in `render/pipeline.py`.
This is the security-critical function. It is **context-aware**: a placeholder already wrapped in
`'…'` has its value escaped (`'` → `''`) in place; one wrapped in `"…"` (a DAX string literal — how
Fabric queries take filter values) gets `"` → `""`; a placeholder that is the whole content of an
`IN (…)` list (`IN (${region})` — how a **multi-select Dropdown** feeds its value) splits the
comma-separated value into a quoted, *per-item* `'`-escaped literal list (`IN ('East', 'West')`),
capped at `MAX_IN_VALUES`, empty → `IN (NULL)` so the author's `'${x}' = '' OR …` all-guard stays
valid (`_IN_BEFORE_RE`/`_IN_AFTER_RE` detect the context — word-boundaried so `JOIN`/`MAIN` don't
qualify); a bare placeholder gets wrapped in quotes. Every value becomes a quoted string literal (per
item for `IN`), so `${id}` with value `1 OR 1=1` is inert. `test_pipeline.py` (`TestSubstituteParams`
+ `TestInListExpansion`) locks this behavior — extend those tests for any change here. There is no
parameterized-query/bind mechanism; this string substitution is the only defense.

## Components

A component is a Python class subclassing `Component` (`components/base.py`) with a
`render(attrs, ctx, inner) -> str` method, registered via `@register_component("Name")`. Built-ins
live in `components/builtin/` and register on import via `components/__init__.py`. Importantly,
importing `dashdown.components.base` runs that package `__init__`, which is what wires the built-ins
into the registry — adding a new built-in means adding its import line there.

Most visual components render a `<div data-async-component="…" data-config="…">` placeholder and defer
all data handling to the matching JS module. `attrs` values are parsed by `render/attrs.py`:
`key="str"`, `key={query_ref}` → `DataRef`, `key=bare` → coerced to bool/int/float, `key` → `True`.
User projects can drop `.py` files in their `components/` dir; `project.py::_import_user_modules`
auto-imports them at load (recursively — `components/**/*.py`, module name keyed on the relative path
so same-named files in different folders don't collide) so their `@register_component` runs.
`_`-prefixed files are skipped (shared helpers / `__init__.py`).

**Colocated custom components (backend + frontend in one folder).** A custom component can ship its
hydration JS/CSS *next to* its `.py` — `components/Timeline/{Timeline.py,Timeline.js,Timeline.css}`.
`project.py::_discover_component_assets` finds every non-`_` `.js`/`.css` under `components/`;
`server.py` mounts them at `/_dashdown/components/<rel>` via `ComponentStaticFiles` — a
`NoCacheStaticFiles` subclass that serves **only** `.js`/`.css`/`.mjs`/`.map` (the `.py` source is
never web-readable) — and threads the lists to `page.html`, which emits a `<link>` per CSS and a
`<script type="module">` per JS (after `dashdown.js`). `build.py` copies the same assets into
`_dashdown/components/` and injects identical tags, so colocated components work on the dev server, in
`dashdown build` exports, and in embeds. The template also emits a one-line **import map**
(`{"dashdown/": "<importmap_base>_dashdown/static/"}`, only when `component_js` is non-empty) so a
custom module imports framework helpers with a hosting-robust specifier:
`import { fetchQueryData } from "dashdown/core.js"`. **Gotcha:** an import-map *address* must be an
absolute URL or start with `/`, `./`, or `../`, else the browser **nulls** it and the import silently
fails — so `importmap_base` is the asset prefix on the dev server (absolute) but **`./`** in a static
build. `app.js` wires only the *built-in* `data-async-component` types, so a custom module must
**self-init** (scan `[data-async-component="…"]` on `DOMContentLoaded`). Each `.py` is imported
standalone (no package context) — use absolute imports, not relative.

**CSV export** lives on every `<Table>`: a ↓ button in the table header opens a settings dialog
(include-header / delimiter) and downloads the table's *current filtered* result.
`static/components/export.js` is a pure utility (`toCsv(data, opts)` / `exportQueryCsv(queryName,
opts)`); `table.js` wires the button and calls it. It reuses the shared `fetchQueryData` path (live
filter state, same cache as the tables, works in static exports / authed embeds for free) and builds
the CSV client-side (RFC 4180) — there is **no** server-side export endpoint. `export=false` /
`export_filename=` on `<Table>` opt out / rename. The settings dialog is a shared, native
`<dialog class="modal">` helper (`static/components/export_modal.js`), also used by the PDF export
button.

## Frontend (static JS, no bundler)

`templates/page.html` loads `static/dashdown.js` as a single ES module that imports everything else.
Served at `/_dashdown/static/…`. Architecture:

- `core.js` — API client (`fetchQueryData` with in-flight dedup + cache), `parseUrlParams` (the single
  source — other modules import it, don't redefine), record/HTML helpers.
- `store.js` — Alpine.js stores; `filters` is the central reactive state, seeded from URL params.
- `components/*.js` — one module per component, each finding its `[data-async-component]` nodes,
  fetching data, and rendering. Charts use ECharts; UI uses DaisyUI/Tailwind — **all self-hosted** (see
  "Self-hosted assets"), no CDN.
  **All chart types share one path:** every `*Chart` (plus CalendarHeatmap/BoxPlot/Violin/MapChart/
  RadarChart/GaugeChart/HeatmapChart/SankeyChart/CandlestickChart/ThemeRiver/GraphChart/SunburstChart/
  TreeChart/ParallelChart and `<Chart auto />` inference) is a `type` branch in
  `chart.js::buildChartOption` behind the same `data-async-component="chart"` placeholder — add new
  chart types there, not as new modules. A few reuse `x`/`y`/`series` but most carry an extra config
  key threaded by their Python component via `_chart_html(..., extra={…})`. Bar/Line also take a
  `stacked` flag. Two ways to get multiple coloured series: **(a) a second dimension** (`series=`
  splits one metric into a series per value) and **(b) multi-metric** (a comma-separated `y`); the two
  are mutually exclusive. **ComboChart** is the one cartesian type that **mixes** bar and line series
  and carries a **second y-axis**, so it has its own component emitting the same placeholder with
  `bars`/`lines` + `right_axis`. On a **PieChart**, `series=` instead renders **faceted small
  multiples**. A **zero-row result short-circuits the same shared path**: `updateChart` checks
  `isEmptyChartData(records)` and renders `emptyChartOption(config)` — a centered, muted message
  (`empty_message` attr) with the title kept and axes hidden. `pivot.js` is separate
  (`data-async-component="pivot"`): a client-side cross-tab with drag-and-drop axes.
- `legacy.js` — backward-compat path for old `data-dashdown-*` attributes; new work uses the async
  path.

**Self-hosted assets (no CDN).** `templates/page.html` links only local files: the prebuilt
`static/vendor/tailwind.css` (Tailwind base+components+utilities + *all* DaisyUI themes),
`static/vendor/{echarts,alpine}.min.js`, the Inter `@font-face` in `dashdown.css`
(`vendor/fonts/inter.woff2`), the MapChart `world.json` (resolved in `chart.js` via
`new URL("../vendor/world.json", import.meta.url)`), and `vendor/mermaid.min.js` (lazy-loaded by
`mermaid.js`). These live under `dashdown/static/vendor/`, are **committed**, and ship in the wheel
(`package-data` globs `static/**/*` — note the `**`; a flat `static/*` does **not** recurse). They are
regenerated by release-only Node tooling in `tooling/` (`npm install && npm run build`); `pip install`
users never run it. The Tailwind JIT only emits classes it can find, so utility classes a *user's*
dashboard authors beyond the framework's own are covered by an auto-linked per-project
`assets/custom.css`, **not** by widening the safelist. After editing component markup that introduces
a new utility/DaisyUI class the framework hadn't used, rebuild the CSS or it won't be in the bundle.

**Filter reactivity is a single path** (deliberately consolidated): a dropdown writes to
`$store.filters[name]` via Alpine `x-model`; an `Alpine.effect()` syncs that to the URL; data
components re-fetch when filters they reference (`queryUsesFilters`) change. Don't reintroduce parallel
event-listener paths for the same state. The `<Toggle>` boolean filter joins this path with one
wrinkle: filter values are **strings** in the store (so `_substitute_params` and the URL stay uniform),
but Alpine `x-model` on a checkbox binds a *boolean*, so the toggle binds explicitly instead.

**Design tokens.** Spacing/radius/shadow values are `--dashdown-*` custom properties at the top of
`dashdown.css` (single source), and the shipped light/dark themes pin DaisyUI's color vars to the
design palette there. Reference tokens in new CSS rather than hard-coding radii/gaps/colors, and keep
such overrides scoped to `[data-theme="light"|"dark"]` so other DaisyUI themes keep their own look.
ECharts styling (palette, axis tones, transparent canvas) lives in `static/components/echarts_theme.js`
and must stay visually in sync with those surfaces. **Don't reintroduce a global `* { transition }`**
for theme changes: that also dulls every hover/focus into a 300ms fade. The light/dark crossfade is a
small cross-file contract instead — `page.html`'s `__dashdownThemeFlash()` adds a transient
`dashdown-theme-anim` class to `<html>` (on the Alpine `theme` `$watch` + the OS-preference listener)
that a scoped `html.dashdown-theme-anim *` rule in `dashdown.css` animates for ~360ms; a
`prefers-reduced-motion` block neutralizes it (and the rest of the framework's motion). Interactive
states keep their own short transitions.

## Data connectors

`Connector` (`data/base.py`) is the ABC: `query(sql) -> QueryResult(columns, rows)` + `close()`,
registered with `@register_connector("type")`. Connectors are discovered through the
`dashdown.connectors` **entry-point group** (`get_connector_type` in `data/base.py`): the built-ins
declare entry points in `pyproject.toml` and load **lazily** the first time a `sources.yaml` asks for
that type. Third-party connectors ship as separate PyPI packages declaring the same entry-point group;
no core change needed. Connector families:

- **DuckDB-backed** (`csv`, `json`, `parquet`, `duckdb`, `motherduck`, `quack`) run SQL on an embedded
  DuckDB. `CSVConnector` subclasses `DuckDBConnector`, overriding only `_setup()` (its per-file
  tables); `JSONConnector`/`ParquetConnector` are the same shape. `MotherDuckConnector` subclasses it,
  overriding only the connect seam (an `md:<db>` target + threading the `motherduck_token`).
  `QuackConnector` (a remote RPC protocol for DuckDB) does the same but `ATTACH`-es a remote target.
  All inherit **reconnect-on-fatal**: if a query *invalidates* the connection
  (`FatalException`/`InternalException`), `query()` rebuilds the connection (re-running `_setup()` to
  restore views) and retries once; ordinary transient errors are raised as normal retryable failures
  (`_is_fatal_duckdb_error()` draws that line).
- **SQL DB-API** (`postgres`, `mysql`, `mssql`, `snowflake`, `bigquery`, `clickhouse`) share
  `data/dbapi.py` — each is a thin subclass implementing `_connect()`, with lazy connect + driver
  import, JSON-safe value coercion, and one reconnect-and-retry on a dropped connection. `mssql`
  (pyodbc) builds an ODBC connection string from discrete config keys (SQL login, Azure AD service
  principal, managed identity, AD password, or a raw `connection_string`/`url` escape hatch); needs
  Microsoft's ODBC driver on the host. `clickhouse` rides clickhouse-connect's PEP 249 wrapper
  (HTTP protocol; `secure: true` for ClickHouse Cloud).
- **Tabular spreadsheets** (`excel`, `sheets`) share `data/tabular.py`: a subclass returns
  `{table_name: DataFrame}` from `_load_tables()` (lazy, on first query); the base loads each into an
  in-memory DuckDB so spreadsheets answer the same SQL as everything else.
- **REST** (`dax`) targets Microsoft Fabric / Power BI via the REST API + MSAL.
- **Cube** (`cube`) is a thin HTTP client for a Cube deployment: it **stubs `query(sql)`** and exposes
  `load(json_query)` + `meta()` over HTTP+JWT (used by the Cube semantic backend below).

Each backend-specific connector's heavy deps are an **optional extra** (`pip install
'dashdown-md[postgres|mysql|snowflake|bigquery|clickhouse|excel|sheets|dax|cube]'`); a missing-dep load raises a
friendly install hint. `load_connectors()` reads the user's `sources.yaml`, injecting `_project_root`
into each connector's config.

## Routing

`project.py::page_path()` maps URL → `pages/**/*.md`, including dynamic segments: `[id].md` or an
`[id]/` directory captures a path segment as a param (passed to SQL as `${id}`). Matching is ordered
exact-dir → exact-file → dynamic-dir → dynamic-file, and every resolved path is checked to stay under
`pages/` (path-traversal guard). Dynamic `[slug]` pages are excluded from the sidebar nav tree.

## Authentication

`auth.py` provides optional built-in auth, configured via an `auth:` block in `dashdown.yaml` (parsed
into `ProjectConfig.auth` by `parse_auth_config`). Two modes: `basic` (HTTP Basic, browser-friendly)
and `api_key` (a shared secret in a configurable header); default `none`. Secrets support `${ENV_VAR}`
expansion. A single `@app.middleware("http")` guard in `server.py` reads the *live*
`app.state.project.config.auth` (so a config reload picks up changes), exempts `/_dashdown/health`, and
401s everything else when unauthorized. Credentials compare with `secrets.compare_digest`. A malformed
`auth:` block raises in `load_project`, so the server refuses to start open.

## Global date filter

A project-wide date-range control, configured via a `global_filters.date` block in `dashdown.yaml`
(`parse_global_filters_config` → `ProjectConfig.global_date`). It's shown **only on pages whose
effective query set references it** — `render_page` checks whether any effective spec's SQL contains
the `${start_param}`/`${end_param}` placeholders. It reuses the `DateRange` built-in
(`_render_global_date_control`), so it shares the preset math / URL sync / pill styling. Placement is
`embed`-driven: **non-embed** → rendered into the sticky app header; **embed** → injected into
`body_html` as an ordinary filter. Static builds omit it. Queries opt in purely by **convention**: a
query using `${date_start}` / `${date_end}` is filtered; one without them is untouched — there's **no**
backend query rewriting, the control just writes those two keys into `$store.filters` and the existing
`queryUsesFilters` re-fetch path does the rest.

## Real-time streaming

A `:::query` block can opt into live updates with `live` (+ optional `interval=N` seconds): the
WebSocket endpoint `@app.websocket("/_dashdown/ws/data/{query_name}")` in `server.py` streams fresh
results over a **shared poll loop** and pushes a fresh `serialize_result` payload only when its
`payload_digest` (`render/pipeline.py`) changes. **Additive by design** — the stateless data API, the
instant page render, and "queries never run during page render" are all untouched. Polling (every
connector works) is the deliberate MVP.

**Fan-out (`dashdown/streaming.py`).** The poll loop is **not** per-connection — `StreamHub` keeps
**one `_Poller` per (query, connector, params) key** that fans each change out to every subscribed
socket's `asyncio.Queue`, reference-counted so the loop stops when the last subscriber leaves. So N
viewers of the same live query cost one query per interval, not N. Key pieces: `live`/`interval` ride
on `QuerySpec` and are recorded in a separate `_stream_def_cache`; `get_stream_interval()` returns
`None` for non-live, so the WS endpoint refuses to stream anything not in that map. WS auth is
mandatory and separate (Starlette's HTTP middleware does not run for WebSockets, so the endpoint calls
`is_authorized` itself). On the client, `core.js` adds `subscribeQueryData` + `bindLiveQuery`; live
components **skip the one-shot data-API fetch** and let the socket deliver the first payload.

## Embedding

Any page can be served **chrome-less** for embedding in an external site via an auto-resizing iframe.
`dashdown/embed.py` owns the config + crypto; the request-scoping that needs the query registry lives
in `server.py`.

- **Opt-in + deny-by-default.** An `embed:` block (`enabled`, `frame_ancestors`, `secret`,
  `token_ttl`) is parsed by `parse_embed_config`. `frame_headers()` emits
  `Content-Security-Policy: frame-ancestors …` when an allowlist is set, else `X-Frame-Options: DENY` —
  applied to **every** page response.
- **Chrome-less render.** `?_embed` (any value) → `page()` renders with `embed=True`, which the
  `page.html` template uses to omit the header/aside/breadcrumbs. The filter bar lives in `body_html`
  (not the template) so a chrome-less page stays interactive.
- **Signed tokens (when `auth` is on).** A cross-origin iframe can't send Basic/api_key creds, so an
  authed page is embedded with `?_embed=<token>`: an HMAC over the page `path` + the `connector:query`
  pairs that page reads (`sign_embed_token`/`verify_embed_token`, base64url, constant-time compare,
  optional `exp`). The `auth_guard` middleware lets a request through when `_embed_authorizes()`
  validates a token **scoped to that exact resource**.

## Mermaid diagrams

A ` ```mermaid ` fenced block renders as an SVG diagram **client-side, fully offline**.
`highlight_code()` (`render/markdown.py`) special-cases `lang == "mermaid"`: it emits a marker shell
`<pre class="dashdown-code dashdown-mermaid" data-lang="mermaid"><code>…escaped source…</code></pre>`
(kept as `<pre><code>` so it degrades to readable source if JS is off). The client
(`static/components/mermaid.js`) is called from `app.js::init()` **outside** the async-component gate,
self-gates (returns if no `pre[data-lang="mermaid"]`), and **lazy-loads** the bundle only then. Themed
light/dark and re-rendered on toggle. `vendor/mermaid.min.js` is the self-contained IIFE build,
resolved via `new URL("../vendor/mermaid.min.js", import.meta.url)`.

## Shared query library

Authors can define a query **once**, outside any page, in a `queries/` directory and reference it by
name from any page. **Purely additive:** inline `:::query` still works, and the feature *formalizes*
the existing global-by-name contract (`_query_def_cache` keyed by `(name, connector)`).

- **Loader (`dashdown/query_library.py`).** `load_queries(queries_dir)` scans `queries/**/*.{sql,dax}`
  recursively into `{name: QuerySpec}`. `parse_query_file()` reuses
  `render/markdown.py::split_frontmatter` (the same `---` YAML frontmatter + body shape a page has).
  **Name = path under `queries/` with separators as dots** (`finance/mrr.sql` → `finance.mrr`), so a
  namespaced name stays one safe URL/cache-key segment. A path-traversal guard and a uniqueness check
  on the *derived* name (collision → `ValueError`, fail-at-startup).
- **Load + register (`project.py`).** `load_project()` keeps the parsed set on `Project.queries` and
  calls `register_library_queries()`, which registers each into `_query_def_cache`/`_stream_def_cache`
  with **empty** default params — so `/api/data` and the WS stream endpoint resolve library queries
  with **zero** endpoint changes. It first **evicts the prior load's library keys** so a
  renamed/deleted file leaves no stale ghost on a dev reload.
- **Page-driven resolution.** `render_components` records every `data={name}` DataRef into
  `ctx.referenced_queries`; after the scan, a referenced name not satisfied by a page-local `:::query`
  is resolved from the library (**precedence local → library**). The effective specs get registered
  (with the *page's* route params) and emitted into the client `query_defs` (**never the SQL** — it
  stays server-side, looked back up by name).
- **Composition.** A library query can reference another by name with a dbt-style `ref('other')` call,
  compiled into inline CTEs at load time by `dashdown/query_composition.py` (called from `load_project`
  between `load_queries` and `register_library_queries`). Composition runs **before**
  `_substitute_params`, so the one context-aware substitution stays the only injection path. `ref()` is
  matched only in executable SQL (comments and string literals are masked). **SQL connectors only**; a
  `ref()` on `dax`, a cross-connector reference, an unknown reference, a cycle, or an alias collision
  all raise at load.

## Python queries

A query can be defined **as Python** instead of SQL/DAX: a `queries/**/*.py` file with one
`@query`-decorated entry function returning a table. It's the **third body language** in the same query
library machinery — name = dotted path (`queries/ml/churn.py` → `ml.churn`), referenced by name
(`data={ml.churn}`), dev-watched, and snapshotted by `dashdown build`. The engine is the author's
(pandas core; pyarrow/polars theirs). `dashdown/python_query.py` owns the decorator, loader,
normalizer and runner; the *registry* lives in `render/pipeline.py`.

- **The contract.** `from dashdown import query`; `@query(connector=…, cache_ttl=…, live=…,
  interval=…, description=…)` on `def name(params, connect): …`. The **name comes from the file path,
  not the function name**. `params` is the merged filter+route values as a `dict[str,str]`;
  `connect(name, sql, params=None)` runs SQL on any project connector and returns a `QueryResult`. The
  function returns a `pa.Table`/dataset/`RecordBatchReader`, a pandas/Polars `DataFrame`, a
  `QueryResult`, or a **list of dicts** — `normalize_to_query_result()` duck-types all of them (no hard
  pyarrow/polars import).
- **Params are data, never code.** There is **no `_substitute_params` on a Python body** — values
  arrive as a runtime dict, so the `${param}` injection surface *doesn't exist*. Author-built SQL passed
  to `connect(…, params=…)` runs through the one blessed, context-aware substitution.
- **Parallel `_python_def_cache`** (`render/pipeline.py`) mirrors the `_stream_def_cache` precedent —
  don't widen `_query_def_cache`'s `(sql, params, cache_ttl)` tuple. The data API, the stream WS
  endpoint, and the static build all **check `get_python_query_def()` first → run
  `run_python_query(...)` in the threadpool**; absent → the existing SQL path.
- **Trust boundary == custom components.** A `queries/*.py` is author Python imported at load — the
  same trust boundary as a `components/*.py`. Default **on**; a managed host sets
  `python_queries:\n  enabled: false` to refuse semi-trusted code.

## Semantic metric layer

A `semantic/**/*.yml` model defines measures/dimensions/joins **once**, then components reference
*metrics + groupings* (`<BarChart metric={sales.revenue} by={sales.region} />`) instead of named
queries. A reference resolves at render (`resolve_ref` in `components/builtin/_util.py::resolve_semantic`
→ `ctx.semantic_refs`) into a **synthetic `PythonQuerySpec`** (`build_semantic_spec`), so it rides the
*exact* Python-query `_python_def_cache` path — data API, WS poller, static build, cache, and the
"filtered by" badge all resolve it with **no new request paths**. `build_filters` maps live
`$store.filters` → a typed filter list (data, never `${param}`-interpolated). Gated by
`python_queries.enabled`.

**Pluggable backends behind one grammar.** Each backend is a `SemanticBackend` (`semantic_base.py`)
with **two methods** — `introspect(handle, connectors)` and `build_spec(handle, ref, connectors)` —
plus an optional `claims_connector(conn)`. They register into one registry via an eager
`@register_semantic_backend("name")` decorator **and** the `dashdown.semantic_backends` entry-point
group. So a new backend is ~2 methods and inherits the whole downstream. The two built-ins:

- **`backend: ibis`** (default) — `IbisBackend` in `semantic.py`. Bridges a `sources.yaml` connector to
  an Ibis backend (or a boring-semantic-layer `profile:`); compiles to SQL **pushed down** to the
  warehouse. DuckDB/CSV share the live in-process connection; postgres/mysql/snowflake/bigquery open a
  fresh native Ibis connection cached on the connector. Needs `dashdown-md[semantic]`.
- **`backend: cube`** — `CubeBackend` in `semantic_cube.py`, **auto-detected** when the model's
  `connector:` is a `CubeConnector`. `introspect` is config-free (a live `GET /meta` auto-populates the
  catalogue); `build_spec` compiles via `build_cube_query` (a **pure dict**) and drives the connector's
  `load()`. **No injection surface at all** — values are JSON data, so there is *no* `_substitute_params`.

**Time grain (`grain=`).** A chart buckets a time dimension at a chosen grain
(`<LineChart metric={sales.revenue} by={sales.order_date} grain="month" />`). The IR carries a neutral
lowercase token (`GRAIN_TOKENS`) and each backend translates (ibis truncates; cube sets
`timeDimensions[].granularity`). Grain is a *grouping* modifier, not a filter. The control is
`<TimeGrain name="…" default="month">` — sugar over a grain-token `<Dropdown>`; a chart reads it with
`grain={trendGrain}`.

Both backends — and the semantic layer as a whole — are still **preview**.

## Static export, PDF, screenshots, search

- **`dashdown build`** (`build.py::build_site()`) renders the project to a serverless static site,
  reusing the *exact same* render path as the server. Each page's queries are executed once at build
  time and written to `_dashdown/data/<connector>/<query>.json`; the build injects a `#dashdown-build`
  config script, and `core.js`'s `fetchQueryData` reads it and fetches the static JSON instead of the
  data API. URLs resolve via a runtime `<base>`. Filter controls (`is_filter = True`) are stripped.
  Dynamic `[slug]` pages and a failing query are recorded, not aborted on.
- **`dashdown pdf`** (`dashdown/pdf.py`) renders a presentation-quality PDF from the static export via
  headless Chromium (the `dashdown-md[pdf]` extra: Playwright + pypdf). The static site draws charts
  client-side, so a real browser must rasterize it. The **chart-render handshake**
  (`static/components/print.js` → `window.__dashdownPrintReady`) waits for every data component's first
  `dashdown:data-loaded` and each chart's `<canvas>` before printing; it's time-boxed. The in-app
  "Export PDF" button hits `GET /_dashdown/api/pdf` (same engine).
- **`dashdown screenshot`** (`dashdown/screenshot.py`) captures a page to a PNG **and reports whether
  its charts actually drew** (the gap `dashdown check` can't cover). Reuses `pdf.py`'s headless
  plumbing; sets a separate `window.__dashdownCapture` flag that arms the same readiness signal without
  the print dressing. A blank/errored chart makes the CLI exit non-zero — a CI/agent gate.
- **Full-text search** (`dashdown/search.py`) builds one index entry per page; ranking is **entirely
  client-side**. Served live (`GET /_dashdown/api/search-index`) and baked into the static build. A
  built-in `<SiteSearch />` box sits in the app header / mobile menu; it is `is_filter = False` so it
  **survives** static builds.

## Scaffolded coding-agent guide

`dashdown new` drops a **tool-agnostic** authoring guide into every project so any coding agent
(Claude Code, Cursor, Codex, …) knows the platform — **progressive disclosure**: a small map
(`AGENTS.md`) + per-topic shards (`.references/<topic>.md`), so an agent reads the map first and loads
only the one shard a task needs. The shards install as a **hidden `.references/`** to keep the project
root clean; like `.claude`, they ship dotless as `scaffold/references/` in the wheel (setuptools' `**`
glob skips dotfiles) and are renamed to `.references/` on install (`cli.py::_agent_doc_files`), so the
map/skill links point at `.references/` while `build_outputs()` keys and the freshness test stay
`references/`.

- These artifacts are **generated** from the `docs/` project by release-only tooling
  `tooling/gen-agent-docs.py` (like `tooling/build-assets.mjs` regenerates vendored assets). The core
  is a pure `build_outputs() -> {relpath: content}` seam; `main()` writes them and evicts stale shards.
  **Re-run it after editing `docs/`** or the shipped guide goes stale —
  `tests/test_scaffold.py::test_agent_docs_are_freshly_generated` fails on drift.
- The generated tree lives in the package under `dashdown/scaffold/` (`AGENTS.md`, `references/*.md`
  — dotless in the wheel, installed as `.references/`, and a Claude Code skill under `scaffold/claude/`). The same generator emits `docs/llms.txt` (the
  network-fetchable map) and `docs/llms-full.txt` (the monolith); `build.py` copies any root-level
  `llms.txt`/`llms-full.txt` into the static-build root.
- **Content vs. wrapper (`dashdown/agent_targets.py`).** The `AGENTS.md` + `.references/` content is
  **always** installed (it's tool-agnostic); only the thin per-tool **wrapper** that routes into it
  varies by tool. Each tool is an `AgentTarget` (`name`, `emit(scaffold_src) -> [EmittedFile]`,
  `detect(root)`) in a small registry — same shape as the connector/component registries. Built-ins:
  `claude` (the real skill, copied `scaffold/claude/` → `.claude/` — the dotted rename happens here,
  on install, since setuptools' `**` glob skips the dot) and `mistral` (the **same** skill tree under
  `.vibe/`, which mirrors `.claude`'s `skills/<name>/SKILL.md` layout — both share `_skill_tree`), plus
  pointer-stub wrappers `cursor` (`.cursor/rules/dashdown.mdc`), `gemini` (`GEMINI.md`), and `copilot`
  (`.github/copilot-instructions.md`). Codex et al. need no wrapper — they read the baseline
  `AGENTS.md`. Add a tool = one `register_agent_target(...)`.
- **Target selection (`_resolve_targets` in `cli.py`).** Precedence: explicit `--target a,b` → the
  project's `dashdown.yaml` `agents:` list → auto-detected tools (a marker dir already present) →
  `["claude"]`. `dashdown new --target …` can't detect (fresh dir), so it takes the flag (default
  `claude`) **and records it** into the scaffolded `dashdown.yaml` `agents:` — which then becomes the
  default for every later `dashdown skill` in that project.
- **`dashdown skill [--refresh] [--target …]`** installs/updates the bundled guide into an existing
  project (resolving targets as above), so a project scaffolded on an older release pulls the current
  one without re-scaffolding.

## Introspected catalog (`dashdown components`)

`dashdown/catalog.py::build_catalog()` produces a **dense, drift-proof** reference of every registered
component (name, summary, `is_filter`, attribute list) and connector type (config keys, install extra,
summary) by **introspecting the registries**, not hand-writing docs. Component attrs are recovered by
AST-analyzing each component's `render` method (every `attr_str(attrs, "x")` / `attrs.get("x")` /
`attrs["x"]` / `"x" in attrs` read), *following* the shared `_chart_html` helper and the `_util`
accessor/helper functions — so a new attr appears with no hand edit and can't drift. Connector config
keys come from the connector's `self.config.get("k")` reads (AST-parsed from source, not imported).
`build_catalog()` is the single function the CLI and any future wrapper share.

## Conventions

- Python targets 3.10+; every module uses `from __future__ import annotations` and modern type hints
  (`X | None`).
- The dev server (`dashdown serve`) watches `pages/`, `components/`, `data/`, `assets/`, `queries/`,
  `dashdown.yaml`, `sources.yaml`. Page edits live-reload via SSE (`/_dashdown/reload`); config /
  sources / component / query-library changes trigger a full `reload_project`.
- Add tests for any change to the render pipeline, SQL parameter substitution, or a connector.
- Keep new code in the style of the code around it.
