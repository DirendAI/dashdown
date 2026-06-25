"""`dashdown metric` queries the semantic layer (metrics/groupings) by ref.

These exercise the CLI glue around the existing semantic resolution path; the
compilation/pushdown itself is covered by ``test_semantic.py``. Needs the
``dashdown-md[semantic]`` extra (ibis), like the rest of the semantic suite.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from dashdown.cli import app

pytest.importorskip("ibis")

runner = CliRunner()


def _make_project(tmp_path: Path) -> Path:
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "orders.csv").write_text(
        "region,amount\nNorth,100\nSouth,200\nNorth,50\n", encoding="utf-8"
    )
    (tmp_path / "sources.yaml").write_text(
        "main:\n  type: csv\n  directory: data\n", encoding="utf-8"
    )
    (tmp_path / "semantic").mkdir()
    (tmp_path / "semantic" / "sales.yml").write_text(
        "sales:\n"
        "  connector: main\n"
        "  table: orders\n"
        "  dimensions:\n"
        "    region: _.region\n"
        "  measures:\n"
        "    revenue:\n"
        "      expr: _.amount.sum()\n"
        "    orders: _.count()\n",
        encoding="utf-8",
    )
    return tmp_path


def test_metric_list_shows_models(tmp_path: Path) -> None:
    proj = _make_project(tmp_path)
    res = runner.invoke(app, ["metric", "--list", "-p", str(proj)])
    assert res.exit_code == 0, res.stdout
    assert "sales" in res.stdout
    assert "revenue" in res.stdout and "orders" in res.stdout  # metrics
    assert "region" in res.stdout  # dimension
    assert "1 model(s)" in res.stderr


def test_metric_revenue_by_region(tmp_path: Path) -> None:
    proj = _make_project(tmp_path)
    res = runner.invoke(
        app, ["metric", "sales.revenue", "--by", "sales.region", "-p", str(proj)]
    )
    assert res.exit_code == 0, res.stdout
    assert "North" in res.stdout and "South" in res.stdout
    assert "150" in res.stdout and "200" in res.stdout  # summed revenue


def test_metric_json_output(tmp_path: Path) -> None:
    proj = _make_project(tmp_path)
    res = runner.invoke(
        app, ["metric", "sales.revenue", "-p", str(proj), "-f", "json"]
    )
    assert res.exit_code == 0, res.stdout
    payload = json.loads(res.stdout)
    assert "revenue" in payload["columns"][0]
    assert payload["rows"][0][0] == 350  # total revenue, no grouping


def test_metric_filter_param(tmp_path: Path) -> None:
    proj = _make_project(tmp_path)
    res = runner.invoke(
        app,
        ["metric", "sales.revenue", "-p", str(proj), "--param", "region=North", "-f", "json"],
    )
    assert res.exit_code == 0, res.stdout
    payload = json.loads(res.stdout)
    assert payload["rows"][0][0] == 150  # only North rows


def test_metric_unknown_ref_exits_nonzero(tmp_path: Path) -> None:
    proj = _make_project(tmp_path)
    res = runner.invoke(app, ["metric", "sales.nope", "-p", str(proj)])
    assert res.exit_code == 1
    assert "Semantic resolution failed" in res.stderr


def test_metric_requires_ref_or_list(tmp_path: Path) -> None:
    proj = _make_project(tmp_path)
    res = runner.invoke(app, ["metric", "-p", str(proj)])
    assert res.exit_code != 0
    assert "metric reference" in res.stderr or "metric reference" in res.stdout


def test_metric_bad_param_format(tmp_path: Path) -> None:
    proj = _make_project(tmp_path)
    res = runner.invoke(
        app, ["metric", "sales.revenue", "-p", str(proj), "--param", "noequals"]
    )
    assert res.exit_code != 0


def test_metric_no_models_errors(tmp_path: Path) -> None:
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "orders.csv").write_text("a\n1\n", encoding="utf-8")
    (tmp_path / "sources.yaml").write_text(
        "main:\n  type: csv\n  directory: data\n", encoding="utf-8"
    )
    res = runner.invoke(app, ["metric", "--list", "-p", str(tmp_path)])
    assert res.exit_code == 1
    assert "No semantic models" in res.stderr
