"""Snowflake connector (via snowflake-connector-python).

Heavy/optional dependency: install with `pip install 'dashdown-md[snowflake]'`.

snowflake-connector-python exposes the standard PEP 249 DB-API 2.0 interface, so
this connector is a thin subclass of the shared `DBAPIConnector` (see `dbapi.py`)
— only the driver and the `connect()` call differ from PostgreSQL/MySQL.

sources.yaml example:
    warehouse:
      type: snowflake
      account: ab12345.eu-central-1   # the account identifier (required)
      user: reader
      password: secret
      warehouse: COMPUTE_WH           # optional
      database: ANALYTICS             # optional
      schema: PUBLIC                  # optional
      role: REPORTER                  # optional
      # connect_args:                 # optional extra kwargs passed to connect()
      #   authenticator: externalbrowser
"""
from __future__ import annotations

from typing import Any

from dashdown.data.base import register_connector
from dashdown.data.dbapi import DBAPIConnector, _import_driver


@register_connector("snowflake")
class SnowflakeConnector(DBAPIConnector):
    extra = "snowflake"
    driver = "snowflake.connector"

    #: Config keys that map straight onto snowflake.connector.connect() kwargs.
    _PASSTHROUGH = (
        "account", "user", "password", "warehouse", "database",
        "schema", "role", "authenticator", "token", "host", "port",
    )

    def _connect(self) -> Any:
        snowflake = _import_driver("snowflake.connector", "snowflake")
        params: dict[str, Any] = {
            k: self.config[k] for k in self._PASSTHROUGH if self.config.get(k) is not None
        }
        params.update(self.config.get("connect_args") or {})
        return snowflake.connect(**params)
