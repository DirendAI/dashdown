"""Tests for the shared query library (Stage 15a).

Covers the file loader (`queries/**/*.{sql,dax}` → QuerySpec), registration into
the module-global query-def cache (incl. reload eviction), page-driven resolution
precedence (local :::query shadows library, library resolves when no local,
unknown stays unresolved), the connector-aware ${param} escaping a DAX library
query inherits, `load_project` integration, and the static-build snapshot.
"""
import json
from pathlib import PurePosixPath

import pytest

from dashdown.query_library import (
    derive_query_name,
    load_queries,
    parse_query_file,
)
from dashdown.render.markdown import QuerySpec
from dashdown.render.pipeline import (
    _library_keys,
    _query_def_cache,
    _stream_def_cache,
    _substitute_params,
    get_query_def,
    get_stream_interval,
    register_library_queries,
    render_page,
)


def _clear_caches():
    _query_def_cache.clear()
    _stream_def_cache.clear()
    _library_keys.clear()


# --------------------------------------------------------------------------- #
# derive_query_name (pure)
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "rel,expected",
    [
        ("foo.sql", "foo"),
        ("foo.dax", "foo"),
        ("finance/mrr.sql", "finance.mrr"),
        ("a/b/c.sql", "a.b.c"),
    ],
)
def test_derive_query_name(rel, expected):
    assert derive_query_name(PurePosixPath(rel)) == expected


# --------------------------------------------------------------------------- #
# parse_query_file
# --------------------------------------------------------------------------- #
class TestParseQueryFile:
    def test_frontmatter_and_body(self, tmp_path):
        f = tmp_path / "mrr.sql"
        f.write_text(
            "---\n"
            "connector: warehouse\n"
            "cache_ttl: 300\n"
            "description: Monthly recurring revenue\n"
            "---\n"
            "SELECT SUM(amount) FROM subscriptions\n",
            encoding="utf-8",
        )
        spec = parse_query_file(f, "mrr")
        assert spec.name == "mrr"
        assert spec.connector == "warehouse"
        assert spec.cache_ttl == 300
        assert spec.sql == "SELECT SUM(amount) FROM subscriptions"
        assert spec.live is False
        assert spec.interval is None
        # Description is retained for introspection (Project.queries catalogue).
        assert spec.description == "Monthly recurring revenue"

    def test_description_absent_is_none(self, tmp_path):
        f = tmp_path / "q.sql"
        f.write_text("---\nconnector: main\n---\nSELECT 1\n", encoding="utf-8")
        assert parse_query_file(f, "q").description is None

    def test_defaults_without_frontmatter(self, tmp_path):
        f = tmp_path / "all.sql"
        f.write_text("SELECT 1\n", encoding="utf-8")
        spec = parse_query_file(f, "all")
        assert spec.connector == "main"  # default
        assert spec.cache_ttl is None
        assert spec.live is False
        assert spec.sql == "SELECT 1"

    def test_live_and_interval(self, tmp_path):
        f = tmp_path / "ticks.sql"
        f.write_text(
            "---\nconnector: main\nlive: true\ninterval: 10\n---\nSELECT now()\n",
            encoding="utf-8",
        )
        spec = parse_query_file(f, "ticks")
        assert spec.live is True
        assert spec.interval == 10

    def test_body_only_strips_whitespace(self, tmp_path):
        f = tmp_path / "q.sql"
        f.write_text("---\nconnector: main\n---\n\n  SELECT 2  \n\n", encoding="utf-8")
        spec = parse_query_file(f, "q")
        assert spec.sql == "SELECT 2"

    def test_bare_live_without_value_is_false(self, tmp_path):
        # `live:` with no value is YAML null -> not live (must be explicit true).
        f = tmp_path / "q.sql"
        f.write_text("---\nlive:\n---\nSELECT 1\n", encoding="utf-8")
        spec = parse_query_file(f, "q")
        assert spec.live is False


