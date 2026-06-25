"""JSON connector. Backed by an in-memory DuckDB for SQL uniformity.

The JSON sibling of the CSV connector: each file in a directory becomes a table
named after its stem, materialized once via DuckDB's ``read_json_auto`` (which
auto-detects both a JSON **array of objects** and **newline-delimited** JSON, so
``.json``, ``.ndjson`` and ``.jsonl`` are all picked up). No database to stand up
and no extra dependency — the ``json`` reader ships in core DuckDB.

``sources.yaml`` entry example::

    events:
      type: json
      directory: data          # all *.json/*.ndjson/*.jsonl → a table per stem
      files:                   # optional explicit {table_name: path} mapping
        orders: data/orders.json
"""
from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

from dashdown.data.base import Connector, register_connector
from dashdown.data.duckdb_connector import DuckDBConnector

#: File globs auto-discovered in ``directory``. ``read_json_auto`` handles both
#: array-of-objects JSON and newline-delimited JSON, so all three extensions map
#: through the same reader.
_JSON_GLOBS = ("*.json", "*.ndjson", "*.jsonl")


@register_connector("json")
class JSONConnector(DuckDBConnector):
    """JSON source: an always-in-memory DuckDB with one table per file.

    Inherits ``query`` (incl. reconnect-on-fatal), ``_execute`` and ``close``
    from :class:`DuckDBConnector`; only the schema setup differs, so the tables
    are rebuilt in ``_setup`` — restored after a reconnect too. Each file is
    materialized once (not a view), so repeated queries don't re-parse the JSON.
    """

    def __init__(self, name: str, config: dict[str, Any]) -> None:
        # Skip DuckDBConnector.__init__ (it reads config["path"]); JSON is always
        # in-memory and its tables are (re)built in _setup on every connect.
        Connector.__init__(self, name, config)
        self._lock = threading.Lock()
        self._target = ":memory:"
        self._connect()

    def _setup(self) -> None:
        project_root: Path = self.config.get("_project_root", Path("."))

        # Auto-discover JSON files in a directory.
        directory = self.config.get("directory")
        if directory:
            dir_path = (project_root / directory).resolve()
            if dir_path.is_dir():
                for pattern in _JSON_GLOBS:
                    for json_file in sorted(dir_path.glob(pattern)):
                        self._register_table(json_file.stem, json_file)

        # Explicit file mapping overrides / adds.
        for table_name, json_path in (self.config.get("files") or {}).items():
            self._register_table(table_name, (project_root / json_path).resolve())

    def _register_table(self, table_name: str, json_path: Path) -> None:
        # Materialize the JSON into a real in-memory table ONCE, not a view over
        # read_json_auto(): a view re-parses the file from disk on every query
        # (same perf reasoning as the CSV connector). The dev server re-creates
        # the connector when data/ changes, so edits are still picked up.
        path_lit = str(json_path).replace("'", "''")
        self._con.execute(
            f'CREATE OR REPLACE TABLE "{table_name}" AS '
            f"SELECT * FROM read_json_auto('{path_lit}')"
        )
