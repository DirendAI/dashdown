<!-- AUTO-GENERATED from docs/pages/ by tooling/gen-agent-docs.py — do not edit. -->
<!-- Topic: exporting. Regenerate with: python tooling/gen-agent-docs.py -->

<!-- source: docs/pages/exporting.md -->

# Exporting

## Static site (`dashdown build`)

```bash
dashdown build . --out dist
```

Renders the project to a serverless static site through the *exact same* render
path as the live server. Each page's queries are executed once at build time and
written to `_dashdown/data/<connector>/<query>.json`; the client reads those
snapshots instead of a data API. The output hosts on any static host (Netlify,
GitHub Pages, S3) — and the [full-text search](/search) index is baked in too, so
search keeps working with no server.

:::note
Every built page shows a small **"Generated &lt;time&gt;"** stamp at the foot of the
content, so readers know exactly when the snapshot was produced. It's automatic
(nothing to configure), localized to the reader's time zone, and appears only in
`dashdown build` output — never on the live server. It's hidden in PDF/print.
:::

### Dynamic detail pages (`static_paths`)

`dashdown build` **skips** a `[param]` [detail page](/detail-pages) by default — it
can't know which slug values exist. Opt in with a `static_paths` frontmatter block
(the `getStaticPaths` pattern): a query whose rows enumerate the route-param values.

```markdown
---
title: Channel detail
static_paths:
  connector: demo        # optional — defaults to the project's default source
  query: SELECT DISTINCT channel FROM downloads ORDER BY channel
---
```

The query runs once at build time and each row supplies the route params **by column
name** — a `[channel]` route needs a `channel` column (extra columns are ignored, so
a richer list query can double as the source). The build then emits **one page plus
one query snapshot per row**. Rows whose value is empty or contains a `/` (can't be a
single URL segment) are skipped with a logged warning.

:::note
Snapshots are keyed **per record**: two slugs of one template write to distinct JSON
files (there is no shared, param-less data URL), so each detail page loads its own
data. That's what makes detail pages work in a static export — not only on the live
server. See [Detail pages](/detail-pages).
:::

## Presentation PDF (`dashdown pdf`)

```bash
pip install 'dashdown-md[pdf]'    # Playwright + pypdf
playwright install chromium

dashdown pdf .                 # whole project → one deck
dashdown pdf . --page /sales   # a single page
dashdown pdf . --separate      # one file per page
```

PDF export builds on the static export and drives headless Chromium to rasterize
the real ECharts canvases — so charts come out crisp, not blank. Orientation,
page size, and scale are CLI options. There's also an **Export PDF** button on
the breadcrumb line (top-right of every page) that renders the current page
(with its current filters).

## CSV (per table)

Every `<Table>` has a ↓ button in its header that downloads its *current,
filtered* rows as RFC 4180 CSV — built client-side, no server endpoint. Opt out
with `export=false`, rename with `export_filename=`.
