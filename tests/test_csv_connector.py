"""Tests for dashdown.data.csv_connector.

The CSV connector wraps an in-memory DuckDB: each discovered/declared CSV becomes
a view named after the file stem (or the explicit key), queryable via SQL. These
tests cover view discovery, explicit file mapping, unusual column names, the
single-quote escaping in file paths, and basic query semantics.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from dashdown.data.csv_connector import CSVConnector
from dashdown.data.base import QueryResult, get_connector_type


def _write_csv(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")


class TestRegistration:
    """The connector registers itself under the 'csv' type name."""

    def test_registered_as_csv(self):
        assert get_connector_type("csv") is CSVConnector


class TestDirectoryDiscovery:
    """Auto-discovery of *.csv files in a directory."""

    def test_each_csv_becomes_a_view_named_by_stem(self, tmp_path):
        data = tmp_path / "data"
        data.mkdir()
        _write_csv(data / "sales.csv", "id,amount\n1,10\n2,20\n")
        _write_csv(data / "regions.csv", "code,name\nNW,Northwest\n")

        conn = CSVConnector("csv", {"directory": "data", "_project_root": tmp_path})

        sales = conn.query('SELECT * FROM "sales" ORDER BY id')
        assert sales.columns == ["id", "amount"]
        assert sales.rows == [[1, 10], [2, 20]]

        regions = conn.query('SELECT name FROM "regions"')
        assert regions.rows == [["Northwest"]]
        conn.close()

    def test_missing_directory_is_silently_skipped(self, tmp_path):
        # A directory that does not exist must not raise at construction time.
        conn = CSVConnector(
            "csv", {"directory": "does_not_exist", "_project_root": tmp_path}
        )
        # No views registered -> querying a non-existent view raises a DuckDB error.
        with pytest.raises(Exception):
            conn.query('SELECT * FROM "anything"')
        conn.close()

    def test_no_directory_config_is_valid(self, tmp_path):
        # Neither directory nor files -> empty connector, no error.
        conn = CSVConnector("csv", {"_project_root": tmp_path})
        conn.close()


class TestExplicitFileMapping:
    """The `files` mapping names views explicitly and overrides discovery."""

    def test_explicit_files_register_under_given_names(self, tmp_path):
        _write_csv(tmp_path / "raw.csv", "x\n1\n2\n3\n")
        conn = CSVConnector(
            "csv",
            {"files": {"my_view": "raw.csv"}, "_project_root": tmp_path},
        )
        result = conn.query('SELECT count(*) AS n FROM "my_view"')
        assert result.rows == [[3]]
        conn.close()

    def test_explicit_mapping_overrides_directory_view(self, tmp_path):
        # A discovered view and an explicit view with the same name -> explicit wins
        # (CREATE OR REPLACE runs after discovery).
        data = tmp_path / "data"
        data.mkdir()
        _write_csv(data / "sales.csv", "id\n1\n")
        _write_csv(tmp_path / "override.csv", "id\n9\n9\n")
        conn = CSVConnector(
            "csv",
            {
                "directory": "data",
                "files": {"sales": "override.csv"},
                "_project_root": tmp_path,
            },
        )
        result = conn.query('SELECT count(*) AS n FROM "sales"')
        assert result.rows == [[2]]
        conn.close()


class TestUnusualColumnNames:
    """Column names with spaces, punctuation, and unicode survive round-trip."""

    def test_spaces_and_punctuation_in_headers(self, tmp_path):
        _write_csv(
            tmp_path / "weird.csv",
            'Order ID,Total ($),Région\n1,9.99,Nord\n',
        )
        conn = CSVConnector(
            "csv", {"files": {"weird": "weird.csv"}, "_project_root": tmp_path}
        )
        result = conn.query('SELECT * FROM "weird"')
        assert result.columns == ["Order ID", "Total ($)", "Région"]
        assert result.rows == [[1, 9.99, "Nord"]]
        conn.close()

    def test_quoted_identifier_select(self, tmp_path):
        _write_csv(tmp_path / "g.csv", 'group,select\na,1\nb,2\n')
        conn = CSVConnector(
            "csv", {"files": {"g": "g.csv"}, "_project_root": tmp_path}
        )
        # "group" and "select" are SQL keywords; they must be addressable as
        # quoted identifiers.
        result = conn.query('SELECT "group", "select" FROM "g" ORDER BY "select"')
        assert result.columns == ["group", "select"]
        assert result.rows == [["a", 1], ["b", 2]]
        conn.close()


class TestPathEscaping:
    """File paths containing single quotes must not break the CREATE VIEW SQL."""

    def test_single_quote_in_path(self, tmp_path):
        odd_dir = tmp_path / "o'brien"
        odd_dir.mkdir()
        _write_csv(odd_dir / "t.csv", "v\n42\n")
        conn = CSVConnector(
            "csv",
            {"files": {"t": "o'brien/t.csv"}, "_project_root": tmp_path},
        )
        result = conn.query('SELECT v FROM "t"')
        assert result.rows == [[42]]
        conn.close()


class TestQuerySemantics:
    """Return shape and edge cases of query()."""

    def test_returns_queryresult(self, tmp_path):
        _write_csv(tmp_path / "n.csv", "n\n1\n")
        conn = CSVConnector(
            "csv", {"files": {"n": "n.csv"}, "_project_root": tmp_path}
        )
        result = conn.query('SELECT * FROM "n"')
        assert isinstance(result, QueryResult)
        conn.close()

    def test_empty_result_set_has_columns(self, tmp_path):
        _write_csv(tmp_path / "n.csv", "n\n1\n2\n")
        conn = CSVConnector(
            "csv", {"files": {"n": "n.csv"}, "_project_root": tmp_path}
        )
        result = conn.query('SELECT n FROM "n" WHERE n > 100')
        assert result.columns == ["n"]
        assert result.rows == []
        conn.close()

    def test_to_records_round_trip(self, tmp_path):
        _write_csv(tmp_path / "p.csv", "k,v\na,1\nb,2\n")
        conn = CSVConnector(
            "csv", {"files": {"p": "p.csv"}, "_project_root": tmp_path}
        )
        result = conn.query('SELECT * FROM "p" ORDER BY k')
        assert result.to_records() == [{"k": "a", "v": 1}, {"k": "b", "v": 2}]
        conn.close()

    def test_close_is_idempotent(self, tmp_path):
        conn = CSVConnector("csv", {"_project_root": tmp_path})
        conn.close()
        # Second close must not raise (swallowed in implementation).
        conn.close()


class TestDefaultProjectRoot:
    """Without an explicit _project_root, paths resolve against the CWD."""

    def test_default_root_is_cwd(self, tmp_path, monkeypatch):
        data = tmp_path / "data"
        data.mkdir()
        _write_csv(data / "sales.csv", "id\n1\n2\n")
        monkeypatch.chdir(tmp_path)
        # No _project_root -> defaults to Path(".") == tmp_path after chdir.
        conn = CSVConnector("csv", {"directory": "data"})
        result = conn.query('SELECT count(*) AS n FROM "sales"')
        assert result.rows == [[2]]
        conn.close()


class TestReconnectOnFatal:
    """A query that invalidates the DuckDB connection (e.g. an httpfs read
    corrupting it) must not permanently break the connector: the next query
    reconnects, rebuilds the CSV views, and recovers."""

    def test_fatal_error_reconnects_and_rebuilds_views(self, tmp_path):
        import duckdb

        _write_csv(tmp_path / "data.csv", "id\n1\n2\n3\n")
        conn = CSVConnector("csv", {"files": {"t": "data.csv"}, "_project_root": tmp_path})
        assert conn.query('SELECT count(*) AS n FROM "t"').rows == [[3]]

        # Make the next _execute raise a real FatalException once; the retry on a
        # freshly-rebuilt connection must succeed AND still see the "t" view.
        real_execute = conn._execute
        state = {"failed": False}

        def flaky(con, sql):
            if not state["failed"]:
                state["failed"] = True
                raise duckdb.FatalException(
                    "database has been invalidated because of a previous fatal error"
                )
            return real_execute(con, sql)

        conn._execute = flaky
        result = conn.query('SELECT count(*) AS n FROM "t"')
        assert result.rows == [[3]]  # reconnected + view rebuilt
        assert state["failed"] is True
        conn.close()

    def test_transient_error_is_not_reconnected(self, tmp_path):
        # A non-fatal error (e.g. a 429 from an API read) is raised as-is, not
        # swallowed by a reconnect — the connection is still fine afterward.
        import duckdb

        _write_csv(tmp_path / "data.csv", "id\n1\n")
        conn = CSVConnector("csv", {"files": {"t": "data.csv"}, "_project_root": tmp_path})
        with pytest.raises(duckdb.Error):
            conn.query("SELECT * FROM read_json_auto('http://127.0.0.1:9/nope.json')")
        # Connection survived a transient failure.
        assert conn.query('SELECT count(*) AS n FROM "t"').rows == [[1]]
        conn.close()