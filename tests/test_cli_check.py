"""`dashdown check` validates a project without serving or running queries;
`dashdown connectors` lists (and optionally probes) the configured connectors."""
from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from dashdown.cli import app

runner = CliRunner()


def _make_project(tmp_path: Path) -> Path:
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "sales.csv").write_text(
        "region,amount\nNorth,100\nSouth,200\n", encoding="utf-8"
    )
    (tmp_path / "sources.yaml").write_text(
        "main:\n  type: csv\n  directory: data\n", encoding="utf-8"
    )
    (tmp_path / "pages").mkdir()
    (tmp_path / "pages" / "index.md").write_text("# Home\n\nWelcome.\n", encoding="utf-8")
    return tmp_path


# --- check -------------------------------------------------------------------


def test_check_valid_project(tmp_path: Path) -> None:
    proj = _make_project(tmp_path)
    res = runner.invoke(app, ["check", "-p", str(proj)])
    assert res.exit_code == 0, res.stdout
    assert "project is valid" in res.stdout
    assert "1/1 page(s) OK" in res.stderr


def test_check_flags_unknown_component(tmp_path: Path) -> None:
    proj = _make_project(tmp_path)
    (proj / "pages" / "bad.md").write_text(
        "# Bad\n\n<NoSuchComponent foo=bar />\n", encoding="utf-8"
    )
    res = runner.invoke(app, ["check", "-p", str(proj)])
    assert res.exit_code == 1
    assert "/bad" in res.stderr
    assert "Unknown component" in res.stderr
    assert "1/2 page(s) OK" in res.stderr


def test_check_bad_sources_fails_to_load(tmp_path: Path) -> None:
    (tmp_path / "pages").mkdir()
    (tmp_path / "pages" / "index.md").write_text("# Home\n", encoding="utf-8")
    # An entry with no `type` key is rejected at load time.
    (tmp_path / "sources.yaml").write_text("main:\n  directory: data\n", encoding="utf-8")
    res = runner.invoke(app, ["check", "-p", str(tmp_path)])
    assert res.exit_code == 1
    assert "failed to load" in res.stderr


def test_check_no_pages_is_valid(tmp_path: Path) -> None:
    (tmp_path / "sources.yaml").write_text(
        "main:\n  type: csv\n  directory: data\n", encoding="utf-8"
    )
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "x.csv").write_text("a\n1\n", encoding="utf-8")
    res = runner.invoke(app, ["check", "-p", str(tmp_path)])
    assert res.exit_code == 0, res.stdout
    assert "0/0 page(s) OK" in res.stderr


# --- connectors --------------------------------------------------------------


def test_connectors_lists_with_types(tmp_path: Path) -> None:
    proj = _make_project(tmp_path)
    res = runner.invoke(app, ["connectors", "-p", str(proj)])
    assert res.exit_code == 0, res.stdout
    assert "main" in res.stdout and "csv" in res.stdout
    assert "1 connector(s)" in res.stderr


def test_connectors_test_probes_reachable(tmp_path: Path) -> None:
    proj = _make_project(tmp_path)
    res = runner.invoke(app, ["connectors", "--test", "-p", str(proj)])
    assert res.exit_code == 0, res.stdout
    assert "reachable" in res.stdout


def test_connectors_none_configured(tmp_path: Path) -> None:
    (tmp_path / "sources.yaml").write_text("{}\n", encoding="utf-8")
    res = runner.invoke(app, ["connectors", "-p", str(tmp_path)])
    assert res.exit_code == 1
    assert "No connectors" in res.stderr
