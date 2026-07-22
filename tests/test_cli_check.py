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


# --- check: ${param} coverage lint -------------------------------------------


_PARAM_QUERY = (
    "```sql sales\n"
    "SELECT region, SUM(amount) AS total FROM sales\n"
    "WHERE '${region}' = '' OR region = '${region}'\n"
    "GROUP BY region\n"
    "```\n\n"
    "<Table data={sales} />\n"
)


def test_check_warns_on_unsupplied_param(tmp_path: Path) -> None:
    proj = _make_project(tmp_path)
    (proj / "pages" / "index.md").write_text(
        "# Sales\n\n" + _PARAM_QUERY, encoding="utf-8"
    )
    res = runner.invoke(app, ["check", "-p", str(proj)])
    # Warn-only: the page may be driven by URL params — don't fail the build.
    assert res.exit_code == 0, res.stderr
    assert "${region}" in res.stderr
    assert "no supplier" in res.stderr
    assert "1 warning(s)" in res.stderr


def test_check_strict_fails_on_unsupplied_param(tmp_path: Path) -> None:
    proj = _make_project(tmp_path)
    (proj / "pages" / "index.md").write_text(
        "# Sales\n\n" + _PARAM_QUERY, encoding="utf-8"
    )
    res = runner.invoke(app, ["check", "-p", str(proj), "--strict"])
    assert res.exit_code == 1
    assert "no supplier" in res.stderr


def test_check_filter_control_supplies_param(tmp_path: Path) -> None:
    proj = _make_project(tmp_path)
    (proj / "pages" / "index.md").write_text(
        "# Sales\n\n"
        '<Dropdown name="region" data={sales} column="region" />\n\n' + _PARAM_QUERY,
        encoding="utf-8",
    )
    res = runner.invoke(app, ["check", "-p", str(proj)])
    assert res.exit_code == 0, res.stderr
    assert "warning" not in res.stderr


def test_check_daterange_supplies_derived_params(tmp_path: Path) -> None:
    proj = _make_project(tmp_path)
    (proj / "pages" / "index.md").write_text(
        "# Sales\n\n"
        '<DateRange name="period" />\n\n'
        "```sql sales\n"
        "SELECT * FROM sales WHERE region >= '${period_start}' AND region <= '${period_end}'\n"
        "```\n\n"
        "<Table data={sales} />\n",
        encoding="utf-8",
    )
    res = runner.invoke(app, ["check", "-p", str(proj)])
    assert res.exit_code == 0, res.stderr
    assert "warning" not in res.stderr


def test_check_route_segment_supplies_param(tmp_path: Path) -> None:
    proj = _make_project(tmp_path)
    (proj / "pages" / "teams").mkdir()
    (proj / "pages" / "teams" / "[team].md").write_text(
        "# Team\n\n"
        "```sql team_detail\n"
        "SELECT * FROM sales WHERE region = '${team}'\n"
        "```\n\n"
        "<Table data={team_detail} />\n",
        encoding="utf-8",
    )
    res = runner.invoke(app, ["check", "-p", str(proj)])
    assert res.exit_code == 0, res.stderr
    assert "warning" not in res.stderr


def test_check_frontmatter_params_escape_hatch(tmp_path: Path) -> None:
    proj = _make_project(tmp_path)
    (proj / "pages" / "index.md").write_text(
        "---\nparams: [region]\n---\n\n# Sales\n\n" + _PARAM_QUERY,
        encoding="utf-8",
    )
    res = runner.invoke(app, ["check", "-p", str(proj)])
    assert res.exit_code == 0, res.stderr
    assert "warning" not in res.stderr


def test_check_global_date_supplies_params(tmp_path: Path) -> None:
    proj = _make_project(tmp_path)
    (proj / "dashdown.yaml").write_text(
        "title: X\nglobal_filters:\n  date:\n    enabled: true\n", encoding="utf-8"
    )
    (proj / "pages" / "index.md").write_text(
        "# Sales\n\n"
        "```sql sales\n"
        "SELECT * FROM sales WHERE region >= '${date_start}' AND region <= '${date_end}'\n"
        "```\n\n"
        "<Table data={sales} />\n",
        encoding="utf-8",
    )
    res = runner.invoke(app, ["check", "-p", str(proj)])
    assert res.exit_code == 0, res.stderr
    assert "warning" not in res.stderr


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
