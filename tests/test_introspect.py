"""Schema introspection — `list_tables()` / `describe_table()` across connector
families (backing `dashdown query --tables` / `--schema <table>`).

The default speaks ANSI `information_schema` (exercised live against DuckDB and
the tabular base, which both run a real in-memory DuckDB); the non-SQL / qualified
connectors (DAX, Cube, BigQuery) override and are tested by driving their `query`
/ `meta` seam with canned data — no live Fabric/Cube/BigQuery needed.
"""
from __future__ import annotations

import pandas as pd
import pytest

from dashdown.data.base import IntrospectionUnsupported, QueryResult
from dashdown.data.duckdb_connector import DuckDBConnector
from dashdown.data.introspect import (
    information_schema_columns,
    information_schema_tables,
    sql_str_literal,
)
from dashdown.data.tabular import TabularConnector


# --- the shared helpers (pure, fake `query` callable) -----------------------


def test_sql_str_literal_escapes_single_quotes():
    assert sql_str_literal("sales") == "'sales'"
    # The injection-critical case: a `'` is doubled, never left to break the literal.
    assert sql_str_literal("x'; DROP TABLE y; --") == "'x''; DROP TABLE y; --'"


def test_information_schema_tables_sql_shape_and_normalization():
    captured = {}

    def fake_query(sql):
        captured["sql"] = sql
        return QueryResult(columns=["table_name", "table_schema", "table_type"], rows=[["t", "main", "BASE TABLE"]])

    res = information_schema_tables(fake_query)
    assert "information_schema.tables" in captured["sql"].lower()
    # System schemas are excluded, case-insensitively.
    assert "lower(table_schema) not in" in captured["sql"].lower()
    assert "'pg_catalog'" in captured["sql"] and "'information_schema'" in captured["sql"]
    # Columns are normalized regardless of the engine's own casing.
    assert res.columns == ["table", "schema", "type"]
    assert res.rows == [["t", "main", "BASE TABLE"]]


def test_information_schema_columns_matches_name_as_escaped_literal():
    captured = {}

    def fake_query(sql):
        captured["sql"] = sql
        return QueryResult(columns=["column_name", "data_type", "is_nullable"], rows=[])

    information_schema_columns(fake_query, "x'; DROP TABLE y; --")
    # The table name is a quoted, escaped literal in a WHERE clause — never an identifier.
    assert "where table_name = 'x''; drop table y; --'" in captured["sql"].lower()
    assert "information_schema.columns" in captured["sql"].lower()


# --- the DuckDB family (base default, live information_schema) ---------------


def _duckdb_with_tables() -> DuckDBConnector:
    conn = DuckDBConnector("d", {})  # in-memory
    conn.query("CREATE TABLE sales (region VARCHAR, amount BIGINT)")
    conn.query("CREATE TABLE orders (id INTEGER)")
    return conn


def test_duckdb_list_tables_uses_base_default():
    conn = _duckdb_with_tables()
    res = conn.list_tables()
    assert res.columns == ["table", "schema", "type"]
    names = [r[0] for r in res.rows]
    assert "sales" in names and "orders" in names
    # No system tables leak in.
    assert all(r[1] not in ("information_schema", "pg_catalog") for r in res.rows)
    conn.close()


def test_duckdb_describe_table():
    conn = _duckdb_with_tables()
    res = conn.describe_table("sales")
    assert res.columns == ["column", "type", "nullable"]
    cols = {r[0]: r[1] for r in res.rows}
    assert cols.keys() == {"region", "amount"}
    assert cols["amount"].upper().startswith("BIGINT")
    conn.close()


def test_describe_table_injection_is_inert():
    conn = _duckdb_with_tables()
    # A crafted name is matched as data, returns nothing, and DROPs nothing.
    res = conn.describe_table("sales'; DROP TABLE sales; --")
    assert res.rows == []
    assert any(r[0] == "sales" for r in conn.list_tables().rows)  # still there
    conn.close()


# --- the Tabular family (excel/sheets — DuckDB-backed, lazy) -----------------


class _FakeTabular(TabularConnector):
    extra = "fake"

    def __init__(self, tables):
        super().__init__("t", {})
        self._tables = tables

    def _load_tables(self):
        return self._tables


def test_tabular_list_and_describe():
    conn = _FakeTabular({"people": pd.DataFrame({"id": [1], "name": ["a"]})})
    tables = [r[0] for r in conn.list_tables().rows]
    assert "people" in tables
    cols = [r[0] for r in conn.describe_table("people").rows]
    assert cols == ["id", "name"]
    conn.close()


# --- DAX override (INFO.VIEW functions, not SQL) -----------------------------


