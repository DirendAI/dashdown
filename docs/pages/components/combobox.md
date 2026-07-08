---
title: Combobox
sidebar_label: Combobox
sidebar_position: 16
icon: "\U0001F50E"
---

# Combobox

A **searchable** single-select for a **high-cardinality** column — type to filter
over thousands of customers, SKUs, or users where a plain
[`<Dropdown>`](/components/dropdown) (which loads *every* distinct value) would
choke.

Options are fetched **server-side as you type**: the browser hits
`/_dashdown/api/options/{query}` and the backend runs a `SELECT DISTINCT … WHERE
col ILIKE '%term%' LIMIT N` against the warehouse, so only a small matching page
is ever shipped. Results rank **prefix matches first** (typing `num` surfaces
`numpy` above `abnum`), alphabetical within each band. The picked value lands in
`filters[name]` like every other filter, so your SQL reads it with `${name}` and
the empty (nothing picked) value trips the all-guard:

```sql
SELECT country, downloads
FROM by_country
WHERE '${country}' = '' OR country = '${country}'
ORDER BY downloads DESC
```

```sql country_rows
SELECT country, downloads
FROM by_country
WHERE '${country}' = '' OR country = '${country}'
ORDER BY downloads DESC
```

```sql countries
SELECT country FROM by_country
```

<Combobox name="country" data={countries} column="country" label="Country" placeholder="Search countries…" />

<BarChart data={country_rows} x="country" y="downloads" title="Downloads by country" />

Start typing — the panel lists matching values fetched from the server; pick one
and the chart re-queries. The **×** clears the selection.

The `data={query}` + `column` pair names where the distinct values come from. It
needn't be the same query the chart shows — point it at a lightweight lookup
(`SELECT country FROM by_country`) and let the heavier display query carry the
`${country}` guard.

## Multi-select

Add **`multi`** to pick several values. They're stored as one comma-joined string
that feeds an `IN (…)` clause — identical to a multi-select
[`<Dropdown>`](/components/dropdown), so the values expand into a quoted,
per-item-escaped literal list (empty selection → matches all):

```sql
SELECT country, downloads
FROM by_country
WHERE '${countries}' = '' OR country IN (${countries})
ORDER BY downloads DESC
```

```sql country_multi
SELECT country, downloads
FROM by_country
WHERE '${countries}' = '' OR country IN (${countries})
ORDER BY downloads DESC
```

<Combobox name="countries" data={countries} column="country" label="Countries" multi placeholder="Add a country…" />

<BarChart data={country_multi} x="country" y="downloads" title="Downloads (selected countries)" />

Picks show as removable chips before the search box; the panel marks chosen rows
with a ✓ and stays open so you can add several. **Backspace** on an empty input
removes the last chip.

| Attribute   | Purpose                                                            |
| ----------- | ----------------------------------------------------------------- |
| `name`      | **Required.** Filter key your SQL reads as `${name}`.             |
| `data` + `column` | **Required.** The query + column the distinct values come from. |
| `multi`     | Multi-select → a comma-joined value for an `IN (…)` clause.        |
| `label`     | Inline label (defaults to `name`).                                |
| `placeholder` | Input placeholder (default `"Search…"`).                        |
| `limit`     | Max options fetched per keystroke (default `50`; server caps at `200`). |
| `min_chars` | Only search once this many characters are typed (default `0`).    |
| `bar`       | Lift into the top [filter bar](/filters) (default: inline).       |

:::note
**SQL connectors only** — the options endpoint wraps your query as a subquery,
which a non-SQL backend (DAX) or a [Python query](/python-queries) can't satisfy.
The search term and column go through the **same injection-safe rules** as
`${param}` substitution (the column must be a bare identifier; the term is always
a quoted literal), so there's no new injection surface. Like the other filter
controls, `<Combobox>` is stripped from [static builds](/exporting) — a fixed
snapshot has no server to search.
:::
