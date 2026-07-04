<!-- AUTO-GENERATED from docs/pages/ by tooling/gen-agent-docs.py — do not edit. -->
<!-- Topic: cli. Regenerate with: python tooling/gen-agent-docs.py -->

<!-- source: docs/pages/cli.md -->

# CLI reference

Everything Dashdown does from the terminal goes through the `dashdown` command.
Each subcommand takes a **project directory** (default `.`) and prints its result
to stdout — diagnostics and row counts go to **stderr**, so `json`/`csv` output
pipes cleanly. This page is the quick reference; the deeper guides linked below
cover each feature in full.

```bash
dashdown --help            # list every command
dashdown <command> --help  # options for one command
```

| Command | What it does |
| --- | --- |
| [`serve`](#serve) | Run the project locally with live-reload |
| [`new`](#new) | Scaffold a new project |
| [`check`](#check) | Validate the project without serving or running queries |
| [`connectors`](#connectors) | List (and optionally probe) the configured connectors |
| [`query`](#query) | Run raw SQL/DAX against a connector |
| [`metric`](#metric) | Query the semantic layer by metric + grouping |
| [`build`](#build) | Export a static site (HTML + pre-rendered data) |
| [`pdf`](#pdf) | Export a presentation PDF |
| [`screenshot`](#screenshot) | Capture a page to a PNG and verify its charts drew |

## serve

Serve the project on a local web server, watching files and live-reloading the
browser as you edit pages, components, queries, config, and data.

```bash
dashdown serve .                       # http://127.0.0.1:8000
dashdown serve my-dashboard --port 8001
dashdown serve . --host 0.0.0.0 --no-watch
```

| Option | Default | Notes |
| --- | --- | --- |
| `--host` | `127.0.0.1` | Bind address. Use `0.0.0.0` to expose on your LAN. |
| `--port` | `8000` | Bind port. |
| `--no-watch` | off | Disable the file watcher (no live-reload). |

The watcher reloads on changes under `pages/`, `components/`, `data/`, `assets/`,
`queries/`, `semantic/`, and to `dashdown.yaml` / `sources.yaml`. Page edits
hot-reload the browser; config, sources, components, queries, and data trigger a
full project reload (connectors are rebuilt). See [Getting started](getting-started).

## new

Scaffold a new project directory — a `dashdown.yaml`, `sources.yaml`, a sample
page and data, and a tool-agnostic `AGENTS.md` authoring guide (plus a per-tool skill)
so a coding agent opening the project knows the platform.

```bash
dashdown new my-dashboard
cd my-dashboard
dashdown serve .
```

`--target` picks which coding agents to set up a wrapper for (default `claude`); the
choice is recorded in `dashdown.yaml` as `agents:`, so later `dashdown skill` runs keep
them in sync. `AGENTS.md` + `.references/` are always installed regardless.

```bash
dashdown new my-dashboard --target claude,cursor   # set up both wrappers
```

See [Coding agents](/ai/coding-agents) for the supported tools.

## skill

Install or update the bundled coding-agent guide (`AGENTS.md` + the `.references/`
shards + a per-tool authoring skill) in an **existing** project. The guide is
versioned with the framework, so a project scaffolded on an older release pulls the
current one without re-scaffolding. See [Coding agents](/ai/coding-agents) for the full
story.

```bash
dashdown skill                 # fill in anything missing (keeps your local edits)
dashdown skill --refresh       # overwrite to this version's guide (prunes stale shards)
dashdown skill -p ./dashboard  # target another project directory
dashdown skill --target cursor # also/instead install the Cursor wrapper
```

Which tools it installs for resolves by precedence: an explicit `--target a,b` → the
project's `dashdown.yaml` `agents:` list → tools it auto-detects (a marker dir like
`.claude/` or `.cursor/` already present) → `claude`.

## check

Validate the project **without serving it or running any queries**. It loads
`dashdown.yaml`/`sources.yaml`, the query library, and the semantic models
(surfacing any config or parse error), then renders every page — queries are
never executed during render — and reports component/render errors (an unknown
tag, a bad attribute). Exits non-zero if anything is wrong, so it's the fast
edit→validate loop for authors and coding agents.

```bash
dashdown check
dashdown check -p my-dashboard
```

| Option | Default | Notes |
| --- | --- | --- |
| `-p, --project` | `.` | Project directory. |

A clean project prints `✓ project is valid`; otherwise each problem is listed as
`✗ <page>: <message>` on stderr and the command exits 1.

## connectors

List the connectors configured in `sources.yaml` with their types — a quick way
to see what data sources a project has before writing SQL against them.

```bash
dashdown connectors
dashdown connectors --test -p my-dashboard
```

| Option | Default | Notes |
| --- | --- | --- |
| `-p, --project` | `.` | Project directory. |
| `--test` | off | Probe each connector with a trivial `SELECT 1` to confirm it connects. A `dax` connector takes DAX, not SQL, so its probe is skipped. |

With `--test`, each connector is marked `✓ reachable` or `✗ <error>`. See
[Connectors](connectors/).

## query

Run a SQL statement (or DAX, against a `dax` connector) verbatim against any
connector in `sources.yaml` and print the rows — without opening the app. The
quickest way to **test that a connector connects** and **inspect real data** while
authoring; an agent uses it the same way.

```bash
dashdown query "SELECT * FROM sales LIMIT 5"                       # runs on the default source
dashdown query "SELECT count(*) FROM orders" -c warehouse -f json
dashdown query "SELECT region, sum(amount) FROM sales GROUP BY region" -p .
```

| Option | Default | Notes |
| --- | --- | --- |
| `-p, --project` | `.` | Project directory. |
| `-c, --connector` | the default source | Connector from `sources.yaml`. An unknown name lists the configured ones. |
| `-f, --format` | `table` | `table`, `json` (`{columns, rows}`), or `csv`. |
| `--max-rows` | `50` | Cap rows printed (`0` = all). Total count goes to stderr. |
| `--tables` | off | List the connector's tables/views (`table`/`schema`/`type`) and exit — instead of a SQL argument. |
| `--schema <table>` | — | Describe one table's columns (`column`/`type`/`nullable`) and exit. |

This runs **raw** SQL — there is no `${param}` substitution here (that is a
page/query-library concern). A failing query prints the connector's error and
exits non-zero. See [Connectors](connectors/).

### Schema introspection (`--tables` / `--schema`)

Instead of hand-writing a `SELECT * … LIMIT 0` (and remembering each warehouse's
`information_schema` dialect), ask the connector directly:

```bash
dashdown query --tables                          # what tables/views exist?
dashdown query --schema sales -f json            # what columns does `sales` have?
```

Each connector knows how to answer in its own dialect, so the same two commands
work everywhere: SQL warehouses and DuckDB/CSV/Excel/Sheets answer via
`information_schema`; **BigQuery** qualifies it from a `dataset:`/`location:` in
`sources.yaml`; **DAX** (Fabric/Power BI) reads the model's `INFO.VIEW.*` metadata;
**Cube** lists its cubes and their measures/dimensions from `/meta`. A connector
that can't introspect prints a one-line hint and exits non-zero. The `--schema`
table name is matched as an escaped literal, never spliced as SQL.

For a **semantic model**'s metrics and dimensions (not a connector's physical
tables), use [`metric --list`](#metric) instead.

## metric

Query the [semantic layer](semantic-layer) by **metric + grouping** — the same
`metric={sales.revenue} by={sales.region}` grammar your chart components use,
compiled and pushed down to the warehouse. The semantic-layer counterpart of
`query`: where `query` probes a connector with raw SQL, `metric` probes a *model*
by name. Start with `--list` when you don't know the model.

```bash
dashdown metric --list                                  # models + their metrics/dimensions
dashdown metric "sales.revenue" --by sales.region
dashdown metric "sales.revenue,sales.orders" -b sales.order_date -g month -f json
dashdown metric "sales.revenue" -b sales.region --param region=East
```

| Option | Default | Notes |
| --- | --- | --- |
| `-l, --list` | off | List every model with its measures and dimensions (the time dimension is tagged `[time]`), then exit. |
| `-b, --by` | — | Group by a dimension (`model.dim` or bare `dim`). |
| `-s, --series` | — | Split into a coloured series by a *second* dimension. |
| `-g, --grain` | — | Bucket a time `--by`/`--series` (`second`…`year`). |
| `--param key=value` | — | Repeatable filter: a dimension name, or `date_start`/`date_end` for the model's time dimension. Passed as **data**, never substituted into SQL. |
| `-p, --project` | `.` | Project directory. |
| `-f, --format` | `table` | `table`, `json`, or `csv`. |
| `--max-rows` | `50` | Cap rows printed (`0` = all). |

Needs the `dashdown-md[semantic]` extra. A bad model/metric/dimension prints the
resolution error (with the known names) and exits non-zero.

## build

Export the project to a **static site** — pre-rendered HTML plus each page's query
data as JSON — that you can host on any static file server, with no Python backend.

```bash
dashdown build .                       # → <project>/.dist
dashdown build my-dashboard -o dist
python -m http.server -d .dist         # preview the export
```

| Option | Default | Notes |
| --- | --- | --- |
| `-p, --project` | `.` | Project directory (positional). |
| `-o, --out` | `<project>/.dist` | Output directory. |

Each query runs once at build time with default parameters; filter controls are
stripped (a fixed snapshot can't re-query). Dynamic `[slug]` pages are skipped, and
a failing query is recorded but doesn't abort the build. See [Exporting](exporting).

## pdf

Export the project to a **presentation PDF**, rendered from the static export with
headless Chromium so charts draw exactly as in the live app. By default the whole
project is merged into one deck.

```bash
dashdown pdf .                                 # combined deck → <project>/.pdf
dashdown pdf . --page /sales --orientation landscape
dashdown pdf . --separate --format Letter
```

| Option | Default | Notes |
| --- | --- | --- |
| `-o, --out` | `<project>/.pdf` | Output directory. |
| `--page` | all | Limit to one page URL (repeatable), e.g. `--page /sales`. |
| `--dist` | — | Reuse an existing static build instead of rebuilding. |
| `--separate` | off | One PDF per page instead of a combined deck. |
| `--orientation` | `portrait` | `portrait` or `landscape`. |
| `--format` | `A4` | Page size: `A4`, `Letter`, `Legal`, `A3`, … |
| `--scale` | `1.0` | Render scale passed to Chromium (0.1–2.0). |

Requires the `pdf` extra and a one-time browser download:

```bash
pip install 'dashdown-md[pdf]'
playwright install chromium
```

See [Exporting](exporting).

## screenshot

Capture a page to a **PNG** and report whether its charts actually drew. Charts
paint **client-side** with ECharts, so [`check`](#check) confirms a page *renders*
but not that a chart *painted*; `screenshot` closes that gap. It drives headless
Chromium (the same engine as [`pdf`](#pdf)) over the **interactive** page — no
print cover, no reflow — waits for the chart-render handshake, saves the image,
and prints a verdict: how many chart canvases drew vs stayed blank, plus any
browser console errors.

```bash
dashdown screenshot /sales                          # → <project>/.shots/sales.png
dashdown screenshot / --full-page -o home.png
dashdown screenshot /sales --server http://127.0.0.1:8000   # capture a running serve
```

| Argument / Option | Default | Notes |
| --- | --- | --- |
| `page` | `/` | Page URL to capture (positional), e.g. `/sales`. |
| `-o, --out` | `<project>/.shots/<page>.png` | Output PNG path. |
| `-p, --project` | `.` | Project directory. |
| `--dist` | — | Reuse an existing static build instead of rebuilding. |
| `--server` | — | Capture a page from an already-running `dashdown serve` instead of building. |
| `--full-page` | off | Capture the full scroll height, not just the viewport. |
| `--width` / `--height` | `1280` / `800` | Viewport size in px (desktop, sidebar-visible layout). |

By default it builds and serves a static export — **scoped to just the page you
name**, so unrelated pages' queries (a slow or flaky external source elsewhere in
the project) never run — then captures it. It **exits non-zero** when a chart
fails to draw, so an agent or CI can use it as a visual gate. Requires the `pdf`
extra and the Chromium download (same as [`pdf`](#pdf)):

```bash
pip install 'dashdown-md[pdf]'
playwright install chromium
```

See [Exporting](exporting).
