"""Tests for Python queries (Stage 18a — ``queries/*.py`` → Arrow).

Covers the ``@query`` decorator metadata capture, the loader (name = dotted path,
``.py`` alongside ``.sql``/``.dax``, ``_``-prefixed skipped, duplicate-name +
SQL-name collisions + traversal raise, import error fails load), the
``normalize_to_query_result`` adapter over Arrow/pandas/Polars/list/QueryResult
(+ Decimal/NaN/datetime/numpy via ``serialize_value``), the runner with a fake
``connect`` (params-as-dict — **no string substitution of the Python body**;
``connect(..., params=)`` *does* escape), the ``_python_def_cache`` branch in the
data API + live poll push + static-build snapshot/error, the
``python_queries: enabled=false`` gate, and dev-reload eviction.
"""
import json
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from dashdown.data.base import Connector, QueryResult
from dashdown.python_query import (
    PythonQuerySpec,
    _find_entry_function,
    load_python_queries,
    make_connect,
    normalize_to_query_result,
    parse_python_query_file,
    query,
    run_python_query,
)
from dashdown.render.pipeline import (
    _python_def_cache,
    _python_library_keys,
    _result_cache,
    _stream_def_cache,
    get_python_query_def,
    get_stream_interval,
    register_python_library_queries,
    serialize_result,
    serialize_value,
)
from dashdown.server import create_app


def _clear_caches():
    _python_def_cache.clear()
    _python_library_keys.clear()
    _stream_def_cache.clear()
    _result_cache.clear()


# --------------------------------------------------------------------------- #
# Fakes
# --------------------------------------------------------------------------- #
class _FakeConnector(Connector):
    """Records the SQL strings it's asked to run, returns a canned result."""

    def __init__(self, result: QueryResult | None = None):
        super().__init__("fake", {})
        self.seen: list[str] = []
        self._result = result or QueryResult(columns=["x"], rows=[[1]])

    def query(self, sql: str) -> QueryResult:
        self.seen.append(sql)
        return self._result


# --------------------------------------------------------------------------- #
# @query decorator + parse + _find_entry_function
# --------------------------------------------------------------------------- #
class TestDecorator:
    def test_marks_function_with_metadata(self):
        @query(connector="warehouse", cache_ttl=300, live=True, interval=10,
               description="d")
        def fn(params, connect):
            return []

        meta = getattr(fn, "__dashdown_query__")
        assert meta == {
            "connector": "warehouse",
            "cache_ttl": 300,
            "live": True,
            "interval": 10,
            "description": "d",
        }

    def test_defaults(self):
        @query()
        def fn(params, connect):
            return []

        assert getattr(fn, "__dashdown_query__")["connector"] == "main"


def _write_py(path: Path, body: str):
    path.write_text(body, encoding="utf-8")


class TestParseAndFind:
    def test_parse_reads_metadata_and_path_name(self, tmp_path):
        f = tmp_path / "forecast.py"
        _write_py(
            f,
            "from dashdown import query\n"
            "@query(connector='main', cache_ttl=120)\n"
            "def whatever(params, connect):\n"
            "    return [{'a': 1}]\n",
        )
        spec = parse_python_query_file(f, "forecast")
        assert isinstance(spec, PythonQuerySpec)
        # Name comes from the path arg, NOT the function name.
        assert spec.name == "forecast"
        assert spec.connector == "main"
        assert spec.cache_ttl == 120
        assert spec.fn({}, None) == [{"a": 1}]

    def test_no_decorated_fn_raises(self, tmp_path):
        f = tmp_path / "bad.py"
        _write_py(f, "def fn(params, connect):\n    return []\n")
        with pytest.raises(ValueError, match="no @query-decorated function"):
            parse_python_query_file(f, "bad")

    def test_multiple_decorated_fns_raise(self, tmp_path):
        f = tmp_path / "two.py"
        _write_py(
            f,
            "from dashdown import query\n"
            "@query()\ndef a(p, c):\n    return []\n"
            "@query()\ndef b(p, c):\n    return []\n",
        )
        with pytest.raises(ValueError, match="multiple @query functions"):
            parse_python_query_file(f, "two")

    def test_import_error_propagates(self, tmp_path):
        f = tmp_path / "boom.py"
        _write_py(f, "import this_module_does_not_exist_xyz\n")
        with pytest.raises(ModuleNotFoundError):
            parse_python_query_file(f, "boom")


