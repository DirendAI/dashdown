---
title: RangeSlider
sidebar_label: RangeSlider
sidebar_position: 14
icon: "\U0001F39A"
---

# RangeSlider

A dual-handle numeric slider for a *between* bound on a numeric column (price,
age, score, …). Like [`<DateRange>`](/components/date-range) it owns **two** URL
params — `min_param` / `max_param` (default `name_min` / `name_max`) — that your
SQL reads.

A handle resting on its track bound writes an **empty value** (the same
empty-means-all convention as every other filter), so **guard each bound** — the
empty case (a wide-open slider, or the first fetch before the control seeds)
then shows everything instead of erroring on `CAST('' AS DOUBLE)`:

```sql devices_in_range
SELECT device, tier, price
FROM device_specs
WHERE ('${price_min}' = '' OR price >= CAST(${price_min} AS DOUBLE))
  AND ('${price_max}' = '' OR price <= CAST(${price_max} AS DOUBLE))
ORDER BY price DESC
```

```sql
SELECT device, tier, price
FROM device_specs
WHERE ('${price_min}' = '' OR price >= CAST(${price_min} AS DOUBLE))
  AND ('${price_max}' = '' OR price <= CAST(${price_max} AS DOUBLE))
ORDER BY price DESC
```

<RangeSlider name="price" min={500} max={1500} step={50} default={[700,1300]} label="Price ($)" format="currency" currency="$" />

<BarChart data={devices_in_range} x="device" y="price" title="Devices in price range" explain />

Drag either handle — the chart re-queries. The readout above the track shows the
live low/high, formatted with the same `format=`/`currency=` options the other
components use.

Each handle substitutes as a **quoted string literal** (the one injection-safe
path every filter shares), so the `CAST(… AS DOUBLE)` turns it back into a number
for the comparison. The `'${price_min}' = ''` half of each clause is the
empty-means-all guard — drag a handle back to its bound and that side stops
filtering.

## The `default` bounds

`default=` seeds the initial `[low, high]` on first load (URL params still win).
Write it as an **array literal** or a **comma string** — both clamp into
`[min, max]`:

```html
<RangeSlider name="price" min={0} max={10000} step={50} default={[2000,8000]} />
<RangeSlider name="price" min={0} max={10000} step={50} default="2000, 8000" />
```

:::note
Inside `{…}` keep the values **space-free** (`{[2000,8000]}`, not
`{[2000, 8000]}`) — an unquoted attribute value with a space isn't recognized as
a tag and renders as plain text. Use the quoted `default="2000, 8000"` form if
you want spaces. Omitting `default` starts the slider wide open at `[min, max]`.
:::

| Attribute       | Purpose                                                       |
| --------------- | ------------------------------------------------------------- |
| `name`          | **Required.** Base filter key.                                |
| `min` / `max`   | Track bounds (default `0` / `100`). `max` must exceed `min`.  |
| `step`          | Handle increment (default `1`).                               |
| `default`       | Initial `[low, high]` pair (default: the full `[min, max]`).  |
| `min_param` / `max_param` | URL/SQL param names (default `name_min` / `name_max`). |
| `format` / `currency` / `decimals` / `locale` | Format the readout values. |
| `bar`           | Lift into the top [filter bar](/filters) (default: inline).   |

Filter controls drive **server-side** SQL substitution, so they're stripped from
[static builds](/exporting) automatically.
