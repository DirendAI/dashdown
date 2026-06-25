"""Parquet connector. Backed by an in-memory DuckDB for SQL uniformity.

The Parquet sibling of the CSV connector: each ``.parquet`` (or ``.pq``) file in
a directory becomes a table named after its stem, exposed via DuckDB's
``read_parquet``. Parquet is columnar and already typed, so this is the fastest
file source — and the reader ships in core DuckDB, so there's no extra dependency.

``sources.yaml`` entry example::

    warehouse:
      type: parquet
      directory: data          # all *.parquet/*.pq → a table per stem
      files:                   # optional explicit {table_name: path} mapping
        orders: data/orders.parquet
"""
from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

from dashdown.data.base import Connector, register_connector
from dashdown.data.duckdb_connector import DuckDBConnector

#: File globs auto-discovered in ``directory``.
_PARQUET_GLOBS = ("*.parquet", "*.pq")


@register_connector("parquet")
class ParquetConnector(DuckDBConnector):
    """Parquet source: an always-in-memory DuckDB with one table per file.

    Inherits ``query`` (incl. reconnect-on-fatal), ``_execute`` and ``close``
    from :class:`DuckDBConnector`; only the schema setup differs, so the tables
    are rebuilt in ``_setup`` — restored after a reconnect too. Each file is
    exposed via ``read_parquet`` and materialized once at connect.
    """

    def __init__(self, name: str, config: dict[str, Any]) -> None:
        # Skip DuckDBConnector.__init__ (it reads config["path"]); Parquet is
        # always in-memory and its tables are (re)built in _setup on every connect.
        Connector.__init__(self, name, config)
        self._lock = threading.Lock()
        self._target = ":memory:"
        self._connect()

    def _setup(self) -> None:
        project_root: Path = self.config.get("_project_root", Path("."))

        # Auto-discover Parquet files in a directory.
        directory = self.config.get("directory")
        if directory:
            dir_path = (project_root / directory).resolve()
            if dir_path.is_dir():
                for pattern in _PARQUET_GLOBS:
                    for pq_file in sorted(dir_path.glob(pattern)):
                        self._register_table(pq_file.stem, pq_file)

        # Explicit file mapping overrides / adds.
        for table_name, pq_path in (self.config.get("files") or {}).items():
            self._register_table(table_name, (project_root / pq_path).resolve())

    def _register_table(self, table_name: str, pq_path: Path) -> None:
        # Materialize once at connect (rebuilt on reconnect, since _setup re-runs)
        # so repeated queries hit columnar memory rather than re-opening the file.
        path_lit = str(pq_path).replace("'", "''")
        self._con.execute(
            f'CREATE OR REPLACE TABLE "{table_name}" AS '
            f"SELECT * FROM read_parquet('{path_lit}')"
        )
