"""Tests for the Cube backend of the semantic layer (Stage 18d).

Like the DAX tests, **none of these need an optional extra**: the Cube backend builds
a structured JSON query (a plain ``dict``) and the ``cube`` connector's HTTP/JWT calls
go through lazily-imported ``httpx``/``jwt`` modules — so the query builder, the
``/meta`` parser, introspection and the execute path are all exercised with *fake*
modules + a fake connector, and this whole file runs on a bare checkout.

The headline security property is **there is no string interpolation on the Cube path**
— filter values are JSON data, never substituted into a query string — and a test below
asserts ``_substitute_params`` is never reached.
"""
from __future__ import annotations

import sys

import pytest

from dashdown.data.base import QueryResult
from dashdown.python_query import PythonQuerySpec, run_python_query
from dashdown.semantic import (
    SemanticModelHandle,
    _detect_backend,
    build_filters,
    build_semantic_spec,
    resolve_ref,
)
from dashdown.semantic_cube import (
    CubeBackend,
    build_cube_catalogue,
    build_cube_query,
    cube_result_keys,
    parse_cube_meta,
)


# A captured-shape Cube /meta document: one cube with a string dim, a time dim, and
# two measures (one currency-formatted). Member names are fully-qualified, as Cube
# returns them and keys its result rows by.
META = {
    "cubes": [
        {
            "name": "orders",
            "title": "Orders",
            "measures": [
                {"name": "orders.count", "type": "number", "aggType": "count"},
                {
                    "name": "orders.revenue",
                    "type": "number",
                    "format": "currency",
                    "meta": {"currency": "$"},
                },
            ],
            "dimensions": [
                {"name": "orders.status", "type": "string"},
                {"name": "orders.createdAt", "type": "time"},
            ],
        }
    ]
}


def _cube_handle(cfg: dict | None = None, **over) -> SemanticModelHandle:
    """A Cube-backed handle introspected from the META fixture (no network)."""
    h = SemanticModelHandle(
        name="orders",
        connector="cube",
        file_config={"orders": cfg or {}},
        table_connectors={},
        profile=None,
        profile_path=None,
        backend="cube",
    )
    CubeBackend().introspect(h, {"cube": _FakeCube(META)})
    for k, v in over.items():
        setattr(h, k, v)
    return h


class _FakeCube:
    """A stand-in cube connector: serves a fixed /meta and records /load queries.

    The class name matters for one detection test below (``_detect_backend`` keys off
    ``type(c).__name__``), so a separate ``CubeConnector``-named stub is used there.
    """

    def __init__(self, meta, load_payload=None):
        self._meta = meta
        self._load_payload = load_payload
        self.queries: list[dict] = []

    def meta(self):
        return self._meta

    def load(self, query):
        self.queries.append(query)
        return self._load_payload


# --------------------------------------------------------------------------- #
# /meta parsing + catalogue
# --------------------------------------------------------------------------- #


def test_parse_cube_meta_walks_measures_and_dimensions():
    cat = parse_cube_meta(META)
    assert set(cat["dimensions"]) == {"orders.status", "orders.createdAt"}
    assert set(cat["measures"]) == {"orders.count", "orders.revenue"}
    assert cat["dimensions"]["orders.createdAt"]["type"] == "time"
    assert cat["time_members"] == {"orders.createdAt"}
    assert cat["time_dimension"] == "orders.createdAt"
    # Cube `format: currency` + `meta.currency` -> our display-format hint.
    assert cat["measures"]["orders.revenue"]["format"] == {"format": "currency", "currency": "$"}
    assert cat["measures"]["orders.count"]["format"] == {}


def test_build_catalogue_merges_yaml_granularity_aliases():
    cfg = {"dimensions": {"month": {"member": "orders.createdAt", "granularity": "month"}}}
    cat = build_cube_catalogue(META, cfg)
    members = cat["members"]
    assert members["month"] == {
        "member": "orders.createdAt", "kind": "dimension",
        "type": "time", "granularity": "month",
    }
    # The auto members are still present.
    assert members["orders.status"]["kind"] == "dimension"
    assert members["orders.count"]["kind"] == "measure"


def test_build_catalogue_default_granularity_override():
    assert build_cube_catalogue(META, {})["default_granularity"] == "day"
    assert build_cube_catalogue(META, {"granularity": "week"})["default_granularity"] == "week"


def test_build_catalogue_bad_alias_raises():
    with pytest.raises(ValueError):
        build_cube_catalogue(META, {"dimensions": {"month": "not-a-mapping"}})