# --------------------------------------------------------------------------- #
# load_python_queries
# --------------------------------------------------------------------------- #
class TestLoader:
    def _q(self, body="from dashdown import query\n@query()\ndef f(p, c):\n    return []\n"):
        return body

    def test_scans_dotted_names_and_skips_underscore(self, tmp_path):
        qd = tmp_path / "queries"
        (qd / "ml").mkdir(parents=True)
        _write_py(qd / "top.py", self._q())
        _write_py(qd / "ml" / "churn.py", self._q())
        _write_py(qd / "_helper.py", "x = 1\n")  # underscore: skipped
        out = load_python_queries(qd)
        assert set(out) == {"top", "ml.churn"}

    def test_absent_dir_is_empty(self, tmp_path):
        assert load_python_queries(tmp_path / "nope") == {}

    def test_reserved_sql_name_collision_raises(self, tmp_path):
        qd = tmp_path / "queries"
        qd.mkdir()
        _write_py(qd / "sales.py", self._q())
        with pytest.raises(ValueError, match="collides with a .sql/.dax"):
            load_python_queries(qd, reserved_names={"sales"})

    def test_clean_single_load(self, tmp_path):
        qd = tmp_path / "queries"
        qd.mkdir()
        _write_py(qd / "a.py", self._q())
        assert set(load_python_queries(qd)) == {"a"}


# --------------------------------------------------------------------------- #
# normalize_to_query_result
# --------------------------------------------------------------------------- #
class TestNormalize:
    def test_query_result_passthrough(self):
        qr = QueryResult(columns=["a"], rows=[[1]])
        assert normalize_to_query_result(qr) is qr

    def test_list_of_dicts(self):
        qr = normalize_to_query_result([{"a": 1, "b": 2}, {"a": 3, "b": 4}])
        assert qr.columns == ["a", "b"]
        assert qr.rows == [[1, 2], [3, 4]]

    def test_ragged_list_unions_columns(self):
        qr = normalize_to_query_result([{"a": 1}, {"b": 2}])
        assert qr.columns == ["a", "b"]
        assert qr.rows == [[1, None], [None, 2]]

    def test_empty_list(self):
        qr = normalize_to_query_result([])
        assert qr.columns == [] and qr.rows == []

    def test_pandas(self):
        pd = pytest.importorskip("pandas")
        df = pd.DataFrame({"a": [1, 2], "b": ["x", "y"]})
        qr = normalize_to_query_result(df)
        assert qr.columns == ["a", "b"]
        assert qr.rows == [[1, "x"], [2, "y"]]

    def test_polars(self):
        pl = pytest.importorskip("polars")
        df = pl.DataFrame({"a": [1, 2], "b": ["x", "y"]})
        qr = normalize_to_query_result(df)
        assert qr.columns == ["a", "b"]
        assert qr.rows == [[1, "x"], [2, "y"]]

    def test_pyarrow_table(self):
        pa = pytest.importorskip("pyarrow")
        t = pa.table({"a": [1, 2], "b": ["x", "y"]})
        qr = normalize_to_query_result(t)
        assert qr.columns == ["a", "b"]
        assert qr.rows == [[1, "x"], [2, "y"]]

    def test_none_raises(self):
        with pytest.raises(ValueError, match="returned None"):
            normalize_to_query_result(None)

    def test_bad_type_raises(self):
        with pytest.raises(TypeError, match="unsupported type"):
            normalize_to_query_result(42)

    def test_list_of_non_dicts_raises(self):
        with pytest.raises(TypeError, match="not dicts"):
            normalize_to_query_result([1, 2, 3])