# --------------------------------------------------------------------------- #
# load_queries (directory scan)
# --------------------------------------------------------------------------- #
class TestLoadQueries:
    def test_absent_dir_returns_empty(self, tmp_path):
        assert load_queries(tmp_path / "nope") == {}

    def test_flat_and_nested_names(self, tmp_path):
        qdir = tmp_path / "queries"
        (qdir / "finance").mkdir(parents=True)
        (qdir / "foo.sql").write_text("SELECT 1\n", encoding="utf-8")
        (qdir / "finance" / "mrr.sql").write_text("SELECT 2\n", encoding="utf-8")
        specs = load_queries(qdir)
        assert set(specs) == {"foo", "finance.mrr"}
        assert specs["finance.mrr"].sql == "SELECT 2"

    def test_sql_and_dax_both_loaded(self, tmp_path):
        qdir = tmp_path / "queries"
        qdir.mkdir()
        (qdir / "a.sql").write_text("SELECT 1\n", encoding="utf-8")
        (qdir / "b.dax").write_text(
            "---\nconnector: fabric\n---\nEVALUATE Sales\n", encoding="utf-8"
        )
        specs = load_queries(qdir)
        assert set(specs) == {"a", "b"}
        assert specs["b"].connector == "fabric"
        assert specs["b"].sql == "EVALUATE Sales"

    def test_non_query_files_ignored(self, tmp_path):
        qdir = tmp_path / "queries"
        qdir.mkdir()
        (qdir / "a.sql").write_text("SELECT 1\n", encoding="utf-8")
        (qdir / "README.md").write_text("docs", encoding="utf-8")
        (qdir / "notes.txt").write_text("x", encoding="utf-8")
        assert set(load_queries(qdir)) == {"a"}

    def test_duplicate_derived_name_raises(self, tmp_path):
        # foo.sql and foo.dax both derive `foo` -> collision (fail-at-startup).
        qdir = tmp_path / "queries"
        qdir.mkdir()
        (qdir / "foo.sql").write_text("SELECT 1\n", encoding="utf-8")
        (qdir / "foo.dax").write_text("EVALUATE T\n", encoding="utf-8")
        with pytest.raises(ValueError, match="duplicate query name 'foo'"):
            load_queries(qdir)

    def test_path_traversal_via_symlink_blocked(self, tmp_path):
        outside = tmp_path / "outside.sql"
        outside.write_text("SELECT secret\n", encoding="utf-8")
        qdir = tmp_path / "queries"
        qdir.mkdir()
        link = qdir / "link.sql"
        try:
            link.symlink_to(outside)
        except (OSError, NotImplementedError):
            pytest.skip("symlinks not supported on this platform")
        with pytest.raises(ValueError, match="escapes queries/"):
            load_queries(qdir)


# --------------------------------------------------------------------------- #
# register_library_queries (cache + reload eviction)
# --------------------------------------------------------------------------- #
class TestRegisterLibraryQueries:
    def setup_method(self):
        _clear_caches()

    def teardown_method(self):
        _clear_caches()

    def test_registers_into_query_def_cache(self):
        specs = {"mrr": QuerySpec(name="mrr", connector="main", sql="SELECT 1", cache_ttl=120)}
        register_library_queries(specs)
        got = get_query_def("mrr", "main")
        assert got is not None
        sql, params, ttl = got
        assert sql == "SELECT 1"
        assert params == {}  # library queries register with empty default params
        assert ttl == 120

    def test_live_query_registers_stream_interval(self):
        specs = {
            "ticks": QuerySpec(
                name="ticks", connector="main", sql="SELECT now()", live=True, interval=10
            )
        }
        register_library_queries(specs)
        assert get_stream_interval("ticks", "main") == 10

    def test_reload_evicts_stale_keys(self):
        # First load registers `old`; a second load without it must evict it so
        # a renamed/deleted query file leaves no ghost in the global cache.
        register_library_queries(
            {"old": QuerySpec(name="old", connector="main", sql="SELECT 1")}
        )
        assert get_query_def("old", "main") is not None
        register_library_queries(
            {"new": QuerySpec(name="new", connector="main", sql="SELECT 2")}
        )
        assert get_query_def("old", "main") is None  # ghost gone
        assert get_query_def("new", "main") is not None

    def test_reload_evicts_stale_live_keys(self):
        register_library_queries(
            {"t": QuerySpec(name="t", connector="main", sql="SELECT 1", live=True, interval=5)}
        )
        assert get_stream_interval("t", "main") == 5
        register_library_queries({})  # everything removed
        assert get_stream_interval("t", "main") is None


