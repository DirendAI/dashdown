---
description: Monthly downloads with each channel as its own column (a multi-metric shape)
---
SELECT
    month,
    SUM(downloads) FILTER (WHERE channel = 'pip')    AS pip,
    SUM(downloads) FILTER (WHERE channel = 'docker') AS docker,
    SUM(downloads) FILTER (WHERE channel = 'source') AS source
FROM downloads
GROUP BY month
ORDER BY month