class TestNormalizeCellCoercion:
    """The JSON coercion lives in serialize_value, applied to a normalized result."""

    def test_decimal_nan_datetime_through_serialize(self):
        qr = normalize_to_query_result(
            [{"d": Decimal("1.50"), "t": datetime(2024, 1, 2, 3, 4, 5),
              "day": date(2024, 1, 2), "n": float("nan")}]
        )
        payload = serialize_result(qr)
        row = dict(zip(payload["columns"], payload["rows"][0]))
        assert row["d"] == 1.5
        assert row["t"] == "2024-01-02T03:04:05"
        assert row["day"] == "2024-01-02"
        assert row["n"] is None

    def test_numpy_scalars_are_json_safe(self):
        np = pytest.importorskip("numpy")
        qr = QueryResult(
            columns=["i", "f", "b"],
            rows=[[np.int64(7), np.float64(1.5), np.bool_(True)]],
        )
        payload = serialize_result(qr)
        # Must be JSON-serializable (numpy scalars are not, natively).
        json.dumps(payload)
        assert payload["rows"][0] == [7, 1.5, True]

    def test_serialize_value_numpy_datetime(self):
        np = pytest.importorskip("numpy")
        v = serialize_value(np.datetime64("2024-01-02T03:04:05"))
        assert isinstance(v, str) and v.startswith("2024-01-02T03:04:05")


# --------------------------------------------------------------------------- #
# run_python_query + the connect() helper (params are data, never code)
# --------------------------------------------------------------------------- #
class TestRunner:
    def test_params_arrive_as_dict_not_substituted(self):
        captured = {}

        @query()
        def fn(params, connect):
            captured.update(params)
            return [{"got": params.get("id")}]

        spec = parse_python_query_file_inline(fn, "q")
        qr = run_python_query(spec, {"id": "1 OR 1=1"}, {})
        # The raw value is handed through verbatim as data — never escaped into a
        # SQL body, because there is no body substitution for a Python query.
        assert captured == {"id": "1 OR 1=1"}
        assert qr.rows == [["1 OR 1=1"]]

    def test_connect_without_params_runs_verbatim(self):
        conn = _FakeConnector()
        c = make_connect({"main": conn})
        c("main", "SELECT 1")
        assert conn.seen == ["SELECT 1"]

    def test_connect_with_params_escapes(self):
        conn = _FakeConnector()
        c = make_connect({"main": conn})
        c("main", "WHERE name = '${name}'", params={"name": "O'Reilly"})
        # Goes through the one blessed _substitute_params: ' -> ''.
        assert conn.seen == ["WHERE name = 'O''Reilly'"]

    def test_connect_unknown_connector_raises(self):
        c = make_connect({})
        with pytest.raises(KeyError, match="unknown connector"):
            c("nope", "SELECT 1")

    def test_runner_normalizes_return(self):
        @query()
        def fn(params, connect):
            return QueryResult(columns=["a"], rows=[[9]])

        spec = parse_python_query_file_inline(fn, "q")
        assert run_python_query(spec, {}, {}).rows == [[9]]


def parse_python_query_file_inline(fn, name) -> PythonQuerySpec:
    """Build a PythonQuerySpec directly from an in-memory decorated fn (no file)."""
    meta = getattr(fn, "__dashdown_query__")
    return PythonQuerySpec(
        name=name,
        connector=meta["connector"],
        fn=fn,
        cache_ttl=meta["cache_ttl"],
        live=meta["live"],
        interval=meta["interval"],
        description=meta["description"],
    )


