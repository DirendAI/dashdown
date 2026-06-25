---
title: GraphChart
sidebar_label: GraphChart
sidebar_position: 18
---

# GraphChart

A force-directed network — nodes connected by weighted links. Feed it an **edge
list**: each row is one edge with a `source`, a `target`, and an optional
`value` (the edge weight). Nodes are the union of the two columns, sized by
their total incident weight. Drag to rearrange; scroll to zoom.

```markdown
<GraphChart data={user_flow} source="stage_from" target="stage_to" value="users" title="Stage network" />
```

<GraphChart data={user_flow} source="stage_from" target="stage_to" value="users" title="Stage network" />

## From the semantic layer

Like every chart, GraphChart also takes [semantic metric
refs](/semantic-layer) instead of `data={query}`. `source`/`target` are two
**dimensions** and `value` is the **measure** weighting each edge:

```markdown
<GraphChart source={flow.stage_from} target={flow.stage_to} value={flow.users} />
```

In semantic mode `value` is **required** — a measure aggregates the edge list (use
a `count` measure for unweighted edges); for raw unweighted edges use
`data={query}` with just `source`/`target`.

## Attributes

| Attribute | Purpose |
| --------- | ------- |
| `data` | The query to plot (`data={query}`) — or omit it and use semantic refs. |
| `source` | **Required.** Source-node column (alias for `x`) — or a `{model.dim}` in semantic mode. |
| `target` | **Required.** Target-node column (alias for `y`) — or a `{model.dim}` in semantic mode. |
| `value` | Edge weight (also sizes the nodes). Optional in query mode; **required** as a `{model.measure}` in semantic mode. |
| `title` | Chart title. |
| `sort_by` | Column to sort by before plotting. |
| `color` | Single color or comma-separated palette override for the nodes. |
| `height` | Pixel height (default `300`). |
| `col-span` | Columns to span inside a `<Grid>`. |
| `empty_message` | Text shown when the query returns no rows. |

`source`/`target` are aliases for `x`/`y`. For a strictly directional flow with proportional widths use [SankeyChart](/components/charts/sankey-chart); for a free network use GraphChart. The rest are the shared chart attributes — see [Charts](/components/charts).
