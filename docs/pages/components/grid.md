---
title: Grid
sidebar_label: Grid
sidebar_position: 7
icon: "\U0001F9F1"
---

# Grid

Lay widgets out in equal-width columns. Wrap any components in `<Grid>`; set
`cols` (default `2`) and an optional `gap`.

```markdown
<Grid cols=2>
  <Counter data={downloads_total} column="downloads" label="Downloads" delta="12.4"
           sparkline={downloads_by_month} sparkline-column="downloads" color="primary" />
  <Counter data={downloads_total} column="months" label="Months tracked" />
</Grid>
```

<Grid cols=2>
  <Counter data={downloads_total} column="downloads" label="Downloads" delta="12.4"
           sparkline={downloads_by_month} sparkline-column="downloads" color="primary" />
  <Counter data={downloads_total} column="months" label="Months tracked" />
</Grid>

A child can span more than one column with `col-span=`:

<Grid cols=3>
  <LineChart data={downloads_by_month} x="month" y="downloads" col-span=2 title="Trend (spans 2 cols)" explain />
  <BarChart data={channel_totals} x="channel" y="downloads" title="By channel" explain />
</Grid>

| Attribute        | Purpose                                          |
| ---------------- | ------------------------------------------------ |
| `cols` / `columns` | Number of equal columns (default `2`).         |
| `gap`            | CSS gap between cells.                            |

Charts inside a grid honor `col-span=` to span multiple columns. In a printed/PDF
export the grid stacks to one widget per row automatically.
