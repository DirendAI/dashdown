---
connector: main
description: This month's downloads as a percentage of the 3,000 goal (a gauge value)
---
SELECT ROUND(100.0 * SUM(downloads) / 3000, 0) AS pct
FROM downloads
WHERE month = '2026-06'
