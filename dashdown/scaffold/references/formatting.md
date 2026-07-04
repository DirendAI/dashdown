<!-- AUTO-GENERATED from docs/pages/ by tooling/gen-agent-docs.py — do not edit. -->
<!-- Topic: formatting. Regenerate with: python tooling/gen-agent-docs.py -->

<!-- source: docs/pages/formatting.md -->

# Number & date formatting

Tables, counters, values and chart axes all render numbers and dates through
**one** formatter, so `63712.895` becomes `$63,712.90` the same way everywhere.
You control it two ways:

- **Per component** — `format=` (and its modifiers) on a `<Table>`, `<Counter>`,
  `<Value>` or any chart.
- **Project-wide** — a `format:` block in `dashdown.yaml` that sets the default
  **locale**, **currency** and **date pattern** for the whole dashboard (the
  closest thing to a "culture" setting).

Formatting is **display only** — it runs in the browser over the query result and
never changes the underlying data, so a CSV export still carries the raw values.

## The `format` attribute

`format=` chooses *how* a value reads. It is honored by `<Table>` (per column),
`<Counter>`, `<Value>` and every chart (value axis + tooltips).

| `format`     | Renders                          | Example (`en-US`)        |
| ------------ | -------------------------------- | ------------------------ |
| `number`     | Grouped integer/decimal          | `1234567` → `1,234,567`  |
| `compact`    | Abbreviated magnitude — 3 significant digits (or pin with `decimals=`); on a `<Counter>` the exact value shows on hover | `3338316067` → `3.34B`   |
| `currency`   | Money, with a symbol             | `63712.9` → `$63,712.90` |
| `percent`    | Number with a `%` suffix         | `42.5` → `42.5%` *(not ×100 — the value is shown as-is)* |
| `date`       | Locale-aware date                | `2026-01-31` → `Jan 31, 2026` |
| `datetime`   | Date **and** time                | `2026-01-31T14:05` → `Jan 31, 2026, 2:05 PM` |

Omit `format` and values pass through unchanged — except that `decimals=` on its
own still rounds a bare number, so `decimals=2` rounds without picking a style.

On charts, value-axis **ticks** compact automatically once they reach 1,000
(`1,500,000,000` → `1.5B`) so a big-number scale stays readable instead of
clipping — tooltips and data labels keep the exact format (`number`/`currency`
symbols included). Set `format="compact"` explicitly to compact the tooltips too.

:::note
`percent` does **not** multiply by 100 — it appends `%` to the value as stored.
If your column holds a fraction like `0.425`, compute `× 100` in SQL (or store it
as `42.5`) before formatting.
:::

## Modifiers

These tune the chosen `format`. Set only the ones you need:

| Attribute      | Applies to        | Purpose                                                                 |
| -------------- | ----------------- | ----------------------------------------------------------------------- |
| `currency`     | `currency`        | A bare **symbol** (`$`, `€`) is prepended; an **ISO 4217 code** (`EUR`, `USD`, `GBP`) uses full locale-aware currency formatting — correct symbol placement *and* separators (e.g. `de-DE` → `1.157,33 €`). Default `$`. |
| `decimals`     | numbers / currency | Pin the fraction-digit count, overriding the format's default.         |
| `locale`       | all               | A **BCP-47** tag (`de-DE`, `fr-FR`, `en-IN`) controlling grouping / decimal separators and date labels. Omitted → the project default, then the viewer's browser locale. |
| `date_format`  | `date` / `datetime` | A date pattern — see below.                                           |

### Example

````markdown
```sql fmt_demo
SELECT SUM(downloads) AS downloads, SUM(downloads) * 0.12 AS revenue
FROM downloads
```

<Counter data={fmt_demo} column="downloads" format="number" label="Total downloads" />
<Counter data={fmt_demo} column="revenue" format="currency" currency="EUR" decimals=2 label="Revenue (€0.12 / download)" />
````

Rendered live from this site's CSV:

<Grid columns=2>
<Counter data={fmt_demo} column="downloads" format="number" label="Total downloads" />
<Counter data={fmt_demo} column="revenue" format="currency" currency="EUR" decimals=2 label="Revenue (€0.12 / download)" />
</Grid>

On a `<Table>`, `format=` is **per column** — `key=value` pairs:

```markdown
<Table data={orders} format="amount=currency, ordered_at=date, rate=percent" currency="USD" decimals=2 />
```

## Date patterns (`date_format`)

`date_format` overrides the default locale-aware date. It accepts either a
**style keyword** or a **token pattern** — no moment.js dependency, the token
subset is implemented in the framework.

**Style keywords** (locale-aware, the OS/browser decides the exact layout):
`short` · `medium` · `long` · `full` — e.g. `date_format="long"` → `January 31, 2026`.

**Token patterns** — e.g. `date_format="DD.MM.YYYY"` → `31.01.2026`,
`date_format="MMM D, YYYY h:mm A"` → `Jan 31, 2026 2:05 PM`:

| Token  | Meaning                | Token  | Meaning                  |
| ------ | ---------------------- | ------ | ------------------------ |
| `YYYY` | 4-digit year           | `dddd` | Weekday name (long)      |
| `YY`   | 2-digit year           | `ddd`  | Weekday name (short)     |
| `MMMM` | Month name (long)      | `HH` / `H` | 24-hour, padded / bare |
| `MMM`  | Month name (short)     | `hh` / `h` | 12-hour, padded / bare |
| `MM` / `M` | Month number, padded / bare | `mm` / `m` | Minutes, padded / bare |
| `DD` / `D` | Day of month, padded / bare | `ss` / `s` | Seconds, padded / bare |
| `A` / `a` | `AM`/`PM` · `am`/`pm` | `[text]` | Literal text, emitted verbatim |

Localized names (`MMMM`, `ddd`, …) follow the active `locale`. Wrap any literal
characters that look like tokens in brackets: `[Q]MMM` keeps the `Q`.

## Project-wide defaults — the `format:` block

Set defaults once in `dashdown.yaml` and every component inherits them, so you
don't repeat `locale=` / `currency=` on each widget:

```yaml
format:
  locale: de-DE            # BCP-47 tag: grouping / decimal separators + date labels
  currency: EUR            # default symbol ("€") or ISO 4217 code ("EUR")
  date_format: DD.MM.YYYY  # default date pattern for format="date"
```

All three keys are optional, and a malformed block fails fast at startup (like
`branding:`), so the server never runs with a half-broken config.

**Precedence — component attribute wins, the block fills the gaps.** A widget's
own `locale=` / `currency=` / `date_format=` always overrides the project default;
where a widget says nothing, the block supplies the value. `locale` also drives
`format="date"` rendering, so dates follow the project locale without a per-column
setting.

:::tip
The `currency` default is applied **only** where a component already opts into
`format="currency"`. It supplies *which* symbol to use — it never turns a plain
number (a row count, a download total) into money. The block sets *values*, not
*whether* a value is formatted.
:::

Defaults are baked into the page, so they apply identically on the dev server and
in a `dashdown build` static export.

:::note
These docs themselves set `format: { locale: en-US }` in `docs/dashdown.yaml` —
which is why the numbers on every page group with `,` thousands separators.
:::

## Where formatting applies

- **[Table](/components/table)** — per-column via `format="col=type, …"`.
- **[Counter](/components/counter)** · **[Value](/components/value)** — the single
  displayed figure.
- **[Charts](/components/charts)** — value axis labels and tooltips.