# --------------------------------------------------------------------------- #
# Registry: register / get / reload eviction
# --------------------------------------------------------------------------- #
class TestRegistry:
    def setup_method(self):
        _clear_caches()

    def teardown_method(self):
        _clear_caches()

    def test_register_and_get(self):
        @query(connector="main")
        def fn(p, c):
            return []

        spec = parse_python_query_file_inline(fn, "q")
        register_python_library_queries({"q": spec})
        assert get_python_query_def("q", "main") is spec

    def test_live_registers_stream_interval(self):
        @query(connector="main", live=True, interval=3)
        def fn(p, c):
            return []

        spec = parse_python_query_file_inline(fn, "live_q")
        register_python_library_queries({"live_q": spec})
        assert get_stream_interval("live_q", "main") == 3

    def test_reload_evicts_stale_keys(self):
        @query(connector="main")
        def a(p, c):
            return []

        register_python_library_queries(
            {"a": parse_python_query_file_inline(a, "a")}
        )
        assert get_python_query_def("a", "main") is not None
        # A reload that no longer defines 'a' must evict it (no ghost).
        register_python_library_queries({})
        assert get_python_query_def("a", "main") is None


# --------------------------------------------------------------------------- #
# Project integration: load, gate, data API, build, live
# --------------------------------------------------------------------------- #
_FORECAST_PY = (
    "from dashdown import query\n"
    "@query(connector='main', cache_ttl=120)\n"
    "def revenue(params, connect):\n"
    "    rows = connect('main', 'SELECT region, amount FROM sales').to_pandas()\n"
    "    rows = rows.assign(doubled=rows['amount'] * 2)\n"
    "    return rows\n"
)


def _make_py_project(root: Path, *, gate: str = "", live: bool = False):
    (root / "pages").mkdir()
    (root / "data").mkdir()
    (root / "queries").mkdir()
    (root / "dashdown.yaml").write_text("title: Py Test\n" + gate, encoding="utf-8")
    (root / "sources.yaml").write_text(
        "main:\n  type: csv\n  directory: data\n", encoding="utf-8"
    )
    (root / "data" / "sales.csv").write_text(
        "region,amount\nNorth,100\nSouth,200\n", encoding="utf-8"
    )
    deco = (
        "@query(connector='main', live=True, interval=1)\n"
        if live
        else "@query(connector='main', cache_ttl=120)\n"
    )
    (root / "queries" / "revenue.py").write_text(
        "from dashdown import query\n"
        + deco
        + "def revenue(params, connect):\n"
        "    rows = connect('main', 'SELECT region, amount FROM sales ORDER BY region').to_pandas()\n"
        "    return rows.assign(doubled=rows['amount'] * 2)\n",
        encoding="utf-8",
    )
    (root / "pages" / "index.md").write_text(
        "# Home\n\n<Table data={revenue} title=\"Revenue\" />\n", encoding="utf-8"
    )


