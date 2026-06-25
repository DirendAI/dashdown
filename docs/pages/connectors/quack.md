---
title: Quack
sidebar_label: Quack
sidebar_position: 8
---

# Quack connector

[Quack](https://duckdb.org/quack/) is a remote protocol for DuckDB — it turns
DuckDB from an embedded engine into a client-server one. A DuckDB instance runs
as a server (`CALL quack_serve('quack:host', …)`) and clients reach it over the
network by attaching a `quack:` target. It's still the same `duckdb` driver and
the same SQL — only *where the data lives* changes. So this connector is a thin
subclass of the [DuckDB connector](/connectors/duckdb): same engine, same
resilience, pointed at a remote server instead of a local file.

```yaml
# sources.yaml
remote:
  type: quack
  host: data.example.com         # the quack server host (target becomes quack:<host>)
  port: 9494                     # optional — omit for the server's default port
  token: ${QUACK_TOKEN}          # optional — ${ENV_VAR} expansion supported
  database: remote               # optional — ATTACH alias (default "remote")
```

| Key                    | Purpose                                                                 |
| ---------------------- | ----------------------------------------------------------------------- |
| `host`                 | The Quack server host. Becomes the `quack:<host>` attach target. (Or set a full `target: quack:…` to pass it through verbatim.) |
| `port`                 | Optional server port, appended as `quack:<host>:<port>`. Omit for the default. |
| `token`                | Optional Quack auth token, registered as a `CREATE SECRET (TYPE quack …)`. `${ENV_VAR}` is expanded. |
| `database`             | The `ATTACH … AS <alias>` name you qualify remote tables with (default `remote`). |
| `install_extension`    | Whether to `INSTALL quack` before loading it (default `true`). Set `false` if the extension is already present in your DuckDB. |
| `extension_repository` | Where to install from (default `community`; a `https://…` URL is also accepted). |
| `duckdb_config`        | Optional extra settings passed to `duckdb.connect` (e.g. `allow_unsigned_extensions`). |

On connect the connector loads the Quack extension, registers the token secret
(if any), and `ATTACH`-es the remote. Then you query it like any DuckDB source,
qualifying remote tables with the attach alias:

```sql
SELECT region, sum(amount) AS revenue
FROM remote.sales
GROUP BY region
ORDER BY revenue DESC
```

**Resilience:** inherited from the DuckDB connector — if a query *invalidates* the
local connection, `query()` rebuilds it (re-loading the extension and re-attaching
the remote) and retries once.

:::note
A `${param}` always substitutes a **quoted string literal** (injection-safe — see
[Queries](/queries#parameters--injection-safety)), exactly as with the local
DuckDB connector.
:::

:::warning
**Experimental / preview.** Quack itself is in beta. This connector covers the
documented attach + token-secret flow and is not yet verified against a live Quack
server. The extension is a community extension, so a managed DuckDB build may need
`duckdb_config: { allow_unsigned_extensions: true }` to load it.
:::

**Extra:** none — in the core install. The Quack extension is downloaded at
runtime by DuckDB (`INSTALL quack FROM community`), so there is no extra `pip`
dependency.
