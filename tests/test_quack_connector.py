"""Tests for dashdown.data.quack_connector.

Quack is remote DuckDB over an RPC protocol: a local DuckDB loads the ``quack``
extension, registers a token secret, and ``ATTACH``-es a ``quack:`` target. So
the connector is a thin :class:`DuckDBConnector` subclass. These tests cover
registration, target construction, ``${ENV}`` token resolution, and the exact
``_setup()`` SQL (INSTALL/LOAD/CREATE SECRET/ATTACH) — all without a live Quack
server by stubbing ``duckdb.connect``.
"""
from __future__ import annotations

import pytest

import dashdown.data.quack_connector as q
from dashdown.data.base import get_connector_type
from dashdown.data.quack_connector import QuackConnector


class _FakeConn:
    """Records every ``execute(sql)`` so we can assert on the setup statements."""

    def __init__(self):
        self.executed: list[str] = []

    def execute(self, sql):
        self.executed.append(sql)
        return self

    def close(self):
        pass


@pytest.fixture
def captured(monkeypatch):
    """Stub duckdb.connect; expose the (target, config) and the fake connection."""
    calls = {}

    def fake_connect(target, config=None, **kw):
        calls["target"] = target
        calls["config"] = dict(config or {})
        conn = _FakeConn()
        calls["conn"] = conn
        return conn

    monkeypatch.setattr(q.duckdb, "connect", fake_connect)
    return calls


def _sql(captured) -> list[str]:
    return captured["conn"].executed


class TestRegistration:
    def test_registered_as_quack(self):
        assert get_connector_type("quack") is QuackConnector


class TestLocalConnection:
    def test_local_connection_is_in_memory(self, captured):
        QuackConnector("remote", {"host": "h"})
        assert captured["target"] == ":memory:"

    def test_duckdb_config_passthrough(self, captured):
        QuackConnector(
            "remote", {"host": "h", "duckdb_config": {"allow_unsigned_extensions": True}}
        )
        assert captured["config"]["allow_unsigned_extensions"] is True


class TestTarget:
    def test_host_becomes_quack_target(self, captured):
        QuackConnector("remote", {"host": "data.example.com"})
        assert any("ATTACH 'quack:data.example.com'" in s for s in _sql(captured))

    def test_host_and_port(self, captured):
        QuackConnector("remote", {"host": "data.example.com", "port": 9494})
        assert any("ATTACH 'quack:data.example.com:9494'" in s for s in _sql(captured))

    def test_full_quack_target_passed_through(self, captured):
        QuackConnector("remote", {"target": "quack:already:1234"})
        assert any("ATTACH 'quack:already:1234'" in s for s in _sql(captured))

    def test_missing_host_and_target_raises(self, captured):
        with pytest.raises(ValueError, match="host"):
            QuackConnector("remote", {})

    def test_default_alias_is_remote(self, captured):
        QuackConnector("remote", {"host": "h"})
        assert any('AS "remote"' in s for s in _sql(captured))

    def test_custom_alias(self, captured):
        QuackConnector("remote", {"host": "h", "database": "warehouse"})
        assert any('AS "warehouse"' in s for s in _sql(captured))


class TestExtension:
    def test_installs_from_community_then_loads_by_default(self, captured):
        QuackConnector("remote", {"host": "h"})
        sql = _sql(captured)
        assert "INSTALL quack FROM community" in sql
        assert "LOAD quack" in sql
        # INSTALL must precede LOAD.
        assert sql.index("INSTALL quack FROM community") < sql.index("LOAD quack")

    def test_install_can_be_disabled(self, captured):
        QuackConnector("remote", {"host": "h", "install_extension": False})
        sql = _sql(captured)
        assert not any(s.startswith("INSTALL quack") for s in sql)
        # LOAD still runs (the secret type / attach need the extension present).
        assert "LOAD quack" in sql

    def test_url_repository_is_quoted(self, captured):
        QuackConnector(
            "remote", {"host": "h", "extension_repository": "https://ext.example.com"}
        )
        assert "INSTALL quack FROM 'https://ext.example.com'" in _sql(captured)


class TestToken:
    def test_no_token_skips_secret(self, captured):
        QuackConnector("remote", {"host": "h"})
        assert not any("CREATE OR REPLACE SECRET" in s for s in _sql(captured))

    def test_literal_token_creates_secret(self, captured):
        QuackConnector("remote", {"host": "h", "token": "super_secret"})
        sql = _sql(captured)
        secret = next(s for s in sql if "CREATE OR REPLACE SECRET" in s)
        assert "TYPE quack" in secret
        assert "TOKEN 'super_secret'" in secret
        # The secret type comes from the extension → LOAD before CREATE SECRET.
        assert sql.index("LOAD quack") < sql.index(secret)

    def test_quack_token_key_alias(self, captured):
        QuackConnector("remote", {"host": "h", "quack_token": "tok-xyz"})
        assert any("TOKEN 'tok-xyz'" in s for s in _sql(captured))

    def test_env_var_token_is_expanded(self, captured, monkeypatch):
        monkeypatch.setenv("QUACK_TOKEN", "secret-from-env")
        QuackConnector("remote", {"host": "h", "token": "${QUACK_TOKEN}"})
        assert any("TOKEN 'secret-from-env'" in s for s in _sql(captured))

    def test_missing_env_var_raises(self, captured, monkeypatch):
        monkeypatch.delenv("QUACK_TOKEN", raising=False)
        with pytest.raises(ValueError, match="QUACK_TOKEN"):
            QuackConnector("remote", {"host": "h", "token": "${QUACK_TOKEN}"})

    def test_token_with_quote_is_escaped(self, captured):
        QuackConnector("remote", {"host": "h", "token": "a'b"})
        assert any("TOKEN 'a''b'" in s for s in _sql(captured))

    def test_secret_name_is_identifier_safe(self, captured):
        # A connector name with non-identifier chars must yield a valid secret name.
        QuackConnector("my-remote.1", {"host": "h", "token": "t"})
        secret = next(s for s in _sql(captured) if "CREATE OR REPLACE SECRET" in s)
        assert '"quack_my_remote_1"' in secret


class TestInheritedBehavior:
    def test_query_uses_duckdb_execute_path(self, captured):
        # The connector inherits query()/_execute() from DuckDBConnector.
        conn = QuackConnector("remote", {"host": "h"})

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

    def test_reconnect_reattaches_remote(self, captured, monkeypatch):
        # On reconnect, _setup() must re-run so the remote is re-attached.
        conn = QuackConnector("remote", {"host": "h", "token": "t"})
        first = captured["conn"]
        assert any("ATTACH" in s for s in first.executed)

        conn._connect()  # simulate the reconnect-on-fatal rebuild
        second = captured["conn"]
        assert second is not first
        assert any("ATTACH 'quack:h'" in s for s in second.executed)
        assert any("CREATE OR REPLACE SECRET" in s for s in second.executed)
