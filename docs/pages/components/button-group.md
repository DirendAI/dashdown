---
title: ButtonGroup
sidebar_label: ButtonGroup
sidebar_position: 14
icon: "\U0001F39B"
---

# ButtonGroup

A single-select filter shown as an inline **segmented control** — a row of pill
buttons where exactly one is active (`All · High · Mid · Budget`). A
lower-friction alternative to a [`<Dropdown>`](/components/dropdown) for a small,
**fixed** set of choices: one click instead of open-then-pick.

The picked value is stored as a string under `name`, exactly like the other
filters, so your SQL reads it with `${name}`. The default **"All"** segment stores
`""`, so the `'${tier}' = ''` guard passes and every row shows — the same
empty-means-all convention a single-select Dropdown uses:

```sql
SELECT device, tier, price
FROM device_specs
WHERE '${tier}' = '' OR tier = '${tier}'
ORDER BY price DESC
```

:::query name=devices_by_tier connector=main
SELECT device, tier, price
FROM device_specs
WHERE '${tier}' = '' OR tier = '${tier}'
ORDER BY price DESC
:::

<ButtonGroup name="tier" label="Tier" options="High,Mid,Budget" />

<BarChart data={devices_by_tier} x="device" y="price" title="Devices by tier" />

Click a segment — the chart re-queries. Click **All** to drop the filter again
(selecting a segment doesn't toggle off, so "All" is how you clear it).

| Attribute     | Purpose                                                            |
| ------------- | ----------------------------------------------------------------- |
| `name`        | **Required.** Filter key your SQL reads as `${name}`.             |
| `options`     | **Required.** Choices — `options="High,Mid,Budget"` or `options={[High,Mid,Budget]}`. Value == label. |
| `label`       | Inline label shown before the segments (defaults to `name`).      |
| `include_all` | Prepend an **All** segment that clears the filter (default `true`). |
| `all_label`   | Text for that segment (default `"All"`).                          |
| `default`     | Value selected on first load (URL params still win).              |
| `bar`         | Lift into the top [filter bar](/filters) (default: inline).       |

:::note
A ButtonGroup is for a handful of **fixed** options you'd lay out as buttons. For
a **dynamic** or high-cardinality column, use a [`<Dropdown>`](/components/dropdown)
— it populates its options from the data. The value reaches SQL through the same
context-aware `${param}` substitution as every other filter (always a quoted
string literal, no new injection surface), and like the other controls it's
stripped from [static builds](/exporting).
:::
