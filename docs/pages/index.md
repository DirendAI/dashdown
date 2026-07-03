---
title: Dashdown
sidebar_label: Home
sidebar_position: 1
icon: "\U0001F4D8"
---

![Dashdown](/assets/dashdown-logo-indigo.svg)

<div align="center">
<video src="/assets/dashdown.mp4" poster="/assets/dashdown-demo-poster.png" controls muted loop playsinline preload="metadata" style="width:100%;max-width:960px;display:block;margin:1.5rem auto;border-radius:12px;border:1px solid var(--b3,#e2e8f0);box-shadow:var(--dashdown-shadow-card,0 1px 3px rgba(0,0,0,.1));">
Your browser can't play this video — <a href="/assets/dashdown.mp4">download the demo</a> instead.
</video>
</div>

# Dashdown Documentation

**Dashdown turns Markdown files — with embedded SQL and component tags — into
interactive analytics dashboards.** No JavaScript to write, no frontend toolchain. You
write `.md`, point the CLI at the folder, and get a live dashboard.

> These docs are *themselves* a Dashdown project. Every page here is a
> `pages/*.md` file rendered by the very pipeline it describes, and the chart
> below is a live query against a CSV. Read the source under [`docs/`](https://github.com/DirendAI/dashdown/tree/main/docs).

## A page is just Markdown + SQL + components

````markdown
```sql downloads_by_month connector=main
SELECT month, SUM(downloads) AS downloads
FROM downloads GROUP BY month ORDER BY month
```

<LineChart data={downloads_by_month} x="month" y="downloads" title="Downloads" />
````

That snippet renders this — real widgets, drawn in your browser from the query
result (here via the shared `queries/downloads_by_month.sql`):

```sql downloads_total connector=main
SELECT SUM(downloads) AS downloads FROM downloads
```

<Counter data={downloads_total} column="downloads" label="Total downloads (all months)" />

<LineChart data={downloads_by_month} x="month" y="downloads" title="Monthly Downloads" />

## Try the search

Press <kbd>/</kbd> anywhere, or click the box above, and type — for example
`connector`, `pdf`, or `injection`. Results rank pages and jump straight to the
matching section. It is a built-in component, [`<SiteSearch />`](/search), backed
by an index of every page; it works the same on the live server and in a static
export.

## AI ready

Dashdown is built to be authored **with** a coding agent. `dashdown new` scaffolds a
tool-agnostic `AGENTS.md` guide (plus a Claude Code skill) into every project, the CLI
exposes its own catalog so an agent checks facts instead of guessing, and the whole
manual ships as `llms.txt` for any model to read. See **[Coding agents →](/ai/coding-agents)**.

Point an agent straight at a task:

- **[Query JSON / nested data](/connectors/duckdb#querying-json-and-nested-data)** —
  read local or remote JSON in SQL: `unnest()` arrays, struct fields, 1-indexed
  lists, quoting reserved words, and the quoted-string `${param}` rule.
- **[Write a custom data-driven component](/extending#data-driven-components)** —
  the `data-async-component` placeholder contract, the `/_dashdown/api/data` shape
  (rows are arrays), self-init JS, and reading the baked snapshot in a static build.
- **[Export dynamic detail pages](/exporting#dynamic-detail-pages-static_paths)** —
  the `static_paths` frontmatter that pre-renders one page (and one snapshot) per
  record, plus [how `${param}` reaches data](/detail-pages#how-a-route-value-reaches-your-data).

## Where to go next

- **[Installation](/installation)** — install the CLI globally, `uvx`, extras, PATH.
- **[Getting started](/getting-started)** — install, scaffold, run.
- **[Examples](/examples)** — real demo dashboards, live and cloneable.
- **[Configuration](/configuration)** — every `dashdown.yaml` block in one place.
- **[Writing pages](/pages)** — frontmatter, callouts, Mermaid, includes.
- **[Detail pages](/detail-pages)** — drill-down sub pages with clickable table rows.
- **[Components](/components)** — charts, tables, counters, pivots.
- **[Formatting](/formatting)** — number, currency, percent & date display; project-wide locale.
- **[Filters & parameters](/filters)** — dropdowns, search, date ranges.
- **[Queries](/queries)** — the shared library, params, injection safety.
- **[Python queries](/python-queries)** — define a query as Python (forecasts, ML, cross-connector joins).
- **[Real-time data](/realtime)** — live-streaming queries that repaint as data changes.
- **[Connectors](/connectors)** — CSV, Postgres, BigQuery, Fabric, and more.
- **[AI](/ai)** — LLM commentary in your dashboard (`<Ask />`) + building with coding agents.
- **[Full-text search](/search)** — how `<SiteSearch />` works.
- **[Exporting](/exporting)** — static builds, PDF, CSV.
- **[Embedding](/embedding)** — drop a page into another site.
- **[Authentication](/authentication)** — password-protect a dashboard.
- **[Extending](/extending)** — write your own components and connectors.
- **[CLI reference](/cli)** — every `dashdown` command from the terminal.

{% include 'partials/cta.md' %}
