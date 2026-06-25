"""`dashdown components` prints a registry-introspected catalog.

Covers the `build_catalog()` seam (component attr + connector config-key
introspection) and the CLI command's table/json output. The catalog is generated
from the registries, so these tests also *lock* that every registered component
and connector is covered and that the JSON shape stays stable (the contract a
future MCP wrapper / generated reference depends on).
"""
from __future__ import annotations

import json

from typer.testing import CliRunner

import dashdown.components  # noqa: F401 — populate the component registry
from dashdown.catalog import (
    build_catalog,
    build_component_catalog,
    build_connector_catalog,
)
from dashdown.cli import app
from dashdown.components.base import get_component, known_components
from dashdown.data.base import known_connector_types

runner = CliRunner()


def _builtin_names() -> set[str]:
    """Registered components shipped by the framework.

    The component registry is process-global, so other tests may register their
    own custom components into it; scope built-in assertions to ours.
    """
    return {
        name
        for name in known_components()
        if type(get_component(name)).__module__.startswith(
            "dashdown.components.builtin"
        )
    }


# --- build_catalog() structure / coverage -----------------------------------


def test_catalog_has_both_sections() -> None:
    cat = build_catalog()
    assert set(cat) == {"components", "connectors"}
    assert cat["components"] and cat["connectors"]


def test_every_component_is_covered() -> None:
    names = {row["name"] for row in build_component_catalog()}
    assert names == set(known_components())
    assert _builtin_names() <= names


def test_every_connector_type_is_covered() -> None:
    types = {row["type"] for row in build_connector_catalog()}
    # Every built-in connector type shows up (entry-point-derived).
    assert types == set(known_connector_types())
    assert {"csv", "postgres", "snowflake", "mssql", "duckdb"} <= types


def test_catalog_is_deterministic() -> None:
    assert build_catalog() == build_catalog()


def test_component_row_shape() -> None:
    for row in build_component_catalog():
        assert set(row) == {"name", "summary", "is_filter", "attrs"}
        assert isinstance(row["name"], str) and row["name"]
        assert isinstance(row["summary"], str)
        assert isinstance(row["is_filter"], bool)
        assert isinstance(row["attrs"], list)
        assert row["attrs"] == sorted(row["attrs"])  # stable ordering


def test_connector_row_shape() -> None:
    for row in build_connector_catalog():
        assert set(row) == {"type", "extra", "summary", "config_keys"}
        assert isinstance(row["type"], str) and row["type"]
        assert row["extra"] is None or isinstance(row["extra"], str)
        assert isinstance(row["config_keys"], list)
        assert row["config_keys"] == sorted(row["config_keys"])


# --- component attribute introspection ---------------------------------------


def _component(name: str) -> dict:
    return next(r for r in build_component_catalog() if r["name"] == name)


def test_chart_attrs_include_shared_chart_base() -> None:
    # Every chart routes through `_chart_html`, so its shared attrs are grafted on.
    bar = _component("BarChart")["attrs"]
    for expected in ("data", "x", "y", "series", "title", "format", "currency"):
        assert expected in bar, expected


def test_chart_extra_attrs_are_scoped_per_class() -> None:
    # `horizontal` is read only by BarChart, `stacked` by Line/Bar — scoping is
    # per-render-method, so LineChart must NOT inherit BarChart's `horizontal`.
    line = _component("LineChart")["attrs"]
    bar = _component("BarChart")["attrs"]
    assert "stacked" in line and "stacked" in bar
    assert "horizontal" in bar
    assert "horizontal" not in line


def test_heatmap_value_attr_introspected() -> None:
    assert "value" in _component("HeatmapChart")["attrs"]


def test_semantic_attrs_present_on_metric_capable_components() -> None:
    # resolve_semantic() contributes metric/by/grain to charts, Value, Table.
    for name in ("BarChart", "Value", "Table"):
        attrs = _component(name)["attrs"]
        assert {"metric", "by", "grain"} <= set(attrs), name


def test_counter_kpi_attrs_introspected() -> None:
    attrs = set(_component("Counter")["attrs"])
    # direct reads + the module-local `_ref_name` accessor + overridden
    # resolve_semantic keys (sparkline / sparkline-by).
    assert {"compare", "delta", "label", "column", "sparkline", "sparkline-by"} <= attrs