# --------------------------------------------------------------------------- #
# Introspection populates the shared catalogue
# --------------------------------------------------------------------------- #


def test_introspect_fills_shared_catalogue():
    h = _cube_handle()
    assert h.dimensions == {"orders.status", "orders.createdAt"}
    assert h.measures == {"orders.count", "orders.revenue"}
    assert h.time_dimension == "orders.createdAt"
    # short-segment lookups resolve `by={orders.status}` and `metric={orders.count}`
    assert h.dim_lookup["status"] == "orders.status"
    assert h.measure_lookup["count"] == "orders.count"
    assert h.measure_formats == {"orders.revenue": {"format": "currency", "currency": "$"}}


def test_introspect_unknown_connector_raises():
    h = SemanticModelHandle(
        name="orders", connector="nope", file_config={"orders": {}},
        table_connectors={}, profile=None, profile_path=None, backend="cube",
    )
    with pytest.raises(ValueError):
        CubeBackend().introspect(h, {})


def test_introspect_no_measures_raises():
    empty_meta = {"cubes": [{"name": "x", "dimensions": [{"name": "x.d", "type": "string"}]}]}
    h = SemanticModelHandle(
        name="x", connector="cube", file_config={"x": {}},
        table_connectors={}, profile=None, profile_path=None, backend="cube",
    )
    with pytest.raises(ValueError):
        CubeBackend().introspect(h, {"cube": _FakeCube(empty_meta)})


def test_introspect_meta_failure_fails_at_load():
    class Boom:
        def meta(self):
            raise RuntimeError("connection refused")

    h = SemanticModelHandle(
        name="orders", connector="cube", file_config={"orders": {}},
        table_connectors={}, profile=None, profile_path=None, backend="cube",
    )
    with pytest.raises(RuntimeError):
        CubeBackend().introspect(h, {"cube": Boom()})


def test_introspect_optional_skips_on_meta_failure():
    class Boom:
        def meta(self):
            raise RuntimeError("connection refused")

    h = SemanticModelHandle(
        name="orders", connector="cube", file_config={"orders": {"optional": True}},
        table_connectors={}, profile=None, profile_path=None, backend="cube",
    )
    CubeBackend().introspect(h, {"cube": Boom()})  # no raise
    assert h.cube_meta["members"] == {}
    assert h.measures == set()


# --------------------------------------------------------------------------- #
# Query builder — (ref, filters) -> structured Cube JSON
# --------------------------------------------------------------------------- #


def test_build_query_basic_groupby_and_measure():
    h = _cube_handle()
    ref = resolve_ref({"orders": h}, "orders.count", "orders.status")
    q = build_cube_query(h, ref, [])
    assert q["measures"] == ["orders.count"]
    assert q["dimensions"] == ["orders.status"]
    assert q["order"] == [["orders.status", "asc"]]
    assert "timeDimensions" not in q and "filters" not in q


def test_build_query_scalar_has_no_dimensions_or_order():
    h = _cube_handle()
    ref = resolve_ref({"orders": h}, "orders.revenue", None)
    q = build_cube_query(h, ref, [])
    assert q["measures"] == ["orders.revenue"]
    assert "dimensions" not in q and "order" not in q


def test_build_query_multi_metric():
    h = _cube_handle()
    ref = resolve_ref({"orders": h}, "orders.count,orders.revenue", "orders.status")
    q = build_cube_query(h, ref, [])
    assert q["measures"] == ["orders.count", "orders.revenue"]


def test_build_query_series_mixes_plain_and_time_dims():
    # by = plain dim (status), series = time dim (createdAt) -> the plain one routes to
    # dimensions[], the time one to timeDimensions[] with the default granularity.
    h = _cube_handle()
    ref = resolve_ref({"orders": h}, "orders.count", "orders.status", series_ref="orders.createdAt")
    q = build_cube_query(h, ref, [])
    assert q["dimensions"] == ["orders.status"]
    assert q["timeDimensions"] == [{"dimension": "orders.createdAt", "granularity": "day"}]
    # ordered by both grouping members
    assert q["order"] == [["orders.status", "asc"], ["orders.createdAt", "asc"]]
    assert cube_result_keys(h, ref) == ["orders.status", "orders.createdAt.day", "orders.count"]


def test_build_query_in_filter_becomes_equals():
    h = _cube_handle()
    ref = resolve_ref({"orders": h}, "orders.count", "orders.status")
    q = build_cube_query(h, ref, build_filters(h, {"status": "shipped,new"}))
    assert q["filters"] == [
        {"member": "orders.status", "operator": "equals", "values": ["shipped", "new"]}
    ]


