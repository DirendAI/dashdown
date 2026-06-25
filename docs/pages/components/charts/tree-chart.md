---
title: TreeChart
sidebar_label: TreeChart
sidebar_position: 20
---

# TreeChart

A hierarchy drawn as a node-link diagram — an org chart / file tree, laid out
left-to-right and collapsible. Same **adjacency list** as
[SunburstChart](/components/charts/sunburst-chart): `id` names each node,
`parent` points at its parent's id. `value`/`label` are optional. Multiple
roots are gathered under one synthetic root.

```markdown
<TreeChart data={org_tree} id="id" parent="parent" label="name" title="Org chart" />
```

<TreeChart data={org_tree} id="id" parent="parent" label="name" title="Org chart" />

:::note
Like [SunburstChart](/components/charts/sunburst-chart), TreeChart needs an
`id`/`parent` **hierarchy**, which the [semantic](/semantic-layer)
`metric=`/`by=` grammar can't express — so it takes `data={query}` only.
:::

## Attributes

| Attribute | Purpose |
| --------- | ------- |
| `data` | **Required.** The query to plot (`data={query}`). |
| `id` | **Required.** Unique node-id column. |
| `parent` | **Required.** Parent-id column (blank/unknown ⇒ a root). |
| `value` | Optional value column (shown in the tooltip). |
| `label` | Optional display-name column (defaults to the id). |
| `title` | Chart title. |
| `color` | Single color or comma-separated palette override. |
| `height` | Pixel height (default `300`). |
| `col-span` | Columns to span inside a `<Grid>`. |
| `empty_message` | Text shown when the query returns no rows. |

Tree shows *structure*; [SunburstChart](/components/charts/sunburst-chart) shows the same hierarchy as *proportions*. The rest are the shared chart attributes — see [Charts](/components/charts).
