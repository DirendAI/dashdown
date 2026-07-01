"""Tests for the ClickHouse connector (clickhouse-connect).

`ClickHouseConnector` subclasses the shared `DBAPIConnector` (its execute/fetch/
clean/retry plumbing is covered in `test_dbapi_connectors.py`), so these tests
focus on what is specific to it: mapping the sources.yaml config onto the
driver's `dbapi.connect()` kwargs (discrete keys, aliases, the DSN escape hatch,
`connect_args` passthrough), the friendly missing-driver hint, and
registration / entry-point metadata. clickhouse-connect is not installed, so
`_connect()` is exercised against a fake `clickhouse_connect.dbapi` module.
"""
from __future__ import annotations

import sys
import types

import pytest

from dashdown.data.base import get_connector_type
from dashdown.data.clickhouse_connector import ClickHouseConnector


def _capture_connect(monkeypatch) -> dict:
    """Install a fake `clickhouse_connect.dbapi` that records connect() kwargs."""
    captured: dict = {}

    def connect(**kwargs):
        captured.update(kwargs)
        return object()

    dbapi_mod = types.ModuleType("clickhouse_connect.dbapi")
    dbapi_mod.connect = connect
    pkg = types.ModuleType("clickhouse_connect")
    pkg.dbapi = dbapi_mod
    monkeypatch.setitem(sys.modules, "clickhouse_connect", pkg)
    monkeypatch.setitem(sys.modules, "clickhouse_connect.dbapi", dbapi_mod)
    return captured


class TestConnectParams:
    def test_discrete_keys(self, monkeypatch):
        captured = _capture_connect(monkeypatch)
        c = ClickHouseConnector(
            "events",
            {
                "host": "ch.example.com",
                "port": 8443,
                "database": "analytics",
                "user": "reader",
                "password": "secret",
                "secure": True,
            },
        )
        c._connect()
        assert captured == {
            "host": "ch.example.com",
            "port": 8443,
            "database": "analytics",
            "username": "reader",
            "password": "secret",
            "secure": True,
        }

    def test_defaults_and_omitted_keys(self, monkeypatch):
        captured = _capture_connect(monkeypatch)
        c = ClickHouseConnector("events", {})
        c._connect()
        # Only host defaults; port/database/credentials stay with the driver's
        # own defaults (port depends on `secure` there, so it is never guessed).
        assert captured == {"host": "localhost"}

    def test_aliases(self, monkeypatch):
        captured = _capture_connect(monkeypatch)
        c = ClickHouseConnector(
            "events", {"db": "analytics", "username": "reader", "port": "8123"}
        )
        c._connect()
        assert captured["database"] == "analytics"
        assert captured["username"] == "reader"
        assert captured["port"] == 8123  # coerced to int

    def test_url_replaces_discrete_keys(self, monkeypatch):
        captured = _capture_connect(monkeypatch)
        c = ClickHouseConnector(
            "events",
            {
                "url": "clickhouse://reader:secret@ch.example.com:8443/analytics?secure=true",
                "host": "ignored",
                "user": "ignored",
            },
        )
        c._connect()
        assert captured == {
            "dsn": "clickhouse://reader:secret@ch.example.com:8443/analytics?secure=true"
        }

    def test_dsn_alias(self, monkeypatch):
        captured = _capture_connect(monkeypatch)
        c = ClickHouseConnector("events", {"dsn": "clickhouse://h/db"})
        c._connect()
        assert captured == {"dsn": "clickhouse://h/db"}

    def test_connect_args_passthrough_and_override(self, monkeypatch):
        captured = _capture_connect(monkeypatch)
        c = ClickHouseConnector(
            "events",
            {
                "host": "h",
                "connect_args": {"connect_timeout": 10, "host": "override"},
            },
        )
        c._connect()
        assert captured["connect_timeout"] == 10
        assert captured["host"] == "override"


class TestMissingDriverHint:
    def test_missing_driver_message(self, monkeypatch):
        # A `None` entry in sys.modules makes importlib.import_module raise
        # ImportError regardless of the dev venv, surfacing the install hint.
        monkeypatch.setitem(sys.modules, "clickhouse_connect", None)
        monkeypatch.setitem(sys.modules, "clickhouse_connect.dbapi", None)
        c = ClickHouseConnector("events", {})
        with pytest.raises(ImportError) as exc:
            c._connect()
        msg = str(exc.value)
        assert "dashdown-md[clickhouse]" in msg
        assert "clickhouse_connect" in msg


class TestRegistration:
    def test_registered_type_name(self):
        assert get_connector_type("clickhouse") is ClickHouseConnector

    def test_entry_point_exposes_connector(self):
        from importlib import metadata
        from dashdown.data.base import ENTRY_POINT_GROUP

        names = {ep.name for ep in metadata.entry_points(group=ENTRY_POINT_GROUP)}
        assert "clickhouse" in names
