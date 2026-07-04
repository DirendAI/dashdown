---
description: Daily visits and signups in long format (a ThemeRiver stream shape)
---
SELECT date, 'visits' AS metric, visits AS value FROM daily
UNION ALL
SELECT date, 'signups' AS metric, signups AS value FROM daily
ORDER BY date
