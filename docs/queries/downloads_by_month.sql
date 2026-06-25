---
connector: main
description: Total downloads per month across all channels
cache_ttl: 300
---
-- A shared library query: defined once, referenced by name (`data={downloads_by_month}`)
-- from any page. The `---` frontmatter fence reads as inert SQL comments, so the
-- file still opens cleanly in a SQL editor.
SELECT month, SUM(downloads) AS downloads
FROM downloads
GROUP BY month
ORDER BY month
