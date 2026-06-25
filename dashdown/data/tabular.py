"""Shared base for tabular (non-SQL) sources loaded into DuckDB.

Spreadsheet-style sources (Excel workbooks, Google Sheets) aren't SQL engines —
they're grids of cells. To give them the same SQL interface as every other
connector, we load each sheet/tab into an in-memory DuckDB as a table, then run
the user's SQL against it. This mirrors how `CSVConnector` backs CSVs with
DuckDB; the only difference is *where* the rows come from.

Design notes:
- **Lazy load.** Tables are materialized on the first `query()`, not in
  `__init__`. This matches the framework's "pages ship instantly, data is fetched
  per-query" model — an unreachable Google Sheet or a missing workbook surfaces
  as a per-query error card rather than breaking page render at project load.
- **NaN scrubbing.** pandas represents empty cells as NaN, which is not valid
  JSON. We convert NaN/NaT to NULL when loading so query results serialize
  cleanly (the data API's serializer handles dates but not NaN).
"""
from __future__ import annotations

import importlib
import threading
from typing import Any

import duckdb

from dashdown.data.base import Connector, QueryResult


def _import_driver(module: str, extra: str):
    """Import an optional driver, re-raising a missing one as an install hint.

    Mirrors `dbapi._import_driver` so spreadsheet connectors give the same
    `pip install 'dashdown-md[<extra>]'` message as the SQL ones.
    """
    try:
        return importlib.import_module(module)
    except ImportError as e:  # pragma: no cover - exercised when driver absent
        raise ImportError(
            f"The '{extra}' connector requires the '{module}' driver, which is not "
            f"installed. Install it with: pip install 'dashdown-md[{extra}]'  "
            f"(underlying error: {e})"
        ) from e


def _dedupe_headers(headers: list[Any]) -> list[str]:
    """Make column names non-empty and unique (DuckDB needs distinct identifiers)."""
    seen: dict[str, int] = {}
    out: list[str] = []
    for i, h in enumerate(headers):
        name = str(h).strip() if h is not None and str(h).strip() else f"col{i}"
        if name in seen:
            seen[name] += 1
            name = f"{name}_{seen[name]}"
        else:
            seen[name] = 0
        out.append(name)
    return out


class TabularConnector(Connector):
    """Base for connectors that materialize sheets into an in-memory DuckDB.

    Subclasses implement `_load_tables()` returning `{table_name: DataFrame}`;
    everything else (DuckDB registration, SQL execution, locking) is shared.
    """

    extra: str = "tabular"

    def __init__(self, name: str, config: dict[str, Any]) -> None:
        super().__init__(name, config)
        self._lock = threading.Lock()
        self._con: Any = None

    def _load_tables(self) -> dict[str, Any]:  # pragma: no cover - abstract
        """Return a mapping of table name -> pandas DataFrame to register."""
        raise NotImplementedError

    def _ensure_con(self) -> Any:
        if self._con is None:
            import pandas as pd

            con = duckdb.connect(":memory:")
            for i, (tname, df) in enumerate(self._load_tables().items()):
                # NaN/NaT aren't JSON-serializable; store them as NULL.
                df = df.where(df.notnull(), None)
                tmp = f"_src_{i}"
                con.register(tmp, df)
                ident = '"' + str(tname).replace('"', '""') + '"'
                con.execute(f"CREATE TABLE {ident} AS SELECT * FROM {tmp}")
                con.unregister(tmp)
            self._con = con
        return self._con

    def query(self, sql: str) -> QueryResult:
        with self._lock:
            con = self._ensure_con()
            cur = con.execute(sql)
            cols = [d[0] for d in cur.description] if cur.description else []
            rows = [list(r) for r in cur.fetchall()]
        return QueryResult(columns=cols, rows=rows)

    def close(self) -> None:
        with self._lock:
            if self._con is not None:
                try:
                    self._con.close()
                except Exception:
                    pass
                self._con = None