# --------------------------------------------------------------------------- #
# Page-driven resolution + precedence
# --------------------------------------------------------------------------- #
_LIB = {
    "sales": QuerySpec(name="sales", connector="warehouse", sql="SELECT * FROM sales"),
    "finance.mrr": QuerySpec(name="finance.mrr", connector="main", sql="SELECT mrr FROM m"),
}


class TestResolutionPrecedence:
    def setup_method(self):
        _clear_caches()

    def teardown_method(self):
        _clear_caches()

    def test_library_resolves_when_no_local(self):
        page = render_page(
            "# P\n\n<Table data={sales} />\n", {}, library=dict(_LIB)
        )
        assert "sales" in page.query_defs
        assert page.query_defs["sales"]["connector"] == "warehouse"
        # SQL is never shipped to the client.
        assert "sql" not in page.query_defs["sales"]

    def test_dotted_namespaced_ref_resolves(self):
        page = render_page(
            "# P\n\n<Table data={finance.mrr} />\n", {}, library=dict(_LIB)
        )
        assert "finance.mrr" in page.query_defs
        assert page.query_defs["finance.mrr"]["connector"] == "main"

    def test_local_shadows_library(self):
        src = (
            "# P\n\n"
            ":::query name=sales connector=main\n"
            "SELECT local FROM t\n"
            ":::\n\n"
            "<Table data={sales} />\n"
        )
        page = render_page(src, {}, library=dict(_LIB))
        # The page-local block wins: its connector (main), not the library's
        # (warehouse). SQL never reaches query_defs, so the registered cache
        # entry confirms the local block's SQL is the one that runs.
        assert page.query_defs["sales"]["connector"] == "main"
        got = get_query_def("sales", "main")
        assert got is not None and got[0] == "SELECT local FROM t"

    def test_unknown_ref_stays_unresolved(self):
        page = render_page(
            "# P\n\n<Table data={nope} />\n", {}, library=dict(_LIB)
        )
        # Not local, not in the library -> not added to query_defs (the existing
        # client-side 404 path handles it, unchanged).
        assert "nope" not in page.query_defs

    def test_unreferenced_library_query_not_emitted(self):
        # A page referencing only `sales` must not dump the whole library into
        # its query_defs (discovery is page-driven).
        page = render_page(
            "# P\n\n<Table data={sales} />\n", {}, library=dict(_LIB)
        )
        assert "finance.mrr" not in page.query_defs

    def test_referenced_library_query_registered_in_cache(self):
        render_page("# P\n\n<Table data={sales} />\n", {}, library=dict(_LIB))
        got = get_query_def("sales", "warehouse")
        assert got is not None and got[0] == "SELECT * FROM sales"

    def test_library_query_body_not_shipped_to_client(self):
        page = render_page(
            "# P\n\n<Table data={sales} />\n",
            {},
            library=dict(_LIB),
        )
        assert "sales" in page.query_defs
        assert "sql" not in page.query_defs["sales"]
        assert page.query_defs["sales"]["connector"] == "warehouse"

    def test_live_library_query_surfaced_in_defs(self):
        lib = {
            "ticks": QuerySpec(
                name="ticks", connector="main", sql="SELECT now()", live=True, interval=7
            )
        }
        page = render_page("# P\n\n<Counter data={ticks} />\n", {}, library=lib)
        assert page.query_defs["ticks"]["live"] is True
        assert page.query_defs["ticks"]["interval"] == 7
        # And the WS endpoint will accept it (registered in the stream cache).
        assert get_stream_interval("ticks", "main") == 7

    def test_static_build_omits_live_flag(self):
        lib = {
            "ticks": QuerySpec(
                name="ticks", connector="main", sql="SELECT now()", live=True, interval=7
            )
        }
        page = render_page(
            "# P\n\n<Counter data={ticks} />\n", {}, static_build=True, library=lib
        )
        assert "live" not in page.query_defs["ticks"]

    def test_referenced_library_query_in_query_connectors(self):
        # <Ask /> binds its connector from ctx.query_connectors at render time;
        # a referenced library query must resolve to the right connector there.
        lib = {"q": QuerySpec(name="q", connector="warehouse", sql="SELECT 1")}
        page = render_page(
            '# P\n\n<Ask data={q} ask="why?" />\n', {}, library=lib
        )
        # The Ask def must carry the library query's connector, not the "main" default.
        assert page.ask_defs and page.ask_defs[0].connector == "warehouse"


