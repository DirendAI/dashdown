"""CSV connector. Backed by an in-memory DuckDB for SQL uniformity.

`sources.yaml` entry example:
    my_csv:
      type: csv
      directory: data           # all *.csv become views named after the file stem
      files:                    # optional explicit mapping
        sales: data/sales.csv
"""
from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

from dashdown.data.base import Connector, register_connector
from dashdown.data.duckdb_connector import DuckDBConnector


@register_connector("csv")
class CSVConnector(DuckDBConnector):
    """CSV source: an always-in-memory DuckDB with one table per file.

    Inherits ``query`` (incl. reconnect-on-fatal), ``_execute`` and ``close``
    from :class:`DuckDBConnector`; only the schema setup differs, so the tables
    are rebuilt in ``_setup`` — which means they're restored after a reconnect
    too. Each file is materialized once (not a view), so repeated queries don't
    re-parse the CSV from disk.
    """

    def __init__(self, name: str, config: dict[str, Any]) -> None:
        # Skip DuckDBConnector.__init__ (it reads config["path"]); CSV is always
        # in-memory and its views are (re)built in _setup on every connect.
        Connector.__init__(self, name, config)
        self._lock = threading.Lock()
        self._target = ":memory:"
        self._connect()

    def _setup(self) -> None:
        project_root: Path = self.config.get("_project_root", Path("."))

        # Auto-discover CSVs in a directory.
        directory = self.config.get("directory")
        if directory:
            dir_path = (project_root / directory).resolve()
            if dir_path.is_dir():
                for csv_file in sorted(dir_path.glob("*.csv")):
                    self._register_table(csv_file.stem, csv_file)

        # Explicit file mapping overrides / adds.
        for table_name, csv_path in (self.config.get("files") or {}).items():
            self._register_table(table_name, (project_root / csv_path).resolve())

    def _register_table(self, table_name: str, csv_path: Path) -> None:
        # Materialize the CSV into a real in-memory table ONCE, not a view over
        # read_csv_auto(): a view re-parses the entire file from disk on *every*
        # query, so a page with many widgets re-reads a big CSV many times and
        # stalls. A table is parsed once at connect (and re-built on reconnect,
        # since _setup runs again) and every later query hits columnar memory.
        # The dev server re-creates the connector when data/ changes, so edits
        # to the CSV are still picked up.
        path_lit = str(csv_path).replace("'", "''")
        self._con.execute(
            f'CREATE OR REPLACE TABLE "{table_name}" AS '
            f"SELECT * FROM read_csv_auto('{path_lit}')"
        )
