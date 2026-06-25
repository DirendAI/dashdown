"""Tests for real-time data streaming (Stage 10a).

Layers: ``:::query … live interval=N`` parsing, the live-query registry +
interval flooring, the payload digest used for change-detection, the
``live``/``interval`` keys surfaced into the client query_defs, and the
``/_dashdown/ws/data/{query_name}`` WebSocket endpoint driven through
``TestClient.websocket_connect`` against a fake connector (happy path, param/
connector parity, not-live + unknown refusal, auth, and push-on-change).
"""
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from dashdown.data.base import QueryResult
from dashdown.render import pipeline
from dashdown.render.markdown import parse_markdown
from dashdown.render.pipeline import (
    DEFAULT_STREAM_INTERVAL,
    MIN_STREAM_INTERVAL,
    get_stream_interval,
    payload_digest,
    register_query_def,
    render_page,
)
from dashdown.server import create_app
from dashdown.streaming import hub as stream_hub


@pytest.fixture(autouse=True)
def _clear_caches():
    """Query defs / stream registry / result cache / pollers are module-global."""
    pipeline._query_def_cache.clear()
    pipeline._stream_def_cache.clear()
    pipeline._result_cache.clear()
    stream_hub.reset()
    yield
    pipeline._query_def_cache.clear()
    pipeline._stream_def_cache.clear()
    pipeline._result_cache.clear()
    stream_hub.reset()


# --------------------------------------------------------------------------- #
# Fake connectors (duck-typed: the endpoint only calls .query())
# --------------------------------------------------------------------------- #
class EchoConnector:
    """Returns the (already param-substituted) SQL it was handed, as one cell."""

    def query(self, sql: str) -> QueryResult:
        return QueryResult(columns=["sql"], rows=[[sql]])

    def close(self):  # pragma: no cover - parity with the Connector ABC
        pass


class CountingConnector:
    """Returns a different result on every call, to exercise push-on-change."""

    def __init__(self):
        self.n = 0

    def query(self, sql: str) -> QueryResult:
        self.n += 1
        return QueryResult(columns=["n"], rows=[[self.n]])

    def close(self):  # pragma: no cover
        pass


class FlakyConnector:
    """Raises on the first call (e.g. a rate-limited API), succeeds after."""

    def __init__(self):
        self.n = 0

    def query(self, sql: str) -> QueryResult:
        self.n += 1
        if self.n == 1:
            raise ValueError("Malformed JSON in file")
        return QueryResult(columns=["ok"], rows=[[1]])

    def close(self):  # pragma: no cover
        pass


# --------------------------------------------------------------------------- #
# :::query live / interval parsing
# --------------------------------------------------------------------------- #
class TestQueryStreamParsing:
    def test_not_live_by_default(self):
        _, specs, _ = parse_markdown(":::query name=q\nSELECT 1\n:::")
        assert specs[0].live is False
        assert specs[0].interval is None

    def test_live_flag(self):
        _, specs, _ = parse_markdown(":::query name=q live\nSELECT 1\n:::")
        assert specs[0].live is True
        assert specs[0].interval is None

    def test_live_with_interval(self):
        _, specs, _ = parse_markdown(":::query name=q live interval=3\nSELECT 1\n:::")
        assert specs[0].live is True
        assert specs[0].interval == 3

    def test_interval_without_live_is_inert(self):
        # interval alone doesn't opt a query into streaming.
        _, specs, _ = parse_markdown(":::query name=q interval=3\nSELECT 1\n:::")
        assert specs[0].live is False
        assert specs[0].interval == 3


