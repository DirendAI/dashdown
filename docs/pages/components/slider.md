---
title: Slider
sidebar_label: Slider
sidebar_position: 16
icon: "\U0001F39A"
---

# Slider

A single-value numeric **threshold** filter — one handle on a track for a
`min rating ≥`, `price ≤`, or `top N` style bound. The one-handled sibling of
[`<RangeSlider>`](/components/range-slider) (which carries a low/high pair).

The value is stored under `name`, so your SQL reads it with `${name}`. Guard the
comparison so a missing value (the brief moment before the control seeds) shows
everything rather than erroring on `CAST('' AS DOUBLE)`. The **operator you pick
decides which handle position means "all"**: `>=` → the minimum, `<=` → the
maximum.

```sql
SELECT device, rating
FROM device_specs
WHERE '${min_rating}' = '' OR rating >= CAST(${min_rating} AS DOUBLE)
ORDER BY rating DESC
```

```sql devices_rated connector=main
SELECT device, rating
FROM device_specs
WHERE '${min_rating}' = '' OR rating >= CAST(${min_rating} AS DOUBLE)
ORDER BY rating DESC
```

<Slider name="min_rating" min={0} max={5} step={0.1} default={4} label="Min rating" />

<BarChart data={devices_rated} x="device" y="rating" title="Devices at or above the rating" />

Drag the handle — the chart re-queries. The readout above the track shows the
live value, formatted with the same `format=`/`currency=` options the other
components use.

| Attribute     | Purpose                                                          |
| ------------- | ---------------------------------------------------------------- |
| `name`        | **Required.** Filter key your SQL reads as `${name}`.           |
| `min` / `max` | Track bounds (default `0` / `100`). `max` must exceed `min`.    |
| `step`        | Handle increment (default `1`).                                 |
| `default`     | Initial value (default: `min`). URL params still win.           |
| `format` / `currency` / `decimals` / `locale` | Format the readout value.       |
| `bar`         | Lift into the top [filter bar](/filters) (default: inline).     |

:::tip
For a **between** bound (low *and* high), use [`<RangeSlider>`](/components/range-slider)
— it carries two handles and a `${name}_min` / `${name}_max` pair. `<Slider>` is
the single-threshold control.
:::

Filter controls drive **server-side** SQL substitution, so they're stripped from
[static builds](/exporting) automatically.
