"""`dashdown schema` — the one-shot project context pack.

Covers the pure `build_schema_pack()` seam (connector introspection, query
library params, per-page query map, bounded table introspection) and the CLI
wrapper's two output formats.
"""
from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from dashdown.cli import app
from dashdown.project import load_project
from dashdown.schema_pack import build_schema_pack, schema_pack_markdown

runner = CliRunner()


def _make_project(tmp_path: Path) -> Path:
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "sales.csv").write_text(
        "region,amount\nNorth,100\nSouth,200\n", encoding="utf-8"
    )
    (tmp_path / "sources.yaml").write_text(
        "main:\n  type: csv\n  directory: data\n", encoding="utf-8"
    )
    (tmp_path / "queries").mkdir()
    (tmp_path / "queries" / "by_region.sql").write_text(
        "SELECT region, SUM(amount) AS total FROM sales\n"
        "WHERE '${region}' = '' OR region = '${region}' GROUP BY region\n",
        encoding="utf-8",
    )
    (tmp_path / "pages").mkdir()
    (tmp_path / "pages" / "index.md").write_text(
        "# Home\n\n<Table data={by_region} />\n", encoding="utf-8"
    )
    return tmp_path


def test_pack_contains_tables_and_columns(tmp_path: Path) -> None:
    proj = load_project(_make_project(tmp_path))
    try:
        pack = build_schema_pack(proj)
    finally:
        proj.close()
    tables = pack["connectors"]["main"]["tables"]
    assert "sales" in tables
    cols = {c["name"] for c in tables["sales"]}
    assert {"region", "amount"} <= cols


def test_pack_queries_carry_params(tmp_path: Path) -> None:
    proj = load_project(_make_project(tmp_path))
    try:
        pack = build_schema_pack(proj)
    finally:
        proj.close()
    assert pack["queries"]["by_region"]["params"] == ["region"]
    assert pack["queries"]["by_region"]["connector"] == "main"


def test_pack_pages_map_queries(tmp_path: Path) -> None:
    proj = load_project(_make_project(tmp_path))
    try:
        pack = build_schema_pack(proj)
    finally:
        proj.close()
    assert pack["pages"]["/"]["queries"] == ["by_region"]


def test_pack_no_pages_and_no_columns(tmp_path: Path) -> None:
    proj = load_project(_make_project(tmp_path))
    try:
        pack = build_schema_pack(proj, include_pages=False, include_columns=False)
    finally:
        proj.close()
    assert pack["pages"] is None
    assert pack["connectors"]["main"]["tables"]["sales"] is None


def test_pack_max_tables_truncates(tmp_path: Path) -> None:
    root = _make_project(tmp_path)
    (root / "data" / "orders.csv").write_text("id\n1\n", encoding="utf-8")
    proj = load_project(root)
    try:
        pack = build_schema_pack(proj, max_tables=1)
    finally:
        proj.close()
    entry = pack["connectors"]["main"]
    assert entry["tables_truncated"] == 1
    assert len(entry["tables"]) == 1


def test_markdown_rendering(tmp_path: Path) -> None:
    proj = load_project(_make_project(tmp_path))
    try:
        md = schema_pack_markdown(build_schema_pack(proj))
    finally:
        proj.close()
    assert "## Connectors" in md
    assert "**sales**" in md
    assert "`by_region`" in md and "params: region" in md
    assert "## Pages" in md


def test_cli_schema_md(tmp_path: Path) -> None:
    res = runner.invoke(app, ["schema", "-p", str(_make_project(tmp_path))])
    assert res.exit_code == 0, res.stdout
    assert "project schema" in res.stdout
    assert "sales" in res.stdout


def test_cli_schema_json(tmp_path: Path) -> None:
    import json

    res = runner.invoke(
        app, ["schema", "-p", str(_make_project(tmp_path)), "-f", "json"]
    )
    assert res.exit_code == 0, res.stdout
    pack = json.loads(res.stdout)
    assert "connectors" in pack and "queries" in pack


def test_cli_schema_bad_format(tmp_path: Path) -> None:
    res = runner.invoke(
        app, ["schema", "-p", str(_make_project(tmp_path)), "-f", "xml"]
    )
    assert res.exit_code != 0
