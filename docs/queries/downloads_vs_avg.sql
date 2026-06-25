---
connector: main
description: Monthly downloads per channel as a signed deviation from each channel's average (a diverging heatmap shape)
---
WITH wide AS (
    SELECT
        month,
        SUM(downloads) FILTER (WHERE channel = 'pip')    AS pip,
        SUM(downloads) FILTER (WHERE channel = 'docker') AS docker,
        SUM(downloads) FILTER (WHERE channel = 'source') AS source
    FROM downloads
    GROUP BY month
)
SELECT
    month,
    CAST(ROUND(pip    - AVG(pip)    OVER ()) AS INTEGER) AS pip,
    CAST(ROUND(docker - AVG(docker) OVER ()) AS INTEGER) AS docker,
    CAST(ROUND(source - AVG(source) OVER ()) AS INTEGER) AS source
FROM wide
ORDER BY month
