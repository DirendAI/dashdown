---
title: ClickHouse
sidebar_label: ClickHouse
sidebar_position: 14
---

# ClickHouse connector

Connects to ClickHouse over the shared SQL DB-API base (lazy connect, JSON-safe
value coercion, reconnect-and-retry) via `clickhouse-connect`, ClickHouse's
official HTTP client. Works with self-hosted ClickHouse and ClickHouse Cloud.

```yaml
# sources.yaml
events:
  type: clickhouse
  host: ${CH_HOST}
  port: 8443
  database: analytics
  user: ${CH_USER}
  password: ${CH_PASSWORD}
  secure: true          # TLS — required by ClickHouse Cloud
```

| Key            | Purpose                                                         |
| -------------- | --------------------------------------------------------------- |
| `host` / `port`| Server address. Port is optional — the driver defaults to `8123` (`8443` when `secure`). |
| `database`     | Database name (`db` also accepted).                             |
| `user` / `password` | Credentials (`username` also accepted).                    |
| `secure`       | Connect over TLS.                                               |
| `dsn` / `url`  | …or a single `clickhouse://user:pass@host:port/db` URL instead of the above. |
| `connect_args` | Extra kwargs passed to the driver.                              |

**Install:** `uv add 'dashdown-md[clickhouse]'` (or `pip install 'dashdown-md[clickhouse]'`).
Secrets support `${ENV_VAR}` expansion — keep them out of the YAML.
