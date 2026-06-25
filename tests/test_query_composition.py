"""Tests for query composition (Stage 15b).

Covers the dbt-style ``ref('other')`` → inline-CTE compiler
(``dashdown.query_composition``): single + nested + diamond references,
dotted-namespace aliases, ``${param}`` survival (so the one injection path still
runs once over the composed SQL), comment/string masking, the load-time guards
(unknown ref, cycle, cross-connector, DAX connector, alias collision), and a
``load_project`` integration that composes and runs against a real CSV connector.
"""
from __future__ import annotations

import pytest

from dashdown.query_composition import compose_library_queries
from dashdown.render.markdown import QuerySpec
from dashdown.render.pipeline import _substitute_params


def _spec(name: str, sql: str, connector: str = "main") -> QuerySpec:
    return QuerySpec(name=name, connector=connector, sql=sql)


def _compose(specs_list, dax_connectors=frozenset()):
    return compose_library_queries({s.name: s for s in specs_list}, dax_connectors)


# --------------------------------------------------------------------------- #
# No-op: a query without ref() is unchanged
# --------------------------------------------------------------------------- #


def test_query_without_ref_is_unchanged():
    out = _compose([_spec("plain", "SELECT 1")])
    assert out["plain"].sql == "SELECT 1"


def test_compose_preserves_other_spec_fields():
    spec = QuerySpec(
        name="active",
        connector="main",
        sql="SELECT * FROM ref('base')",
        cache_ttl=300,
        live=True,
        interval=10,
        description="desc",
    )
    base = _spec("base", "SELECT 1 AS x")
    out = compose_library_queries({"active": spec, "base": base})
    composed = out["active"]
    assert composed.cache_ttl == 300
    assert composed.live is True
    assert composed.interval == 10
    assert composed.description == "desc"
    assert composed.connector == "main"


# --------------------------------------------------------------------------- #
# Single + nested + diamond references
# --------------------------------------------------------------------------- #


def test_single_ref_becomes_cte():
    out = _compose([
        _spec("base", "SELECT user_id FROM events"),
        _spec("active", "SELECT * FROM ref('base') WHERE active"),
    ])
    sql = out["active"].sql
    assert sql == (
        "WITH base AS (\n"
        "SELECT user_id FROM events\n"
        ")\n"
        "SELECT * FROM base WHERE active"
    )
    # base itself has no refs → untouched
    assert out["base"].sql == "SELECT user_id FROM events"


def test_nested_refs_topologically_ordered():
    out = _compose([
        _spec("a", "SELECT 1 AS n"),
        _spec("b", "SELECT n + 1 AS n FROM ref('a')"),
        _spec("c", "SELECT n + 1 AS n FROM ref('b')"),
    ])
    sql = out["c"].sql
    # Dependencies declared before use: a before b before c's body.
    assert sql.index("a AS (") < sql.index("b AS (")
    assert sql.startswith("WITH a AS (")
    assert sql.rstrip().endswith("SELECT n + 1 AS n FROM b")
    # The inner CTE rewrites its own ref too.
    assert "FROM ref(" not in sql
    assert "SELECT n + 1 AS n FROM a" in sql


def test_diamond_dependency_emits_each_cte_once():
    # d -> b -> a, d -> c -> a : `a` must appear exactly once.
    out = _compose([
        _spec("a", "SELECT 1 AS n"),
        _spec("b", "SELECT n FROM ref('a')"),
        _spec("c", "SELECT n FROM ref('a')"),
        _spec("d", "SELECT * FROM ref('b') JOIN ref('c') USING (n)"),
    ])
    sql = out["d"].sql
    assert sql.count("a AS (") == 1
    assert "SELECT * FROM b JOIN c USING (n)" in sql


# --------------------------------------------------------------------------- #
# Dotted-namespace aliasing
# --------------------------------------------------------------------------- #


def test_dotted_ref_aliases_to_underscore_identifier():
    out = _compose([
        _spec("finance.mrr", "SELECT amount FROM subs"),
        _spec("report", "SELECT SUM(amount) FROM ref('finance.mrr')"),
    ])
    sql = out["report"].sql
    assert "finance_mrr AS (" in sql
    assert "FROM finance_mrr" in sql
    assert "ref(" not in sql


def test_alias_collision_raises():
    with pytest.raises(ValueError, match="CTE alias"):
        _compose([
            _spec("a.b", "SELECT 1"),
            _spec("a_b", "SELECT 2"),
            _spec("top", "SELECT * FROM ref('a.b'), ref('a_b')"),
        ])


# --------------------------------------------------------------------------- #
# ${param} survives composition (single injection path preserved)
# --------------------------------------------------------------------------- #


def test_params_survive_into_composed_sql_and_substitute_once():
    out = _compose([
        _spec("base", "SELECT * FROM sales WHERE region = '${region}'"),
        _spec("top", "SELECT * FROM ref('base') LIMIT ${limit}"),
    ])
    composed = out["top"].sql
    assert "${region}" in composed  # not yet substituted
    assert "${limit}" in composed
    final = _substitute_params(composed, {"region": "East", "limit": "5"})
    assert "region = 'East'" in final
    assert "LIMIT '5'" in final
    assert "${" not in final


