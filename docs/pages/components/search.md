---
title: Search (filter)
sidebar_label: Search
sidebar_position: 10
icon: "\U0001F50D"
---

# Search (filter)

A free-text filter. Its value lands in `filters[name]`; reference it in SQL with
`${name}`. This is a **query filter** — for searching across *pages*, use
[SiteSearch](/components/site-search) instead.

```sql
SELECT channel, SUM(downloads) AS downloads
FROM downloads
WHERE '${q}' = '' OR channel ILIKE '%' || '${q}' || '%'
GROUP BY channel
ORDER BY downloads DESC
```

```sql channel_like
SELECT channel, SUM(downloads) AS downloads
FROM downloads
WHERE '${q}' = '' OR channel ILIKE '%' || '${q}' || '%'
GROUP BY channel
ORDER BY downloads DESC
```

<Search name="q" label="Filter channels" placeholder="Type a channel…" />

<Table data={channel_like} title="Matching channels" />

| Attribute     | Purpose                                          |
| ------------- | ------------------------------------------------ |
| `name`        | **Required.** Filter key (`${name}` in SQL).     |
| `label`       | Accessible label.                                |
| `placeholder` | Input placeholder.                               |
| `debounce`    | Debounce in ms (default `300`).                  |
| `url_sync`    | Mirror to the URL (default `true`).              |
| `bar`         | Lift into the top [filter bar](/filters) (default: inline). |

Like other filter controls, `<Search>` is stripped from static builds.
