---
title: Dropdown
sidebar_label: Dropdown
sidebar_position: 8
icon: "\U0001F53D"
---

# Dropdown

A select filter. Its value lands in the reactive `filters` store under `name`;
any query using `${name}` re-runs. Options come from a query column (`data` +
`column`) or a literal `options` list. Add `multi` for a multi-select that feeds
an `IN (…)` clause.

```sql
SELECT month, SUM(downloads) AS downloads
FROM downloads
WHERE '${channel}' = '' OR channel = '${channel}'
GROUP BY month
ORDER BY month
```

```sql dl_by_channel connector=main
SELECT month, SUM(downloads) AS downloads
FROM downloads
WHERE '${channel}' = '' OR channel = '${channel}'
GROUP BY month
ORDER BY month
```

<Dropdown name="channel" data={all_channels} column="channel" label="Channel" />

<LineChart data={dl_by_channel} x="month" y="downloads" title="Downloads (filtered)" />

Pick a channel — the chart re-queries. The `'${channel}' = ''` guard makes "no
selection" mean "all".

Add `multi` for a multi-select whose chosen values expand into an `IN (…)` list
(the same empty-means-all guard applies):

```sql
SELECT month, SUM(downloads) AS downloads
FROM downloads
WHERE '${channels}' = '' OR channel IN (${channels})
GROUP BY month
ORDER BY month
```

```sql dl_multi connector=main
SELECT month, SUM(downloads) AS downloads
FROM downloads
WHERE '${channels}' = '' OR channel IN (${channels})
GROUP BY month
ORDER BY month
```

<Dropdown name="channels" data={all_channels} column="channel" label="Channels" multi />

<LineChart data={dl_multi} x="month" y="downloads" title="Downloads (multi-select)" />

| Attribute   | Purpose                                                  |
| ----------- | -------------------------------------------------------- |
| `name`      | **Required.** Filter key (the `${name}` your SQL reads). |
| `data` + `column` | Populate options from a query column.              |
| `options`   | …or a literal comma-separated list.                      |
| `label`     | Control label.                                           |
| `multi`     | Multi-select → `IN (…)`.                                  |
| `url_sync`  | Mirror the value to the URL (default `true`).            |
| `bar`       | Lift into the top [filter bar](/filters) (default: inline). |

:::note
Filter controls drive server-side SQL substitution, so they're stripped from
`dashdown build` static exports (a fixed snapshot can't re-query). See
[Filters](/filters).
:::