def test_build_query_empty_in_filter_dropped():
    h = _cube_handle()
    ref = resolve_ref({"orders": h}, "orders.count", "orders.status")
    q = build_cube_query(h, ref, [{"field": "orders.status", "operator": "in", "values": []}])
    assert "filters" not in q


def test_build_query_time_groupby_default_granularity():
    h = _cube_handle()
    ref = resolve_ref({"orders": h}, "orders.count", "orders.createdAt")
    q = build_cube_query(h, ref, [])
    assert q["timeDimensions"] == [{"dimension": "orders.createdAt", "granularity": "day"}]
    assert "dimensions" not in q
    assert cube_result_keys(h, ref) == ["orders.createdAt.day", "orders.count"]


def test_build_query_time_groupby_alias_granularity():
    # A YAML alias `month` pins the bucket -> by={orders.month} groups monthly.
    h = _cube_handle({"dimensions": {"month": {"member": "orders.createdAt", "granularity": "month"}}})
    ref = resolve_ref({"orders": h}, "orders.count", "orders.month")
    q = build_cube_query(h, ref, [])
    assert q["timeDimensions"] == [{"dimension": "orders.createdAt", "granularity": "month"}]
    assert cube_result_keys(h, ref) == ["orders.createdAt.month", "orders.count"]


# --------------------------------------------------------------------------- #
# Time grain — `grain=` routes straight to Cube's native granularity (Stage 18e)
# --------------------------------------------------------------------------- #


def test_build_query_grain_overrides_granularity():
    """A `grain=` token (literal or control-driven) routes to
    `timeDimensions[].granularity`, overriding the model default — no YAML alias."""
    h = _cube_handle()
    ref = resolve_ref({"orders": h}, "orders.count", "orders.createdAt", grain="month")
    q = build_cube_query(h, ref, [], grain="month")
    assert q["timeDimensions"] == [{"dimension": "orders.createdAt", "granularity": "month"}]
    # the result-key rename uses the same granularity so the columns line up
    assert cube_result_keys(h, ref, grain="month") == ["orders.createdAt.month", "orders.count"]


def test_build_query_grain_beats_static_alias():
    """An explicit `grain=` wins over a member's fixed YAML granularity alias."""
    h = _cube_handle({"dimensions": {"month": {"member": "orders.createdAt", "granularity": "month"}}})
    ref = resolve_ref({"orders": h}, "orders.count", "orders.month", grain="year")
    q = build_cube_query(h, ref, [], grain="year")
    assert q["timeDimensions"] == [{"dimension": "orders.createdAt", "granularity": "year"}]
    assert cube_result_keys(h, ref, grain="year") == ["orders.createdAt.year", "orders.count"]


def test_build_query_grain_ignored_on_non_time_dim():
    """A grain on a plain (non-time) `by` is a no-op — only time members bucket."""
    h = _cube_handle()
    ref = resolve_ref({"orders": h}, "orders.count", "orders.status", grain="month")
    q = build_cube_query(h, ref, [], grain="month")
    assert q["dimensions"] == ["orders.status"]
    assert "timeDimensions" not in q


def test_build_spec_interactive_grain_reads_param():
    """`grain={g}` is one synthetic query; build_spec.fn reads the live param and
    compiles the matching Cube granularity (+ renames the result column to match)."""
    h = _cube_handle()
    models = {"orders": h}
    ref = resolve_ref(models, "orders.count", "orders.createdAt", grain_param="g")
    assert "grain" not in ref.query_name  # one def, grain varies per fetch
    conn = _FakeCube(
        META,
        load_payload={
            "data": [{"orders.createdAt.quarter": "2024-01", "orders.count": 9}],
            "annotation": {
                "timeDimensions": {"orders.createdAt.quarter": {"title": "Created at"}},
                "measures": {"orders.count": {"title": "Count"}},
            },
        },
    )
    spec = build_semantic_spec(models, ref, {"cube": conn})
    result = run_python_query(spec, {"g": "quarter"}, {"cube": conn})
    # the live grain compiled into the JSON query…
    assert conn.queries[0]["timeDimensions"] == [
        {"dimension": "orders.createdAt", "granularity": "quarter"}
    ]
    # …and the `member.quarter` key was renamed to the canonical `by` column.
    assert result.columns == ["orders.createdAt", "orders.count"]
    assert result.rows == [["2024-01", 9]]


