---
description: Demo world indicators (ISO numeric code, decade, population/GDP/life expectancy)
---
SELECT iso, country, year, population, gdp_per_capita, life_expectancy
FROM world_indicators
ORDER BY year, country
