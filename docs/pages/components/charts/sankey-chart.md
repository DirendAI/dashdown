---
title: SankeyChart
sidebar_label: SankeyChart
sidebar_position: 15
---

# SankeyChart

A flow diagram — width-weighted links between nodes. Feed it an **edge list**:
each row is one link with a `source`, a `target`, and a `value` (the link
width). Nodes are the union of the two columns.

```markdown
<SankeyChart data={user_flow} source="stage_from" target="stage_to" value="users" title="Lifecycle flow" />
```

<SankeyChart data={user_flow} source="stage_from" target="stage_to" value="users" title="Lifecycle flow" explain />

## From the semantic layer

Like every chart, SankeyChart also takes [semantic metric
refs](/semantic-layer) instead of `data={query}`. `source`/`target` are two
**dimensions** and `value` is the **measure** weighting each link — one link per
source×target pair:

```markdown
<SankeyChart source={flow.stage_from} target={flow.stage_to} value={flow.users} />
```

This needs a model that exposes the two endpoints as dimensions and a link-weight
measure; the link width is the aggregated `value` per pair.

## Attributes

| Attribute | Purpose |
| --------- | ------- |
| `data` | The query to plot (`data={query}`) — or omit it and use semantic refs. |
| `source` | **Required.** Source-node column (alias for `x`) — or a `{model.dim}` in semantic mode. |
| `target` | **Required.** Target-node column (alias for `y`) — or a `{model.dim}` in semantic mode. |
| `value` | **Required.** Link-width column — or a `{model.measure}` in semantic mode. |
| `title` | Chart title. |
| `sort_by` | Column to sort by before plotting. |
| `color` | Single color or comma-separated palette override for the nodes. |
| `height` | Pixel height (default `300`). |
| `col-span` | Columns to span inside a `<Grid>`. |
| `format` · `currency` · `decimals` · `locale` | Tooltip value formatting. |
| `empty_message` | Text shown when the query returns no rows. |

`source`/`target` are aliases for `x`/`y`; `value` is the link width. The flow must be **acyclic** — ECharts cannot lay out a Sankey that loops back on itself. Otherwise these are the shared chart attributes — see [Charts](/components/charts).