# --------------------------------------------------------------------------- #
# stream registry + interval flooring + digest
# --------------------------------------------------------------------------- #
class TestStreamRegistry:
    def test_non_live_not_registered(self):
        register_query_def("q", "main", "SELECT 1", {}, live=False)
        assert get_stream_interval("q", "main") is None

    def test_live_default_interval(self):
        register_query_def("q", "main", "SELECT 1", {}, live=True)
        assert get_stream_interval("q", "main") == DEFAULT_STREAM_INTERVAL

    def test_explicit_interval(self):
        register_query_def("q", "main", "SELECT 1", {}, live=True, interval=12)
        assert get_stream_interval("q", "main") == 12

    def test_interval_floored(self):
        register_query_def("q", "main", "SELECT 1", {}, live=True, interval=0)
        assert get_stream_interval("q", "main") == MIN_STREAM_INTERVAL

    def test_re_register_non_live_clears_stream(self):
        register_query_def("q", "main", "SELECT 1", {}, live=True, interval=4)
        assert get_stream_interval("q", "main") == 4
        register_query_def("q", "main", "SELECT 1", {}, live=False)
        assert get_stream_interval("q", "main") is None


class TestPayloadDigest:
    def test_stable_for_same_payload(self):
        p = {"columns": ["a"], "rows": [[1], [2]], "query": "q"}
        assert payload_digest(p) == payload_digest({**p})

    def test_changes_when_rows_change(self):
        a = {"columns": ["a"], "rows": [[1]], "query": "q"}
        b = {"columns": ["a"], "rows": [[2]], "query": "q"}
        assert payload_digest(a) != payload_digest(b)


# --------------------------------------------------------------------------- #
# query_defs surfacing (client payload)
# --------------------------------------------------------------------------- #
class TestQueryDefsSurfacing:
    SOURCE = ":::query name=live_q connector=main live interval=7\nSELECT 1\n:::\n\n# Page\n"

    def test_live_and_interval_emitted(self):
        page = render_page(self.SOURCE, {})
        d = page.query_defs["live_q"]
        assert d["live"] is True
        assert d["interval"] == 7

    def test_omitted_for_non_live(self):
        page = render_page(":::query name=q\nSELECT 1\n:::\n\n# P\n", {})
        assert "live" not in page.query_defs["q"]

    def test_omitted_in_static_build(self):
        # No server to stream from in a static export.
        page = render_page(self.SOURCE, {}, static_build=True)
        assert "live" not in page.query_defs["live_q"]


# --------------------------------------------------------------------------- #
# WebSocket endpoint integration
# --------------------------------------------------------------------------- #
def _make_project(tmp: Path, auth_yaml: str = "") -> Path:
    (tmp / "pages").mkdir()
    (tmp / "pages" / "index.md").write_text("# Home\n", encoding="utf-8")
    (tmp / "dashdown.yaml").write_text(
        "title: Test\ntheme: light\n" + auth_yaml, encoding="utf-8"
    )
    return tmp


@pytest.fixture
def tmp_project():
    with tempfile.TemporaryDirectory() as d:
        yield Path(d)


def _app_with(tmp, connector, *, auth_yaml="", **regkw):
    """Build an app, inject a fake connector named 'fake', register 'live_q'."""
    app = create_app(_make_project(tmp, auth_yaml))
    app.state.project.connectors["fake"] = connector
    register_query_def("live_q", "fake", regkw.pop("sql", "SELECT 1"), {}, **regkw)
    return app


