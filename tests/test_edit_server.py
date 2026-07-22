"""AI edit mode: endpoint security matrix + full run lifecycle through the app.

Drives the real stack — create_app(edit=runtime) + TestClient + the edit WS —
against a scripted fake agent (tests/fixtures/fake_agent.py), so the subprocess
spawn, stream normalization, snapshot diff, undo, cancel, timeout and
single-flight paths are all exercised end-to-end. The security tests lock the
arming/token/loopback/Host/Origin model the way TestSubstituteParams locks SQL
injection.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from dashdown.agent_presets import AgentPreset
from dashdown.edit import EditRuntime
from dashdown.edit_session import edit_hub
from dashdown.render import pipeline
from dashdown.server import create_app

FAKE_AGENT = str(Path(__file__).parent / "fixtures" / "fake_agent.py")

HOST_HEADERS = {"host": "127.0.0.1:8000"}


@pytest.fixture(autouse=True)
def _isolate():
    pipeline._query_def_cache.clear()
    pipeline._stream_def_cache.clear()
    pipeline._result_cache.clear()
    edit_hub.reset()
    yield
    pipeline._query_def_cache.clear()
    pipeline._stream_def_cache.clear()
    pipeline._result_cache.clear()
    edit_hub.reset()


class _ForceClient:
    """ASGI wrapper pinning scope['client'] — TestClient reports 'testclient',
    which the loopback guard rightly refuses; tests that should PASS the guard
    pretend to be 127.0.0.1."""

    def __init__(self, app, host="127.0.0.1"):
        self.app = app
        self.host = host

    async def __call__(self, scope, receive, send):
        if scope["type"] in ("http", "websocket"):
            scope = dict(scope)
            scope["client"] = (self.host, 54321)
        await self.app(scope, receive, send)


def _make_project(tmp: Path, extra_yaml: str = "") -> Path:
    (tmp / "pages").mkdir()
    (tmp / "pages" / "index.md").write_text(
        "# Home\n\n"
        ":::query name=sales connector=main\nSELECT * FROM sales\n:::\n\n"
        "<Table data={sales} />\n",
        encoding="utf-8",
    )
    (tmp / "data").mkdir()
    (tmp / "data" / "sales.csv").write_text("region,amount\nEast,1\n", encoding="utf-8")
    (tmp / "sources.yaml").write_text(
        "main:\n  type: csv\n  directory: data\n", encoding="utf-8"
    )
    (tmp / "dashdown.yaml").write_text("title: T\n" + extra_yaml, encoding="utf-8")
    return tmp


def _runtime(root: Path, scenario: str = "happy", **kw) -> EditRuntime:
    preset = AgentPreset(
        name="fake",
        summary="scripted fake agent",
        binary=sys.executable,
        install_hint="",
        argv=(sys.executable, FAKE_AGENT, scenario),
        prompt_via="stdin",
        parser="text",
    )
    defaults = dict(
        project_root=root.resolve(),
        preset=preset,
        probe="fake agent (tests)",
        token="test-token-1234567890-abcdefghijklmnop",
        timeout=30,
    )
    defaults.update(kw)
    return EditRuntime(**defaults)


def _client(root: Path, runtime: EditRuntime | None) -> TestClient:
    app = create_app(root, edit=runtime)
    return TestClient(_ForceClient(app))


def _auth_headers(runtime: EditRuntime) -> dict:
    return {**HOST_HEADERS, "X-Dashdown-Edit-Token": runtime.token}


def _drive_to_result(client: TestClient, runtime: EditRuntime, body: dict) -> tuple[list, dict]:
    """POST a run and read the WS until its result event; returns
    (all events, result event)."""
    with client.websocket_connect("/_dashdown/ws/edit", headers=HOST_HEADERS) as ws:
        ws.send_json({"token": runtime.token})
        hello = ws.receive_json()
        assert hello["protocol"] == "dashdown-edit.v1"
        resp = client.post(
            "/_dashdown/api/edit/run", json=body, headers=_auth_headers(runtime)
        )
        assert resp.status_code == 202, resp.text
        run_id = resp.json()["run_id"]
        events = []
        while True:
            envelope = ws.receive_json()
            if envelope.get("run_id") != run_id:
                continue
            events.append(envelope["event"])
            if envelope["event"]["type"] == "result":
                return events, envelope["event"]


# --------------------------------------------------------------------------- #
# Arming + request guards
# --------------------------------------------------------------------------- #
class TestEditSecurity:
    def test_unarmed_server_has_no_edit_routes(self, tmp_path):
        client = _client(_make_project(tmp_path), None)
        assert client.get("/_dashdown/api/edit/state", headers=HOST_HEADERS).status_code == 404
        # POST paths fall through to the GET-only page catch-all → 405; either
        # way the route does not exist when unarmed.
        assert client.post(
            "/_dashdown/api/edit/run", headers=HOST_HEADERS
        ).status_code in (404, 405)

    def test_edit_with_prod_posture_refuses(self, tmp_path):
        root = _make_project(tmp_path)
        with pytest.raises(ValueError, match="dev-server"):
            create_app(root, dev=False, edit=_runtime(root))

    def test_missing_token_403(self, tmp_path):
        root = _make_project(tmp_path)
        client = _client(root, _runtime(root))
        resp = client.get("/_dashdown/api/edit/state", headers=HOST_HEADERS)
        assert resp.status_code == 403
        assert "token" in resp.json()["detail"]

    def test_wrong_token_403(self, tmp_path):
        root = _make_project(tmp_path)
        client = _client(root, _runtime(root))
        resp = client.get(
            "/_dashdown/api/edit/state",
            headers={**HOST_HEADERS, "X-Dashdown-Edit-Token": "wrong"},
        )
        assert resp.status_code == 403

    def test_non_loopback_peer_403(self, tmp_path):
        root = _make_project(tmp_path)
        runtime = _runtime(root)
        app = create_app(root, edit=runtime)
        client = TestClient(_ForceClient(app, host="10.1.2.3"))
        resp = client.get("/_dashdown/api/edit/state", headers=_auth_headers(runtime))
        assert resp.status_code == 403
        assert "loopback" in resp.json()["detail"]

    def test_bad_host_header_403(self, tmp_path):
        """DNS rebinding: evil.example resolves to 127.0.0.1 — the Host header
        betrays it even though the peer is loopback."""
        root = _make_project(tmp_path)
        runtime = _runtime(root)
        client = _client(root, runtime)
        resp = client.get(
            "/_dashdown/api/edit/state",
            headers={"host": "evil.example", "X-Dashdown-Edit-Token": runtime.token},
        )
        assert resp.status_code == 403
        assert "Host" in resp.json()["detail"]

    def test_cross_origin_403(self, tmp_path):
        root = _make_project(tmp_path)
        runtime = _runtime(root)
        client = _client(root, runtime)
        resp = client.get(
            "/_dashdown/api/edit/state",
            headers={**_auth_headers(runtime), "origin": "https://evil.example"},
        )
        assert resp.status_code == 403

    def test_localhost_origin_allowed(self, tmp_path):
        root = _make_project(tmp_path)
        runtime = _runtime(root)
        client = _client(root, runtime)
        resp = client.get(
            "/_dashdown/api/edit/state",
            headers={**_auth_headers(runtime), "origin": "http://localhost:8000"},
        )
        assert resp.status_code == 200

    def test_ws_bad_token_closes_1008(self, tmp_path):
        root = _make_project(tmp_path)
        client = _client(root, _runtime(root))
        with client.websocket_connect("/_dashdown/ws/edit", headers=HOST_HEADERS) as ws:
            ws.send_json({"token": "wrong"})
            with pytest.raises(WebSocketDisconnect) as exc:
                ws.receive_json()
            assert exc.value.code == 1008

    def test_ws_bad_host_refused(self, tmp_path):
        root = _make_project(tmp_path)
        client = _client(root, _runtime(root))
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect(
                "/_dashdown/ws/edit", headers={"host": "evil.example"}
            ) as ws:
                ws.receive_json()

    def test_auth_guard_covers_edit_endpoints(self, tmp_path):
        root = _make_project(
            tmp_path,
            "auth:\n  type: basic\n  users:\n    admin: secret\n",
        )
        runtime = _runtime(root)
        client = _client(root, runtime)
        # No credentials → the ordinary auth middleware 401s first.
        resp = client.get("/_dashdown/api/edit/state", headers=_auth_headers(runtime))
        assert resp.status_code == 401
        # Credentials + token → allowed.
        resp = client.get(
            "/_dashdown/api/edit/state",
            headers=_auth_headers(runtime),
            auth=("admin", "secret"),
        )
        assert resp.status_code == 200

    def test_page_carries_token_only_in_full_shell(self, tmp_path):
        root = _make_project(tmp_path, "embed:\n  enabled: true\n")
        runtime = _runtime(root)
        client = _client(root, runtime)
        full = client.get("/", headers=HOST_HEADERS)
        assert runtime.token in full.text
        assert "dashdown-edit" in full.text
        embedded = client.get("/?_embed=1", headers=HOST_HEADERS)
        assert runtime.token not in embedded.text
        assert 'id="dashdown-edit"' not in embedded.text

    def test_unarmed_page_has_no_edit_script(self, tmp_path):
        client = _client(_make_project(tmp_path), None)
        resp = client.get("/", headers=HOST_HEADERS)
        assert 'id="dashdown-edit"' not in resp.text

    def test_unavailable_agent_503_on_run(self, tmp_path):
        root = _make_project(tmp_path)
        runtime = _runtime(root, preset=None, probe="nothing installed")
        client = _client(root, runtime)
        resp = client.post(
            "/_dashdown/api/edit/run",
            json={"prompt": "x"},
            headers=_auth_headers(runtime),
        )
        assert resp.status_code == 503
        assert "nothing installed" in resp.json()["detail"]


# --------------------------------------------------------------------------- #
# Run lifecycle (through the fake agent)
# --------------------------------------------------------------------------- #
class TestEditRuns:
    def test_happy_path_events_diff_and_verify(self, tmp_path):
        root = _make_project(tmp_path)
        runtime = _runtime(root)
        client = _client(root, runtime)
        with client:
            events, result = _drive_to_result(
                client,
                runtime,
                {"prompt": "add a note", "page": "/", "params": {"region": "East"}},
            )

        texts = [e["text"] for e in events if e["type"] == "text"]
        prompt_echo = next(t for t in texts if t.startswith("PROMPT:"))
        # The context preamble reached the agent: page file, params, guide, loop.
        assert "pages/index.md" in prompt_echo
        assert "region=East" in prompt_echo
        assert "AGENTS.md" in prompt_echo
        assert "add a note" in prompt_echo

        assert result["ok"] is True
        assert result["changed_files"] == ["pages/index.md"]
        assert result["created_files"] == ["pages/new.md"]
        assert result["config_changed"] is False
        assert result["verify"] == {"ok": True}
        assert result["undo_available"] is True
        # The agent's edits actually landed.
        assert "Edited by the fake agent" in (root / "pages" / "index.md").read_text()
        # Audit log recorded the run.
        assert (root / ".dashdown" / "edit-log.jsonl").is_file()

    def test_undo_restores_and_deletes(self, tmp_path):
        root = _make_project(tmp_path)
        runtime = _runtime(root)
        client = _client(root, runtime)
        before = (root / "pages" / "index.md").read_text()
        with client:
            _events, _result = _drive_to_result(client, runtime, {"prompt": "x"})
            run_id = edit_hub.current.run_id
            resp = client.post(
                "/_dashdown/api/edit/undo",
                json={"run_id": run_id},
                headers=_auth_headers(runtime),
            )
        assert resp.status_code == 200, resp.text
        assert resp.json()["restored"] == ["pages/index.md"]
        assert resp.json()["deleted"] == ["pages/new.md"]
        assert (root / "pages" / "index.md").read_text() == before
        assert not (root / "pages" / "new.md").exists()

    def test_failed_agent_reports_stderr_tail(self, tmp_path):
        root = _make_project(tmp_path)
        runtime = _runtime(root, scenario="fail")
        client = _client(root, runtime)
        with client:
            _events, result = _drive_to_result(client, runtime, {"prompt": "x"})
        assert result["ok"] is False
        assert result["exit_code"] == 3
        assert "boom" in result.get("stderr_tail", "")

    def test_cancel_kills_the_run(self, tmp_path):
        root = _make_project(tmp_path)
        runtime = _runtime(root, scenario="sleep")
        client = _client(root, runtime)
        with client:
            with client.websocket_connect(
                "/_dashdown/ws/edit", headers=HOST_HEADERS
            ) as ws:
                ws.send_json({"token": runtime.token})
                ws.receive_json()  # hello
                resp = client.post(
                    "/_dashdown/api/edit/run",
                    json={"prompt": "x"},
                    headers=_auth_headers(runtime),
                )
                run_id = resp.json()["run_id"]
                # Wait until the agent is demonstrably running…
                while True:
                    env = ws.receive_json()
                    if env["event"].get("text") == "sleeping":
                        break
                # …then stop it.
                client.post(
                    "/_dashdown/api/edit/cancel",
                    json={"run_id": run_id},
                    headers=_auth_headers(runtime),
                )
                while True:
                    env = ws.receive_json()
                    if env["event"]["type"] == "result":
                        assert env["event"]["state"] == "cancelled"
                        break

    def test_timeout_kills_the_run(self, tmp_path):
        root = _make_project(tmp_path)
        runtime = _runtime(root, scenario="sleep", timeout=1)
        client = _client(root, runtime)
        with client:
            _events, result = _drive_to_result(client, runtime, {"prompt": "x"})
        assert result["state"] == "timeout"
        assert "timed out" in result.get("reason", "")

    def test_single_flight_409_without_prompt_leak(self, tmp_path):
        root = _make_project(tmp_path)
        runtime = _runtime(root, scenario="sleep")
        client = _client(root, runtime)
        with client:
            first = client.post(
                "/_dashdown/api/edit/run",
                json={"prompt": "the secret plan"},
                headers=_auth_headers(runtime),
            )
            assert first.status_code == 202
            second = client.post(
                "/_dashdown/api/edit/run",
                json={"prompt": "another"},
                headers=_auth_headers(runtime),
            )
            assert second.status_code == 409
            body = second.json()
            assert body["run_id"] == first.json()["run_id"]
            assert "the secret plan" not in second.text
            client.post(
                "/_dashdown/api/edit/cancel",
                json={"run_id": first.json()["run_id"]},
                headers=_auth_headers(runtime),
            )

    def test_undo_refused_while_active(self, tmp_path):
        root = _make_project(tmp_path)
        runtime = _runtime(root, scenario="sleep")
        client = _client(root, runtime)
        with client:
            resp = client.post(
                "/_dashdown/api/edit/run",
                json={"prompt": "x"},
                headers=_auth_headers(runtime),
            )
            run_id = resp.json()["run_id"]
            undo = client.post(
                "/_dashdown/api/edit/undo",
                json={"run_id": run_id},
                headers=_auth_headers(runtime),
            )
            assert undo.status_code == 409
            client.post(
                "/_dashdown/api/edit/cancel",
                json={"run_id": run_id},
                headers=_auth_headers(runtime),
            )

    def test_ring_buffer_caps_replay(self, tmp_path):
        root = _make_project(tmp_path)
        runtime = _runtime(root, scenario="noisy", max_events=50)
        client = _client(root, runtime)
        with client:
            _events, result = _drive_to_result(client, runtime, {"prompt": "x"})
            assert result["truncated"] is True
            # A late subscriber replays at most the capped buffer.
            with client.websocket_connect(
                "/_dashdown/ws/edit", headers=HOST_HEADERS
            ) as ws:
                ws.send_json({"token": runtime.token})
                hello = ws.receive_json()
                assert hello["active"] is None
                replayed = 0
                while True:
                    env = ws.receive_json()
                    replayed += 1
                    if env["event"]["type"] == "result":
                        break
                assert replayed <= 50

    def test_state_reports_last_run(self, tmp_path):
        root = _make_project(tmp_path)
        runtime = _runtime(root)
        client = _client(root, runtime)
        with client:
            _events, _result = _drive_to_result(client, runtime, {"prompt": "x"})
            resp = client.get(
                "/_dashdown/api/edit/state", headers=_auth_headers(runtime)
            )
        body = resp.json()
        assert body["agent"]["name"] == "fake"
        assert body["active"] is None
        assert body["last"]["state"] == "done"
