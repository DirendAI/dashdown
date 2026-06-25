"""Tests for the Microsoft SQL Server / Azure SQL connector (pyodbc).

`MSSQLConnector` subclasses the shared `DBAPIConnector` (its execute/fetch/clean/
retry plumbing is covered in `test_dbapi_connectors.py`), so these tests focus on
what is specific to it: assembling the ODBC connection string from the discrete
config keys across the auth modes (SQL login, service principal, managed identity,
raw connection string / URL), value escaping, the friendly missing-driver hint, and
registration / entry-point metadata. pyodbc is not installed, so `_connect()` is
exercised only where the connection is actually built (and then stubbed).
"""
from __future__ import annotations

import pytest

from dashdown.data.base import QueryResult, get_connector_type
from dashdown.data.mssql_connector import (
    DEFAULT_ODBC_DRIVER,
    MSSQLConnector,
    _odbc_escape,
    _yes_no,
)


def _kv(conn_str: str) -> dict[str, str]:
    """Parse an ODBC connection string into a dict (DRIVER's braces preserved)."""
    out: dict[str, str] = {}
    for part in conn_str.split(";"):
        if not part:
            continue
        key, _, value = part.partition("=")
        out[key] = value
    return out


class TestConnectionStringSqlLogin:
    def test_basic_sql_login(self):
        c = MSSQLConnector(
            "db",
            {
                "host": "db.example.com",
                "port": 1433,
                "database": "analytics",
                "user": "reader",
                "password": "secret",
            },
        )
        kv = _kv(c._build_connection_string())
        assert kv["DRIVER"] == "{" + DEFAULT_ODBC_DRIVER + "}"
        assert kv["SERVER"] == "db.example.com,1433"
        assert kv["DATABASE"] == "analytics"
        assert kv["UID"] == "reader"
        assert kv["PWD"] == "secret"
        assert "Authentication" not in kv

    def test_aliases_and_default_server(self):
        c = MSSQLConnector(
            "db",
            {"server": "h", "dbname": "d", "uid": "u", "pwd": "p"},
        )
        kv = _kv(c._build_connection_string())
        assert kv["SERVER"] == "h"  # no port → bare host
        assert kv["DATABASE"] == "d"
        assert kv["UID"] == "u"
        assert kv["PWD"] == "p"

    def test_custom_driver(self):
        c = MSSQLConnector(
            "db", {"host": "h", "driver": "ODBC Driver 17 for SQL Server"}
        )
        kv = _kv(c._build_connection_string())
        assert kv["DRIVER"] == "{ODBC Driver 17 for SQL Server}"

    def test_encrypt_and_trust_cert(self):
        c = MSSQLConnector(
            "db",
            {"host": "h", "encrypt": True, "trust_server_certificate": False},
        )
        kv = _kv(c._build_connection_string())
        assert kv["Encrypt"] == "Yes"
        assert kv["TrustServerCertificate"] == "No"


class TestServicePrincipal:
    def test_inferred_from_client_id_secret(self):
        c = MSSQLConnector(
            "db",
            {
                "host": "myserver.database.windows.net",
                "database": "analytics",
                "client_id": "app-id",
                "client_secret": "app-secret",
            },
        )
        kv = _kv(c._build_connection_string())
        assert kv["Authentication"] == "ActiveDirectoryServicePrincipal"
        assert kv["UID"] == "app-id"
        assert kv["PWD"] == "app-secret"

    def test_tenant_id_appended_to_uid(self):
        c = MSSQLConnector(
            "db",
            {
                "host": "h",
                "client_id": "app-id",
                "client_secret": "sec",
                "tenant_id": "tenant",
            },
        )
        kv = _kv(c._build_connection_string())
        assert kv["UID"] == "app-id@tenant"
        assert kv["PWD"] == "sec"

    def test_explicit_authentication_kept(self):
        c = MSSQLConnector(
            "db",
            {
                "host": "h",
                "authentication": "ActiveDirectoryServicePrincipal",
                "client_id": "app-id",
                "client_secret": "sec",
            },
        )
        kv = _kv(c._build_connection_string())
        assert kv["Authentication"] == "ActiveDirectoryServicePrincipal"
        assert kv["UID"] == "app-id"


