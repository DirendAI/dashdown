"""Tests for the DB-API SQL connectors (PostgreSQL + MySQL).

These run without a real database or the optional drivers installed: each
connector's `_connect()` is patched to return a fake PEP 249 connection. The
shared plumbing (`dashdown/data/dbapi.py`) is what actually does execute/fetch/
clean/retry, so most assertions go through either concrete connector.

Covered: column/row extraction, JSON-safe value coercion (Decimal, datetime,
bytes, None, NaN, bool), empty result sets, commit-per-query, lazy connect,
reconnect-and-retry on a dropped connection, the friendly missing-driver hint,
URL parsing, and registration/entry-point metadata.
"""
from __future__ import annotations

import math
import sys
from datetime import date, datetime, time, timedelta
from decimal import Decimal

import pytest

from dashdown.data.base import QueryResult, get_connector_type
from dashdown.data import dbapi
from dashdown.data.dbapi import parse_db_url
from dashdown.data.postgres_connector import PostgresConnector
from dashdown.data.mysql_connector import MySQLConnector


class _FakeCursor:
    def __init__(self, conn: "_FakeConn") -> None:
        self._conn = conn
        self.description = None
        self._rows: list = []
        self.closed = False

    def execute(self, sql, params=None):
        self._conn.executed.append(sql)
        if self._conn.raise_on_execute is not None:
            exc = self._conn.raise_on_execute
            self._conn.raise_on_execute = None  # raise once, then succeed
            raise exc
        result = self._conn.result
        if result is None:
            self.description = None
            self._rows = []
        else:
            cols, rows = result
            self.description = [(c,) for c in cols]
            self._rows = list(rows)

    def fetchall(self):
        return self._rows

    def close(self):
        self.closed = True


class _FakeConn:
    """Minimal PEP 249 connection stand-in."""

    def __init__(self, result=None, raise_on_execute=None):
        self.result = result  # (columns, rows) or None
        self.raise_on_execute = raise_on_execute
        self.executed: list[str] = []
        self.commits = 0
        self.closed = False

    def cursor(self):
        return _FakeCursor(self)

    def commit(self):
        self.commits += 1

    def close(self):
        self.closed = True


class OperationalError(Exception):
    """Stand-in matching the PEP 249 class name the retry heuristic looks for."""


def _patch_connect(monkeypatch, connector, conn):
    monkeypatch.setattr(connector, "_connect", lambda: conn)


class TestColumnAndRowExtraction:
    def test_columns_and_rows(self, monkeypatch):
        conn = _FakeConn(result=(["id", "name"], [(1, "a"), (2, "b")]))
        c = PostgresConnector("pg", {})
        _patch_connect(monkeypatch, c, conn)
        result = c.query("SELECT id, name FROM t")
        assert isinstance(result, QueryResult)
        assert result.columns == ["id", "name"]
        assert result.rows == [[1, "a"], [2, "b"]]

    def test_empty_result_keeps_columns(self, monkeypatch):
        conn = _FakeConn(result=(["n"], []))
        c = MySQLConnector("my", {})
        _patch_connect(monkeypatch, c, conn)
        result = c.query("SELECT n FROM t WHERE 1=0")
        assert result.columns == ["n"]
        assert result.rows == []

    def test_no_description_yields_empty(self, monkeypatch):
        # e.g. a statement that returns no result set
        conn = _FakeConn(result=None)
        c = PostgresConnector("pg", {})
        _patch_connect(monkeypatch, c, conn)
        result = c.query("CREATE TEMP TABLE x (i int)")
        assert result.columns == []
        assert result.rows == []


class TestValueCoercion:
    def test_json_unsafe_types_are_normalized(self, monkeypatch):
        dt = datetime(2026, 6, 10, 12, 30, 0)
        d = date(2026, 6, 10)
        t = time(8, 15)
        conn = _FakeConn(
            result=(
                ["dec", "dt", "d", "t", "td", "blob", "nil", "flag"],
                [(Decimal("9.99"), dt, d, t, timedelta(seconds=90), b"hi", None, True)],
            )
        )
        c = PostgresConnector("pg", {})
        _patch_connect(monkeypatch, c, conn)
        row = c.query("SELECT *").rows[0]
        assert row[0] == 9.99 and isinstance(row[0], float)
        assert row[1] == "2026-06-10T12:30:00"
        assert row[2] == "2026-06-10"
        assert row[3] == "08:15:00"
        assert row[4] == "0:01:30"
        assert row[5] == "hi"
        assert row[6] is None
        assert row[7] is True

    def test_nan_and_inf_become_none(self, monkeypatch):
        conn = _FakeConn(result=(["a", "b"], [(float("nan"), float("inf"))]))
        c = MySQLConnector("my", {})
        _patch_connect(monkeypatch, c, conn)
        assert c.query("SELECT *").rows == [[None, None]]

    def test_non_utf8_bytes_fall_back_to_hex(self, monkeypatch):
        conn = _FakeConn(result=(["b"], [(b"\xff\xfe",)]))
        c = PostgresConnector("pg", {})
        _patch_connect(monkeypatch, c, conn)
        assert c.query("SELECT *").rows == [["fffe"]]

    def test_memoryview_decoded(self, monkeypatch):
        conn = _FakeConn(result=(["b"], [(memoryview(b"ok"),)]))
        c = PostgresConnector("pg", {})
        _patch_connect(monkeypatch, c, conn)
        assert c.query("SELECT *").rows == [["ok"]]


