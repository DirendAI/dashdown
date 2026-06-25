---
title: Value
sidebar_label: Value
sidebar_position: 5
icon: "\U0001F539"
---

# Value

A single value rendered inline — handy for weaving a number into a sentence.
Reads `column` from a `row` (default `0`).

```markdown
We have shipped <Value data={downloads_total} column="downloads" suffix=" downloads" /> so far.
```

We have shipped <Value data={downloads_total} column="downloads" suffix=" downloads" /> across
<Value data={downloads_total} column="months" suffix=" months" />.

`format=` applies number/currency/percent formatting inline — e.g. thousands
separators: we have <Value data={downloads_total} column="downloads" format="number" /> downloads.

| Attribute        | Purpose                              |
| ---------------- | ------------------------------------ |
| `data`           | **Required.** The query to read.     |
| `column`         | Which column to display.             |
| `row`            | Row index (default `0`).             |
| `prefix`/`suffix`| Text around the value.               |
| `format` · `currency` · `decimals` · `locale` | Number/date formatting — see [Formatting](/formatting). |

For a large standalone KPI card, use [Counter](/components/counter).
