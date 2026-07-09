<!-- AUTO-GENERATED from docs/pages/ by tooling/gen-agent-docs.py — do not edit. -->
<!-- Topic: filters. Regenerate with: python tooling/gen-agent-docs.py -->

<!-- source: docs/pages/filters.md -->

# Filters & parameters

Filters write into a central reactive store; any query that references the
filter's name with `${...}` re-runs when it changes. The selection is mirrored to
the URL, so a filtered view is shareable and bookmarkable.

<Dropdown name="channel" data={all_channels} column="channel" label="Channel" />

<LineChart data={filtered} x="month" y="downloads" title="Downloads (filtered)" explain />

Pick a channel above — the chart re-queries. The `'${channel}' = ''` guard makes
"no selection" mean "all".

## The filter components

| Component     | Purpose                                                   |
| ------------- | -------------------------------------------------------- |
| `<Dropdown>`  | Single- or multi-select (`multi`). Multi feeds `IN (…)`. |
| `<Combobox>`  | Searchable single-select for high-cardinality columns (server-side search). See [Combobox](/components/combobox). |
| `<Search>`    | A free-text filter value.                                |
| `<DateRange>` | A start/end date pair with presets.                      |
| `<RangeSlider>` | A numeric low/high range, dual-handle slider. See [RangeSlider](/components/range-slider). |
| `<Slider>`    | A single-value numeric threshold, one-handle slider. See [Slider](/components/slider). |
| `<ButtonGroup>` | A single-select segmented control for a few fixed options. See [ButtonGroup](/components/button-group). |
| `<Toggle>`    | A boolean on/off switch — "show only X". See [Toggle](/components/toggle). |

:::note
Filter controls drive **server-side** SQL substitution, so they can't re-query a
fixed snapshot. `dashdown build` strips them from static exports automatically.
The [full-text `<SiteSearch>`](/search) is **not** a filter — it searches a
static index and ships in exports.
:::

### Debouncing

Typing in a `<Search>` / `<Combobox>` or dragging a `<Slider>` / `<RangeSlider>`
doesn't fire a query on every keystroke or pixel. A change waits for a short
**quiet period** (default **300 ms**) before it commits and re-fetches, so a burst
of input coalesces into one fetch — the handle and readout still move instantly.

Set the project-wide default in `dashdown.yaml`, and override per control with a
`debounce=` attribute (both in milliseconds):

```yaml
filters:
  debounce: 500   # raise it for a slow, per-query-expensive warehouse (BigQuery)
```

```markdown
<Search name="q" debounce={800} />   <!-- this control only -->
```

Raise it when each query is slow or billed so deliberate typing can't fan out a
wave of partial-input queries; lower it (even `0` = immediate) for a snappy local
backend. → **[Configuration](/configuration#filters)**.

## Where filters appear

A filter renders **inline, exactly where you place it** in the Markdown. So you
can drop a `<Dropdown>` directly above the one chart it controls — like the
Channel picker above — and it stays there:

```markdown
## Revenue by region

<Dropdown name="region" data={regions} column="region" label="Region" />

<BarChart data={revenue} x="month" y="revenue" series="region" />
```

To gather page-wide filters into a single row at the top instead, add **`bar`**:

```markdown
<DateRange name="period" bar />
<Dropdown name="region" data={regions} column="region" bar />
<RangeSlider name="price" min={0} max={1000} bar />
<ButtonGroup name="tier" options="High,Mid,Budget" bar />
<Toggle name="paid" label="Paid only" bar />
<Search name="q" bar />
```

Controls marked `bar` collect into a filter bar below the page title. When more
than three are present the extras fold into a **"Filters"** drawer, and the bar
shows dismissible **active-filter chips** with a **"Clear all"**. Frontmatter
`filters: drawer` sends every `bar` control straight to the drawer:

```yaml
---
filters: drawer
---
```

:::tip
Mix freely: keep the one dropdown a chart depends on inline next to it, and lift
the page-wide date range and search into the `bar`. A page with no `bar` controls
has no top filter row at all.
:::

## Global date filter

Unlike the filter components above (placed per page in your Markdown), the
**global date filter** is a single date-range control shown in the **app header
on every page**, applying project-wide. Configure it once in `dashdown.yaml`:

```yaml
global_filters:
  date:
    enabled: true                 # off by default — this turns it on
    label: Period                 # the control's label
    default: last_30_days         # preset applied on a visitor's first load
    presets: last_7_days,last_30_days,last_90_days,this_month,this_year,custom
    start_param: date_start       # the ${param} names your queries reference
    end_param: date_end
```

Only `enabled: true` is required; every other key has the default shown above.

Queries **opt in purely by convention** — any query whose SQL references the
configured `${date_start}` / `${date_end}` placeholders is filtered; queries
without them are untouched. There's no backend query rewriting; the control just
writes those two params into the filter store and the normal re-fetch path does
the rest.

```sql
SELECT day, SUM(downloads) AS downloads
FROM downloads
WHERE day BETWEEN '${date_start}' AND '${date_end}'
GROUP BY day
ORDER BY day
```

:::tip
The control appears **only on pages whose queries actually reference those
params** — a docs or non-date page shows no dead control, while the persisted
selection still applies once you navigate to a date-aware page.
:::

**Placement & persistence.** On a normal page the control lives in the sticky
app header (reachable even when scrolled). The selection **persists across
navigation** (localStorage), so it behaves as one global filter rather than a
per-page one — URL params still win over the remembered value. [Static builds](/exporting)
omit it (a fixed snapshot can't be re-filtered), like the other filter controls.

It reuses the same control as the [`<DateRange>`](/components/date-range)
component, so presets, the custom range picker, and URL sync all behave
identically.
