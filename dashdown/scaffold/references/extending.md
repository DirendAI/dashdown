<!-- AUTO-GENERATED from docs/pages/ by tooling/gen-agent-docs.py — do not edit. -->
<!-- Topic: extending. Regenerate with: python tooling/gen-agent-docs.py -->

<!-- source: docs/pages/extending.md -->

# Extending Dashdown

Dashdown has two extension points, both plain Python: **custom components** and
**custom connectors**. Drop a `.py` file anywhere under your project's
`components/` directory — top-level or in a subfolder — and it's imported
automatically at load, so its `@register_…` decorator runs. (Files whose name
starts with `_` are skipped.) A component that needs a frontend keeps its JS and
CSS in the same folder as its `.py`; Dashdown serves and injects them for you
(see [Data-driven components](#data-driven-components)).

## Custom components

Subclass `Component`, implement `render()`, and register it under a tag name.
The file goes in your project's `components/` folder:

```python
# components/badge.py
from dashdown import Component, register_component

@register_component("Badge")
class Badge(Component):
    def render(self, attrs, ctx, inner=None):
        label = str(attrs.get("label", ""))
        return f'<span class="badge badge-primary">{label}</span>'
```

Use it in any page: `<Badge label="New" />`.

- **`attrs`** is the parsed attribute dict — `label="x"` is a string, bare
  values coerce to bool/int/float, and `data={query}` becomes a query
  reference.
- **`ctx`** is the render context: route params are on `ctx.params` (so a
  component on a [dynamic page](/pages#dynamic-pages-param-routes) can read `${id}`).
- **`inner`** is the inner HTML for a paired tag (`<Badge>…</Badge>`).
- An unknown tag or a `render()` that raises becomes an inline error card —
  never a 500.

:::note
A **presentational** component (like the badge above) just returns HTML. Set
`is_filter = True` on the class for a filter control, so it's stripped from
static builds (which can't re-query a snapshot).
:::

### Data-driven components

Queries never run during page render — they're fetched in the browser. So a
**data-driven** component returns a placeholder `<div data-async-component="…"
data-config="…">` and ships its **own JS** to fetch the query and draw into it.
Keep that JS (and any CSS) in the same folder as the `.py`:

```
components/Timeline/
  Timeline.py     # server render — registers the tag, emits the placeholder
  Timeline.js     # client hydration — fetches the query, draws the timeline
  Timeline.css    # styling (auto-linked)
```

Dashdown imports the `.py` and serves the `.js`/`.css` at
`/_dashdown/components/<folder>/<file>`, injecting them into every page — on the
dev server and in `dashdown build` exports alike. No `assets/`
wiring, no `<script>` tag in `render()`, and the `.py` source is **never**
web-served. The contract:

- **`data={query}` on any component** (built-in *or* custom) registers that query —
  so it's reachable at the data API and snapshotted by `dashdown build`. `attrs["data"]`
  is a `DataRef`; its query name is `attrs["data"].name`.
- **`app.js` only wires the *built-in* async types.** A custom `data-async-component`
  value is unknown to it, so your component must **self-init its JS** (scan for its
  placeholder on `DOMContentLoaded`).
- **`core.js` does the data work for you** — build detection, `data_url`
  resolution, in-flight dedup, caching, route-param merging for
  [detail pages](/detail-pages), and live-query subscriptions. Import its helpers
  through the **`dashdown/` import map** the page injects (a stable specifier that
  resolves no matter how the site is hosted): `recordsOf(await fetchQueryData(name))`
  returns plain `{column: value}` records (the raw data API answers with **rows
  as arrays**).

```python
# components/Timeline/Timeline.py
import html, json
from dashdown import Component, register_component

@register_component("Timeline")
class Timeline(Component):
    def render(self, attrs, ctx, inner=None):
        cfg = {
            "query": attrs["data"].name,                 # data={query} → DataRef
            "date": str(attrs.get("date", "date")),
            "label": str(attrs.get("label", "label")),
        }
        data_config = html.escape(json.dumps(cfg), quote=True)
        return f'<div data-async-component="timeline" data-config="{data_config}">…</div>'
```

```javascript
// components/Timeline/Timeline.js — self-inits (app.js wires only built-in types).
import { fetchQueryData, recordsOf, esc } from "dashdown/core.js";

function initAll() {
  for (const el of document.querySelectorAll('[data-async-component="timeline"]')) {
    const cfg = JSON.parse(el.dataset.config);
    fetchQueryData(cfg.query).then((data) => {
      el.innerHTML = recordsOf(data)
        .map((r) => `<li>${esc(String(r[cfg.date]))} — ${esc(String(r[cfg.label]))}</li>`)
        .join("");
    });
  }
}
// ES modules are deferred, so the DOM is ready when this runs.
if (document.readyState === "loading")
  document.addEventListener("DOMContentLoaded", initAll);
else initAll();
```

Use it on any page: `<Timeline data={recent_orders} date="date" label="product" />`.

:::note
Files whose name starts with `_` aren't auto-injected — name a shared helper
module `_utils.js` and `import` it from your component's JS (it still serves, it
just isn't loaded as its own `<script>`). The same `_` rule skips helper `.py`.
:::

:::tip
Keep a *component's own* JS/CSS in its folder; use the project-wide `assets/`
dir (served at `/assets/`) for assets **shared** across components or pages.
:::

## Custom connectors

Subclass `Connector`, implement `query()` returning a `QueryResult(columns,
rows)`, and register it under a type name. `__init__` receives the connector's
`sources.yaml` block as `config`:

```python
# components/trino.py
from dashdown import Connector, QueryResult, register_connector

@register_connector("trino")
class TrinoConnector(Connector):
    def query(self, sql: str) -> QueryResult:
        rows, cols = my_client.run(sql)   # however your backend executes SQL
        return QueryResult(columns=cols, rows=rows)

    def close(self) -> None:              # optional cleanup
        ...
```

Then point a source at it in `sources.yaml`:

```yaml
# sources.yaml
warehouse:
  type: trino
  host: ${TRINO_HOST}
```

### Shipping a connector as a plugin

To reuse a connector across projects, distribute it as its own PyPI package that
declares a `dashdown.connectors` **entry point** — no in-project file, no core
change. Dashdown discovers it and loads it lazily the first time a `sources.yaml`
asks for that type:

```toml
# the connector package's pyproject.toml
[project.entry-points."dashdown.connectors"]
trino = "dashdown_trino:TrinoConnector"
```

This is the exact mechanism the built-in connectors use — see
[Connectors](/connectors).
