---
title: Table
sidebar_label: Table
sidebar_position: 2
icon: "\U0001F4CB"
---

# Table

A sortable, filterable data grid. Every table has a built-in **CSV export** button
(the ↓ in its header) that downloads the *current, filtered* rows.

```markdown
<Table data={channel_totals} title="Channel totals" search sort />
```

<Table data={channel_totals} title="Channel totals" />

Tables sort, search, format, and paginate client-side. Seed an initial order with
`sort=` (or click a header), format cells per column, page long results, and turn
rows into drill-down links:

<Table data={channel_totals} title="Top channels (sorted, formatted)" sort="downloads desc" format="downloads=number" />

<Table data={device_specs} title="Device specs (price formatted)" format="price=currency" />

<Table data={by_channel} title="By month & channel (search + paging)" search="Filter rows…" page-size="5" />

<Table data={channel_totals} title="Channels (click a row →)" row_link="/detail-pages/{channel}" />

## Common attributes

| Attribute             | Purpose                                                    |
| --------------------- | ---------------------------------------------------------- |
| `data`                | **Required.** The query to display.                        |
| `title`               | Heading above the table.                                   |
| `search`              | Show a per-table search box.                               |
| `sort`                | Enable column sorting.                                     |
| `limit`               | Max rows to render.                                        |
| `format`              | Per-column formatting, e.g. `format="downloads=number"`.   |
| `heatmap`             | Shade numeric cells by value, e.g. `heatmap="amount,profit"` (bare `heatmap` = all numeric columns). |
| `heatmap_scheme`      | `sequential` (default, low→high) or `diverging` (red↔green, centered at zero). |
| `export`              | `export=false` removes the CSV button.                     |
| `export_filename`     | Rename the downloaded CSV.                                 |
| `row_link`            | Make the **whole row** clickable, e.g. `row_link="/customers/{id}"`. |
| `link_column` / `link_pattern` | Turn a single column into links.                  |

Formatting helpers (`currency`, `decimals`, `locale`, `date_format`) work like
they do on charts — see [Formatting](/formatting) for the full reference and the
project-wide `format:` defaults. CSV export is built client-side (RFC 4180) and
works in static exports for free.

## Heatmap cells

`heatmap` shades numeric cells by their value — spreadsheet-style conditional
formatting — so the high and low points in a column jump out at a glance. Pass a
column list, or bare `heatmap` to shade **every** numeric column. Here it shades
the monthly downloads of each channel (the `month` column is text, so it's left
alone):

```markdown
<Table data={downloads_by_channel_wide} title="Monthly downloads by channel"
       heatmap format="pip=number,docker=number,source=number" />
```

<Table data={downloads_by_channel_wide} title="Monthly downloads by channel" heatmap format="pip=number,docker=number,source=number" />

The color scale is computed per column from its own min/max (over the full
result, so it stays stable as you sort, search, and page). Colors are drawn from
your **theme** — they follow the project's primary color and any `custom.css`
override, so the heatmap matches the rest of the UI.

`heatmap_scheme` picks the ramp — `sequential` (the default; low→high in the
theme's primary color, above) or `diverging` for **signed** values like
profit/variance, where it runs from the theme's error color through to its success
color, centered on zero. Here every cell is a channel-month's deviation from that
channel's average, so below-average months read red and above-average read green:

```markdown
<Table data={downloads_vs_avg} title="Downloads vs. channel average"
       heatmap heatmap_scheme="diverging" format="pip=number,docker=number,source=number" />
```

<Table data={downloads_vs_avg} title="Downloads vs. channel average" heatmap heatmap_scheme="diverging" format="pip=number,docker=number,source=number" />

The shading is a translucent overlay, so cell text stays legible in both light
and dark themes.

`row_link` (and `link_column` / `link_pattern`) fill `{column}` placeholders from
each row, so a table becomes the entry point to a [detail page](/detail-pages).
