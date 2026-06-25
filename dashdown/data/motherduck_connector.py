"""MotherDuck connector — cloud DuckDB.

MotherDuck *is* DuckDB: you connect with the same ``duckdb`` driver to an
``md:`` database, authenticated by a service token. So this is a thin
:class:`DuckDBConnector` subclass — it inherits the per-query cursor concurrency,
``reconnect-on-fatal`` resilience, ``_execute`` and ``close`` unchanged, and only
overrides *where* it connects (an ``md:`` target) and *how* (passing the
``motherduck_token`` to ``duckdb.connect``).

No separate extra: the core ``duckdb`` dependency ships the MotherDuck extension,
which auto-loads on first connect to an ``md:`` database.

sources.yaml example:
    cloud:
      type: motherduck
      database: my_db            # optional; omit to attach all your databases
      token: ${MOTHERDUCK_TOKEN} # optional; falls back to the env var DuckDB reads
      # duckdb_config:           # optional extra settings passed to duckdb.connect
      #   custom_user_agent: my-app
"""
from __future__ import annotations

import os
import re
from typing import Any

import duckdb

from dashdown.data.base import Connector, register_connector
from dashdown.data.duckdb_connector import DuckDBConnector

#: A ``${VAR}`` reference resolved from the environment (mirrors auth/embed/cube).
_ENV_RE = re.compile(r"^\$\{(\w+)\}$")


def _resolve_secret(value: Any) -> str:
    """Expand a ``${VAR}`` reference from the environment, else return as-is.

    A missing variable raises so a misconfigured deployment fails loudly at
    startup rather than connecting with the literal string ``"${MOTHERDUCK_TOKEN}"``.
    """
    s = str(value)
    m = _ENV_RE.match(s.strip())
    if m:
        env_val = os.environ.get(m.group(1))
        if env_val is None:
            raise ValueError(
                f"motherduck connector references environment variable "
                f"{m.group(1)!r}, which is not set"
            )
        return env_val
    return s


@register_connector("motherduck")
class MotherDuckConnector(DuckDBConnector):
    """Connects to a MotherDuck cloud database (``md:`` over the DuckDB driver)."""

    def __init__(self, name: str, config: dict[str, Any]) -> None:
        # Skip DuckDBConnector.__init__ (it reads config["path"] for a local file
        # target); MotherDuck's target is an ``md:`` URI and connect takes a token.
        Connector.__init__(self, name, config)
        import threading

        self._lock = threading.Lock()

        database = str(config.get("database") or config.get("db") or "").strip()
        # Allow either a bare db name ("my_db") or a full "md:my_db" target.
        if database.startswith("md:"):
            self._target = database
        else:
            self._target = f"md:{database}" if database else "md:"

        token = config.get("token") or config.get("motherduck_token")
        self._token = _resolve_secret(token) if token else None
        self._connect()

    def _connect(self) -> None:
        """(Re)open the MotherDuck connection, passing the auth token.

        Overridden because the base opens a plain local connection; here the
        token must be threaded into ``duckdb.connect``. When no token is
        configured, DuckDB falls back to the ``motherduck_token`` environment
        variable on its own.
        """
        duck_config: dict[str, Any] = {}
        extra = self.config.get("duckdb_config") or self.config.get("config")
        if isinstance(extra, dict):
            duck_config.update(extra)
        if self._token:
            duck_config["motherduck_token"] = self._token
        self._con = duckdb.connect(self._target, config=duck_config)
        self._setup()
