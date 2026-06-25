---
name: dashdown-authoring
description: Author this Dashdown analytics dashboard — pages, embedded SQL queries, and <Component /> tags (charts, tables, counters, filters), plus connectors, theming, semantic metrics, and the dashdown CLI. Use whenever editing .md pages, queries/, semantic/, sources.yaml, dashdown.yaml, or components/ in this project.
---

# Authoring this Dashdown dashboard

This project is a **Dashdown** dashboard: Markdown files under `pages/` with embedded SQL
(`:::query` blocks) and `<Component />` tags render to an interactive analytics app — no
build step, no JS framework, no npm.

## Start here — don't read everything

The guide is **sharded for cheap reading**. Load only what the task needs:

1. **[`AGENTS.md`](../../../AGENTS.md)** (project root) — the *map*: a one-screen cheat-sheet
   (`:::query` / `${param}` / component syntax) + an index of per-topic references. Skim it first.
2. **`references/<topic>.md`** (project root, linked below) — the full docs for one topic. Open the
   **one** shard your task needs, not the whole set.
3. **The `dashdown` CLI** — for *facts* (a component's attrs, a connector's keys, real data),
   prefer the CLI over re-reading prose. **Concepts from references, facts from the CLI.** With no
   shell, [`references/catalog.md`](../../../references/catalog.md) is the file-readable form of
   `dashdown components` (every component's attrs + every connector's config keys).

## Decision tree — what to read, how to verify

| You're editing… | Read | Verify with |
|---|---|---|
| A page / chart / table / counter | [components](../../../references/components.md) (or `dashdown components`) | `dashdown check`, then `dashdown screenshot <page>` (did it draw?) |
| `sources.yaml` (a connector) | [connectors](../../../references/connectors.md) (or `dashdown components --connectors`) | `dashdown connectors --test` |
| A shared query in `queries/*.sql` | [queries](../../../references/queries.md) | `dashdown query "…" -c <conn>` |
| A query as Python (`queries/*.py`) | [python-queries](../../../references/python-queries.md) | `dashdown check` |
| A `semantic/*.yml` metric model | [semantic-layer](../../../references/semantic-layer.md) | `dashdown metric --list` |
| A dropdown / search / date filter | [filters](../../../references/filters.md) | `dashdown serve .` (interact) |
| Number / date display | [formatting](../../../references/formatting.md) | `dashdown serve .` |
| `dashdown.yaml` (theme/auth/search/embed) | [configuration](../../../references/configuration.md) (+ [theming](../../../references/theming.md), [authentication](../../../references/authentication.md), [embedding](../../../references/embedding.md)) | `dashdown check` |
| A custom component or connector | [extending](../../../references/extending.md) | `dashdown check` |
| Static export / PDF / CSV | [exporting](../../../references/exporting.md) | `dashdown build . --out dist` |

## Task playbooks

**Add a chart of a query.** Write a `:::query name=q connector=main` block (or reuse a
`queries/*.sql`), then `<LineChart data={q} x="…" y="…" [series="…"] title="…" />`. Run
`dashdown components` for the exact attrs; `dashdown check` to confirm it renders.

**Add a connector.** Add a block to `sources.yaml` (`name:` → `type: postgres` + keys). Get the
required keys + install extra from `dashdown components --connectors`. Install the extra, then
`dashdown connectors --test` to confirm it connects before authoring queries against it.

**Write a shared query.** Drop a `.sql` (or `.py`) under `queries/`; the name is its path with
`/`→`.` (`queries/finance/mrr.sql` → `finance.mrr`). Reference it from any page as `data={finance.mrr}`.
Test the SQL with `dashdown query "…" -c <conn>`. Details: [queries](../../../references/queries.md).

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
Confirm `data={name}` matches `:::query name=`.

**Verify your work.** Charts draw **client-side**, so `dashdown check` proves a page *renders*,
not that a chart *painted*. Full loop: `dashdown check` (renders, no bad tags/attrs?) →
`dashdown connectors --test` (data reachable?) → `dashdown screenshot <page>` (saves a PNG and
reports `N/M chart(s) drew`, exits non-zero if any stayed blank or errored — your visual gate).

**Ship a build.** `dashdown build . --out dist` (static export — queries run once at build) or
`dashdown pdf .` (presentation PDF; needs the `[pdf]` extra). See [exporting](../../../references/exporting.md).

## The CLI loop

```bash
dashdown serve .            # dev server at http://127.0.0.1:8000 (live reload)
dashdown check              # config loads + every page renders? (queries never run at render)
dashdown components         # dense attr catalog for every component (-f json for machine-readable)
dashdown components --connectors   # config keys + install extra per connector type
dashdown connectors --test  # probe each connector (SELECT 1)
dashdown query "SELECT * FROM t LIMIT 5" -c main   # inspect real data
dashdown query --tables -c main            # what tables/views exist? (--schema <t> for columns)
dashdown metric --list      # semantic metrics & dimensions, if a semantic/ model exists
dashdown screenshot /page   # PNG + verdict: did the chart canvases actually draw? (needs [pdf])
```

Charts draw **client-side**, so `dashdown check` confirms the page *renders*, not that a chart
*painted* — `dashdown screenshot <page>` captures a PNG and reports how many chart canvases drew
(exiting non-zero if any failed), so you can confirm a chart actually drew without eyeballing the
dev server.
