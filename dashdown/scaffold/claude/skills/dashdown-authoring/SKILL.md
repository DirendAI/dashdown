---
name: dashdown-authoring
description: Author this Dashdown analytics dashboard — pages, embedded SQL queries, and <Component /> tags (charts, tables, counters, filters), plus connectors, theming, semantic metrics, and the dashdown CLI. Use whenever editing .md pages, queries/, semantic/, sources.yaml, dashdown.yaml, or components/ in this project.
---

# Authoring this Dashdown dashboard

This project is a **Dashdown** dashboard: Markdown files under `pages/` with embedded SQL
(fenced ```` ```sql <name> ```` query blocks) and `<Component />` tags render to an interactive analytics app — no
JavaScript to write, no frontend toolchain.

## Start here — don't read everything

The guide is **sharded for cheap reading**. Load only what the task needs:

1. **[`AGENTS.md`](../../../AGENTS.md)** (project root) — the *map*: a one-screen cheat-sheet
   (fenced ```` ```sql <name> ```` query / `${param}` / component syntax) + an index of per-topic references. Skim it first.
2. **`.references/<topic>.md`** (project root, linked below) — the full docs for one topic. Open the
   **one** shard your task needs, not the whole set.
3. **The `dashdown` CLI** — for *facts* (a component's attrs, a connector's keys, real data),
   prefer the CLI over re-reading prose. **Concepts from references, facts from the CLI.** With no
   shell, [`references/catalog.md`](../../../.references/catalog.md) is the file-readable form of
   `dashdown components` (every component's attrs + every connector's config keys).

## Decision tree — what to read, how to verify

| You're editing… | Read | Verify with |
|---|---|---|
| A page / chart / table / counter | [components](../../../.references/components.md) (or `dashdown components`) | `dashdown check`, then `dashdown screenshot <page>` (did it draw?) |
| `sources.yaml` (a connector) | [connectors](../../../.references/connectors.md) (or `dashdown components --connectors`) | `dashdown connectors --test` |
| A shared query in `queries/*.sql` | [queries](../../../.references/queries.md) | `dashdown query "…" -c <conn>` |
| A query as Python (`queries/*.py`) | [python-queries](../../../.references/python-queries.md) | `dashdown check` |
| A `semantic/*.yml` metric model | [semantic-layer](../../../.references/semantic-layer.md) | `dashdown metric --list` |
| A dropdown / search / date filter | [filters](../../../.references/filters.md) | `dashdown serve .` (interact) |
| Number / date display | [formatting](../../../.references/formatting.md) | `dashdown serve .` |
| `dashdown.yaml` (theme/search/branding) | [configuration](../../../.references/configuration.md) (+ [theming](../../../.references/theming.md)) | `dashdown check` |
| A custom component or connector | [extending](../../../.references/extending.md) | `dashdown check` |
| Static export / PDF / CSV | [exporting](../../../.references/exporting.md) | `dashdown build . --out dist` |

## Task playbooks

**Add a chart of a query.** Write a fenced query block — ```` ```sql q ```` … ```` ``` ```` (first word
after the language = name; add `connector=` only for a non-default source) — or reuse a
`queries/*.sql`, then `<LineChart data={q} x="…" y="…" [series="…"] title="…" />`. Run
`dashdown components` for the exact attrs; `dashdown check` to confirm it renders.

**Add a connector.** Add a block to `sources.yaml` (`name:` → `type: postgres` + keys). Get the
required keys + install extra from `dashdown components --connectors`. Install the extra, then
`dashdown connectors --test` to confirm it connects before authoring queries against it.

**Write a shared query.** Drop a `.sql` (or `.py`) under `queries/`; the name is its path with
`/`→`.` (`queries/finance/mrr.sql` → `finance.mrr`). Reference it from any page as `data={finance.mrr}`.
Test the SQL with `dashdown query "…" -c <conn>`. Details: [queries](../../../.references/queries.md).

