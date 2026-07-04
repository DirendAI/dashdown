# Dashdown — authoring guide for coding agents

This file is auto-generated from the Dashdown documentation and bundled into every
project scaffolded with `dashdown new`. It is **tool-agnostic**: any coding agent
that reads `AGENTS.md` (Claude Code, Cursor, Codex, …) can use it to help author this
dashboard. Do not edit by hand — regenerate with `python tooling/gen-agent-docs.py`
against the `docs/` project.

Dashdown renders Markdown files (with embedded SQL and `<Component />` tags) as
interactive analytics dashboards: no JavaScript to write, no frontend toolchain. You write
`.md` under `pages/`, point the CLI at the folder (`dashdown serve .`), and get a
live dashboard.

## How to use this guide

This is a **map**, not the whole manual. Skim the cheat-sheet below, then open **only the
one reference shard** your task needs (see the index) — each shard under `.references/` is the
full, flattened docs for one topic. Don't read every shard; that's the token cost this
structure exists to avoid.

> **Concepts from the references, facts from the CLI.** Don't guess a component's attributes
> or a connector's config keys — ask the tool. `dashdown components` prints a dense,
> introspected catalog (every component + its attrs, every connector + its config keys);
> `dashdown check` tells you if the project still renders; `dashdown query` shows real data.
> These answer factual lookups far cheaper than re-reading prose. See "The CLI loop" below.

---

## Cheat-sheet

A **page** is Markdown under `pages/**/*.md`: prose + fenced query blocks + `<Component />`
tags. Queries are collected at render and run **in the browser** — never server-side at
render time, so a page ships instantly and fetches its data after.

### A page is a query plus components

````markdown
```sql sales
SELECT month, region, SUM(amount) AS amount
FROM orders GROUP BY month, region ORDER BY month
```

<LineChart data={sales} x="month" y="amount" series="region" title="Sales" />
<Table data={sales} />
````

- ` ```sql <name> [connector=…] [ttl=60] [live] [interval=5] ` — the first word after the
  language is the query **name**; `connector` is a key in `sources.yaml` (omit it to use
  the project's default source). The SQL is collected, not run at render. A plain
  ` ```sql ` fence with nothing after the language is an ordinary display-only code
  sample.
- A query can instead live once in `queries/<name>.sql` (or `.py` for Python) and be
  referenced by name from any page — see `.references/queries.md`.
- `data={query_name}` wires a component to a result; `column="col"` picks one column.

### Parameters & filters (the security-critical bit)

- `${param}` in SQL is filled from filter/route values. It is **always** substituted as a
  quoted string literal (context-aware `'`→`''` / `"`→`""` / `IN (…)` expansion), so a value
  like `1 OR 1=1` is inert. **Never** build SQL by string-concatenating a value yourself.
- Filter controls write those params: `<Dropdown name="region" data={q} column="region" />`,
  `<Search name="q" />`, `<DateRange />`, `<Toggle name="active" />`. The project-wide date
  filter uses `${date_start}` / `${date_end}` by convention.

### Most-used components (run `dashdown components` for the full attr list)

- **Charts** share `data={} x="" y="" [series=""] [title=""]`: `<LineChart>` `<BarChart>`
  `<PieChart>` `<ScatterChart>` (+ box plot, heatmap, sankey, gauge, map, radar, treemap,
  funnel, … — all in `.references/components.md`). Multiple series: `y="a,b"` **or** `series=`.
- `<Counter data={q} column="amount" label="Revenue" />` — one big KPI number.
- `<Value>` — an inline metric. `<Table data={q} />` — sortable, CSV-exportable grid.
- `<Grid cols=2>…</Grid>` — lay widgets side by side.
- With a `semantic/` model, components can take **metrics** instead of a query:
  `<BarChart metric={sales.revenue} by={sales.region} />` — see `.references/semantic-layer.md`.

---

## Reference index

Open the one shard your task needs:

