---
title: MapChart
sidebar_label: MapChart
sidebar_position: 11
---

# MapChart

A choropleth map — regions shaded by value. `location` names the region column,
`value` the metric. The built-in `world` map ships offline; point `geojson=` at a
custom GeoJSON (resolved from the project's `assets/`) for other maps.

```markdown
<MapChart data={downloads_by_country} location="country" value="downloads"
          map="world" title="Downloads by country" />
```

<MapChart data={downloads_by_country} location="country" value="downloads" map="world" title="Downloads by country" />

## From the semantic layer

Like every chart, MapChart also takes [semantic metric refs](/semantic-layer)
instead of `data={query}` — `by` is the region dimension (its values must match
the GeoJSON names) and `metric` shades each region; `map`/`geojson` stay literal:

```markdown
<MapChart metric={sales.revenue} by={sales.country} map="world" />
```

## Attributes

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

`location`/`value` are aliases for `x`/`y`; `map`/`geojson` are MapChart-specific. The rest are the shared chart attributes — see [Charts](/components/charts).
