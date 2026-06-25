"""Tests for the connector plugin system (entry-point discovery).

Connectors register through the ``dashdown.connectors`` entry-point group. The
built-in csv/duckdb/dax connectors use the exact same mechanism as third-party
PyPI plugins — they are declared in pyproject.toml and loaded lazily by
``get_connector_type`` only when a project actually asks for that type.

These tests cover: built-ins being discoverable via the entry-point path,
lazy loading + registry caching, the friendly error when a plugin's optional
dependencies are missing, the type check on a malformed entry point, and the
``Unknown connector type`` error for absent types.
"""
from __future__ import annotations

from importlib import metadata
from typing import Any

import pytest

from dashdown.data import base
from dashdown.data.base import (
    Connector,
    QueryResult,
    ENTRY_POINT_GROUP,
    get_connector_type,
    known_connector_types,
)


@pytest.fixture(autouse=True)
def _reset_registry():
    """Snapshot and restore the module-global registry/discovery caches.

    The lazy entry-point cache and the type registry are process-global; each
    test that mutates them must not leak into the others.
    """
    saved_types = dict(base._CONNECTOR_TYPES)
    saved_eps = base._ENTRY_POINTS
    yield
    base._CONNECTOR_TYPES = saved_types
    base._ENTRY_POINTS = saved_eps


class TestBuiltinsViaEntryPoints:
    """The built-in connectors are discovered through the entry-point group."""

    def test_known_types_include_builtins(self):
        known = known_connector_types()
        assert {"csv", "duckdb", "dax"} <= set(known)

    def test_csv_loads_from_entry_point(self):
        # Start from a clean registry so the only path to 'csv' is discovery.
        base._CONNECTOR_TYPES = {}
        base._ENTRY_POINTS = None
        from dashdown.data.csv_connector import CSVConnector

        assert get_connector_type("csv") is CSVConnector

    def test_loaded_type_is_cached(self):
        base._CONNECTOR_TYPES = {}
        base._ENTRY_POINTS = None
        cls = get_connector_type("duckdb")
        # Second lookup must hit the cache, not rediscover.
        assert "duckdb" in base._CONNECTOR_TYPES
        assert get_connector_type("duckdb") is cls


class TestUnknownType:
    def test_unknown_raises_keyerror_listing_known(self):
        with pytest.raises(KeyError) as exc:
            get_connector_type("definitely_not_a_connector")
        msg = str(exc.value)
        assert "Unknown connector type" in msg
        assert "csv" in msg  # known types are listed to help the user


class _FakeEntryPoint:
    """Minimal stand-in for importlib.metadata.EntryPoint."""

    def __init__(self, name: str, value: str, loader):
        self.name = name
        self.value = value
        self._loader = loader

    def load(self):
        return self._loader()


class TestPluginLoading:
    """Third-party plugin behavior, simulated with fake entry points."""

    def test_custom_plugin_registers_and_instantiates(self):
        class MyConnector(Connector):
            def query(self, sql: str) -> QueryResult:
                return QueryResult(columns=["x"], rows=[[1]])

        base._ENTRY_POINTS = {
            "myplugin": _FakeEntryPoint(
                "myplugin", "pkg.mod:MyConnector", lambda: MyConnector
            )
        }
        base._CONNECTOR_TYPES = {}

        assert get_connector_type("myplugin") is MyConnector
        assert "myplugin" in known_connector_types()
        inst = get_connector_type("myplugin")("src", {})
        assert inst.query("anything").rows == [[1]]

    def test_missing_optional_deps_gives_install_hint(self):
        def _boom():
            raise ImportError("No module named 'pyarrow'")

        base._ENTRY_POINTS = {
            "dax": _FakeEntryPoint("dax", "dashdown.data.dax_connector:DAXConnector", _boom)
        }
        base._CONNECTOR_TYPES = {}

        with pytest.raises(ImportError) as exc:
            get_connector_type("dax")
        msg = str(exc.value)
        assert "dashdown-md[dax]" in msg
        assert "pyarrow" in msg  # original error is chained in

    def test_non_connector_entry_point_rejected(self):
        class NotAConnector:
            pass

        base._ENTRY_POINTS = {
            "bogus": _FakeEntryPoint("bogus", "pkg:NotAConnector", lambda: NotAConnector)
        }
        base._CONNECTOR_TYPES = {}

        with pytest.raises(TypeError) as exc:
            get_connector_type("bogus")
        assert "Connector subclass" in str(exc.value)


class TestRealEntryPointMetadata:
    """The pyproject-declared entry points are actually installed."""

    def test_group_exposes_builtins(self):
        names = {ep.name for ep in metadata.entry_points(group=ENTRY_POINT_GROUP)}
        assert {"csv", "duckdb", "dax"} <= names


class TestInProjectConnector:
    """A connector defined in a project's components/ dir must be usable.

    Regression guard: load_project used to call load_connectors *before*
    importing user modules, so an in-project connector's `@register_connector`
    hadn't run yet and its type was 'unknown' at load time. User modules are now
    imported first.
    """

    def test_components_connector_is_registered_and_usable(self, tmp_path):
        from dashdown.project import load_project

        (tmp_path / "pages").mkdir()
        (tmp_path / "components").mkdir()
        (tmp_path / "dashdown.yaml").write_text("title: T\n", encoding="utf-8")
        (tmp_path / "pages" / "index.md").write_text("# Hi\n", encoding="utf-8")
        (tmp_path / "sources.yaml").write_text(
            "custom:\n  type: in_project_demo\n", encoding="utf-8"
        )
        (tmp_path / "components" / "conn.py").write_text(
            "from dashdown import Connector, QueryResult, register_connector\n"
            "@register_connector('in_project_demo')\n"
            "class C(Connector):\n"
            "    def query(self, sql):\n"
            "        return QueryResult(columns=['n'], rows=[[42]])\n",
            encoding="utf-8",
        )

        project = load_project(tmp_path)

        assert "custom" in project.connectors
        result = project.connectors["custom"].query("SELECT 1")
        assert result.columns == ["n"]
        assert result.rows == [[42]]