def test_build_query_date_range_collapses_into_time_dimension():
    h = _cube_handle()
    ref = resolve_ref({"orders": h}, "orders.count", "orders.createdAt")
    q = build_cube_query(
        h, ref, build_filters(h, {"date_start": "2024-01-01", "date_end": "2024-03-31"})
    )
    # The >=/<= pair merges onto the grouping entry as a single dateRange.
    assert q["timeDimensions"] == [
        {"dimension": "orders.createdAt", "granularity": "day",
         "dateRange": ["2024-01-01", "2024-03-31"]}
    ]
    assert "filters" not in q


def test_build_query_date_range_only_start():
    h = _cube_handle()
    ref = resolve_ref({"orders": h}, "orders.count", "orders.createdAt")
    q = build_cube_query(h, ref, build_filters(h, {"date_start": "2024-01-01"}))
    assert q["timeDimensions"][0]["dateRange"] == ["2024-01-01"]


def test_build_query_limit_from_config():
    h = _cube_handle()
    h.cube_meta["limit"] = 100
    ref = resolve_ref({"orders": h}, "orders.count", "orders.status")
    assert build_cube_query(h, ref, [])["limit"] == 100


def test_build_query_unknown_member_raises():
    h = _cube_handle()
    ref = resolve_ref({"orders": h}, "orders.count", "orders.status")
    # corrupt the catalogue so the by-dim no longer resolves
    h.cube_meta["members"].pop("orders.status")
    with pytest.raises(ValueError):
        build_cube_query(h, ref, [])


def test_build_query_values_are_data_not_escaped():
    # A hostile-looking value flows through verbatim as JSON data — there's nothing
    # to escape because it never becomes a query string.
    h = _cube_handle()
    ref = resolve_ref({"orders": h}, "orders.count", "orders.status")
    q = build_cube_query(h, ref, build_filters(h, {"status": '"); DROP'}))
    assert q["filters"][0]["values"] == ['"); DROP']


# --------------------------------------------------------------------------- #
# build_semantic_spec (Cube branch) — end to end via run_python_query
# --------------------------------------------------------------------------- #


def test_build_spec_executes_and_renames_via_annotation():
    # The Cube backend *captures* the connectors dict at build_spec time (the
    # IbisBackend idiom — in production it's the same proj.connectors object the data
    # API later runs with), so the same connector must back both calls.
    h = _cube_handle()
    models = {"orders": h}
    ref = resolve_ref(models, "orders.count", "orders.status")
    conn = _FakeCube(
        META,
        load_payload={
            "data": [
                {"orders.status": "shipped", "orders.count": 10},
                {"orders.status": "new", "orders.count": 5},
            ],
            "annotation": {
                "dimensions": {"orders.status": {"title": "Status"}},
                "measures": {"orders.count": {"title": "Count"}},
            },
        },
    )
    spec = build_semantic_spec(models, ref, {"cube": conn})
    assert isinstance(spec, PythonQuerySpec)
    assert spec.connector == "cube"
    result = run_python_query(spec, {"status": "shipped"}, {"cube": conn})
    # Cube member keys are renamed to the canonical [by, *metrics] the chart reads.
    assert result.columns == ["orders.status", "orders.count"]
    assert result.rows == [["shipped", 10], ["new", 5]]
    # The live filter compiled into the JSON query that hit the connector.
    assert conn.queries[0]["filters"] == [
        {"member": "orders.status", "operator": "equals", "values": ["shipped"]}
    ]


def test_build_spec_renames_time_granularity_column():
    h = _cube_handle({"dimensions": {"month": {"member": "orders.createdAt", "granularity": "month"}}})
    models = {"orders": h}
    ref = resolve_ref(models, "orders.count", "orders.month")
    conn = _FakeCube(
        META,
        load_payload={
            "data": [{"orders.createdAt.month": "2024-01", "orders.count": 7}],
            "annotation": {
                "timeDimensions": {"orders.createdAt.month": {"title": "Created at"}},
                "measures": {"orders.count": {"title": "Count"}},
            },
        },
    )
    spec = build_semantic_spec(models, ref, {"cube": conn})
    result = run_python_query(spec, {}, {"cube": conn})
    # `by={orders.month}` resolves to the alias's canonical name `month`.
    assert result.columns == ["month", "orders.count"]
    assert result.rows == [["2024-01", 7]]


