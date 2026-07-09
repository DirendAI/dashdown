---
title: Maps
sidebar_label: Maps
sidebar_position: 2
icon: "\U0001F5FA"
---

# Maps

Every map component on one page: the ECharts-based [MapChart](#mapchart)
choropleth first — the quickest way to put values on a world map — then five
self-drawn SVG geo maps: an animated choropleth, small-multiple choropleths, a
bivariate choropleth, a proportional-symbol map, and a dot-density map.

## MapChart

An ECharts choropleth that joins by country **name** instead of ISO code —
handy when a dataset carries names and no codes. It's a regular chart (shared
chart attributes, canvas-rendered), not one of the SVG geo maps below, but it
follows the same conventions: overlaid title and legend, **Ctrl/⌘ + scroll**
to zoom (plain scrolling stays with the page), and a **Reset view** pill once
zoomed. `location` names the region column, `value` the metric; the built-in
`world` map ships offline, and `geojson=` takes a custom map here too.

```markdown
<MapChart data={downloads_by_country} location="country" value="downloads"
          map="world" title="Downloads by country" explain />
```

<MapChart data={downloads_by_country} location="country" value="downloads" map="world" title="Downloads by country" explain />

The `explain` attribute works here like on any chart — and the [AI
commentary](/ai/ask#annotations-on-the-chart) can highlight regions on the
map, each one validated against the locations the query actually returned.
(The SVG geo maps below take `explain` too — see
[AI commentary on geo maps](#ai-commentary-on-geo-maps).)

Like every chart, MapChart also takes [semantic metric refs](/semantic-layer)
instead of `data={query}` — `by` is the region dimension (its values must match
the GeoJSON names) and `metric` shades each region; `map`/`geojson` stay
literal:

```markdown
<MapChart metric={sales.revenue} by={sales.country} map="world" />
```

### MapChart attributes

| Attribute | Purpose |
| --------- | ------- |
| `data` | **Required.** The query to plot (`data={query}`). |
| `location` | **Required.** Region-name column (must match the GeoJSON; alias for `x`). |
| `value` | **Required.** Metric that shades each region (alias for `y`). |
| `map` | Built-in map name (default `world`). |
| `geojson` | URL/path to a custom GeoJSON for non-world maps (resolved from `assets/`). |
| `title` | Chart title. |
| `color` | Single color or comma-separated palette for the scale. |
| `height` | Pixel height (default `300`). |
| `col-span` | Columns to span inside a `<Grid>`. |
| `format` · `currency` · `decimals` · `locale` | Tooltip value formatting. |
| `empty_message` | Text shown when the query returns no rows. |

`location`/`value` are aliases for `x`/`y`; `map`/`geojson` are
MapChart-specific. The rest are the shared chart attributes — see
[Charts](/components/charts).

## Geo maps

The five SVG geo maps share one design:

- **Countries join on ISO 3166-1 numeric codes** (`id=` names the code column,
  default `iso`) against the bundled world geometry — the join key analytics
  datasets actually carry. Values like `840`, `"840"` and `"076"` all match.
  ([MapChart](#mapchart) instead joins by country *name*.)
- **Self-drawn SVG, fully offline** — no mapping library, no CDN, an
  equirectangular projection with standard parallels ±35° and antimeridian
  handling. The frame auto-fits the loaded geometry's extent, so the world
  shows no empty polar bands and a [custom region](#custom-regions) fills the
  card.
- **Static-export safe.** Every frame ships in the one query result, and the
  year scrubber / metric toggles are the component's own controls (not page
  filters) — so `dashdown build` exports stay fully interactive.
- **Deterministic.** DotDensityMap seeds its dot placement per country+metric,
  so the same data draws the identical map on every load and in exports.
- **Chrome overlays the map.** The title (top-left), legend (bottom-left) and
  metric toggle (bottom-right) float over the map on translucent washes, so
  the geometry gets the whole card. Only the ChoroplethTime timeline is a
  footer row, and ChoroplethFacets keeps a flow header/footer — a facet grid
  has no spare corners.

The demos below query a demo dataset of decade-level world indicators
(`data/world_indicators.csv`).

## Data shape

One result shape feeds every map: **one row per country (and per year, for the
time-aware maps), one numeric column per metric**. The demo dataset is already
in that shape, so its query is just:

```sql
SELECT iso, country, year, population, gdp_per_capita, life_expectancy
FROM world_indicators
ORDER BY year, country
```

Getting a fact table there is a `GROUP BY` country and year with one aggregate
per metric — plus, usually, a join to translate whatever country key your data
carries (alpha-2 codes like `US`, names) into ISO numeric:

```sql
SELECT c.iso_numeric                        AS iso,
       EXTRACT(year FROM o.created_at)      AS year,
       SUM(o.amount)                        AS revenue,
       COUNT(DISTINCT o.customer_id)        AS customers
FROM orders o
JOIN country_codes c ON c.alpha2 = o.country_code
GROUP BY 1, 2
```

`<ChoroplethTime data={sales_by_country} id="iso" year="year"
metrics="revenue|Revenue|$,customers|Customers|customers" />` then works as-is
— and the same result drives the other maps (`year_value=` picks a snapshot;
BivariateMap reads two of the columns as `x`/`y`).

- **Sparse is fine.** A country missing from a year (or a `NULL` value) renders
  in the no-data wash; year gaps are fine too — each distinct year is one
  scrubber stop or facet, so decade-level data plays as five frames.
- **Keep it aggregated.** Every frame ships in the one query result (that's
  what keeps exports interactive), so return countries × years rows, not raw
  events.
- **Don't log-transform in SQL.** Use `scale="log"` instead, so tooltips and
  legends keep the real values.

## Zoom, pan & fullscreen

Every map card has the charts' hover-revealed ⛶ button, opening it in a
fullscreen modal with a **Map / Table** switcher (the table shows the same
query result). On the map itself — inline or fullscreen — **Ctrl + scroll**
(⌘ on macOS) or a trackpad pinch zooms around the pointer, dragging pans once
zoomed, double-click zooms in, and a **Reset view** pill (bottom-center, where
the zoom hint flashes) restores the full extent. Plain scrolling is
deliberately left to the page, so a map never traps the wheel.
(ChoroplethFacets panels stay un-zoomable small multiples — fullscreen is the
"see them bigger" affordance there.)

## AI commentary on geo maps

Every geo map takes the charts' `explain` attribute (needs an
[`llm:` block](/ai/ask#configuration)): a hover-revealed ✨ button that
generates commentary on demand into a footer under the map. On **BubbleMap**
and **DotDensityMap** the commentary can also [mark the map
itself](/ai/ask#annotations-on-the-chart): a cited country gets a dashed
**halo ring** with a leader-line label, referenced from the text by numbered
chips (hover a chip to bold its halo). Every proposal is validated
server-side against the join ids in the active year slice — a country the
frame doesn't draw can't earn a halo — and a halo scoped to one metric shows
only while that metric is toggled active. The choropleths
(ChoroplethTime/ChoroplethFacets/BivariateMap) stay commentary-only: facets,
animation frames, and two-metric encodings give one static mark nothing
stable to point at. `annotations=false` keeps any map commentary-only;
`explain="…"` and `cache_ttl=` work exactly as on charts.

```markdown
<BubbleMap data={world_indicators} id="iso" year="year" year_value="2020"
    metrics="population|Population|people" max_radius=35
    title="Population, 2020" explain />
```

## Shared attributes

Every map takes `data={query}` plus:

| Attribute | Purpose |
| --------- | ------- |
| `id` | Column holding the ISO 3166-1 numeric country code (default `iso`). |
| `title` | Card title (overlaid on the map's top-left). |
| `scheme` | Named color ramp: `blues`, `greens`, `oranges`, `purples`, `reds`, `viridis` (BivariateMap instead takes `blue-purple`, `green-blue`, `red-blue`). |
| `color` | Base color to derive a ramp from (defaults to `branding.palette`). |
| `scale` | Value→color mapping: `linear` (default), `log`, `quantile`. |
| `map` / `geojson` | Basemap: the bundled `world` (default), or a custom GeoJSON URL. |
| `id_field` | Feature property to join on in a custom GeoJSON (default `iso`). |
| `height` | Pixel height (default `420`). |
| `col-span` | Columns to span inside a `<Grid>`. |
| `empty_message` | Text shown when the query returns no rows. |
| `explain` | AI commentary footer, ✨ on hover (`explain="…"` pins your own question) — see [above](#ai-commentary-on-geo-maps). |
| `annotations` | `false` keeps an explained BubbleMap/DotDensityMap commentary-only (no halo marks). |
| `cache_ttl` / `max_rows` | Explain answer-cache TTL and row cap, as on charts. |

## ChoroplethTime

An animated choropleth: countries shaded by a metric, stepped across the
`year=` column by a play/scrub control. With several `metrics` entries
(`column|Label|unit`, comma-separated) a toggle switches between them; the
color scale stays fixed across all years so frames compare honestly.
`interval=` sets the frame duration in milliseconds.

```markdown
<ChoroplethTime data={world_indicators} id="iso" year="year"
    metrics="population|Population|people,gdp_per_capita|GDP per capita|$"
    scale="log" title="World development, 1960–2020" />
```

<ChoroplethTime data={world_indicators} id="iso" year="year" metrics="population|Population|people,gdp_per_capita|GDP per capita|$" scale="log" title="World development, 1960–2020" />

## ChoroplethFacets

Small multiples: one mini map per year on a **shared** color scale, for
comparing snapshots side by side. `years=` picks the facets (default: every
distinct year); `columns=` sets the grid width.

```markdown
<ChoroplethFacets data={world_indicators} id="iso" year="year"
    value="life_expectancy" years="1960,1980,2000,2020" columns=2
    label="Life expectancy" unit="years" scheme="greens"
    title="Life expectancy by decade" />
```

<ChoroplethFacets data={world_indicators} id="iso" year="year" value="life_expectancy" years="1960,1980,2000,2020" columns=2 label="Life expectancy" unit="years" scheme="greens" title="Life expectancy by decade" />

## BivariateMap

Two metrics on one map: each country's `x` and `y` values are classed into
terciles and colored from a 3×3 bivariate palette, with the classic square
legend overlaid in the map's bottom-left corner. With a `year=` column,
`year_value=` picks the snapshot (default: latest year).

```markdown
<BivariateMap data={world_indicators} id="iso" year="year" year_value="2020"
    x="gdp_per_capita" y="life_expectancy"
    xlabel="GDP per capita" ylabel="Life expectancy" xunit="$" yunit="years"
    title="Wealth vs. health, 2020" />
```

<BivariateMap data={world_indicators} id="iso" year="year" year_value="2020" x="gdp_per_capita" y="life_expectancy" xlabel="GDP per capita" ylabel="Life expectancy" xunit="$" yunit="years" title="Wealth vs. health, 2020" />

## BubbleMap

A proportional-symbol map: a circle on each country's centroid, **area** ∝
value, over a muted basemap. `max_radius=` caps the largest circle; several
`metrics` get a toggle.

```markdown
<BubbleMap data={world_indicators} id="iso" year="year" year_value="2020"
    metrics="population|Population|people" max_radius=35
    title="Population, 2020" explain />
```

<BubbleMap data={world_indicators} id="iso" year="year" year_value="2020" metrics="population|Population|people" max_radius=35 title="Population, 2020" explain />

## DotDensityMap

One dot per fixed quantity, scattered inside each country's borders. A metric
is `column|Label|unit|per_dot` — one dot stands for `per_dot` of the metric
(omit it to derive a value that keeps the map under `max_dots`). Placement is
seeded per country+metric, so the pattern is identical on every load.

```markdown
<DotDensityMap data={world_indicators} id="iso" year="year" year_value="2020"
    metrics="population|Population|people|10000000"
    title="Population, 2020 — 1 dot = 10M people" />
```

<DotDensityMap data={world_indicators} id="iso" year="year" year_value="2020" metrics="population|Population|people|10000000" title="Population, 2020 — 1 dot = 10M people" />

## Custom regions

The maps accept a custom basemap: point `geojson=` at a GeoJSON file (e.g.
under your project's `assets/`) and name the feature property that carries
your join key with `id_field=`. The frame **auto-fits** the geometry's extent,
so a regional map fills the card, and pan/zoom/reset stay bounded to it;
bubble and dot sizes stay card-relative. The bundled world geometry is Natural
Earth 110m (public domain), enriched with ISO numeric codes — this demo's
`europe.json` is a subset of it, so the default `id_field="iso"` join works
unchanged:

```markdown
<BubbleMap data={world_indicators} id="iso" year="year" year_value="2020"
    geojson="/assets/europe.json"
    metrics="population|Population|people"
    title="Population, 2020 — Europe" />
```

<BubbleMap data={world_indicators} id="iso" year="year" year_value="2020" geojson="/assets/europe.json" metrics="population|Population|people" title="Population, 2020 — Europe" />

## Per-component attributes

| Component | Attributes |
| --------- | ---------- |
| `ChoroplethTime` | `year` (default `year`), `metrics="col\|Label\|unit,…"` **(required)**, `interval` (ms, default `700`). |
| `ChoroplethFacets` | `year`, `value` **(required)**, `years="1990,2000,…"`, `label`, `unit`, `columns` (default `3`). |
| `BivariateMap` | `x`/`y` **(required)**, `xlabel`/`ylabel`, `xunit`/`yunit`, `year`, `year_value`. |
| `BubbleMap` | `metrics` **(required)**, `max_radius` (default `40`), `year`, `year_value`. |
| `DotDensityMap` | `metrics="col\|Label\|unit\|per_dot,…"` **(required)**, `dot_radius` (default `1.2`), `max_dots` (default `20000`), `year`, `year_value`. |
