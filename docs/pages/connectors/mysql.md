---
title: MySQL
sidebar_label: MySQL
sidebar_position: 10
---

# MySQL connector

MySQL/MariaDB over the same SQL DB-API base as Postgres (lazy connect, JSON-safe
coercion, reconnect-and-retry).

```yaml
# sources.yaml
warehouse:
  type: mysql
  host: ${MYSQL_HOST}
  port: 3306
  database: analytics
  user: ${MYSQL_USER}
  password: ${MYSQL_PASSWORD}
```

| Key            | Purpose                                              |
| -------------- | --------------------------------------------------- |
| `host` / `port`| Server address.                                     |
| `database`     | Database name (`db` also accepted).                 |
| `user` / `password` | Credentials.                                   |
| `dsn` / `url`  | …or a single connection string.                     |
| `connect_args` | Extra kwargs passed to the driver.                  |

**Install:** `uv add 'dashdown-md[mysql]'`.
