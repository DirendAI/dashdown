---
title: SQL Server
sidebar_label: SQL Server
sidebar_position: 11
---

# SQL Server / Azure SQL connector

Microsoft SQL Server and Azure SQL over the shared SQL DB-API base (lazy connect,
JSON-safe value coercion, reconnect-and-retry). Uses **pyodbc**, so it needs
Microsoft's ODBC driver on the host — install `msodbcsql18` (the Azure AD auth
modes below require ODBC Driver 18, or 17.x ≥ 17.3).

```yaml
# sources.yaml — SQL login
warehouse:
  type: mssql
  host: ${MSSQL_HOST}        # alias: server
  port: 1433                 # optional (default 1433)
  database: analytics        # alias: dbname
  user: ${MSSQL_USER}        # alias: uid
  password: ${MSSQL_PASSWORD}# alias: pwd
```

## Azure AD authentication

The connector supports the full Azure AD matrix. The mode is chosen by the
`authentication` key — or **inferred** for a service principal.

```yaml
# Service principal (client credentials) — Authentication is inferred from the pair
warehouse:
  type: mssql
  host: myserver.database.windows.net
  database: analytics
  client_id: ${AZURE_CLIENT_ID}
  client_secret: ${AZURE_CLIENT_SECRET}
  tenant_id: ${AZURE_TENANT_ID}        # optional
```

```yaml
# Managed identity (Azure VM / App Service)
warehouse:
  type: mssql
  host: myserver.database.windows.net
  database: analytics
  authentication: ActiveDirectoryMsi
  # client_id: <user-assigned-identity-id>   # omit for a system-assigned identity
```

Other `authentication` values: `ActiveDirectoryPassword` (Azure AD user + password),
`ActiveDirectoryDefault` (the default credential chain — env → managed identity →
CLI → …), `ActiveDirectoryInteractive`, `ActiveDirectoryIntegrated`.

| Key                          | Purpose                                                        |
| ---------------------------- | -------------------------------------------------------------- |
| `host` / `port`              | Server address (`server` also accepted; default port 1433).    |
| `database`                   | Database name (`dbname` also accepted).                        |
| `user` / `password`          | SQL login (`uid` / `pwd` also accepted).                       |
| `client_id` / `client_secret`| Azure AD service principal → inferred `ActiveDirectoryServicePrincipal`. |
| `tenant_id`                  | Optional; appended as `UID=client_id@tenant_id` when set.       |
| `authentication`             | Pick an Azure AD mode explicitly (see above).                  |
| `driver`                     | ODBC driver name (default `ODBC Driver 18 for SQL Server`).    |
| `encrypt` / `trust_server_certificate` | TLS toggles (`yes`/`no` or `true`/`false`).         |
| `connection_string` / `url`  | …or a full ODBC string / `mssql://…` URL instead of the above. |
| `odbc`                       | Map of extra raw ODBC keywords, merged last.                   |
| `connect_args`               | Extra kwargs passed straight to `pyodbc.connect()`.            |

**Install:** `uv add 'dashdown-md[mssql]'` (or `pip install 'dashdown-md[mssql]'`), plus
the `msodbcsql18` ODBC driver. Keep every secret in `${ENV_VAR}`, not the YAML.