def test_value_attrs_introspected() -> None:
    attrs = set(_component("Value")["attrs"])
    assert {"column", "row", "prefix", "suffix"} <= attrs


def test_filter_components_flagged() -> None:
    filters = {r["name"] for r in build_component_catalog() if r["is_filter"]}
    assert {"Dropdown", "Search", "DateRange", "Toggle"} <= filters
    assert "BarChart" not in filters and "Table" not in filters


def test_dropdown_attrs_and_filter_flag() -> None:
    row = _component("Dropdown")
    assert row["is_filter"] is True
    assert {"name", "column", "options", "multi"} <= set(row["attrs"])


def test_table_attrs_introspected() -> None:
    attrs = set(_component("Table")["attrs"])
    assert {"sort", "headers", "export", "page-size", "title"} <= attrs


def test_every_builtin_has_a_summary() -> None:
    # All built-ins should describe themselves (we added docstrings to the charts).
    builtins = _builtin_names()
    missing = [
        r["name"]
        for r in build_component_catalog()
        if r["name"] in builtins and not r["summary"]
    ]
    assert not missing, f"built-in components missing a summary: {missing}"


# --- connector config-key introspection --------------------------------------


def _connector(type_name: str) -> dict:
    return next(r for r in build_connector_catalog() if r["type"] == type_name)


def test_postgres_config_keys_and_extra() -> None:
    row = _connector("postgres")
    assert {"host", "port", "database", "user", "password"} <= set(row["config_keys"])
    assert row["extra"] == "postgres"
    assert "PostgreSQL" in row["summary"]


def test_core_connector_has_no_extra() -> None:
    csv = _connector("csv")
    assert csv["extra"] is None
    assert "directory" in csv["config_keys"]


def test_snowflake_passthrough_tuple_resolved() -> None:
    # snowflake reads keys via `self.config[k] for k in self._PASSTHROUGH`.
    keys = set(_connector("snowflake")["config_keys"])
    assert {"account", "warehouse", "user", "password", "role"} <= keys


def test_mssql_config_alias_resolved() -> None:
    # mssql reads via a local alias `cfg = self.config`.
    keys = set(_connector("mssql")["config_keys"])
    assert {"host", "database", "authentication", "client_id"} <= keys


# --- CLI command --------------------------------------------------------------


def test_components_command_table() -> None:
    res = runner.invoke(app, ["components"])
    assert res.exit_code == 0, res.stdout
    assert "COMPONENTS" in res.stdout and "CONNECTORS" in res.stdout
    assert "BarChart" in res.stdout and "postgres" in res.stdout
    assert "component(s)" in res.stderr  # summary count to stderr


def test_components_command_connectors_only() -> None:
    res = runner.invoke(app, ["components", "--connectors"])
    assert res.exit_code == 0, res.stdout
    assert "CONNECTORS" in res.stdout
    assert "COMPONENTS" not in res.stdout
    assert "BarChart" not in res.stdout
    assert "postgres" in res.stdout


def test_components_command_json() -> None:
    res = runner.invoke(app, ["components", "-f", "json"])
    assert res.exit_code == 0, res.stdout
    payload = json.loads(res.stdout)
    assert set(payload) == {"components", "connectors"}
    bar = next(c for c in payload["components"] if c["name"] == "BarChart")
    assert "data" in bar["attrs"] and "x" in bar["attrs"]
    pg = next(c for c in payload["connectors"] if c["type"] == "postgres")
    assert "host" in pg["config_keys"] and pg["extra"] == "postgres"


def test_components_command_json_connectors_only() -> None:
    res = runner.invoke(app, ["components", "--connectors", "-f", "json"])
    assert res.exit_code == 0, res.stdout
    payload = json.loads(res.stdout)
    assert set(payload) == {"connectors"}


def test_components_command_bad_format() -> None:
    res = runner.invoke(app, ["components", "-f", "yaml"])
    assert res.exit_code != 0
    assert "format" in (res.stdout + res.stderr).lower()
