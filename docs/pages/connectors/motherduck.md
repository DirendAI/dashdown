---
title: MotherDuck
sidebar_label: MotherDuck
sidebar_position: 7
---

# MotherDuck connector

[MotherDuck](https://motherduck.com) is cloud DuckDB. It uses the same `duckdb`
driver and the same SQL — you just point at an `md:` database and authenticate
with a service token. So this connector is a thin subclass of the
[DuckDB connector](/connectors/duckdb): same engine, same resilience, plus your
cloud-hosted (and shared) databases.

```yaml
# sources.yaml
cloud:
  type: motherduck
  database: my_db                # optional — omit to attach all your databases
  token: ${MOTHERDUCK_TOKEN}     # optional — ${ENV_VAR} expansion supported
```

| Key            | Purpose                                                              |
| -------------- | ------------------------------------------------------------------- |
| `database`     | MotherDuck database name (becomes the `md:<database>` target). Omit for the bare `md:` connection, which attaches all your databases. |
| `token`        | MotherDuck service token. `${ENV_VAR}` is expanded. Omit to use the `motherduck_token` environment variable DuckDB reads on its own. |
| `duckdb_config`| Optional extra settings passed to `duckdb.connect` (e.g. `custom_user_agent`). |

Once connected, query it like any DuckDB source — `database`-qualify a table if
you've attached more than one:

```sql
SELECT region, sum(amount) AS revenue
FROM my_db.sales
GROUP BY region
ORDER BY revenue DESC
```

**Resilience:** inherited from the DuckDB connector — if a query *invalidates* the
connection, `query()` rebuilds it and retries once, so one bad query can't break
every later query on the long-lived connection.

:::note
A `${param}` always substitutes a **quoted string literal** (injection-safe — see
[Queries](/queries#parameters--injection-safety)), exactly as with the local
DuckDB connector.
:::

**Extra:** none — in the core install. The `duckdb` core dependency ships the
MotherDuck extension, which auto-loads on the first `md:` connect.
