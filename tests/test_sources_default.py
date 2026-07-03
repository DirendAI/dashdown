"""Tests for default-source resolution (``default_connector_name``).

A query with no explicit ``connector=`` runs on the project's **default
source**: the one flagged ``default: true`` in sources.yaml, else the sole
configured source, else the source conventionally named ``main``. The flag
rides the ``Connector.is_default`` attribute (never the connector's config);
specs parse with an empty connector and are resolved where a connectors dict
is in hand (``render_page`` / ``load_project``).
"""
from __future__ import annotations

from pathlib import Path

import pytest

from dashdown.data import base
from dashdown.data.base import Connector, QueryResult, register_connector
from dashdown.data.registry import default_connector_name, load_connectors


@pytest.fixture(autouse=True)
def _reset_registry():
    """Snapshot/restore the module-global connector-type registry."""
    saved_types = dict(base._CONNECTOR_TYPES)
    saved_eps = base._ENTRY_POINTS
    yield
    base._CONNECTOR_TYPES = saved_types
    base._ENTRY_POINTS = saved_eps


class DummyConnector(Connector):
    def query(self, sql: str) -> QueryResult:
        return QueryResult(columns=["one"], rows=[[1]])


@pytest.fixture(autouse=True)
def _dummy_type():
    register_connector("dummy")(DummyConnector)


def _load(tmp_path: Path, sources_yaml: str) -> dict[str, Connector]:
    path = tmp_path / "sources.yaml"
    path.write_text(sources_yaml, encoding="utf-8")
    return load_connectors(path, tmp_path)


def test_default_true_flags_the_instance(tmp_path):
    connectors = _load(
        tmp_path,
        "primary:\n  type: dummy\n  default: true\nother:\n  type: dummy\n",
    )
    assert connectors["primary"].is_default is True
    assert connectors["other"].is_default is False
    assert default_connector_name(connectors) == "primary"


def test_flagged_default_wins_over_a_source_named_main(tmp_path):
    # `main` is just a name; the explicit flag decides the default.
    connectors = _load(
        tmp_path,
        "main:\n  type: dummy\nsecondary:\n  type: dummy\n  default: true\n",
    )
    assert default_connector_name(connectors) == "secondary"


def test_single_source_is_implicitly_the_default(tmp_path):
    connectors = _load(tmp_path, "warehouse:\n  type: dummy\n")
    assert default_connector_name(connectors) == "warehouse"


def test_multiple_sources_fall_back_to_main_by_convention(tmp_path):
    connectors = _load(tmp_path, "main:\n  type: dummy\nb:\n  type: dummy\n")
    assert default_connector_name(connectors) == "main"


def test_no_sources_fall_back_to_main(tmp_path):
    assert default_connector_name({}) == "main"


def test_two_defaults_raise(tmp_path):
    with pytest.raises(ValueError, match="only one source"):
        _load(
            tmp_path,
            "a:\n  type: dummy\n  default: true\n"
            "b:\n  type: dummy\n  default: true\n",
        )


def test_default_key_is_stripped_from_connector_config(tmp_path):
    connectors = _load(tmp_path, "primary:\n  type: dummy\n  default: true\n")
    assert "default" not in connectors["primary"].config


def test_render_page_resolves_unqualified_query_to_flagged_default(tmp_path):
    from dashdown.render.pipeline import render_page

    connectors = _load(
        tmp_path,
        "main:\n  type: dummy\nsecondary:\n  type: dummy\n  default: true\n",
    )
    rendered = render_page(
        "```sql deals\nSELECT 1\n```\n\n<Table data={deals} />",
        connectors,
    )
    assert rendered.query_defs["deals"]["connector"] == "secondary"


def test_render_page_resolves_explicit_connector_untouched(tmp_path):
    from dashdown.render.pipeline import render_page

    connectors = _load(
        tmp_path,
        "main:\n  type: dummy\nsecondary:\n  type: dummy\n  default: true\n",
    )
    rendered = render_page(
        "```sql deals connector=main\nSELECT 1\n```\n\n<Table data={deals} />",
        connectors,
    )
    assert rendered.query_defs["deals"]["connector"] == "main"
