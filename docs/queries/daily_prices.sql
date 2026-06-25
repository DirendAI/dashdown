---
connector: main
description: Daily open/high/low/close prices (an OHLC candlestick shape)
---
SELECT day, open, high, low, close
FROM prices
ORDER BY day
