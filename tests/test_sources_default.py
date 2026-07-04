"""Tests for default-source resolution (``default_connector_name``).

A query with no explicit ``connector=`` runs on the project's **default
source**: the one named by sources.yaml's top-level ``default: <source name>``
key, else the sole configured source. Source names carry no special meaning
(there is no magic ``main``); several sources without a ``default:`` have no
default, and an unqualified query then fails with a set-the-``default:``-key
error. The named source gets ``Connector.is_default`` (the key never reaches
any connector's config); specs parse with an empty connector and are resolved
where a connectors dict is in hand (``render_page`` / ``load_project``).
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


def test_top_level_default_names_the_source(tmp_path):
    connectors = _load(
        tmp_path,
        "default: primary\nprimary:\n  type: dummy\nother:\n  type: dummy\n",
    )
    assert connectors["primary"].is_default is True
    assert connectors["other"].is_default is False
    assert default_connector_name(connectors) == "primary"


def test_default_key_wins_regardless_of_names(tmp_path):
    # Names carry no meaning — the `default:` key decides the default.
    connectors = _load(
        tmp_path,
        "default: secondary\nmain:\n  type: dummy\nsecondary:\n  type: dummy\n",
    )
    assert default_connector_name(connectors) == "secondary"


def test_single_source_is_implicitly_the_default(tmp_path):
    connectors = _load(tmp_path, "warehouse:\n  type: dummy\n")
    assert default_connector_name(connectors) == "warehouse"


def test_multiple_unflagged_sources_have_no_default(tmp_path):
    # "main" is not special — with several unflagged sources there is no
    # default and an unqualified query must name its connector.
    connectors = _load(tmp_path, "main:\n  type: dummy\nb:\n  type: dummy\n")
    assert default_connector_name(connectors) is None


def test_no_sources_have_no_default(tmp_path):
    assert default_connector_name({}) is None


def test_unqualified_query_with_ambiguous_sources_raises(tmp_path):
    from dashdown.render.pipeline import render_page

    connectors = _load(tmp_path, "a:\n  type: dummy\nb:\n  type: dummy\n")
    with pytest.raises(ValueError, match="default: <source name>"):
        render_page("```sql deals\nSELECT 1\n```", connectors)


def test_default_naming_unknown_source_raises(tmp_path):
    with pytest.raises(ValueError, match="unknown\\s+source"):
        _load(tmp_path, "default: nope\na:\n  type: dummy\n")


def test_per_source_default_flag_raises_with_migration_hint(tmp_path):
    with pytest.raises(ValueError, match="top-level"):
        _load(tmp_path, "a:\n  type: dummy\n  default: true\n")


def test_non_string_default_raises(tmp_path):
    with pytest.raises(ValueError, match="must name one of the sources"):
        _load(tmp_path, "default: true\na:\n  type: dummy\n")


def test_default_key_never_reaches_connector_config(tmp_path):
    connectors = _load(tmp_path, "default: a\na:\n  type: dummy\n")
    assert "default" not in connectors
    assert "default" not in connectors["a"].config


def test_render_page_resolves_unqualified_query_to_flagged_default(tmp_path):
    from dashdown.render.pipeline import render_page

    connectors = _load(
        tmp_path,
        "default: secondary\nmain:\n  type: dummy\nsecondary:\n  type: dummy\n",
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
        "default: secondary\nmain:\n  type: dummy\nsecondary:\n  type: dummy\n",
    )
    rendered = render_page(
        "```sql deals connector=main\nSELECT 1\n```\n\n<Table data={deals} />",
        connectors,
    )
    assert rendered.query_defs["deals"]["connector"] == "main"