class TestOtherAzureAdModes:
    def test_managed_identity(self):
        c = MSSQLConnector(
            "db", {"host": "h", "authentication": "ActiveDirectoryMsi"}
        )
        kv = _kv(c._build_connection_string())
        assert kv["Authentication"] == "ActiveDirectoryMsi"
        assert "UID" not in kv  # system-assigned identity: no UID
        assert "PWD" not in kv

    def test_user_assigned_managed_identity(self):
        c = MSSQLConnector(
            "db",
            {
                "host": "h",
                "authentication": "ActiveDirectoryMsi",
                "client_id": "uami-id",
            },
        )
        kv = _kv(c._build_connection_string())
        # client_id alone (no secret) is not service-principal: it's the UAMI id.
        assert kv["Authentication"] == "ActiveDirectoryMsi"
        assert kv["UID"] == "uami-id"
        assert "PWD" not in kv

    def test_ad_password(self):
        c = MSSQLConnector(
            "db",
            {
                "host": "h",
                "authentication": "ActiveDirectoryPassword",
                "user": "me@corp.com",
                "password": "pw",
            },
        )
        kv = _kv(c._build_connection_string())
        assert kv["Authentication"] == "ActiveDirectoryPassword"
        assert kv["UID"] == "me@corp.com"
        assert kv["PWD"] == "pw"

    def test_ad_default_chain(self):
        c = MSSQLConnector(
            "db", {"host": "h", "authentication": "ActiveDirectoryDefault"}
        )
        kv = _kv(c._build_connection_string())
        assert kv["Authentication"] == "ActiveDirectoryDefault"


class TestRawAndUrl:
    def test_raw_connection_string_passthrough(self):
        raw = "DRIVER={ODBC Driver 18 for SQL Server};SERVER=h;DATABASE=d;UID=u;PWD=p"
        c = MSSQLConnector("db", {"connection_string": raw})
        assert c._build_connection_string() == raw

    def test_url_fills_discrete_fields(self):
        c = MSSQLConnector(
            "db", {"url": "mssql://reader:s3cr3t@db.example.com:1433/analytics"}
        )
        kv = _kv(c._build_connection_string())
        assert kv["SERVER"] == "db.example.com,1433"
        assert kv["DATABASE"] == "analytics"
        assert kv["UID"] == "reader"
        assert kv["PWD"] == "s3cr3t"

    def test_extra_odbc_keywords_merged(self):
        c = MSSQLConnector(
            "db",
            {"host": "h", "odbc": {"MultiSubnetFailover": "Yes"}},
        )
        kv = _kv(c._build_connection_string())
        assert kv["MultiSubnetFailover"] == "Yes"


class TestEscaping:
    def test_password_with_special_chars_is_braced(self):
        c = MSSQLConnector(
            "db", {"host": "h", "user": "u", "password": "p;w{x}=y"}
        )
        conn = c._build_connection_string()
        # The brace-wrapped value keeps the rest of the string parseable.
        assert "PWD={p;w{x}}=y}" in conn

    def test_odbc_escape_plain_value_untouched(self):
        assert _odbc_escape("analytics") == "analytics"

    def test_odbc_escape_wraps_specials(self):
        assert _odbc_escape("a;b") == "{a;b}"
        assert _odbc_escape("a}b") == "{a}}b}"
        assert _odbc_escape(" x ") == "{ x }"

    def test_yes_no_normalization(self):
        assert _yes_no(True) == "Yes"
        assert _yes_no(False) == "No"
        assert _yes_no("yes") == "Yes"
        assert _yes_no("false") == "No"


class TestDriverAndLifecycle:
    def test_missing_driver_hint(self):
        c = MSSQLConnector("db", {"host": "h"})
        with pytest.raises(ImportError) as exc:
            c._connect()
        msg = str(exc.value)
        assert "dashdown-md[mssql]" in msg
        assert "pyodbc" in msg

    def test_connect_passes_string_and_kwargs(self, monkeypatch):
        captured = {}

        class FakePyodbc:
            @staticmethod
            def connect(conn_str, **kwargs):
                captured["conn_str"] = conn_str
                captured["kwargs"] = kwargs
                return object()

        monkeypatch.setattr(
            "dashdown.data.mssql_connector._import_driver",
            lambda module, extra: FakePyodbc,
        )
        c = MSSQLConnector(
            "db",
            {"host": "h", "user": "u", "password": "p", "connect_args": {"timeout": 30}},
        )
        c._connect()
        assert "SERVER=h" in captured["conn_str"]
        assert captured["kwargs"] == {"timeout": 30}

    def test_end_to_end_via_fake_conn(self, monkeypatch):
        from tests.test_dbapi_connectors import _FakeConn

        conn = _FakeConn(result=(["n"], [(1,)]))
        c = MSSQLConnector("db", {})
        monkeypatch.setattr(c, "_connect", lambda: conn)
        result = c.query("SELECT 1")
        assert isinstance(result, QueryResult)
        assert result.rows == [[1]]


class TestRegistration:
    def test_registered_type_name(self):
        assert get_connector_type("mssql") is MSSQLConnector

    def test_entry_point_exposes_connector(self):
        from importlib import metadata
        from dashdown.data.base import ENTRY_POINT_GROUP

        names = {ep.name for ep in metadata.entry_points(group=ENTRY_POINT_GROUP)}
        assert "mssql" in names
