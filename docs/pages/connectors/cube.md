---
title: Cube
sidebar_label: Cube
sidebar_position: 16
---

# Cube connector

Connects to a [Cube](https://cube.dev) deployment — a standalone semantic-layer
*server* where a team models measures and dimensions, served over a structured
JSON API. Unlike every other connector, **Cube isn't queried with SQL**: the
connector is a thin HTTP+JWT client that powers the [`backend: cube` semantic
layer](/semantic-layer/cube), so you reference its model with
the `metric={…} by={…}` grammar rather than `:::query` SQL.

:::warning Experimental — preview
The Cube integration is **preview** — fully unit-tested with fakes but not yet
verified against a live Cube deployment, and it covers the
`measures`/`dimensions`/`timeDimensions`/`filters` subset of Cube's query shape.
Treat it as a preview to try, not a production guarantee.
:::

```yaml
# sources.yaml
cube:
  type: cube
  url: https://cube.example.com
  secret: ${CUBE_API_SECRET}      # HS256 signing secret (env-expanded)
  token_ttl: 300                  # JWT lifetime in seconds (default)
  security_context:               # optional — embedded in every JWT (the RLS rail)
    tenant_id: acme
```

| Key | Purpose |
| --- | ------- |
| `url` | **Required.** Base URL of the Cube deployment. |
| `secret` | HS256 signing secret — Dashdown mints a short-lived JWT per request. |
| `private_key` | RS256 private key, as an alternative to `secret`. |
| `token` | A static pre-minted JWT (escape hatch — no minting). |
| `algorithm` | Signing algorithm (default `HS256`). |
| `security_context` | Claims embedded in every JWT — Cube applies them server-side for row-level security. |
| `api_path` | API path (default `/cubejs-api/v1`). |
| `token_ttl` | JWT lifetime in seconds (default `300`); re-minted before expiry and once on a `401`. |
| `timeout` | Request timeout in seconds (default `60`). |

One of `secret`, `private_key`, or `token` is required.

## How it's used

Because Cube speaks a structured JSON query (not SQL), this connector is paired
with a `semantic/` model — Cube's `GET /meta` auto-fills the catalogue, so a model
is as small as `orders: { connector: cube }`:

```yaml
# semantic/orders.yml
orders:
  connector: cube
```

```markdown
<LineChart metric={orders.revenue} by={orders.createdAt} grain="month" />
<BarChart  metric={orders.count}   by={orders.status} />
```

See the [semantic layer → Cube backend](/semantic-layer/cube)
for the full modeling, filter mapping, JWT lifecycle, and `grain=` behaviour.
There is **no `${param}` injection surface** — filter values are JSON data, never
assembled into a query string.

:::tip Just want SQL access to Cube?
Cube also exposes a **Postgres-wire-compatible SQL API**. You don't need this
connector for that — point a [`postgres` connector](/connectors/postgres) at
Cube's SQL port and write `queries/*.sql`. This connector is for the first-class
`metric={…} by={…}` grammar.
:::

**Install:** `uv add 'dashdown-md[cube]'`.