def _fake_dax(canned: dict):
    from dashdown.data.dax_connector import DAXConnector

    conn = DAXConnector("f", {"dataset_id": "x"})
    conn.query = lambda sql: canned[sql]  # type: ignore[method-assign]
    return conn


def test_dax_list_tables_reads_info_view():
    conn = _fake_dax(
        {"EVALUATE INFO.VIEW.TABLES()": QueryResult(columns=["Name", "Description"], rows=[["Sales", "d1"], ["Dates", "d2"]])}
    )
    res = conn.list_tables()
    assert res.columns == ["table", "schema", "type"]
    assert [r[0] for r in res.rows] == ["Sales", "Dates"]
    assert all(r[2] == "table" for r in res.rows)


def test_dax_describe_table_filters_by_table():
    conn = _fake_dax(
        {
            "EVALUATE INFO.VIEW.COLUMNS()": QueryResult(
                columns=["Table", "Name", "DataType"],
                rows=[["Sales", "Amount", "Int64"], ["Sales", "Region", "String"], ["Dates", "Day", "DateTime"]],
            )
        }
    )
    res = conn.describe_table("Sales")
    assert res.columns == ["column", "type", "nullable"]
    assert res.rows == [["Amount", "Int64", None], ["Region", "String", None]]


# --- Cube override (from /meta — query() raises) -----------------------------


_CUBE_META = {
    "cubes": [
        {
            "name": "orders",
            "type": "cube",
            "dimensions": [{"name": "orders.status", "type": "string"}, {"name": "orders.created", "type": "time"}],
            "measures": [{"name": "orders.count", "type": "number"}],
        },
        {"name": "users_view", "type": "view", "dimensions": [], "measures": []},
    ]
}


def _fake_cube():
    from dashdown.data.cube_connector import CubeConnector

    conn = CubeConnector("c", {"url": "https://cube.example.com", "secret": "s"})
    conn.meta = lambda: _CUBE_META  # type: ignore[method-assign]
    return conn


def test_cube_list_tables_lists_cubes_and_views():
    conn = _fake_cube()
    res = conn.list_tables()
    assert {r[0]: r[2] for r in res.rows} == {"orders": "cube", "users_view": "view"}


def test_cube_describe_table_lists_members_with_kind():
    conn = _fake_cube()
    res = conn.describe_table("orders")
    assert res.columns == ["member", "type", "kind"]
    assert res.rows == [
        ["orders.status", "string", "dimension"],
        ["orders.created", "time", "dimension"],
        ["orders.count", "number", "measure"],
    ]


def test_cube_describe_unknown_table_raises():
    conn = _fake_cube()
    with pytest.raises(IntrospectionUnsupported, match="not found"):
        conn.describe_table("ghost")


def test_cube_query_still_raises():
    conn = _fake_cube()
    with pytest.raises(NotImplementedError):
        conn.query("SELECT 1")


# --- BigQuery override (qualified INFORMATION_SCHEMA) ------------------------


def _bq(config: dict):
    from dashdown.data.bigquery_connector import BigQueryConnector

    return BigQueryConnector("b", config)


def test_bigquery_prefix_from_dataset_and_location():
    # Each path part is backticked separately so a hyphenated project id is safe,
    # and the region form is project-qualified per BigQuery's INFORMATION_SCHEMA docs.
    assert _bq({"dataset": "d", "project": "p"})._information_schema_prefix() == "`p`.`d`.INFORMATION_SCHEMA"
    assert _bq({"dataset": "d"})._information_schema_prefix() == "`d`.INFORMATION_SCHEMA"
    assert (
        _bq({"location": "EU", "project": "my-gcp-project"})._information_schema_prefix()
        == "`my-gcp-project`.`region-eu`.INFORMATION_SCHEMA"
    )
    assert _bq({"location": "EU"})._information_schema_prefix() == "`region-eu`.INFORMATION_SCHEMA"
    assert _bq({})._information_schema_prefix() is None


def test_bigquery_introspection_without_qualifier_raises():
    conn = _bq({})
    with pytest.raises(IntrospectionUnsupported, match="dataset"):
        conn.list_tables()
    with pytest.raises(IntrospectionUnsupported, match="dataset"):
        conn.describe_table("t")


def test_bigquery_qualified_sql_and_escaping():
    captured = {}
    conn = _bq({"dataset": "d", "project": "p"})

    def fake_query(sql):
        captured["sql"] = sql
        return QueryResult(columns=["column_name", "data_type", "is_nullable"], rows=[])

    conn.query = fake_query  # type: ignore[method-assign]
    conn.describe_table("t'; --")
    assert "`p`.`d`.INFORMATION_SCHEMA.COLUMNS" in captured["sql"]
    assert "table_name = 't''; --'" in captured["sql"]
