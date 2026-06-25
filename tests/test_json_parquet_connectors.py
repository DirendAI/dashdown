"""Tests for the JSON and Parquet file connectors.

Both are DuckDB-backed siblings of the CSV connector (one in-memory table per
file, materialized at connect, rebuilt on reconnect), so they share the CSV
connector's behavior: directory discovery, explicit `files` mapping, single-quote
path escaping, and — because they subclass DuckDBConnector — the
`information_schema` `list_tables()`/`describe_table()` introspection for free.
"""
from __future__ import annotations

import json
from pathlib import Path

import duckdb
import pytest

from dashdown.data.base import QueryResult, get_connector_type
from dashdown.data.json_connector import JSONConnector
from dashdown.data.parquet_connector import ParquetConnector


def _write_parquet(path: Path, select_sql: str) -> None:
    """Write a parquet file via DuckDB itself (no pandas/pyarrow dependency)."""
    con = duckdb.connect()
    lit = str(path).replace("'", "''")
    con.execute(f"COPY ({select_sql}) TO '{lit}' (FORMAT PARQUET)")
    con.close()


class TestRegistration:
    def test_registered_types(self):
        assert get_connector_type("json") is JSONConnector
        assert get_connector_type("parquet") is ParquetConnector


class TestJSONConnector:
    def test_array_and_ndjson_discovered_by_stem(self, tmp_path):
        data = tmp_path / "data"
        data.mkdir()
        (data / "orders.json").write_text(
            json.dumps([{"id": 1, "amount": 10}, {"id": 2, "amount": 20}]),
            encoding="utf-8",
        )
        (data / "events.ndjson").write_text(
            '{"k": "a"}\n{"k": "b"}\n', encoding="utf-8"
        )
        conn = JSONConnector("json", {"directory": "data", "_project_root": tmp_path})
        assert conn.query('SELECT * FROM "orders" ORDER BY id').rows == [[1, 10], [2, 20]]
        assert conn.query('SELECT count(*) AS n FROM "events"').rows == [[2]]
        conn.close()

    def test_jsonl_extension_discovered(self, tmp_path):
        data = tmp_path / "data"
        data.mkdir()
        (data / "log.jsonl").write_text('{"v": 1}\n{"v": 2}\n{"v": 3}\n', encoding="utf-8")
        conn = JSONConnector("json", {"directory": "data", "_project_root": tmp_path})
        assert conn.query('SELECT sum(v) AS s FROM "log"').rows == [[6]]
        conn.close()

    def test_explicit_files_mapping(self, tmp_path):
        (tmp_path / "raw.json").write_text(json.dumps([{"x": 1}, {"x": 2}]), encoding="utf-8")
        conn = JSONConnector(
            "json", {"files": {"my_table": "raw.json"}, "_project_root": tmp_path}
        )
        assert conn.query('SELECT count(*) AS n FROM "my_table"').rows == [[2]]
        conn.close()

    def test_introspection(self, tmp_path):
        (tmp_path / "t.json").write_text(json.dumps([{"id": 1, "name": "a"}]), encoding="utf-8")
        conn = JSONConnector("json", {"files": {"t": "t.json"}, "_project_root": tmp_path})
        assert "t" in [r[0] for r in conn.list_tables().rows]
        cols = [r[0] for r in conn.describe_table("t").rows]
        assert cols == ["id", "name"]
        conn.close()

    def test_single_quote_in_path(self, tmp_path):
        odd = tmp_path / "o'brien"
        odd.mkdir()
        (odd / "t.json").write_text(json.dumps([{"v": 42}]), encoding="utf-8")
        conn = JSONConnector("json", {"files": {"t": "o'brien/t.json"}, "_project_root": tmp_path})
        assert conn.query('SELECT v FROM "t"').rows == [[42]]
        conn.close()

    def test_returns_queryresult(self, tmp_path):
        (tmp_path / "n.json").write_text(json.dumps([{"n": 1}]), encoding="utf-8")
        conn = JSONConnector("json", {"files": {"n": "n.json"}, "_project_root": tmp_path})
        assert isinstance(conn.query('SELECT * FROM "n"'), QueryResult)
        conn.close()

    def test_no_config_is_valid(self, tmp_path):
        conn = JSONConnector("json", {"_project_root": tmp_path})
        conn.close()  # no directory/files -> empty connector, no error


class TestParquetConnector:
    def test_directory_discovery_by_stem(self, tmp_path):
        data = tmp_path / "data"
        data.mkdir()
        _write_parquet(data / "sales.parquet", "SELECT 1 AS id, 10 AS amount UNION ALL SELECT 2, 20")
        _write_parquet(data / "regions.pq", "SELECT 'NW' AS code, 'Northwest' AS name")
        conn = ParquetConnector("parquet", {"directory": "data", "_project_root": tmp_path})
        assert conn.query('SELECT * FROM "sales" ORDER BY id').rows == [[1, 10], [2, 20]]
        assert conn.query('SELECT name FROM "regions"').rows == [["Northwest"]]
        conn.close()

    def test_explicit_files_mapping(self, tmp_path):
        _write_parquet(tmp_path / "raw.parquet", "SELECT * FROM range(3) t(x)")
        conn = ParquetConnector(
            "parquet", {"files": {"my_table": "raw.parquet"}, "_project_root": tmp_path}
        )
        assert conn.query('SELECT count(*) AS n FROM "my_table"').rows == [[3]]
        conn.close()

    def test_introspection(self, tmp_path):
        _write_parquet(tmp_path / "t.parquet", "SELECT 1 AS id, 'a' AS name")
        conn = ParquetConnector("parquet", {"files": {"t": "t.parquet"}, "_project_root": tmp_path})
        assert "t" in [r[0] for r in conn.list_tables().rows]
        assert [r[0] for r in conn.describe_table("t").rows] == ["id", "name"]
        conn.close()

    def test_single_quote_in_path(self, tmp_path):
        odd = tmp_path / "o'brien"
        odd.mkdir()
        _write_parquet(odd / "t.parquet", "SELECT 42 AS v")
        conn = ParquetConnector("parquet", {"files": {"t": "o'brien/t.parquet"}, "_project_root": tmp_path})
        assert conn.query('SELECT v FROM "t"').rows == [[42]]
        conn.close()

    def test_no_config_is_valid(self, tmp_path):
        conn = ParquetConnector("parquet", {"_project_root": tmp_path})
        conn.close()