def test_build_spec_positional_fallback_when_annotation_missing():
    h = _cube_handle()
    models = {"orders": h}
    ref = resolve_ref(models, "orders.count", "orders.status")
    # No annotation, but the row width matches the canonical column count -> rename
    # positionally over the row's own keys.
    conn = _FakeCube(META, load_payload={"data": [{"x": "shipped", "y": 3}], "annotation": {}})
    spec = build_semantic_spec(models, ref, {"cube": conn})
    result = run_python_query(spec, {}, {"cube": conn})
    assert result.columns == ["orders.status", "orders.count"]
    assert result.rows == [["shipped", 3]]


def test_build_spec_empty_data_keeps_canonical_columns():
    h = _cube_handle()
    models = {"orders": h}
    ref = resolve_ref(models, "orders.count", "orders.status")
    conn = _FakeCube(
        META,
        load_payload={"data": [], "annotation": {
            "dimensions": {"orders.status": {}}, "measures": {"orders.count": {}}}},
    )
    spec = build_semantic_spec(models, ref, {"cube": conn})
    result = run_python_query(spec, {}, {"cube": conn})
    assert result.columns == ["orders.status", "orders.count"]
    assert result.rows == []


# --------------------------------------------------------------------------- #
# No-substitute-params: the Cube path never reaches _substitute_params
# --------------------------------------------------------------------------- #


def test_cube_path_never_calls_substitute_params(monkeypatch):
    import dashdown.python_query as pq

    def _boom(*a, **k):  # pragma: no cover - must never run
        raise AssertionError("_substitute_params must not touch the Cube path")

    monkeypatch.setattr(pq, "_substitute_params", _boom)

    h = _cube_handle()
    models = {"orders": h}
    ref = resolve_ref(models, "orders.count", "orders.status")
    conn = _FakeCube(META, load_payload={
        "data": [{"orders.status": "x", "orders.count": 1}],
        "annotation": {"dimensions": {"orders.status": {}}, "measures": {"orders.count": {}}}})
    spec = build_semantic_spec(models, ref, {"cube": conn})
    # A param that *would* be dangerous if string-substituted is just data here.
    result = run_python_query(spec, {"status": "${evil} OR 1=1"}, {"cube": conn})
    assert result.rows == [["x", 1]]
    assert conn.queries[0]["filters"][0]["values"] == ["${evil} OR 1=1"]


# --------------------------------------------------------------------------- #
# Backend detection
# --------------------------------------------------------------------------- #


def test_detect_backend_for_cube():
    class CubeConnector:  # name is what _detect_backend keys off
        pass

    assert _detect_backend("cube", "c", {}) == "cube"
    assert _detect_backend("CUBE", "c", {}) == "cube"
    assert _detect_backend(None, "c", {"c": CubeConnector()}) == "cube"


# --------------------------------------------------------------------------- #
# The `cube` connector — JWT mint, security context, 401 retry, HTTP errors
# --------------------------------------------------------------------------- #


