"""Tests for dashdown.data.motherduck_connector.

MotherDuck is cloud DuckDB reached over an ``md:`` target with a service token,
so the connector is a thin :class:`DuckDBConnector` subclass. These tests cover
registration, ``md:`` target construction, ``${ENV}`` token resolution, and that
the token is threaded into ``duckdb.connect`` — all without a live MotherDuck
account by stubbing ``duckdb.connect``.
"""
from __future__ import annotations

import pytest

import dashdown.data.motherduck_connector as md
from dashdown.data.base import get_connector_type
from dashdown.data.motherduck_connector import MotherDuckConnector


class _FakeConn:
    def close(self):
        pass


@pytest.fixture
def captured(monkeypatch):
    """Stub duckdb.connect; record the (target, config) it was called with."""
    calls = {}

    def fake_connect(target, config=None, **kw):
        calls["target"] = target
        calls["config"] = dict(config or {})
        return _FakeConn()

    monkeypatch.setattr(md.duckdb, "connect", fake_connect)
    return calls


class TestRegistration:
    def test_registered_as_motherduck(self):
        assert get_connector_type("motherduck") is MotherDuckConnector


class TestTarget:
    def test_default_target_is_bare_md(self, captured):
        MotherDuckConnector("cloud", {})
        assert captured["target"] == "md:"

    def test_database_name_becomes_md_prefixed(self, captured):
        MotherDuckConnector("cloud", {"database": "my_db"})
        assert captured["target"] == "md:my_db"

    def test_db_alias(self, captured):
        MotherDuckConnector("cloud", {"db": "analytics"})
        assert captured["target"] == "md:analytics"

    def test_full_md_target_passed_through(self, captured):
        MotherDuckConnector("cloud", {"database": "md:already"})
        assert captured["target"] == "md:already"


class TestToken:
    def test_literal_token_threaded_into_connect(self, captured):
        MotherDuckConnector("cloud", {"token": "tok-123"})
        assert captured["config"]["motherduck_token"] == "tok-123"

    def test_motherduck_token_key_alias(self, captured):
        MotherDuckConnector("cloud", {"motherduck_token": "tok-xyz"})
        assert captured["config"]["motherduck_token"] == "tok-xyz"

    def test_env_var_token_is_expanded(self, captured, monkeypatch):
        monkeypatch.setenv("MD_TOKEN", "secret-from-env")
        MotherDuckConnector("cloud", {"token": "${MD_TOKEN}"})
        assert captured["config"]["motherduck_token"] == "secret-from-env"

    def test_missing_env_var_raises(self, captured, monkeypatch):
        monkeypatch.delenv("MD_TOKEN", raising=False)
        with pytest.raises(ValueError, match="MD_TOKEN"):
            MotherDuckConnector("cloud", {"token": "${MD_TOKEN}"})

    def test_no_token_omits_setting(self, captured):
        # DuckDB falls back to the motherduck_token env var on its own.
        MotherDuckConnector("cloud", {})
        assert "motherduck_token" not in captured["config"]


class TestExtraConfig:
    def test_duckdb_config_passthrough(self, captured):
        MotherDuckConnector(
            "cloud", {"token": "t", "duckdb_config": {"custom_user_agent": "app"}}
        )
        assert captured["config"]["custom_user_agent"] == "app"
        assert captured["config"]["motherduck_token"] == "t"


class TestInheritedBehavior:
    def test_query_uses_duckdb_execute_path(self, captured):
        # The connector inherits query()/_execute() from DuckDBConnector — exercise
        # them against the fake connection via a stub cursor.
        conn = MotherDuckConnector("cloud", {"token": "t"})

        class _Cur:
            description = [("n",)]

            def execute(self, sql):
                pass

            def fetchall(self):
                return [(1,)]

        conn._con.cursor = lambda: _Cur()
        result = conn.query("SELECT 1 AS n")
        assert result.columns == ["n"]
        assert result.rows == [[1]]
