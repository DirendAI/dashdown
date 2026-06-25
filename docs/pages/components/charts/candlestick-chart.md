---
title: CandlestickChart
sidebar_label: CandlestickChart
sidebar_position: 16
---

# CandlestickChart

An OHLC candlestick chart for price or range data over time. `x` is the
date/category axis; `open`, `high`, `low`, and `close` name the four price
columns. Bullish candles (close ≥ open) render green, bearish red.

```markdown
<CandlestickChart data={daily_prices} x="day"
                  open="open" high="high" low="low" close="close"
                  title="Daily price" />
```

<CandlestickChart data={daily_prices} x="day" open="open" high="high" low="low" close="close" title="Daily price" />

## From the semantic layer

Like every chart, CandlestickChart also takes [semantic metric
refs](/semantic-layer) instead of `data={query}`. Each price role
(`open`/`high`/`low`/`close`) names a **measure** of one model and `by=` the date
dimension — exactly how a BI tool binds an OHLC visual to a semantic model: four
measures grouped by a date, each mapped to a candle role.

```markdown
<CandlestickChart by={prices.day}
                  open={prices.open} high={prices.high}
                  low={prices.low} close={prices.close} />
```

The model author defines `open`/`close` as first/last measures and `high`/`low`
as max/min; the four combine into **one** query grouped by `by` (add an optional
`grain=` to bucket the date). The value-axis number format defaults from the
`close` measure.

## Attributes

| Attribute | Purpose |
| --------- | ------- |
| `data` | The query to plot (`data={query}`) — or omit it and use measure refs. |
| `x` | **Required (query mode).** Date/category column for the x-axis. |
| `open` | **Required.** Opening-price column — or `{model.measure}` in semantic mode. |
| `high` | **Required.** High-price column — or `{model.measure}` in semantic mode. |
| `low` | **Required.** Low-price column — or `{model.measure}` in semantic mode. |
| `close` | **Required.** Closing-price column — or `{model.measure}` in semantic mode. |
| `by` | **Semantic mode.** Date dimension to group the measures by. |
| `grain` | **Semantic mode.** Bucket a time `by` — `day`/`week`/`month`/… or `grain={control}`. |
| `title` | Chart title. |
| `sort_by` | Column to sort by before plotting. |
| `height` | Pixel height (default `300`). |
| `col-span` | Columns to span inside a `<Grid>`. |
| `format` · `currency` · `decimals` · `locale` | Value-axis & tooltip formatting. |
| `empty_message` | Text shown when the query returns no rows. |

`open`/`high`/`low`/`close` are CandlestickChart-specific; the rest are the shared chart attributes — see [Charts](/components/charts).
