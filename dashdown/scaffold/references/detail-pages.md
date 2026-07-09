<!-- AUTO-GENERATED from docs/pages/ by tooling/gen-agent-docs.py — do not edit. -->
<!-- Topic: detail-pages. Regenerate with: python tooling/gen-agent-docs.py -->

<!-- source: docs/pages/detail-pages/index.md -->

# Detail pages

A **detail page** — a "drill-down" or sub page — is a single template that serves
a whole collection: one file renders the focused view for *every* record, keyed by
a value in the URL. You write `pages/things/[id].md` once and it answers
`/things/1`, `/things/42`, … — the captured segment lands in your SQL as `${id}`.

Pair it with a **list page** whose rows link into the detail page and you get the
classic analytics flow: an overview table → click a row → a page focused on that
one record.

## 1. The dynamic route

A file or directory named `[param]` captures one URL segment as a route parameter
(the mechanics live in [Writing pages → Dynamic pages](/pages#dynamic-pages-param-routes)). The
captured value flows into queries as `${param}`, through the same context-aware,
**injection-safe** substitution as every other parameter (see
[Queries](/queries#parameters--injection-safety)):

````markdown
<!-- pages/channels/[channel].md  →  /channels/<channel> -->
```sql channel_months
SELECT month, downloads
FROM downloads
WHERE channel = '${channel}'
ORDER BY month
```

<LineChart data={channel_months} x="month" y="downloads" title="Monthly downloads" />
````

:::note
Page **prose** is not templated — only the SQL inside query blocks sees
`${param}`. To show the current record's name in the body, read it from a query
with a `<Value />` (as the demo's detail page does), rather than typing the
placeholder into Markdown.
:::

## How a route value reaches your data

The captured value is substituted into the page's queries **server-side, per
data request** — the browser sends it to `/_dashdown/api/data/<query>` and the
server fills `${param}` before running the SQL (injection-safe). So a detail page
needs **either** the live server **or** a `static_paths` build (see *Static export*
below) that pre-renders one snapshot per record; a plain `dashdown build` skips
`[param]` pages, so a deployed export without `static_paths` has no data for them.

:::note
The framework merges route params into every data/WebSocket request (at lowest
precedence — an explicit filter still wins), which is what makes each record's
request URL **unique**. Without that, two slugs of one template would produce
byte-identical, cacheable data URLs (`/api/data/q?_connector=demo`) and the
browser would serve the first record's response for the second (and the
server-side result cache would collide too). A built-in `<Table>`/chart gets this
for free; a [custom data-driven component](/extending#data-driven-components)
should fetch through `core.js`'s `fetchQueryData` so it inherits the same merge.
:::

## 2. Link a list to it — clickable rows

Make a list table's rows clickable with `row_link`, a URL pattern whose
`{column}` placeholders are filled from each row:

```markdown
<Table data={channel_totals} row_link="/detail-pages/{channel}" />
```

The whole row becomes a link, and its first column also renders as a real anchor so
the drill-down stays reachable by keyboard and screen reader. Prefer a single
clickable column instead of the whole row? Use `link_column` + `link_pattern`, or
the `detail_slug` shorthand that links one column to `{current page}/{value}`:

| Attribute                      | Effect                                                        |
| ------------------------------ | ------------------------------------------------------------ |
| `row_link`                     | Whole row clickable; pattern with `{column}` placeholders.    |
| `link_column` + `link_pattern` | Make one column a link, e.g. `link_pattern="/channels/{channel}"`. |
| `detail_slug`                  | Shorthand: links the named column to `{current path}/{value}`. |

## Live demo

The table below is a real list page. Each row links to `/detail-pages/<channel>`,
served by the single template `pages/detail-pages/[channel].md` — **click any row**
to drill into that channel's detail page.

<Table data={channels_overview} title="Downloads by channel" row_link="/detail-pages/{channel}" format="downloads=number" search=false />

:::note
Dynamic `[param]` pages are **left out of the sidebar** (no single enumerable
URL). They still work under `dashdown serve`, and `dashdown build` can pre-render
one static page per record when the template opts in — see *Static export* below.
:::

## 3. Static export (`dashdown build`)

By default a `[param]` template can't be exported — the build doesn't know which
values exist. Tell it by adding a **`static_paths`** block to the page's
frontmatter: a query whose rows enumerate the slug values (the same idea as
Next.js / Astro `getStaticPaths`). Each row becomes one pre-rendered page, with
its own data snapshot.

```markdown
---
title: Channel detail
static_paths:
  query: SELECT DISTINCT channel FROM downloads ORDER BY channel
---
```

The query runs once at build time; each row supplies the route params **by column
name** — a `[channel]` route needs a `channel` column (extra columns are ignored,
so a richer list query works too). `dashdown build` then emits
`/detail-pages/pip`, `/detail-pages/docker`, … each as a standalone `index.html`
with that record's data baked in.

:::note
Without `static_paths`, a `[param]` page is skipped by the build (live server
only). Rows whose value is empty or contains a `/` (can't be one URL segment) are
skipped with a warning. See [Exporting](/exporting). This demo's detail page ships
a `static_paths` block, so `dashdown build docs` exports all three channels.
:::


<!-- source: docs/pages/detail-pages/[channel].md -->

# Channel detail

You are viewing the **<Value data={channel_summary} column="channel" />** channel.
This page is the single template `pages/detail-pages/[channel].md`; the `${channel}`
in its queries is the URL segment, so the same file renders `pip`, `docker`, and
`source`.

<Grid cols=2>
  <Counter data={channel_summary} column="downloads" label="Total downloads" format="number" />
  <Counter data={channel_summary} column="months" label="Months tracked" />
</Grid>

<LineChart data={channel_months} x="month" y="downloads" title="Monthly downloads" explain />

<Table data={channel_months} format="downloads=number" />

[← Back to all channels](/detail-pages)
