"""Tests for the cloud-warehouse connectors (Snowflake + BigQuery).

Both subclass the shared `DBAPIConnector` (its execute/fetch/clean/retry plumbing
is covered in `test_dbapi_connectors.py`), so these tests focus on what is
specific to each: connect-parameter assembly, client construction, the friendly
missing-driver hint, and registration / entry-point metadata. None of the heavy
drivers are installed, so `_import_driver` is stubbed where a connection is
actually built.
"""
from __future__ import annotations

import sys
import types

import pytest

from dashdown.data.base import QueryResult, get_connector_type
from dashdown.data.snowflake_connector import SnowflakeConnector
from dashdown.data.bigquery_connector import BigQueryConnector


class TestSnowflake:
    def test_connect_params_from_config(self, monkeypatch):
        captured = {}

        class FakeSnowflake:
            @staticmethod
            def connect(**kwargs):
                captured.update(kwargs)
                return object()

        monkeypatch.setattr(
            "dashdown.data.snowflake_connector._import_driver",
            lambda module, extra: FakeSnowflake,
        )
        c = SnowflakeConnector(
            "sf",
            {
                "account": "ab12345.eu-central-1",
                "user": "reader",
                "password": "secret",
                "warehouse": "COMPUTE_WH",
                "database": "ANALYTICS",
                "schema": "PUBLIC",
                "role": "REPORTER",
                "connect_args": {"authenticator": "externalbrowser"},
                "_project_root": "/tmp",  # must be ignored
            },
        )
        c._connect()
        assert captured == {
            "account": "ab12345.eu-central-1",
            "user": "reader",
            "password": "secret",
            "warehouse": "COMPUTE_WH",
            "database": "ANALYTICS",
            "schema": "PUBLIC",
            "role": "REPORTER",
            "authenticator": "externalbrowser",
        }

    def test_omits_unset_keys(self, monkeypatch):
        captured = {}

        class FakeSnowflake:
            @staticmethod
            def connect(**kwargs):
                captured.update(kwargs)
                return object()

        monkeypatch.setattr(
            "dashdown.data.snowflake_connector._import_driver",
            lambda module, extra: FakeSnowflake,
        )
        SnowflakeConnector("sf", {"account": "x", "user": "u"})._connect()
        assert captured == {"account": "x", "user": "u"}

    def test_missing_driver_hint(self):
        c = SnowflakeConnector("sf", {"account": "x"})
        with pytest.raises(ImportError) as exc:
            c._connect()
        msg = str(exc.value)
        assert "dashdown-md[snowflake]" in msg
        assert "snowflake.connector" in msg

    def test_end_to_end_via_fake_conn(self, monkeypatch):
        # Smoke-test that the shared DBAPI plumbing runs through this subclass.
        from tests.test_dbapi_connectors import _FakeConn

        conn = _FakeConn(result=(["n"], [(1,)]))
        c = SnowflakeConnector("sf", {})
        monkeypatch.setattr(c, "_connect", lambda: conn)
        result = c.query("SELECT 1")
        assert isinstance(result, QueryResult)
        assert result.rows == [[1]]


class TestBigQuery:
    def _fake_bigquery_module(self, captured):
        mod = types.SimpleNamespace()

        class FakeClient:
            def __init__(self, **kwargs):
                captured.update(kwargs)

        mod.Client = FakeClient
        return mod

    def test_make_client_params(self, monkeypatch):
        captured = {}
        fake = self._fake_bigquery_module(captured)
        monkeypatch.setattr(
            "dashdown.data.bigquery_connector._import_driver",
            lambda module, extra: fake,
        )
        c = BigQueryConnector("bq", {"project": "my-proj", "location": "EU"})
        client = c._make_client(fake)
        assert client is not None
        assert captured == {"project": "my-proj", "location": "EU"}

    def test_make_client_with_service_account(self, monkeypatch, tmp_path):
        captured = {}
        fake = self._fake_bigquery_module(captured)

        # Stub google.oauth2.service_account so no real key is parsed.
        sa_mod = types.SimpleNamespace()

        class FakeCreds:
            @staticmethod
            def from_service_account_file(path):
                captured["cred_file"] = path
                return "CREDS"

        sa_mod.Credentials = FakeCreds
        google_pkg = types.ModuleType("google")
        oauth2_pkg = types.ModuleType("google.oauth2")
        oauth2_pkg.service_account = sa_mod
        monkeypatch.setitem(sys.modules, "google", google_pkg)
        monkeypatch.setitem(sys.modules, "google.oauth2", oauth2_pkg)
        monkeypatch.setitem(sys.modules, "google.oauth2.service_account", sa_mod)

        key = tmp_path / "sa.json"
        key.write_text("{}")
        c = BigQueryConnector(
            "bq",
            {"project": "p", "credentials_path": "sa.json", "_project_root": tmp_path},
        )
        c._make_client(fake)
        assert captured["credentials"] == "CREDS"
        assert captured["cred_file"].endswith("sa.json")

    def test_missing_driver_hint(self):
        c = BigQueryConnector("bq", {"project": "p"})
        with pytest.raises(ImportError) as exc:
            c._connect()
        msg = str(exc.value)
        assert "dashdown-md[bigquery]" in msg
        assert "google.cloud.bigquery" in msg


class TestRegistration:
    def test_registered_type_names(self):
        assert get_connector_type("snowflake") is SnowflakeConnector
        assert get_connector_type("bigquery") is BigQueryConnector

    def test_entry_points_expose_connectors(self):
        from importlib import metadata
        from dashdown.data.base import ENTRY_POINT_GROUP

        names = {ep.name for ep in metadata.entry_points(group=ENTRY_POINT_GROUP)}
        assert {"snowflake", "bigquery"} <= names
