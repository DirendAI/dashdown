"""ClickHouse connector (via clickhouse-connect).

Heavy/optional dependency: install with `pip install 'dashdown-md[clickhouse]'`.

clickhouse-connect is ClickHouse Inc.'s official Python client (HTTP protocol,
port 8123 / 8443 with TLS) and ships a PEP 249 DB-API 2.0 wrapper, so this
connector is a thin subclass of the shared `DBAPIConnector` (see `dbapi.py`) —
only the driver and the `connect()` call differ from PostgreSQL/MySQL. Works
against self-hosted ClickHouse and ClickHouse Cloud (`secure: true`). ClickHouse
has no transactions, so the base's per-query `commit()` is a driver no-op; its
`OperationalError`/`InterfaceError` names match the base's reconnect heuristic.

sources.yaml example:
    events:
      type: clickhouse
      host: ch.example.com
      port: 8443                # optional; driver defaults to 8123 (8443 when secure)
      database: analytics       # alias: db
      user: reader              # alias: username
      password: secret
      secure: true              # TLS — required by ClickHouse Cloud
      # or a single URL instead of the fields above:
      # url: clickhouse://reader:secret@ch.example.com:8443/analytics?secure=true
      # connect_args:           # optional extra kwargs passed to the client
      #   connect_timeout: 10
"""
from __future__ import annotations

from typing import Any

from dashdown.data.base import register_connector
from dashdown.data.dbapi import DBAPIConnector, _import_driver


@register_connector("clickhouse")
class ClickHouseConnector(DBAPIConnector):
    extra = "clickhouse"
    driver = "clickhouse_connect"

    def _connect(self) -> Any:
        dbapi = _import_driver("clickhouse_connect.dbapi", "clickhouse")
        params: dict[str, Any] = {}
        # The driver parses a clickhouse:// DSN itself (query params included),
        # so a URL replaces the discrete keys rather than merging with them.
        dsn = self.config.get("url") or self.config.get("dsn")
        if dsn:
            params["dsn"] = dsn
        else:
            params = {
                "host": self.config.get("host", "localhost"),
                "database": self.config.get("database") or self.config.get("db"),
                "username": self.config.get("user") or self.config.get("username"),
                "password": self.config.get("password"),
                "secure": self.config.get("secure"),
            }
            port = self.config.get("port")
            if port is not None:
                params["port"] = int(port)
            params = {k: v for k, v in params.items() if v is not None}
        params.update(self.config.get("connect_args") or {})
        return dbapi.connect(**params)
