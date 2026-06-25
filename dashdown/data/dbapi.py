"""Shared base for DB-API 2.0 SQL connectors (PostgreSQL, MySQL, …).

psycopg2 and PyMySQL both expose the standard `PEP 249 <https://peps.python.org/pep-0249/>`_
DB-API 2.0 interface, so the execute / fetch / clean / close logic is identical —
only the driver import and the `connect()` call differ. Subclasses implement
`_connect()` (returning a live DB-API connection); this base handles cursor
execution, JSON-safe value coercion, thread serialization, lazy connection, and
a single reconnect-and-retry when a long-lived connection has dropped.

Design notes:
- **Lazy connect.** The connection is opened on the first `query()`, not in
  `__init__`. This matches the framework's "pages ship instantly, data is fetched
  per-query" model — an unreachable database surfaces as a per-query error card
  rather than breaking page render at project load.
- **Lazy driver import.** The driver (`psycopg2` / `pymysql`) is imported inside
  `_connect()`, not at module top level, so the connector module imports without
  the optional extra installed (which is also what makes it unit-testable without
  a real database). `_import_driver()` re-raises a missing driver as a friendly
  `pip install 'dashdown-md[<extra>]'` hint.
"""
from __future__ import annotations

import importlib
import math
import threading
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from typing import Any
from urllib.parse import unquote, urlparse

from dashdown.data.base import Connector, QueryResult


def _import_driver(module: str, extra: str):
    """Import a DB driver, re-raising a missing one as an install hint.

    Mirrors the entry-point loader's behavior (`data/base.py::_load_entry_point`)
    so the message is the same whether the driver is missing at discovery time or
    at first connect.
    """
    try:
        return importlib.import_module(module)
    except ImportError as e:  # pragma: no cover - exercised when driver absent
        raise ImportError(
            f"The '{extra}' connector requires the '{module}' driver, which is not "
            f"installed. Install it with: pip install 'dashdown-md[{extra}]'  "
            f"(underlying error: {e})"
        ) from e


def parse_db_url(url: str) -> dict[str, Any]:
    """Parse a `scheme://user:pass@host:port/dbname` URL into connect kwargs.

    Returns only the keys present in the URL; percent-encoded credentials are
    decoded. Used by drivers (e.g. PyMySQL) whose `connect()` does not accept a
    URL/DSN string directly.
    """
    parsed = urlparse(url)
    params: dict[str, Any] = {}
    if parsed.hostname:
        params["host"] = parsed.hostname
    if parsed.port:
        params["port"] = parsed.port
    if parsed.username:
        params["user"] = unquote(parsed.username)
    if parsed.password:
        params["password"] = unquote(parsed.password)
    db = parsed.path.lstrip("/")
    if db:
        params["database"] = db
    return params


def _clean_value(val: Any) -> Any:
    """Coerce a DB-API cell value to a JSON-serializable type.

    The data API serializes rows to JSON, so driver-native types
    (Decimal, datetime, bytes, …) must be normalized here.
    """
    if val is None:
        return None
    if isinstance(val, bool):  # bool is an int subclass — keep it a bool
        return val
    if isinstance(val, Decimal):
        return float(val)
    if isinstance(val, float) and (math.isnan(val) or math.isinf(val)):
        return None
    if isinstance(val, (datetime, date, time)):
        return val.isoformat()
    if isinstance(val, timedelta):
        return str(val)
    if isinstance(val, memoryview):
        val = val.tobytes()
    if isinstance(val, (bytes, bytearray)):
        try:
            return bytes(val).decode("utf-8")
        except UnicodeDecodeError:
            return bytes(val).hex()
    return val


def _is_connection_error(exc: BaseException) -> bool:
    """Heuristic: does this exception mean the connection is dead?

    DB-API exception classes are driver-specific, so we match on the standard
    PEP 249 class names (`OperationalError` / `InterfaceError`) across the MRO
    rather than importing each driver's exceptions. Used to decide whether a
    failed query is worth one reconnect-and-retry.
    """
    for cls in type(exc).__mro__:
        if cls.__name__ in ("OperationalError", "InterfaceError"):
            return True
    return False


class DBAPIConnector(Connector):
    """Base class for connectors over a PEP 249 DB-API 2.0 driver.

    Subclasses set `extra`/`driver` and implement `_connect()`.
    """

    #: Name of the optional dependency extra and the driver module, used in the
    #: missing-driver install hint. Subclasses override both.
    extra: str = "dbapi"
    driver: str = ""

    def __init__(self, name: str, config: dict[str, Any]) -> None:
        super().__init__(name, config)
        self._lock = threading.Lock()
        self._con: Any = None

    def _connect(self) -> Any:  # pragma: no cover - abstract
        """Open and return a live DB-API connection."""
        raise NotImplementedError

    def _ensure_con(self) -> Any:
        if self._con is None:
            self._con = self._connect()
        return self._con

    def _reset(self) -> None:
        if self._con is not None:
            try:
                self._con.close()
            except Exception:
                pass
            self._con = None

    def _execute(self, sql: str) -> QueryResult:
        con = self._ensure_con()
        cur = con.cursor()
        try:
            cur.execute(sql)
            if cur.description:
                cols = [d[0] for d in cur.description]
                rows = cur.fetchall()
            else:
                cols, rows = [], []
        finally:
            cur.close()
        # End the transaction so the next query sees fresh data and the
        # connection isn't left "idle in transaction".
        try:
            con.commit()
        except Exception:
            pass
        clean = [[_clean_value(v) for v in row] for row in rows]
        return QueryResult(columns=list(cols), rows=clean)

    def query(self, sql: str) -> QueryResult:
        with self._lock:
            try:
                return self._execute(sql)
            except Exception as e:
                # A long-lived pooled connection may have been dropped by the
                # server. Reconnect once and retry; re-raise anything else.
                if _is_connection_error(e):
                    self._reset()
                    return self._execute(sql)
                raise

    def close(self) -> None:
        with self._lock:
            self._reset()