class _FakeResponse:
    def __init__(self, status_code, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload
        self.text = text

    def json(self):
        return self._payload


class _FakeHttpx:
    """Records requests and returns a queued sequence of responses."""

    def __init__(self, responses):
        self._responses = list(responses)
        self.calls: list[dict] = []

    def request(self, method, url, *, headers=None, json=None, timeout=None):
        self.calls.append(
            {"method": method, "url": url, "headers": headers, "json": json}
        )
        return self._responses.pop(0)


class _FakeJwt:
    """Records minted payloads; returns a distinct token string per mint."""

    def __init__(self):
        self.payloads: list[dict] = []

    def encode(self, payload, key, algorithm=None):
        self.payloads.append(payload)
        return f"TOKEN{len(self.payloads)}"


def _install_fakes(monkeypatch, *, responses):
    fake_httpx = _FakeHttpx(responses)
    fake_jwt = _FakeJwt()
    monkeypatch.setitem(sys.modules, "httpx", fake_httpx)
    monkeypatch.setitem(sys.modules, "jwt", fake_jwt)
    return fake_httpx, fake_jwt


def _connector(**cfg):
    from dashdown.data.cube_connector import CubeConnector

    base = {"url": "https://cube.example.com", "secret": "s3cret"}
    base.update(cfg)
    return CubeConnector("cube", base)


def test_connector_requires_url():
    from dashdown.data.cube_connector import CubeConnector

    with pytest.raises(ValueError):
        CubeConnector("cube", {"secret": "x"})


def test_connector_requires_a_credential():
    from dashdown.data.cube_connector import CubeConnector

    with pytest.raises(ValueError):
        CubeConnector("cube", {"url": "https://cube.example.com"})


def test_connector_env_secret_expansion(monkeypatch):
    monkeypatch.setenv("MY_CUBE_SECRET", "from-env")
    c = _connector(secret="${MY_CUBE_SECRET}")
    assert c._secret == "from-env"


def test_connector_missing_env_secret_raises(monkeypatch):
    monkeypatch.delenv("MISSING_CUBE", raising=False)
    with pytest.raises(ValueError):
        _connector(secret="${MISSING_CUBE}")


def test_connector_query_is_not_implemented():
    with pytest.raises(NotImplementedError):
        _connector().query("SELECT 1")


def test_jwt_payload_embeds_security_context():
    from dashdown.data.cube_connector import build_jwt_payload

    p = build_jwt_payload({"tenant_id": "acme"}, 100, 400)
    assert p == {"tenant_id": "acme", "iat": 100, "exp": 400}


def test_connector_meta_mints_token_with_security_context(monkeypatch):
    fake_httpx, fake_jwt = _install_fakes(
        monkeypatch, responses=[_FakeResponse(200, META)]
    )
    c = _connector(security_context={"tenant_id": "acme"})
    assert c.meta() == META
    # One mint, carrying the security context + a TTL window.
    assert len(fake_jwt.payloads) == 1
    payload = fake_jwt.payloads[0]
    assert payload["tenant_id"] == "acme"
    assert payload["exp"] > payload["iat"]
    # The minted token rode the Authorization header.
    assert fake_httpx.calls[0]["headers"]["Authorization"] == "TOKEN1"
    assert fake_httpx.calls[0]["url"].endswith("/cubejs-api/v1/meta")


def test_connector_load_posts_structured_query(monkeypatch):
    payload = {"data": [{"orders.count": 1}], "annotation": {}}
    fake_httpx, _ = _install_fakes(monkeypatch, responses=[_FakeResponse(200, payload)])
    c = _connector()
    out = c.load({"measures": ["orders.count"]})
    assert out == payload
    call = fake_httpx.calls[0]
    assert call["method"] == "POST" and call["url"].endswith("/load")
    assert call["json"] == {"query": {"measures": ["orders.count"]}}


def test_connector_401_remints_and_retries(monkeypatch):
    ok = {"data": [], "annotation": {}}
    fake_httpx, fake_jwt = _install_fakes(
        monkeypatch, responses=[_FakeResponse(401, text="expired"), _FakeResponse(200, ok)]
    )
    c = _connector()
    assert c.load({"measures": ["orders.count"]}) == ok
    # Re-minted once on the 401, retried with the fresh token.
    assert len(fake_jwt.payloads) == 2
    assert fake_httpx.calls[1]["headers"]["Authorization"] == "TOKEN2"


def test_connector_http_error_raises_runtimeerror(monkeypatch):
    _install_fakes(monkeypatch, responses=[_FakeResponse(500, text="boom")])
    c = _connector()
    with pytest.raises(RuntimeError):
        c.load({"measures": ["orders.count"]})


def test_connector_static_token_skips_minting(monkeypatch):
    fake_httpx, fake_jwt = _install_fakes(monkeypatch, responses=[_FakeResponse(200, META)])
    c = _connector(token="STATIC-JWT", secret=None)
    c.meta()
    assert fake_jwt.payloads == []  # never minted
    assert fake_httpx.calls[0]["headers"]["Authorization"] == "STATIC-JWT"


def test_connector_load_error_field_raises(monkeypatch):
    _install_fakes(monkeypatch, responses=[_FakeResponse(200, {"error": "boom"})])
    c = _connector()
    with pytest.raises(RuntimeError):
        c.load({"measures": ["orders.count"]})


# --------------------------------------------------------------------------- #
# The python_queries.enabled gate skips cube models too (project integration)
# --------------------------------------------------------------------------- #


def test_gate_disables_cube_semantic_models(tmp_path):
    from dashdown.project import load_project

    (tmp_path / "dashdown.yaml").write_text(
        "title: t\npython_queries:\n  enabled: false\n"
    )
    (tmp_path / "sources.yaml").write_text(
        "cube:\n  type: cube\n  url: https://cube.example.com\n  secret: s3cret\n"
    )
    sem = tmp_path / "semantic"
    sem.mkdir()
    (sem / "orders.yml").write_text("orders:\n  connector: cube\n")
    # No /meta call happens: the gate skips semantic loading entirely (so no httpx
    # is needed even though cube is unreachable).
    proj = load_project(tmp_path)
    assert proj.semantic_models == {}