# --------------------------------------------------------------------------- #
# DAX library query inherits connector-aware ${param} escaping
# --------------------------------------------------------------------------- #
def test_dax_library_query_keeps_double_quote_escaping(tmp_path):
    f = tmp_path / "by_type.dax"
    f.write_text(
        '---\nconnector: fabric\n---\n'
        'EVALUATE FILTER(Sales, Sales[Type] = "${kind}")\n',
        encoding="utf-8",
    )
    spec = parse_query_file(f, "by_type")
    # DAX string literals are double-quoted; the "->"" escaping is the same
    # context-aware substitution an inline :::query would get.
    out = _substitute_params(spec.sql, {"kind": 'a "b" c'})
    assert out == 'EVALUATE FILTER(Sales, Sales[Type] = "a ""b"" c")'


# --------------------------------------------------------------------------- #
# load_project integration
# --------------------------------------------------------------------------- #
def _make_project_with_library(root):
    (root / "pages").mkdir()
    (root / "data").mkdir()
    (root / "queries").mkdir()
    (root / "queries" / "finance").mkdir()
    (root / "dashdown.yaml").write_text("title: Lib Test\n", encoding="utf-8")
    (root / "sources.yaml").write_text(
        "main:\n  type: csv\n  directory: data\n", encoding="utf-8"
    )
    (root / "data" / "sales.csv").write_text(
        "region,amount\nNorth,100\nSouth,200\n", encoding="utf-8"
    )
    (root / "queries" / "by_region.sql").write_text(
        "---\nconnector: main\ncache_ttl: 90\n---\n"
        "SELECT region, SUM(amount) AS total FROM sales GROUP BY region ORDER BY region\n",
        encoding="utf-8",
    )
    (root / "queries" / "finance" / "mrr.sql").write_text(
        "---\nconnector: main\n---\nSELECT SUM(amount) AS mrr FROM sales\n",
        encoding="utf-8",
    )
    # Page references the shared queries by name (one of them namespaced).
    (root / "pages" / "index.md").write_text(
        "# Home\n\n"
        '<Table data={by_region} title="By Region" />\n\n'
        "<Counter data={finance.mrr} label=\"MRR\" />\n",
        encoding="utf-8",
    )


class TestLoadProjectIntegration:
    def setup_method(self):
        _clear_caches()

    def teardown_method(self):
        _clear_caches()

    def test_load_project_populates_queries(self, tmp_path):
        from dashdown.project import load_project

        proj = tmp_path / "proj"
        proj.mkdir()
        _make_project_with_library(proj)
        project = load_project(proj)
        try:
            assert set(project.queries) == {"by_region", "finance.mrr"}
            # Registered in the global cache at load (before any page render).
            got = get_query_def("by_region", "main")
            assert got is not None and got[2] == 90  # cache_ttl carried
        finally:
            project.close()

    def test_duplicate_name_fails_project_load(self, tmp_path):
        from dashdown.project import load_project

        proj = tmp_path / "proj"
        proj.mkdir()
        _make_project_with_library(proj)
        # Add a colliding .dax for the same derived name.
        (proj / "queries" / "by_region.dax").write_text(
            "EVALUATE T\n", encoding="utf-8"
        )
        with pytest.raises(ValueError, match="duplicate query name"):
            load_project(proj)


# --------------------------------------------------------------------------- #
# Static build snapshots a referenced library query
# --------------------------------------------------------------------------- #
class TestStaticBuildIntegration:
    def setup_method(self):
        _clear_caches()

    def teardown_method(self):
        _clear_caches()

    def test_build_snapshots_referenced_library_query(self, tmp_path):
        from dashdown.build import build_site

        proj = tmp_path / "proj"
        proj.mkdir()
        _make_project_with_library(proj)
        out = tmp_path / "dist"
        result = build_site(proj, out)

        # The page-referenced library query was executed once and snapshotted via
        # the same query_defs -> execute -> JSON path as an inline query.
        snap = out / "_dashdown" / "data" / "main" / "by_region.json"
        assert snap.is_file()
        payload = json.loads(snap.read_text(encoding="utf-8"))
        assert payload["columns"] == ["region", "total"]
        assert ("main", "by_region") in result.queries
        # The namespaced query stays a single safe path segment (dot, not slash).
        assert (out / "_dashdown" / "data" / "main" / "finance.mrr.json").is_file()