def test_injection_in_composed_dependency_is_inert():
    out = _compose([
        _spec("base", "SELECT * FROM t WHERE name = '${name}'"),
        _spec("top", "SELECT count(*) FROM ref('base')"),
    ])
    final = _substitute_params(out["top"].sql, {"name": "O'Reilly"})
    assert "name = 'O''Reilly'" in final


# --------------------------------------------------------------------------- #
# Guards: unknown / cycle / cross-connector / DAX / quoting variants
# --------------------------------------------------------------------------- #


def test_unknown_ref_raises():
    with pytest.raises(ValueError, match="unknown query 'missing'"):
        _compose([_spec("top", "SELECT * FROM ref('missing')")])


def test_direct_cycle_raises():
    with pytest.raises(ValueError, match="circular query reference"):
        _compose([_spec("a", "SELECT * FROM ref('a')")])


def test_indirect_cycle_raises():
    with pytest.raises(ValueError, match="circular query reference"):
        _compose([
            _spec("a", "SELECT * FROM ref('b')"),
            _spec("b", "SELECT * FROM ref('a')"),
        ])


def test_cross_connector_ref_raises():
    with pytest.raises(ValueError, match="different connector"):
        _compose([
            _spec("warehouse", "SELECT 1", connector="snowflake"),
            _spec("top", "SELECT * FROM ref('warehouse')", connector="main"),
        ])


def test_dax_connector_ref_raises():
    with pytest.raises(ValueError, match="DAX connector"):
        _compose(
            [
                _spec("base", "EVALUATE Sales", connector="fabric"),
                _spec("top", "EVALUATE ref('base')", connector="fabric"),
            ],
            dax_connectors=frozenset({"fabric"}),
        )


def test_ref_in_line_comment_is_ignored():
    # A `ref(...)` mentioned in a `--` comment (e.g. docs) is not a dependency.
    out = _compose([
        _spec("top", "-- see ref('missing') for details\nSELECT 1"),
    ])
    assert out["top"].sql == "-- see ref('missing') for details\nSELECT 1"


def test_ref_in_block_comment_is_ignored():
    out = _compose([
        _spec("top", "/* ref('missing') */ SELECT 1"),
    ])
    assert "WITH" not in out["top"].sql


def test_ref_in_string_literal_is_ignored():
    # A well-formed ref() living inside a single-quoted SQL literal is data, not
    # a dependency — no CTE, no unknown-ref error.
    out = _compose([
        _spec("top", "SELECT 'ref(\"missing\")' AS note"),
    ])
    assert "WITH" not in out["top"].sql
    assert out["top"].sql == "SELECT 'ref(\"missing\")' AS note"


def test_real_ref_alongside_commented_ref():
    out = _compose([
        _spec("base", "SELECT 1 AS n"),
        _spec("top", "-- ignore ref('ghost')\nSELECT * FROM ref('base')"),
    ])
    sql = out["top"].sql
    assert "WITH base AS (" in sql
    assert "ref('ghost')" in sql  # comment text untouched
    assert "FROM base" in sql


def test_double_quoted_ref_is_recognized():
    out = _compose([
        _spec("base", "SELECT 1"),
        _spec("top", 'SELECT * FROM ref("base")'),
    ])
    assert "WITH base AS (" in out["top"].sql
    assert "ref(" not in out["top"].sql


# --------------------------------------------------------------------------- #
# load_project integration: a composed library query runs against a real CSV
# connector and the composed SQL is what got registered.
# --------------------------------------------------------------------------- #


def test_load_project_composes_and_runs(tmp_path):
    from dashdown.project import load_project
    from dashdown.render.pipeline import (
        _library_keys,
        _query_def_cache,
        _stream_def_cache,
        get_query_def,
    )

    _query_def_cache.clear()
    _stream_def_cache.clear()
    _library_keys.clear()

    proj = tmp_path / "proj"
    (proj / "pages").mkdir(parents=True)
    (proj / "data").mkdir()
    (proj / "queries").mkdir()
    (proj / "dashdown.yaml").write_text("title: Compose\n")
    (proj / "sources.yaml").write_text("main:\n  type: csv\n  directory: data\n")
    (proj / "data" / "sales.csv").write_text(
        "region,amount\nEast,100\nEast,50\nWest,200\n"
    )
    (proj / "queries" / "base.sql").write_text(
        "---\nconnector: main\n---\nSELECT region, amount FROM sales\n"
    )
    (proj / "queries" / "east_total.sql").write_text(
        "---\nconnector: main\n---\n"
        "SELECT SUM(amount) AS total FROM ref('base') WHERE region = 'East'\n"
    )

    project = load_project(proj)
    try:
        # The registered SQL is the *composed* one, with base hoisted to a CTE.
        composed_sql = get_query_def("east_total", "main")[0]
        assert "WITH base AS (" in composed_sql
        assert "ref(" not in composed_sql
        # And it actually runs against the CSV connector.
        result = project.connectors["main"].query(composed_sql)
        assert result.rows == [[150]]
    finally:
        project.close()
        _query_def_cache.clear()
        _stream_def_cache.clear()
        _library_keys.clear()
