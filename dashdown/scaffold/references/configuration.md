<!-- AUTO-GENERATED from docs/pages/ by tooling/gen-agent-docs.py — do not edit. -->
<!-- Topic: configuration. Regenerate with: python tooling/gen-agent-docs.py -->

<!-- source: docs/pages/configuration.md -->

# Configuration (`dashdown.yaml`)

Every project has a `dashdown.yaml` at its root — the single config file for the
whole dashboard. Data connectors live separately in
[`sources.yaml`](/connectors).

Three rules hold for the entire file:

- **Everything is optional.** A minimal project is just `title:`. Each block below
  has safe defaults.
- **Malformed config fails fast.** A bad block raises at startup, so the server
  refuses to run half-configured rather than silently ignoring a typo.
- **Secrets read from the environment.** Any secret value supports `${VAR}`
  expansion (`api_key: ${ANTHROPIC_API_KEY}`), so credentials stay out of the file.

The dev server watches `dashdown.yaml` and reloads the project when it changes —
no restart needed.

## Full reference

Every block in one place. All of these are optional; delete what you don't use.

```yaml
title: My Analytics            # browser tab + header title

branding:                      # → see below
  logo: assets/logo.svg
  favicon: assets/favicon.png
  palette: ["#6366f1", "#22c55e", "#f59e0b"]

format:                        # → Formatting
  locale: en-US
  currency: USD
  date_format: MMM D, YYYY

llm:                           # → Ask
  provider: anthropic
  api_key: ${ANTHROPIC_API_KEY}
  model: claude-haiku-4-5

global_filters:                # → Filters
  date:
    enabled: true
    default: last_30_days
    start_param: date_start
    end_param: date_end

filters:                       # → Filters
  debounce: 300                # ms of quiet before a filter change re-fetches

search:                        # → Full-text search
  enabled: true
  placeholder: "Search…"
  max_results: 8

sidebar:                       # → Sidebar
  collapsed: false             # first-visit state; reader's choice is remembered
  toggle: true                 # show the desktop collapse button
  show_single_page: false      # show the nav even on a one-page project

python_queries:                # → Python queries
  enabled: true
```

## Top-level keys

| Key      | Default    | Purpose                                      |
| -------- | ---------- | -------------------------------------------- |
| `title`  | `Dashdown` | Shown in the browser tab and the app header. |

:::note
**Theme** is not configured here — it's viewer-controlled. The page follows the
visitor's OS light/dark preference by default; the header toggle overrides it
(saved per browser).
To restyle the look — colours, radii, spacing, chrome — drop a `assets/custom.css`
in your project; see **[Theming & styling](/theming)**.
:::

## `branding`

Logo, tab icon and chart colors. *(No separate page — this is the reference.)*

```yaml
branding:
  logo: assets/logo.svg        # project-relative path or an https:// URL
  favicon: assets/favicon.png  # overrides the default tab icon
  palette: ["#6366f1", "#22c55e", "#f59e0b"]   # chart series colors (hex)
```

| Key       | Purpose                                                                |
| --------- | --------------------------------------------------------------------- |
| `logo`    | Rendered in the header next to the title. A path is resolved relative to the project; an `http(s)`/`data:` URL is used as-is. |
| `favicon` | Overrides the bundled browser-tab icon (same path/URL rules).         |
| `palette` | List of hex colors overriding the default ECharts series palette on every chart. |

To restyle everything *else* — surfaces, accents, radii, the app chrome — use a
`assets/custom.css` file. → **[Theming & styling](/theming)**.

## `format`

Project-wide defaults for number, currency and date display (`locale`, `currency`,
`date_format`). A component's own `format=` attributes override these.

→ **[Formatting](/formatting)** for the full reference.

## `llm`

The LLM gateway used by [`<Ask />`](/ai/ask). Provider-only — `provider`
(`mistral` · `anthropic` · `openai` · `openrouter`), `api_key`, `model` — so
consumer knobs like `max_rows` stay on the component.

→ **[AI → Ask](/ai/ask)** for providers, extras and usage.

## `global_filters`

A single project-wide date-range control shown on every date-aware page; a query
opts in by using the `${date_start}` / `${date_end}` placeholders. The selection
persists across navigation.

→ **[Filters & parameters](/filters)** for presets, params and how queries opt in.

## `filters`

Cross-cutting behavior for the interactive filter controls (`<Search>`,
`<Combobox>`, `<Slider>`, `<RangeSlider>`, `<DateRange>`).

```yaml
filters:
  debounce: 300   # ms of quiet after the last keystroke / slider drag before
                  # a filter change commits its value and re-fetches data
```

| Key        | Default | Purpose                                                            |
| ---------- | ------- | ----------------------------------------------------------------- |
| `debounce` | `300`   | Project-wide quiet period (ms) before a filter change re-fetches. A burst of keystrokes or slider drag ticks coalesces into a single fetch. Raise it for a slow, per-query-expensive warehouse (e.g. BigQuery) where firing on partial input piles up requests; lower it for a snappy local backend. |

Any single control overrides this with its own `debounce=` attribute
(`<Search name="q" debounce={600} />`).

→ **[Filters & parameters](/filters)** for the controls themselves.

## `search`

The built-in full-text search box in the header. `enabled` (default **true**),
`placeholder`, `max_results`. Disabling it removes only the chrome box — the
[`<SiteSearch />`](/search) component still works.

→ **[Full-text search](/search)** for how the index is built and served.

## `sidebar`

Controls the left navigation menu. *(No separate page — this is the reference.)*

```yaml
sidebar:
  collapsed: false         # nav shown on first visit; true → start collapsed
  toggle: true             # show the desktop "hide sidebar" button
  show_single_page: false  # one-page project hides the nav; true → keep it
```

| Key                | Default | Purpose                                                              |
| ------------------ | ------- | ------------------------------------------------------------------- |
| `collapsed`        | `false` | First-visit state on desktop. Only a *seed* — once a reader toggles the sidebar, that choice is saved per browser and wins over this. |
| `toggle`           | `true`  | Show the desktop collapse button in the header. `false` pins the sidebar to whatever `collapsed` says (no control). |
| `show_single_page` | `false` | A project with a single navigable page has nothing to navigate to, so the nav **and** both menu buttons are hidden. Set `true` to keep the nav anyway. |

:::note
This is **desktop** behavior. The mobile slide-in menu (the ☰ button) is
unaffected by `collapsed`/`toggle`; it's only hidden when the nav is empty
(single page, no `show_single_page`). Dynamic `[slug]` pages don't count toward
the page total — they're already left out of the nav.
:::

## `python_queries`

The one policy knob for [Python queries](/python-queries) — `queries/*.py` files
whose decorated function returns a table.

```yaml
python_queries:
  enabled: true                # default true
```

| Key       | Default | Purpose                                                       |
| --------- | ------- | ------------------------------------------------------------- |
| `enabled` | `true`  | Whether `queries/*.py` files are loaded and run. Set `false` to skip them entirely. |

A Python query runs **author code in-process** — the same trust boundary as a
custom component (`components/*.py`), which is why the default is **on**. A
**managed / multi-tenant** host that serves semi-trusted project directories sets
`enabled: false` to refuse arbitrary in-process code execution; the `.py` files
are then skipped (not imported, not registered) and any reference 404s as an
unknown query. SQL/DAX library queries are unaffected.

→ **[Python queries](/python-queries)** for the function contract and the
params-are-data guarantee.

## Not in this file

- **Data connectors** live in [`sources.yaml`](/connectors), not `dashdown.yaml`.
- **Per-page** settings (title, icon, route params) are page
  [frontmatter](/pages#frontmatter).
