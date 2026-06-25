"""MySQL / MariaDB connector (via PyMySQL).

Heavy/optional dependency: install with `pip install 'dashdown-md[mysql]'`.

Shares the DB-API 2.0 plumbing with the PostgreSQL connector (see `dbapi.py`);
only the driver and connect call differ.

sources.yaml example:
    sales:
      type: mysql
      host: localhost
      port: 3306
      database: shop          # alias: db
      user: reader
      password: secret
      # or a single URL instead of the fields above:
      # url: mysql://reader:secret@localhost:3306/shop
      # connect_args:          # optional extra kwargs passed to pymysql.connect
      #   charset: utf8mb4
"""
from __future__ import annotations

from typing import Any

from dashdown.data.base import register_connector
from dashdown.data.dbapi import DBAPIConnector, _import_driver, parse_db_url


@register_connector("mysql")
class MySQLConnector(DBAPIConnector):
    extra = "mysql"
    driver = "pymysql"

    def _connect(self) -> Any:
        pymysql = _import_driver("pymysql", "mysql")
        # PyMySQL's connect() takes kwargs, not a URL — parse one if given.
        url = self.config.get("url") or self.config.get("dsn")
        params: dict[str, Any] = {
            "host": self.config.get("host", "localhost"),
            "port": int(self.config.get("port", 3306)),
            "database": self.config.get("database") or self.config.get("db"),
            "user": self.config.get("user"),
            "password": self.config.get("password"),
        }
        if url:
            # URL fields fill in / override the discrete keys.
            params.update(parse_db_url(url))
        params = {k: v for k, v in params.items() if v is not None}
        params.update(self.config.get("connect_args") or {})
        return pymysql.connect(**params)
