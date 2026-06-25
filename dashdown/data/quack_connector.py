"""Quack connector — remote DuckDB over the Quack RPC protocol.

`Quack <https://duckdb.org/quack/>`_ turns DuckDB from an embedded engine into a
client-server one: a DuckDB instance runs ``CALL quack_serve('quack:host', …)``
and clients reach it by ``ATTACH``-ing a ``quack:`` target. It's still the same
``duckdb`` driver and the same SQL — only *where the data lives* changes. So this
is a thin :class:`DuckDBConnector` subclass: it opens a local (in-memory) DuckDB,
loads the Quack extension, registers the auth secret, and attaches the remote
database. Everything else — per-query cursor concurrency, ``reconnect-on-fatal``
resilience, ``_execute``, ``query``, ``close`` — is inherited unchanged.

The Quack extension is a (beta) community extension, so unlike MotherDuck it is
**installed + loaded explicitly** in ``_setup()`` (which re-runs on every connect
*and* reconnect, so the remote is re-attached after a reconnect-on-fatal, exactly
as CSV views are rebuilt).

sources.yaml example:
    remote:
      type: quack
      host: data.example.com      # the quack server host (target becomes quack:<host>)
      port: 9494                   # optional; omit for the server's default port
      token: ${QUACK_TOKEN}        # optional; ${ENV_VAR} expansion supported
      database: remote             # optional; ATTACH alias (default "remote")
      # install_extension: true    # optional; INSTALL quack before LOAD (default true)
      # extension_repository: community  # optional; repo to INSTALL from
      # duckdb_config:             # optional extra settings passed to duckdb.connect
      #   allow_unsigned_extensions: true

**Status: experimental / preview** — Quack is itself in beta. This connector
covers the documented attach + token-secret flow; it is not yet verified against
a live Quack server.
"""
from __future__ import annotations

import os
import re
from typing import Any

import duckdb

from dashdown.data.base import register_connector
from dashdown.data.duckdb_connector import DuckDBConnector

#: A ``${VAR}`` reference resolved from the environment (mirrors motherduck/auth).
_ENV_RE = re.compile(r"^\$\{(\w+)\}$")


def _resolve_secret(value: Any) -> str:
    """Expand a ``${VAR}`` reference from the environment, else return as-is.

    A missing variable raises so a misconfigured deployment fails loudly at
    startup rather than connecting with the literal string ``"${QUACK_TOKEN}"``.
    """
    s = str(value)
    m = _ENV_RE.match(s.strip())
    if m:
        env_val = os.environ.get(m.group(1))
        if env_val is None:
            raise ValueError(
                f"quack connector references environment variable "
                f"{m.group(1)!r}, which is not set"
            )
        return env_val
    return s


def _sql_str(value: str) -> str:
    """Quote a value as a single-quoted SQL string literal (``'`` → ``''``)."""
    return "'" + str(value).replace("'", "''") + "'"


@register_connector("quack")
class QuackConnector(DuckDBConnector):
    """Connects to a remote DuckDB over the Quack RPC protocol (``quack:`` target)."""

    def __init__(self, name: str, config: dict[str, Any]) -> None:
        # Compute the quack target / alias / token *before* super().__init__,
        # because DuckDBConnector.__init__ opens an (in-memory) connection and
        # immediately calls _setup(), which attaches the remote using them.
        target = str(config.get("target") or config.get("url") or "").strip()
        if target.startswith("quack:"):
            self._quack_target = target
        else:
            host = str(config.get("host") or config.get("hostname") or "").strip()
            if not host and not target:
                raise ValueError(
                    "quack connector requires a 'host' (or a full 'quack:…' target)"
                )
            host = host or target
            port = config.get("port")
            self._quack_target = f"quack:{host}:{port}" if port else f"quack:{host}"

        alias = str(
            config.get("database") or config.get("db") or config.get("alias") or "remote"
        ).strip() or "remote"
        self._alias = alias

        token = config.get("token") or config.get("quack_token")
        self._token = _resolve_secret(token) if token else None
        # A stable, identifier-safe secret name so reconnects replace (not stack)
        # the secret, and multiple quack connectors don't collide.
        self._secret_name = "quack_" + re.sub(r"\W", "_", name)

        self._install_extension = config.get("install_extension", True)
        self._extension_repo = str(
            config.get("extension_repository") or "community"
        ).strip()

        # No "path" in config → the local DuckDB is in-memory; the data is remote.
        super().__init__(name, config)

    def _connect(self) -> None:
        """Open the local DuckDB, passing through any ``duckdb_config``.

        Overridden (like MotherDuck) so settings such as
        ``allow_unsigned_extensions`` — sometimes needed for a beta community
        extension — can be threaded into ``duckdb.connect`` at startup. Then
        ``_setup()`` loads the extension and attaches the remote.
        """
        duck_config: dict[str, Any] = {}
        extra = self.config.get("duckdb_config") or self.config.get("config")
        if isinstance(extra, dict):
            duck_config.update(extra)
        self._con = duckdb.connect(self._target, config=duck_config)
        self._setup()

    def _setup(self) -> None:
        """Load the Quack extension, register the auth secret, attach the remote.

        Runs on every (re)connect, so a reconnect-on-fatal re-establishes the
        whole remote attachment, not just the local connection. Order matters:
        the ``quack`` *secret type* is provided by the extension, so the extension
        must be loaded before ``CREATE SECRET``.
        """
        if self._install_extension:
            repo = self._extension_repo
            # A bare repo alias (community/core/…) is a keyword; a URL is a string.
            from_clause = _sql_str(repo) if "://" in repo else repo
            self._con.execute(f"INSTALL quack FROM {from_clause}")
        self._con.execute("LOAD quack")

        if self._token:
            self._con.execute(
                f'CREATE OR REPLACE SECRET "{self._secret_name}" '
                f"(TYPE quack, TOKEN {_sql_str(self._token)})"
            )

        alias = self._alias.replace('"', '""')
        self._con.execute(
            f'ATTACH {_sql_str(self._quack_target)} AS "{alias}"'
        )
