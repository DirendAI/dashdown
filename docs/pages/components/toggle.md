---
title: Toggle
sidebar_label: Toggle
sidebar_position: 12
icon: "\U0001F518"
---

# Toggle

A one-click switch (or checkbox) for a **boolean / two-valued** facet — "show
only X", "include archived", "paid only". It writes a string into the filter
store under `name`, exactly like the other filters, so your SQL reads it with
`${name}`.

```sql
SELECT date, visits
FROM daily
WHERE '${busy}' = '' OR visits >= 500
ORDER BY date
```

```sql daily_traffic
SELECT date, visits
FROM daily
WHERE '${busy}' = '' OR visits >= 500
ORDER BY date
```

<Toggle name="busy" label="Busy days only" />

<LineChart data={daily_traffic} x="date" y="visits" title="Daily visits" explain />

Flip the switch — when **off** it stores `""`, so the `'${busy}' = ''` guard
passes and every day shows; when **on** it stores `"true"`, the guard fails, and
only `visits >= 500` days remain. This is the same "empty means all" convention a
[`<Dropdown>`](/components/dropdown) uses.

| Attribute    | Purpose                                                              |
| ------------ | ------------------------------------------------------------------- |
| `name`       | **Required.** Filter key your SQL reads as `${name}`.               |
| `label`      | Inline label shown in the control pill (defaults to `name`).        |
| `on_value`   | Value stored when **checked** (default `"true"`; any string).       |
| `off_value`  | Value stored when **unchecked** (default `""`; any string).         |
| `default`    | Start checked on first load (URL params still win).                 |
| `variant`    | `switch` (default) or `checkbox` styling.                           |
| `bar`        | Lift into the top [filter bar](/filters) (default: inline).         |

## Value modes

The default `on_value="true"` / `off_value=""` is the **all-guard** above — off
shows everything. Because `on_value` / `off_value` are **arbitrary strings**, a
non-empty `off_value` turns it into a **two-state** filter where both directions
narrow the data — including a text column that stores something like `Yes`/`No`:

```sql
SELECT date, visits
FROM daily
WHERE CASE WHEN '${weekend}' = 'Yes' THEN weekday IN ('Sat','Sun')
           ELSE weekday NOT IN ('Sat','Sun') END
ORDER BY date
```

```sql daily_weekend
SELECT date, visits
FROM daily
WHERE CASE WHEN '${weekend}' = 'Yes' THEN weekday IN ('Sat','Sun')
           ELSE weekday NOT IN ('Sat','Sun') END
ORDER BY date
```

<Toggle name="weekend" label="Weekends only" on_value="Yes" off_value="No" />

<LineChart data={daily_weekend} x="date" y="visits" title="Visits (weekend vs weekday)" explain />

Here **checked** sends `weekend = 'Yes'` (weekend days) and **unchecked** sends
`'No'` (weekdays) — there's no "show all" state, so both directions narrow the
data. `variant="checkbox"` swaps the switch for a checkbox:

<Toggle name="weekend_cb" label="Weekends only" on_value="Yes" off_value="No" variant="checkbox" />

(For three states — All / Yes / No — use a
[`<Dropdown options="Yes,No">`](/components/dropdown) instead; `<Toggle>` is the
one-click binary affordance.)

:::note
The value reaches SQL only through the same context-aware `${param}`
substitution as every other filter — it's always a quoted string literal, so a
toggle adds no new injection surface. Like the other filter controls, `<Toggle>`
is stripped from [static builds](/exporting) (a fixed snapshot can't be
re-filtered).
:::
