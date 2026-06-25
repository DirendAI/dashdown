---
title: Postgres
sidebar_label: Postgres
sidebar_position: 9
---

# Postgres connector

Connects to PostgreSQL over the shared SQL DB-API base: lazy connect, JSON-safe
value coercion, and one reconnect-and-retry on a dropped connection.

```yaml
# sources.yaml
warehouse:
  type: postgres
  host: ${PG_HOST}
  port: 5432
  database: analytics
  user: ${PG_USER}
  password: ${PG_PASSWORD}
```

| Key            | Purpose                                            |
| -------------- | -------------------------------------------------- |
| `host` / `port`| Server address.                                    |
| `database`     | Database name (`dbname` also accepted).            |
| `user` / `password` | Credentials.                                  |
| `dsn` / `url`  | …or a single connection string instead of the above.|
| `connect_args` | Extra kwargs passed to the driver.                 |

**Install:** `uv add 'dashdown-md[postgres]'` (or `pip install 'dashdown-md[postgres]'`).
Secrets support `${ENV_VAR}` expansion — keep them out of the YAML.
