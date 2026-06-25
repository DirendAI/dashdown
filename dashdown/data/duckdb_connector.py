"""DuckDB-backed connector. Also serves as the base for CSV sources."""
from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Any

import duckdb

from dashdown.data.base import Connector, QueryResult, register_connector

log = logging.getLogger(__name__)


def _is_fatal_duckdb_error(e: Exception) -> bool:
    """True if a DuckDB error has invalidated the connection (it's unusable and
    every later query will fail until reconnected).

    DuckDB's ``FatalException`` *is* that invalidated state; an
    ``InternalException`` (e.g. httpfs cache corruption from a flaky
    ``read_json_auto`` over HTTP) is what tends to *cause* it. We reconnect on
    both. A normal transient error — notably ``HTTPException`` for a 429 — does
    **not** invalidate the connection, so it isn't matched here and is re-raised
    to the caller as an ordinary (retryable) query failure.
    """
    fatal_types = tuple(
        getattr(duckdb, n)
        for n in ("FatalException", "InternalException")
        if hasattr(duckdb, n)
    )
    if fatal_types and isinstance(e, fatal_types):
        return True
    msg = str(e).lower()
    return "invalidated" in msg or "fatal error" in msg


@register_connector("duckdb")
class DuckDBConnector(Connector):
    """Connects to a DuckDB database file (or in-memory if no path)."""

    def __init__(self, name: str, config: dict[str, Any]) -> None:
        super().__init__(name, config)
        # The lock guards only the connection *lifecycle* (connect/reconnect +
        # schema setup), NOT query execution. DuckDB lets independent cursors on
        # one connection run concurrently (it has its own MVCC concurrency
        # control), so each query takes a fresh ``cursor()`` and they run in
        # parallel — a page firing many widget queries no longer serializes them
        # one-at-a-time behind a global lock (which, under the browser's 6-conn
        # HTTP/1.1 cap, snowballed into multi-second "stalled" requests).
        self._lock = threading.Lock()
        path = config.get("path")
        self._target = str(path) if path else ":memory:"
        self._connect()

    def _connect(self) -> None:
        """(Re)open the connection and rebuild any registered schema.

        Called on init, and again if a query invalidates the connection (see
        ``query``) — so one bad query can't permanently break the connector.
        """
        self._con = duckdb.connect(self._target)
        self._setup()

    def _setup(self) -> None:
        """Register views/tables on the freshly-opened connection.

        Overridden by subclasses (e.g. ``CSVConnector``) so their schema is
        rebuilt after a reconnect, not just on first open.
        """
        # Materialize CSV overlays as tables (parsed once), not views (re-parsed
        # per query) — same reasoning as CSVConnector._register_table.
        for table_name, csv_path in (self.config.get("csv_views") or {}).items():
            path_lit = str(csv_path).replace("'", "''")
            self._con.execute(
                f'CREATE OR REPLACE TABLE "{table_name}" AS '
                f"SELECT * FROM read_csv_auto('{path_lit}')"
            )

    def _execute(self, con: duckdb.DuckDBPyConnection, sql: str) -> QueryResult:
        # A per-query cursor is an independent execution context, so concurrent
        # queries don't trample each other's result state on the shared
        # connection — which is what makes the lock-free path safe.
        cur = con.cursor()
        cur.execute(sql)
        cols = [d[0] for d in cur.description] if cur.description else []
        rows = [list(r) for r in cur.fetchall()]
        return QueryResult(columns=cols, rows=rows)

    def query(self, sql: str) -> QueryResult:
        con = self._con  # snapshot: a concurrent reconnect may swap self._con
        try:
            return self._execute(con, sql)
        except duckdb.Error as e:
            if not _is_fatal_duckdb_error(e):
                raise
            # The connection is poisoned — rebuild it and retry once so a single
            # fatal query (e.g. an httpfs read corrupting the connection) doesn't
            # take down every other query on this connector. The lock serializes
            # the rebuild; the `con is self._con` guard means a query that lost
            # the race just reuses the connection a sibling already rebuilt
            # instead of pointlessly rebuilding again.
            with self._lock:
                if con is self._con:
                    log.warning(
                        "DuckDB connection invalidated (%s: %s); reconnecting",
                        type(e).__name__,
                        e,
                    )
                    self._connect()
                return self._execute(self._con, sql)

    def close(self) -> None:
        try:
            self._con.close()
        except Exception:
            pass