class TestProjectIntegration:
    def setup_method(self):
        _clear_caches()

    def teardown_method(self):
        _clear_caches()

    def test_load_registers_python_query(self, tmp_path):
        from dashdown.project import load_project

        proj = tmp_path / "p"
        proj.mkdir()
        _make_py_project(proj)
        project = load_project(proj)
        try:
            assert set(project.python_queries) == {"revenue"}
            assert get_python_query_def("revenue", "main") is not None
        finally:
            project.close()

    def test_sql_py_name_collision_fails_load(self, tmp_path):
        from dashdown.project import load_project

        proj = tmp_path / "p"
        proj.mkdir()
        _make_py_project(proj)
        (proj / "queries" / "revenue.sql").write_text(
            "---\nconnector: main\n---\nSELECT 1\n", encoding="utf-8"
        )
        with pytest.raises(ValueError, match="collides with a .sql/.dax"):
            load_project(proj)

    def test_gate_disables_python_queries(self, tmp_path):
        from dashdown.project import load_project

        proj = tmp_path / "p"
        proj.mkdir()
        _make_py_project(proj, gate="python_queries:\n  enabled: false\n")
        project = load_project(proj)
        try:
            assert project.python_queries == {}
            assert get_python_query_def("revenue", "main") is None
        finally:
            project.close()

    def test_data_api_runs_python_query(self, tmp_path):
        proj = tmp_path / "p"
        proj.mkdir()
        _make_py_project(proj)
        client = TestClient(create_app(proj))
        resp = client.get("/_dashdown/api/data/revenue?_connector=main")
        assert resp.status_code == 200
        data = resp.json()
        assert data["columns"] == ["region", "amount", "doubled"]
        rows = {r[0]: r[2] for r in data["rows"]}
        assert rows == {"North": 200, "South": 400}

    def test_data_api_unknown_query_404(self, tmp_path):
        proj = tmp_path / "p"
        proj.mkdir()
        _make_py_project(proj)
        client = TestClient(create_app(proj))
        assert client.get("/_dashdown/api/data/nope?_connector=main").status_code == 404

    def test_data_api_failure_is_500(self, tmp_path):
        proj = tmp_path / "p"
        proj.mkdir()
        _make_py_project(proj)
        (proj / "queries" / "revenue.py").write_text(
            "from dashdown import query\n"
            "@query(connector='main')\n"
            "def revenue(params, connect):\n"
            "    raise RuntimeError('boom')\n",
            encoding="utf-8",
        )
        client = TestClient(create_app(proj))
        assert client.get("/_dashdown/api/data/revenue?_connector=main").status_code == 500

    def test_static_build_snapshots_python_query(self, tmp_path):
        from dashdown.build import build_site

        proj = tmp_path / "p"
        proj.mkdir()
        _make_py_project(proj)
        out = tmp_path / "dist"
        result = build_site(proj, out)
        snap = out / "_dashdown" / "data" / "main" / "revenue.json"
        assert snap.is_file()
        payload = json.loads(snap.read_text(encoding="utf-8"))
        assert payload["columns"] == ["region", "amount", "doubled"]
        assert ("main", "revenue") in result.queries

    def test_static_build_records_error_on_failure(self, tmp_path):
        from dashdown.build import build_site

        proj = tmp_path / "p"
        proj.mkdir()
        _make_py_project(proj)
        (proj / "queries" / "revenue.py").write_text(
            "from dashdown import query\n"
            "@query(connector='main')\n"
            "def revenue(params, connect):\n"
            "    raise RuntimeError('boom')\n",
            encoding="utf-8",
        )
        out = tmp_path / "dist"
        result = build_site(proj, out)
        snap = out / "_dashdown" / "data" / "main" / "revenue.json"
        payload = json.loads(snap.read_text(encoding="utf-8"))
        assert "error" in payload and "boom" in payload["error"]
        assert any(name == "revenue" for _c, name, _e in result.failed_queries)

    def test_query_def_emitted_to_client_without_source(self, tmp_path):
        """The page ships the connector/live hints but never the Python source."""
        proj = tmp_path / "p"
        proj.mkdir()
        _make_py_project(proj)
        client = TestClient(create_app(proj))
        html = client.get("/").text
        assert '"revenue"' in html  # query def present
        assert "def revenue" not in html  # source never shipped


class TestLivePythonStreaming:
    def setup_method(self):
        _clear_caches()

    def teardown_method(self):
        from dashdown.streaming import hub

        hub.reset()
        _clear_caches()

    def test_live_python_query_streams(self, tmp_path):
        proj = tmp_path / "p"
        proj.mkdir()
        _make_py_project(proj, live=True)
        client = TestClient(create_app(proj))
        # A live python query is registered in the stream cache and pushes a first
        # payload immediately on connect.
        with client.websocket_connect("/_dashdown/ws/data/revenue?_connector=main") as ws:
            msg = ws.receive_json()
            assert msg["query"] == "revenue"
            assert msg["columns"] == ["region", "amount", "doubled"]
