---
title: DateRange
sidebar_label: DateRange
sidebar_position: 10
icon: "\U0001F4C5"
---

# DateRange

A start/end date control with presets (`last_7_days`, `this_month`, `custom`, …).
It writes two URL params — `start_param` / `end_param` (default `name_start` /
`name_end`) — that your SQL reads.

```sql
SELECT date, visits
FROM daily
WHERE ('${from}' = '' OR date >= '${from}')
  AND ('${to}' = '' OR date <= '${to}')
ORDER BY date
```

```sql daily_in_range
SELECT date, visits
FROM daily
WHERE ('${from}' = '' OR date >= '${from}')
  AND ('${to}' = '' OR date <= '${to}')
ORDER BY date
```

<DateRange name="period" label="Period" start_param="from" end_param="to" presets="last_7_days,last_30_days,custom" />

<LineChart data={daily_in_range} x="date" y="visits" title="Visits in range" />

`default=` seeds a preset on first load (URL params still win):

<DateRange name="period2" label="Period" start_param="from2" end_param="to2" presets="last_7_days,last_30_days,custom" default="last_30_days" />

| Attribute       | Purpose                                                   |
| --------------- | --------------------------------------------------------- |
| `name`          | **Required.** Base filter key.                            |
| `start_param` / `end_param` | URL/SQL param names (default `name_start` / `name_end`). |
| `presets`       | Comma-separated preset list, in display order.            |
| `default`       | A preset applied on first load.                           |
| `persist`       | Remember the selection in `localStorage` across pages.    |
| `bar`           | Lift into the top [filter bar](/filters) (default: inline). |

The project-wide [global date filter](/filters) is this same control configured
once in `dashdown.yaml`. Filter controls are stripped from static builds.