**Define a metric.** Add a `semantic/*.yml` model (measures + dimensions), then use
`<BarChart metric={model.measure} by={model.dimension} />` — no per-chart SQL. List what exists
with `dashdown metric --list`; query one with `dashdown metric model.measure --by model.dim`.

**Add a filter.** Place `<Dropdown name="region" data={q} column="region" />` (or `<Search>`,
`<DateRange>`, `<Toggle>`) and reference `${region}` in the query SQL. `${param}` is auto-escaped —
never hand-concatenate values. The global date filter uses `${date_start}`/`${date_end}`.

**Debug "no data".** `dashdown check` (does the page render? unknown tag / bad attr?) →
`dashdown connectors --test` (is the connector reachable?) → `dashdown query --tables -c <conn>` /
`dashdown query --schema <table> -c <conn>` (does the table/column exist + spelled right?) →
`dashdown query "<the SQL>" -c <conn>` (does the SQL return rows with the right column names?).
Confirm `data={name}` matches the query's name (the first word after ```` ```sql ````).

**Verify your work.** Charts draw **client-side**, so `dashdown check` proves a page *renders*,
not that a chart *painted*. Full loop: `dashdown check` (renders, no bad tags/attrs?) →
`dashdown connectors --test` (data reachable?) → `dashdown screenshot <page>` (saves a PNG and
reports `N/M chart(s) drew`, exits non-zero if any stayed blank or errored — your visual gate).

**Ship a build.** `dashdown build . --out dist` (static export — queries run once at build) or
`dashdown pdf .` (presentation PDF; needs the `[pdf]` extra). See [exporting](../../../.references/exporting.md).

## A complete page, end to end (copy this shape)

