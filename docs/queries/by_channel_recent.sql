---
connector: main
description: Recent-months downloads by channel (a facet/slice/value shape for small-multiples pies)
---
SELECT month, channel, SUM(downloads) AS downloads
FROM downloads
WHERE month >= '2026-04'
GROUP BY month, channel
ORDER BY month, channel