class TestStreamEndpoint:
    URL = "/_dashdown/ws/data/live_q?_connector=fake"

    def test_initial_payload(self, tmp_project):
        app = _app_with(tmp_project, EchoConnector(), live=True, interval=1)
        client = TestClient(app)
        with client.websocket_connect(self.URL) as ws:
            msg = ws.receive_json()
        assert msg["query"] == "live_q"
        assert msg["columns"] == ["sql"]
        # The connector echoes the substituted SQL — the registered query ran.
        assert msg["rows"][0][0] == "SELECT 1"

    def test_param_substitution(self, tmp_project):
        app = _app_with(
            tmp_project,
            EchoConnector(),
            live=True,
            interval=1,
            sql="SELECT * WHERE region = ${region}",
        )
        client = TestClient(app)
        with client.websocket_connect(self.URL + "&region=East") as ws:
            msg = ws.receive_json()
        # Value is wrapped + quoted by _substitute_params, inert against injection.
        assert "'East'" in msg["rows"][0][0]

    def test_not_live_query_refused(self, tmp_project):
        # Registered, but NOT live → endpoint must refuse to stream it.
        app = _app_with(tmp_project, EchoConnector(), live=False)
        client = TestClient(app)
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect(self.URL) as ws:
                ws.receive_json()

    def test_unknown_query_refused(self, tmp_project):
        app = _app_with(tmp_project, EchoConnector(), live=True, interval=1)
        client = TestClient(app)
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect(
                "/_dashdown/ws/data/nope?_connector=fake"
            ) as ws:
                ws.receive_json()

    def test_push_on_change(self, tmp_project):
        app = _app_with(tmp_project, CountingConnector(), live=True, interval=1)
        client = TestClient(app)
        with client.websocket_connect(self.URL) as ws:
            first = ws.receive_json()
            second = ws.receive_json()  # ~1s later, after the next poll
        assert first["rows"][0][0] != second["rows"][0][0]

    def test_transient_error_then_recovers(self, tmp_project):
        # A failing poll (e.g. a rate-limited API) must NOT kill the socket: the
        # client gets an error frame, then the next tick recovers with data.
        app = _app_with(tmp_project, FlakyConnector(), live=True, interval=1)
        client = TestClient(app)
        with client.websocket_connect(self.URL) as ws:
            err = ws.receive_json()
            assert "error" in err and "Malformed JSON" in err["error"]
            recovered = ws.receive_json()  # next poll succeeds
            assert recovered["rows"] == [[1]]

    def test_fanout_one_poller_shared(self, tmp_project):
        # Two viewers of the same live query share ONE poll loop: they see
        # identical data and the connector is queried once, not twice.
        conn = CountingConnector()
        app = _app_with(tmp_project, conn, live=True, interval=3)  # slow: 1 poll
        client = TestClient(app)
        with client.websocket_connect(self.URL) as ws1, client.websocket_connect(
            self.URL
        ) as ws2:
            a = ws1.receive_json()
            b = ws2.receive_json()
            assert a["rows"] == b["rows"]  # same shared snapshot
            assert stream_hub.active == 1  # a single poller for both
        assert conn.n == 1  # one query served both viewers, not two

    def test_distinct_params_get_distinct_pollers(self, tmp_project):
        # Different filter params → different keys → separate pollers.
        app = _app_with(
            tmp_project,
            EchoConnector(),
            live=True,
            interval=3,
            sql="SELECT * WHERE region = ${region}",
        )
        client = TestClient(app)
        with client.websocket_connect(self.URL + "&region=East") as ws1, (
            client.websocket_connect(self.URL + "&region=West")
        ) as ws2:
            ws1.receive_json()
            ws2.receive_json()
            assert stream_hub.active == 2


class TestStreamAuth:
    AUTH = "auth:\n  type: api_key\n  header: X-API-Key\n  key: tok-123\n"
    URL = "/_dashdown/ws/data/live_q?_connector=fake"

    def test_unauthorized_socket_rejected(self, tmp_project):
        # The HTTP auth middleware doesn't cover WS — the endpoint must guard.
        app = _app_with(
            tmp_project, EchoConnector(), auth_yaml=self.AUTH, live=True, interval=1
        )
        client = TestClient(app)
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect(self.URL) as ws:
                ws.receive_json()

    def test_authorized_socket_streams(self, tmp_project):
        app = _app_with(
            tmp_project, EchoConnector(), auth_yaml=self.AUTH, live=True, interval=1
        )
        client = TestClient(app)
        with client.websocket_connect(
            self.URL, headers={"X-API-Key": "tok-123"}
        ) as ws:
            msg = ws.receive_json()
        assert msg["query"] == "live_q"
