"""`dashdown query` runs SQL against a connector and prints the result."""
from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from dashdown.cli import app

runner = CliRunner()


def _make_project(tmp_path: Path) -> Path:
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "sales.csv").write_text(
        "region,amount\nNorth,100\nSouth,200\nNorth,50\n", encoding="utf-8"
    )
    (tmp_path / "sources.yaml").write_text(
        "main:\n  type: csv\n  directory: data\n", encoding="utf-8"
    )
    return tmp_path


def test_query_table_output(tmp_path: Path) -> None:
    proj = _make_project(tmp_path)
    res = runner.invoke(
        app,
        ["query", "SELECT region, amount FROM sales ORDER BY amount", "-p", str(proj)],
    )
    assert res.exit_code == 0, res.stdout
    assert "region" in res.stdout and "amount" in res.stdout
    assert "North" in res.stdout and "South" in res.stdout
    assert "3 rows" in res.stderr  # row count goes to stderr, not the data stream


def test_query_json_output(tmp_path: Path) -> None:
    proj = _make_project(tmp_path)
    res = runner.invoke(
        app,
        ["query", "SELECT sum(amount) AS total FROM sales", "-p", str(proj), "-f", "json"],
    )
    assert res.exit_code == 0, res.stdout
    payload = json.loads(res.stdout)
    assert payload == {"columns": ["total"], "rows": [[350]]}


def test_query_csv_output(tmp_path: Path) -> None:
    proj = _make_project(tmp_path)
    res = runner.invoke(
        app,
        ["query", "SELECT DISTINCT region FROM sales ORDER BY region", "-p", str(proj), "-f", "csv"],
    )
    assert res.exit_code == 0, res.stdout
    assert res.stdout.strip().splitlines() == ["region", "North", "South"]


def test_query_unknown_connector_lists_available(tmp_path: Path) -> None:
    proj = _make_project(tmp_path)
    res = runner.invoke(app, ["query", "SELECT 1", "-p", str(proj), "-c", "ghost"])
    assert res.exit_code != 0
    assert "not found" in res.stderr and "main" in res.stderr


def test_query_sql_error_exits_nonzero(tmp_path: Path) -> None:
    proj = _make_project(tmp_path)
    res = runner.invoke(app, ["query", "SELECT * FROM nope", "-p", str(proj)])
    assert res.exit_code == 1
    assert "Query failed" in res.stderr


def test_query_max_rows_truncates(tmp_path: Path) -> None:
    proj = _make_project(tmp_path)
    res = runner.invoke(
        app, ["query", "SELECT amount FROM sales", "-p", str(proj), "--max-rows", "1"]
    )
    assert res.exit_code == 0, res.stdout
    assert "3 rows (1 shown" in res.stderr


def test_query_tables_lists_tables(tmp_path: Path) -> None:
    proj = _make_project(tmp_path)
    res = runner.invoke(app, ["query", "--tables", "-p", str(proj)])
    assert res.exit_code == 0, res.stdout
    assert "sales" in res.stdout
    assert "table" in res.stdout and "schema" in res.stdout  # header


def test_query_schema_describes_columns_json(tmp_path: Path) -> None:
    proj = _make_project(tmp_path)
    res = runner.invoke(
        app, ["query", "--schema", "sales", "-p", str(proj), "-f", "json"]
    )
    assert res.exit_code == 0, res.stdout
    payload = json.loads(res.stdout)
    assert payload["columns"] == ["column", "type", "nullable"]
    names = [r[0] for r in payload["rows"]]
    assert names == ["region", "amount"]


def test_query_requires_exactly_one_mode(tmp_path: Path) -> None:
    proj = _make_project(tmp_path)
    # Neither a SQL arg nor a flag.
    res = runner.invoke(app, ["query", "-p", str(proj)])
    assert res.exit_code != 0
    assert "exactly one" in res.stderr
    # Both a SQL arg and --tables.
    res = runner.invoke(app, ["query", "SELECT 1", "--tables", "-p", str(proj)])
    assert res.exit_code != 0
    assert "exactly one" in res.stderr