class TestTransactionAndLifecycle:
    def test_commit_per_query(self, monkeypatch):
        conn = _FakeConn(result=(["n"], [(1,)]))
        c = PostgresConnector("pg", {})
        _patch_connect(monkeypatch, c, conn)
        c.query("SELECT 1")
        c.query("SELECT 1")
        assert conn.commits == 2

    def test_lazy_connect(self, monkeypatch):
        calls = []

        def fake_connect():
            calls.append(1)
            return _FakeConn(result=(["n"], [(1,)]))

        c = PostgresConnector("pg", {})
        monkeypatch.setattr(c, "_connect", fake_connect)
        assert calls == []  # nothing opened at construction
        c.query("SELECT 1")
        c.query("SELECT 1")
        assert calls == [1]  # connection reused, opened once

    def test_close_is_idempotent(self, monkeypatch):
        conn = _FakeConn(result=(["n"], [(1,)]))
        c = PostgresConnector("pg", {})
        _patch_connect(monkeypatch, c, conn)
        c.query("SELECT 1")
        c.close()
        assert conn.closed is True
        c.close()  # second close must not raise


class TestReconnectRetry:
    def test_connection_error_triggers_one_reconnect(self, monkeypatch):
        dead = _FakeConn(
            result=(["n"], [(1,)]), raise_on_execute=OperationalError("server closed")
        )
        fresh = _FakeConn(result=(["n"], [(42,)]))
        conns = [dead, fresh]
        c = PostgresConnector("pg", {})
        monkeypatch.setattr(c, "_connect", lambda: conns.pop(0))

        result = c.query("SELECT n")
        assert result.rows == [[42]]  # retried on the fresh connection
        assert dead.closed is True  # the dead one was reset

    def test_non_connection_error_is_not_retried(self, monkeypatch):
        conn = _FakeConn(
            result=(["n"], [(1,)]), raise_on_execute=ValueError("bad SQL")
        )
        c = MySQLConnector("my", {})
        monkeypatch.setattr(c, "_connect", lambda: conn)
        with pytest.raises(ValueError):
            c.query("SELECT bogus")


class TestMissingDriverHint:
    def test_postgres_missing_driver_message(self, monkeypatch):
        # Force the driver to look absent regardless of the dev venv (a `None`
        # entry in sys.modules makes importlib.import_module raise ImportError),
        # so _connect surfaces the friendly install hint.
        monkeypatch.setitem(sys.modules, "psycopg2", None)
        c = PostgresConnector("pg", {})
        with pytest.raises(ImportError) as exc:
            c._connect()
        msg = str(exc.value)
        assert "dashdown-md[postgres]" in msg
        assert "psycopg2" in msg

    def test_mysql_missing_driver_message(self, monkeypatch):
        monkeypatch.setitem(sys.modules, "pymysql", None)
        c = MySQLConnector("my", {})
        with pytest.raises(ImportError) as exc:
            c._connect()
        msg = str(exc.value)
        assert "dashdown-md[mysql]" in msg
        assert "pymysql" in msg


class TestUrlParsing:
    def test_full_url(self):
        params = parse_db_url("mysql://reader:s3cr3t@db.example.com:3307/shop")
        assert params == {
            "host": "db.example.com",
            "port": 3307,
            "user": "reader",
            "password": "s3cr3t",
            "database": "shop",
        }

    def test_percent_encoded_credentials(self):
        params = parse_db_url("postgresql://u%40ser:p%40ss@h/db")
        assert params["user"] == "u@ser"
        assert params["password"] == "p@ss"

    def test_partial_url_omits_absent_keys(self):
        params = parse_db_url("mysql://host/db")
        assert params == {"host": "host", "database": "db"}


class TestRegistration:
    def test_registered_type_names(self):
        assert get_connector_type("postgres") is PostgresConnector
        assert get_connector_type("mysql") is MySQLConnector

    def test_entry_points_expose_connectors(self):
        from importlib import metadata
        from dashdown.data.base import ENTRY_POINT_GROUP

        names = {ep.name for ep in metadata.entry_points(group=ENTRY_POINT_GROUP)}
        assert {"postgres", "mysql"} <= names
