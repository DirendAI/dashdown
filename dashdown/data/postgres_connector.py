"""PostgreSQL connector (via psycopg2).

Heavy/optional dependency: install with `pip install 'dashdown-md[postgres]'`.

sources.yaml example:
    warehouse:
      type: postgres
      host: localhost
      port: 5432
      database: analytics      # alias: dbname
      user: reader
      password: secret
      # or a single libpq connection URL / DSN instead of the fields above:
      # url: postgresql://reader:secret@localhost:5432/analytics
      # connect_args:          # optional extra kwargs passed to psycopg2.connect
      #   sslmode: require
"""
from __future__ import annotations

from typing import Any

from dashdown.data.base import register_connector
from dashdown.data.dbapi import DBAPIConnector, _import_driver


@register_connector("postgres")
class PostgresConnector(DBAPIConnector):
    extra = "postgres"
    driver = "psycopg2"

    def _connect(self) -> Any:
        psycopg2 = _import_driver("psycopg2", "postgres")
        # psycopg2 accepts a libpq URI/DSN string directly.
        dsn = self.config.get("url") or self.config.get("dsn")
        if dsn:
            return psycopg2.connect(dsn)
        params: dict[str, Any] = {
            "host": self.config.get("host", "localhost"),
            "port": self.config.get("port", 5432),
            "dbname": self.config.get("database") or self.config.get("dbname"),
            "user": self.config.get("user"),
            "password": self.config.get("password"),
        }
        params = {k: v for k, v in params.items() if v is not None}
        params.update(self.config.get("connect_args") or {})
        return psycopg2.connect(**params)
