---
title: SunburstChart
sidebar_label: SunburstChart
sidebar_position: 19
---

# SunburstChart

A hierarchy drawn as nested rings — each ring a level, each arc's sweep its
value. Feed it an **adjacency list**: `id` names each node, `parent` points at
its parent's id (blank or unknown ⇒ a root). `value` (optional) sizes a node
and `label` (optional) is its display name.

```markdown
<SunburstChart data={org_tree} id="id" parent="parent" value="headcount" label="name" title="Headcount" />
```

<SunburstChart data={org_tree} id="id" parent="parent" value="headcount" label="name" title="Headcount" />

:::note
SunburstChart needs an `id`/`parent` **hierarchy**, which the
[semantic](/semantic-layer) `metric=`/`by=` grammar can't express — so it takes
`data={query}` only. (For a metric split by one category, a
[PieChart](/components/charts/pie-chart) `metric=`/`by=` works.)
:::

## Attributes

| Attribute | Purpose |
| --------- | ------- |
| `data` | **Required.** The query to plot (`data={query}`). |
| `id` | **Required.** Unique node-id column. |
| `parent` | **Required.** Parent-id column (blank/unknown ⇒ a root ring). |
| `value` | Optional column sizing each arc (leaf values roll up to parents). |
| `label` | Optional display-name column (defaults to the id). |
| `title` | Chart title. |
| `color` | Single color or comma-separated palette override. |
| `height` | Pixel height (default `300`). |
| `col-span` | Columns to span inside a `<Grid>`. |
| `format` · `currency` · `decimals` · `locale` | Tooltip value formatting. |
| `empty_message` | Text shown when the query returns no rows. |

Same `id`/`parent`/`value`/`label` shape as [TreeChart](/components/charts/tree-chart) — Sunburst shows proportions, Tree shows structure. For a single-level part-to-whole use [PieChart](/components/charts/pie-chart) or [TreemapChart](/components/charts/treemap-chart). The rest are the shared chart attributes — see [Charts](/components/charts).
