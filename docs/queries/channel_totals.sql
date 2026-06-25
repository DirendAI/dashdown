---
connector: main
description: Total downloads per channel (a single category/value shape)
---
SELECT channel, SUM(downloads) AS downloads
FROM downloads
GROUP BY channel
ORDER BY downloads DESC