A polished page: a filter bar on top, a KPI row, sections of charts each with its own `title=`, then a
table. Controls marked `bar` **collect into the page's filter bar automatically — so there is no
"Filters" heading to add**. Queries live in `queries/*.sql` (referenced by name) or inline fenced ```` ```sql <name> ```` blocks
at the top; either way the query text renders nothing itself.

````markdown
---
title: Sales
---

<DateRange name="period" start_param="date_start" end_param="date_end" bar />
<Dropdown name="region" data={regions} column="region" label="Region" bar />

## Key metrics

<Grid cols=3>
<Counter data={kpis} column="revenue" format="currency" currency="USD" decimals=0 label="Revenue" />
<Counter data={kpis} column="orders"  format="number"   decimals=0 label="Orders" />
<Counter data={kpis} column="aov"     format="currency" currency="USD" decimals=2 label="Avg order" />
</Grid>

## Revenue over time

<LineChart data={revenue_by_month} x="month" y="revenue" title="Monthly revenue" format="currency" />

## By region

<Grid cols=2>
<BarChart data={by_region} x="region" y="revenue" title="Revenue by region" format="currency" />
<PieChart data={by_region} x="region" y="revenue" title="Share by region" />
</Grid>

<Table data={recent_orders} title="Recent orders" format="amount=currency, ordered_at=date" />
````

Make each filtered query guard its filter so "no selection" means "all":
`WHERE ('${region}' = '' OR region = '${region}') AND ('${date_start}' = '' OR day >= '${date_start}')`.

## Gotchas that cost iterations (read this first)

The mistakes coding agents make most on Dashdown — avoid them up front:

- **A filter's `name=` IS the `${param}` — not the column.** `<Dropdown name="region" column="region">`
  writes `${region}`; the control's `name` must exactly equal the placeholder your SQL guards on. The
  `column=` only says which column to list values from — it does **not** name the param. So
  `<Dropdown name="weather_filter" column="weather">` writes `${weather_filter}`, and the SQL must read
  `('${weather_filter}' = '' OR weather = '${weather_filter}')` — **not** `${weather}`. A name/param
  mismatch makes the filter silently do nothing (the page still renders, so `check` won't catch it).
- **`format=` is an enum, not a format string.** It takes one of `number | currency | percent | date |
  datetime`, tuned by separate attrs `decimals=`, `prefix=`, `suffix=`, `currency=`, `date_format=`,
  `locale=`. It is **not** a Python/printf pattern — `format="{:,.1f}°C"` is silently ignored and the raw
  number shows. For a KPI in °C or mm, write the unit as a `suffix`:
  `<Counter data={k} column="avg_high" format="number" decimals=1 suffix="°C" label="Avg high" />`.
  Project-wide defaults go in a **`format:`** block in `dashdown.yaml` (`locale` / `currency` /
  `date_format`) — there is **no** `theme:` / `theme.formats` config. See [formatting](../../../.references/formatting.md).
- **Default to NO unit suffix — never guess one.** A raw numeric column (`wind`, `speed`, `pressure`, a
  count, an amount) carries whatever unit the source uses, and the CSV almost never states it — so your
  guess is frequently wrong. **Concretely: a weather `wind` column is m/s, not km/h** (labeling it `km/h`
  overstates it ~3.6×); a `pressure` is often hPa; money may not be USD. Unless the unit is written in the
  brief or data, or you converted it yourself in SQL (`ROUND(MAX(wind) * 3.6, 1) AS wind_kmh`), leave the
  `suffix` **off** (`label="Max wind"`, not `suffix="km/h"`). A `suffix` is only a label — it never scales
  the value (same for `$`/`%`).
- **Attribute names are exact-match — a wrong one is silently ignored, not an error.** A misspelled or
  non-existent attr is dropped with no warning, and `dashdown check` won't catch it: `page_size` (the real
  attr is **`page-size`**), or `include_all` / `all_label` on a `<Dropdown>` (those are `<ButtonGroup>`'s), or
  `series="A,B"` used as a *rename* for a multi-metric `y` (it isn't — `series` groups by a column). Confirm
  every attr against `dashdown components` / [catalog](../../../.references/catalog.md). (`<Dropdown>` already
  emits an "All" option for single-select; empty value = all — you don't add one.)
- **No Markdown headings or prose inside a `<Grid>` (or any component tag).** A `### Title` sitting
  between `<Grid …>` and its child components renders as **literal text**, not a heading. Put the section
  heading on its own line *above* the grid, give each chart its own `title="…"`, and keep a grid's children
  to components — one per line, blank-line-separated.
- **Query definition blocks are invisible.** Their SQL is collected and stripped — they render nothing on the
  page. Never give them a heading of their own (any heading — "Queries", "Query Definitions", …) — it just
  leaves a dangling empty section. Put the blocks at the very top of the page (above the first heading) or
  in `queries/*.sql`, and let the components below display the data.
- **A `bar` filter lifts into the page's filter bar**, so don't also add a redundant `## Filters` heading —
  the control won't sit under it.

## The CLI loop

```bash
dashdown serve .            # dev server at http://127.0.0.1:8000 (live reload)
dashdown check              # config loads + every page renders? (queries never run at render)
dashdown components         # dense attr catalog for every component (-f json for machine-readable)
dashdown components --connectors   # config keys + install extra per connector type
dashdown connectors --test  # probe each connector (SELECT 1)
dashdown query "SELECT * FROM t LIMIT 5"          # inspect real data (-c <name> for a non-default source)
dashdown query --tables                    # what tables/views exist? (--schema <t> for columns)
dashdown metric --list      # semantic metrics & dimensions, if a semantic/ model exists
dashdown screenshot /page   # PNG + verdict: did the chart canvases actually draw? (needs [pdf])
```

Charts draw **client-side**, so `dashdown check` confirms the page *renders*, not that a chart
*painted* — `dashdown screenshot <page>` captures a PNG and reports how many chart canvases drew
(exiting non-zero if any failed), so you can confirm a chart actually drew without eyeballing the
dev server.