- [Catalog](.references/catalog.md) — every component's attributes + every connector's config keys, introspected from the registries (the `dashdown components` data; **facts, not prose**)
- [Getting started](.references/getting-started.md) — Dashdown is published to PyPI as the `dashdown-md` package, with a CLI entry point named `dashdown`
- [Installation](.references/installation.md) — Dashdown is published to PyPI as **`dashdown-md`**, and the CLI it installs is the command **`dashdown`**
- [Examples](.references/examples.md) — Real, end-to-end dashboards built with Dashdown
- [Writing pages](.references/pages.md) — Every file under `pages/` is a route
- [Configuration](.references/configuration.md) — Every project has a `dashdown.yaml` at its root — the single config file for the whole dashboard
- [Connectors](.references/connectors.md) — Connectors are declared in `sources.yaml` and loaded **lazily** the first time a query asks for that type
- [Queries](.references/queries.md) — SQL lives either in the shared **query library** under `queries/` (the recommended default) or, for a quick one-off, inline in a named ` ```sql ` block on a…
- [Python queries](.references/python-queries.md) — Some questions are awkward — or impossible — in a single SQL statement: a **forecast**, an ML score, a **cross-connector join**, an external-API pull, a…
- [Semantic layer](.references/semantic-layer.md) — Define your metrics and dimensions **once**, then reference them straight from a component — no per-chart SQL, no copy-pasted queries:
- [Real-time data](.references/realtime.md) — A query opts into **live streaming** with the `live` attribute: `interval=N` sets the poll cadence in seconds (default `5`, floored to `1`)
- [Components](.references/components.md) — A component is a PascalCase tag you drop into a page
- [Filters & parameters](.references/filters.md) — Filters write into a central reactive store; any query that references the filter's name with `${...}` re-runs when it changes
- [Formatting](.references/formatting.md) — Tables, counters, values and chart axes all render numbers and dates through **one** formatter, so `63712.895` becomes `$63,712.90` the same way everywhere
- [Theming & styling](.references/theming.md) — Dashdown ships a polished light/dark theme out of the box, but every project can override the look — colours, radii, spacing, chrome, typography — with **one…
- [Detail pages](.references/detail-pages.md) — A **detail page** — a "drill-down" or sub page — is a single template that serves a whole collection: one file renders the focused view for *every* record,…
- [AI](.references/ai.md) — Dashdown is built for AI in **two directions** — the LLM that helps your *readers* understand a dashboard, and the coding agent that helps *you* build one
- [Full-text search](.references/search.md) — `<SiteSearch />` is a built-in component that searches **every page** of the project
- [Exporting](.references/exporting.md) — Renders the project to a serverless static site through the *exact same* render path as the live server
- [Extending Dashdown](.references/extending.md) — Dashdown has two extension points, both plain Python: **custom components** and **custom connectors**
- [CLI reference](.references/cli.md) — Everything Dashdown does from the terminal goes through the `dashdown` command
- [Telemetry & privacy](.references/telemetry.md) — Dashdown is pip-installed and self-hosted, with no accounts and no server in the loop

---

## The CLI loop — verify your work, don't guess

These answer factual questions and confirm a change cheaper than re-reading docs. After
editing a page, run `check`; before wiring a connector, `query` it.

```bash
dashdown check                       # config loads + every page renders? (queries never run)
dashdown connectors --test           # each connector reachable? (probes SELECT 1)
dashdown query "SELECT * FROM t LIMIT 5"   # real data / schema (-c <name> for a non-default source)
dashdown components                  # dense, introspected attr catalog for every component
dashdown components --connectors     # config keys + install extra per connector type
dashdown metric --list               # semantic metrics & dimensions, if a semantic/ model exists
dashdown serve .                     # run the dev server with live reload (http://127.0.0.1:8000)
dashdown build . --out dist          # static export; dashdown pdf .  → presentation PDF
dashdown screenshot /page            # PNG + verdict: did the chart canvases draw? (needs [pdf])
```

Typical loop: **read** the relevant `.references/<topic>.md` for the concept → **edit** the
page/query/config → **`dashdown check`** it renders → **`dashdown query`/`connectors --test`**
the data is real → **`dashdown serve`** to see it. (Charts draw client-side, so `check`
confirms render, not paint — **`dashdown screenshot <page>`** captures a PNG and reports whether
the chart canvases actually drew, exiting non-zero if any failed.)
