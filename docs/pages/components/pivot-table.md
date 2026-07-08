---
title: PivotTable
sidebar_label: PivotTable
sidebar_position: 4
icon: "\U0001F500"
---

# PivotTable

A client-side cross-tab with drag-and-drop axes. Choose `rows`, `cols`, and a
`values` column; pick the aggregation with `agg` (`sum`, `avg`, `count`, …).

```markdown
<PivotTable data={by_channel} rows="channel" cols="month" values="downloads" agg="sum" />
```

<PivotTable data={by_channel} rows="channel" cols="month" values="downloads" agg="sum" />

Switch the aggregation with `agg=` (here the **average** per cell), or stack
multiple fields on an axis with a comma-separated list:

<PivotTable data={by_channel} rows="channel" cols="month" values="downloads" agg="avg" title="Average downloads" />

<PivotTable data={device_specs} rows="tier,device" values="price" agg="avg" title="Avg price by tier & device" />

| Attribute | Purpose                                       |
| --------- | --------------------------------------------- |
| `data`    | **Required.** The query to cross-tab.         |
| `rows`    | Column(s) for the row axis.                   |
| `cols`    | Column(s) for the column axis.                |
| `values`  | The measure column.                           |
| `agg`     | Aggregation: `sum` (default), `avg`, `count`, `min`, `max`. |

The axes are draggable in the browser, so readers can re-pivot without editing
the page.
