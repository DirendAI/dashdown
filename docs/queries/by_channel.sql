---
description: Monthly downloads split by channel (a multi-series shape)
---
SELECT month, channel, SUM(downloads) AS downloads
FROM downloads
GROUP BY month, channel
ORDER BY month
