"""`dashdown --version` prints the installed distribution version and exits."""
from __future__ import annotations

from importlib.metadata import version

from typer.testing import CliRunner

from dashdown.cli import app

runner = CliRunner()


def test_version_flag_prints_distribution_version() -> None:
    res = runner.invoke(app, ["--version"])
    assert res.exit_code == 0, res.stdout
    assert res.stdout.strip() == f"dashdown {version('dashdown-md')}"


def test_version_flag_is_eager_and_short_circuits_commands() -> None:
    # --version is eager: it prints and exits before any subcommand (or the
    # telemetry first-run notice in the callback body) runs.
    res = runner.invoke(app, ["--version", "serve"])
    assert res.exit_code == 0, res.stdout
    assert res.stdout.strip() == f"dashdown {version('dashdown-md')}"
