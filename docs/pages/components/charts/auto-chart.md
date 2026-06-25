---
title: Chart (auto)
sidebar_label: Chart auto
sidebar_position: 22
---

# Chart (`auto`)

Let Dashdown infer the chart type from the columns instead of naming one. Opt in
with the `auto` flag:

```markdown
<Chart auto data={downloads_by_month} />
```

<Chart auto data={downloads_by_month} />

Rough heuristics: a time/category `x` with a numeric `y` → line or bar; two
numeric columns → scatter. You can still pass explicit `x`/`y`/`series` to guide
it. The `auto` flag is required — a bare `<Chart data=… />` raises, so you never
get a surprise chart type.

## From the semantic layer

`<Chart auto>` also takes [semantic metric refs](/semantic-layer) instead of
`data={query}` — it infers the type from the resolved `metric`/`by`/`series`
shape just as it does from query columns:

```markdown
<Chart auto metric={sales.revenue} by={sales.region} />
```

## Attributes

| Attribute | Purpose |
| --------- | ------- |
| `auto` | **Required** flag — opts into type inference (`<Chart auto … />`). |
| `data` | **Required.** The query to plot (`data={query}`). |
| `x` · `y` · `series` | Optional — provide them to guide the inferred chart instead of letting columns decide. |
| `title` | Chart title. |
| `color` | Single color or comma-separated palette override. |
| `height` | Pixel height (default `300`). |
| `col-span` | Columns to span inside a `<Grid>`. |
| `format` · `currency` · `decimals` · `locale` · `date_format` | Value & tooltip number/date formatting. |
| `empty_message` | Text shown when the query returns no rows. |

`auto` is unique to `<Chart>`; everything else is shared with the typed charts — see [Charts](/components/charts).
