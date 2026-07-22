"""`dashdown inspect` — machine-readable ground truth for one page.

Covers provenance (page/library), component-to-query bindings, unresolved
references, `--data` execution (row counts, `--param` substitution, dynamic
route params), and both output formats.
"""
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
    (tmp_path / "queries").mkdir()
    (tmp_path / "queries" / "lib_totals.sql").write_text(
        "SELECT SUM(amount) AS total FROM sales\n", encoding="utf-8"
    )
    (tmp_path / "pages").mkdir()
    (tmp_path / "pages" / "index.md").write_text(
        "# Sales\n\n"
        "```sql by_region\n"
        "SELECT region, SUM(amount) AS total FROM sales\n"
        "WHERE '${region}' = '' OR region = '${region}'\n"
        "GROUP BY region ORDER BY region\n"
        "```\n\n"
        '<Dropdown name="region" data={by_region} column="region" />\n\n'
        "<Table data={by_region} />\n\n"
        "<Value data={lib_totals} />\n",
        encoding="utf-8",
    )
    return tmp_path


def _inspect_json(proj: Path, *args: str) -> dict:
    res = runner.invoke(app, ["inspect", *args, "-p", str(proj), "-f", "json"])
    assert res.exit_code == 0, res.output
    return json.loads(res.stdout)


def test_inspect_provenance_and_bindings(tmp_path: Path) -> None:
    report = _inspect_json(_make_project(tmp_path), "/")
    q = report["queries"]
    assert q["by_region"]["source"] == "page"
    assert q["by_region"]["connector"] == "main"
    assert q["by_region"]["params"] == ["region"]
    assert sorted(q["by_region"]["components"]) == ["Dropdown", "Table"]
    assert q["lib_totals"]["source"] == "library"
    assert q["lib_totals"]["components"] == ["Value"]
    assert report["filter_params"] == ["region"]
    assert report["unresolved_refs"] == []
    assert report["errors"] == []


def test_inspect_data_runs_queries(tmp_path: Path) -> None:
    report = _inspect_json(_make_project(tmp_path), "/", "--data")
    d = report["queries"]["by_region"]["data"]
    assert d["rows"] == 2  # North + South
    assert d["columns"] == ["region", "total"]
    assert report["queries"]["lib_totals"]["data"]["rows"] == 1


def test_inspect_data_with_param(tmp_path: Path) -> None:
    report = _inspect_json(
        _make_project(tmp_path), "/", "--data", "--param", "region=North"
    )
    assert report["queries"]["by_region"]["data"]["rows"] == 1


def test_inspect_dynamic_route_params(tmp_path: Path) -> None:
    proj = _make_project(tmp_path)
    (proj / "pages" / "regions").mkdir()
    (proj / "pages" / "regions" / "[region].md").write_text(
        "# Region\n\n"
        "```sql region_rows\n"
        "SELECT * FROM sales WHERE region = '${region}'\n"
        "```\n\n"
        "<Table data={region_rows} />\n",
        encoding="utf-8",
    )
    report = _inspect_json(proj, "/regions/South", "--data")
    assert report["route_params"] == {"region": "South"}
    assert report["queries"]["region_rows"]["data"]["rows"] == 1


def test_inspect_unresolved_ref(tmp_path: Path) -> None:
    proj = _make_project(tmp_path)
    (proj / "pages" / "broken.md").write_text(
        "# Broken\n\n<Table data={no_such_query} />\n", encoding="utf-8"
    )
    report = _inspect_json(proj, "/broken")
    assert report["unresolved_refs"] == ["no_such_query"]


def test_inspect_unknown_page_fails(tmp_path: Path) -> None:
    res = runner.invoke(
        app, ["inspect", "/nope", "-p", str(_make_project(tmp_path))]
    )
    assert res.exit_code != 0
    assert "no page" in res.output


def test_inspect_table_output(tmp_path: Path) -> None:
    res = runner.invoke(app, ["inspect", "/", "-p", str(_make_project(tmp_path))])
    assert res.exit_code == 0, res.output
    assert "by_region" in res.stdout
    assert "read by: Dropdown, Table" in res.stdout
    assert "(library, connector: main)" in res.stdout
