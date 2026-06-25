---
title: Snowflake
sidebar_label: Snowflake
sidebar_position: 12
---

# Snowflake connector

Snowflake over the SQL DB-API base. Connection details go under `connect_args`,
which are passed straight to `snowflake.connector.connect`.

```yaml
# sources.yaml
warehouse:
  type: snowflake
  connect_args:
    account: ${SNOWFLAKE_ACCOUNT}
    user: ${SNOWFLAKE_USER}
    password: ${SNOWFLAKE_PASSWORD}
    warehouse: COMPUTE_WH
    database: ANALYTICS
    schema: PUBLIC
```

| Key            | Purpose                                              |
| -------------- | --------------------------------------------------- |
| `connect_args` | All connection kwargs (account, user, warehouse, …).|

**Install:** `uv add 'dashdown-md[snowflake]'`. Use `${ENV_VAR}` for every secret.
