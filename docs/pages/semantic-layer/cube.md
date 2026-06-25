---
title: Cube backend
sidebar_label: Cube
sidebar_position: 2
---

# Cube backend (`backend: cube`)

[Cube](https://cube.dev) is a standalone semantic-layer *server* — a team models
measures, dimensions and joins **in Cube**, and Cube serves them over a structured
JSON API. Dashdown reaches that model with the **same `metric={…} by={…}`
grammar** via this backend: a metric reference compiles to a Cube
[`POST /load`](https://cube.dev/docs/reference/rest-api#load) query
(`{measures, dimensions, timeDimensions, filters, order}`) that runs in Cube.

:::tip References
- **Cube** — the semantic-layer server: [cube.dev](https://cube.dev) ·
  [docs](https://cube.dev/docs) · <https://github.com/cube-js/cube>
- **REST `/load` API** — the query shape Dashdown compiles to:
  <https://cube.dev/docs/reference/rest-api#load>
:::

:::warning Experimental — preview
The Cube backend is **preview**: its query builder, `/meta` parser, JWT lifecycle
and column rename are fully unit-tested with fakes, but it is **not yet verified
against a live Cube deployment**, and it covers the
`measures`/`dimensions`/`timeDimensions`/`filters` subset of Cube's query shape (no
segments, no relative-date keywords, a single time dimension for the global date
range). Treat it as a preview to try, not a production guarantee.
:::

Two things make Cube the *most* config-free and the *safest* backend:

- **`/meta` auto-introspection.** Cube publishes its model, so Dashdown reads
  `GET /meta` at load and fills the catalogue (measures vs dimensions, types, display
  formats, the time dimension) itself — a model is as small as `orders: { connector:
  cube }`, with **nothing re-declared**.
- **No injection surface — JSON, not a query string.** The compiled query is a Python
  `dict` serialized as the request body; filter values are JSON **data**, never
  assembled into a string. There is no `${param}` substitution and no string-escaping
  to get wrong.

## 1. Add the Cube source

A `type: cube` source is a thin HTTP client — it isn't queried with SQL; see the
[`cube` connector](/connectors/cube) for every config key:

```yaml
# sources.yaml
cube:
  type: cube
  url: https://cube.example.com
  secret: ${CUBE_API_SECRET}     # HS256 signing secret (env-expanded)
  token_ttl: 300                 # seconds (default)
  security_context:              # optional — embedded in every JWT (RLS rail)
    tenant_id: acme
```

## 2. Define a model

Typically **just the connector**, since `/meta` does the rest — auto-detected as
`backend: cube` from the `type: cube` connector:

```yaml
# semantic/orders.yml
orders:
  connector: cube
  # backend: cube            # optional — inferred from the `type: cube` connector
```

Reference it exactly like any model — same single-/multi-metric, `series=`, scalar,
table, filter, and [`grain=`](/semantic-layer#time-grain--grain) behaviour. A time
dimension buckets via `grain=` (the canonical token maps straight onto Cube's native
granularity), so there's nothing to declare for a time series:

```markdown
<BarChart  metric={orders.count}   by={orders.status} />
<LineChart metric={orders.revenue} by={orders.createdAt} grain="month" />
<Value     metric={orders.revenue} label="Revenue" />
```

## Filters & time grain on Cube

Filters map ~1:1 onto Cube's query: a `<Dropdown name="status">` becomes a
`{member, operator: "equals", values}` filter, and the global date range collapses
into a single `timeDimensions[].dateRange`. A time-type `by`/`series` dimension routes
to `timeDimensions[].granularity` (from `grain=`, or a model-level `granularity:`
default of `day`), not `dimensions[]` — Cube's `/meta` types tell us which is which.

## JWT & security context — the RLS rail

Dashdown mints a short-lived HS256 token (RS256 via `private_key`, or a static
`token` escape hatch) embedding the `security_context`, re-minting before expiry and
once on a `401`. Cube applies that context **server-side**, so a per-tenant identity
scopes every query without changing the compiled JSON. An HTTP 4xx/5xx surfaces as
the component's error card, never a 500. Cube's **pre-aggregations** are used
transparently (the framework is unaware of them).

If Cube is unreachable at load, project startup fails loudly (parity with a malformed
`sources.yaml`); set `optional: true` on the model to downgrade that to a warning + an
empty catalogue so an outage doesn't wedge the rest of the dashboard.

## Install

```bash
pip install 'dashdown-md[cube]'
```

:::tip Just want SQL access to Cube?
Cube also exposes a **Postgres-wire-compatible SQL API**. You don't need this backend
for that — point a [`postgres` connector](/connectors/postgres) at Cube's SQL port and
write `queries/*.sql` (with Cube's `MEASURE(...)`). This backend is for the
first-class `metric={…} by={…}` grammar, which the structured JSON API serves best
(richer `/meta`, no query-string escaping).
:::
