---
connector: main
description: Single-row total of all downloads (for KPI components)
---
SELECT SUM(downloads) AS downloads, COUNT(DISTINCT month) AS months
FROM downloads
