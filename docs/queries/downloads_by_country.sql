---
connector: main
description: Total downloads per country (names match the world GeoJSON)
---
SELECT country, downloads
FROM by_country
ORDER BY downloads DESC
