"""Anonymous usage telemetry: opt-out chain, install-id, first-run notice, and
the capture path (anonymous payload, no-ops, throttle, error-swallowing).

Every test is isolated via ``DASHDOWN_TELEMETRY_STATE`` (a tmp state file) and a
cleared telemetry env, and the network is never touched — ``_send`` /
``requests.post`` are monkeypatched."""
from __future__ import annotations

import platform

import pytest

from dashdown import telemetry

_ENV_VARS = (
    "DO_NOT_TRACK",
    "DASHDOWN_TELEMETRY",
    "DASHDOWN_TELEMETRY_KEY",
    "DASHDOWN_TELEMETRY_HOST",
)


@pytest.fixture
def tele(tmp_path, monkeypatch):
    """Isolate the state file and clear all telemetry env vars."""
    monkeypatch.setenv("DASHDOWN_TELEMETRY_STATE", str(tmp_path / "telemetry.json"))
    for var in _ENV_VARS:
        monkeypatch.delenv(var, raising=False)
    return telemetry


@pytest.fixture
def sent(tele, monkeypatch):
    """Capture payloads handed to ``_send`` instead of sending them."""
    recorded: list[dict] = []
    monkeypatch.setattr(tele, "_send", recorded.append)
    return recorded


def _project(tmp_path, yaml_text: str):
    (tmp_path / "dashdown.yaml").write_text(yaml_text, encoding="utf-8")
    return tmp_path


# --- install id -------------------------------------------------------------
def test_install_id_is_generated_and_stable(tele):
    first = tele._install_id()
    assert first
    assert tele._install_id() == first  # persisted, stable across calls
    assert tele._state_path().is_file()


# --- opt-out chain ----------------------------------------------------------
def test_enabled_by_default(tele):
    assert tele.is_enabled() is True
    assert tele.disabled_reason() is None


def test_do_not_track_opts_out(tele, monkeypatch):
    monkeypatch.setenv("DO_NOT_TRACK", "1")
    assert tele.is_enabled() is False
    assert "DO_NOT_TRACK" in tele.disabled_reason()


@pytest.mark.parametrize("value", ["0", "false", "off", "no"])
def test_dashdown_telemetry_env_opts_out(tele, monkeypatch, value):
    monkeypatch.setenv("DASHDOWN_TELEMETRY", value)
    assert tele.is_enabled() is False


def test_dashdown_telemetry_env_truthy_stays_on(tele, monkeypatch):
    monkeypatch.setenv("DASHDOWN_TELEMETRY", "1")
    assert tele.is_enabled() is True


def test_state_off_then_on(tele):
    tele.set_enabled(False)
    assert tele.is_enabled() is False
    assert "telemetry off" in tele.disabled_reason()
    tele.set_enabled(True)
    assert tele.is_enabled() is True


def test_project_yaml_opt_out(tele, tmp_path):
    proj = _project(tmp_path, "title: X\ntelemetry:\n  enabled: false\n")
    assert tele.is_enabled(project_path=proj) is False
    assert "dashdown.yaml" in tele.disabled_reason(project_path=proj)
    # the opt-out is scoped to the project — absent a path, telemetry stays on
    assert tele.is_enabled() is True


def test_project_yaml_enabled_true_stays_on(tele, tmp_path):
    proj = _project(tmp_path, "title: X\ntelemetry:\n  enabled: true\n")
    assert tele.is_enabled(project_path=proj) is True


def test_project_yaml_without_block_stays_on(tele, tmp_path):
    proj = _project(tmp_path, "title: X\n")
    assert tele.is_enabled(project_path=proj) is True


def test_malformed_project_yaml_is_lenient(tele, tmp_path):
    # A telemetry config quirk must never disable a command by erroring.
    proj = _project(tmp_path, "title: X\ntelemetry: not-a-mapping\n: : :\n")
    assert tele.is_enabled(project_path=proj) is True


# --- first-run notice -------------------------------------------------------
def test_first_run_notice_prints_once(tele, capsys):
    tele.maybe_print_first_run_notice()
    first = capsys.readouterr()
    assert "anonymous usage" in first.err.lower()
    assert "dashdown telemetry off" in first.err

    tele.maybe_print_first_run_notice()
    assert capsys.readouterr().err == ""  # only once, ever


def test_first_run_notice_suppressed_when_opted_out(tele, capsys, monkeypatch):
    monkeypatch.setenv("DO_NOT_TRACK", "1")
    tele.maybe_print_first_run_notice()
    assert capsys.readouterr().err == ""
    # and we did not burn the one-time flag, so it can show if they later opt back in
    assert tele._load_state().get("first_run_done") is not True


# --- capture ----------------------------------------------------------------
def test_capture_noop_without_real_key(tele, sent, monkeypatch):
    # The dormancy mechanism: a blanked / "phc_REPLACE…" key ⇒ no-op, regardless of
    # the real key now baked into _DEFAULT_PROJECT_KEY.
    monkeypatch.setattr(tele, "_DEFAULT_PROJECT_KEY", "phc_REPLACE_ME")
    tele.capture("cli_serve")
    assert sent == []


def test_capture_sends_anonymous_payload(tele, sent, monkeypatch):
    monkeypatch.setenv("DASHDOWN_TELEMETRY_KEY", "phc_test")
    tele.capture("cli_serve", {"extra": "x"})

    assert len(sent) == 1
    payload = sent[0]
    assert payload["api_key"] == "phc_test"
    assert payload["event"] == "cli_serve"
    assert payload["distinct_id"] == tele._install_id()

    props = payload["properties"]
    assert props["$process_person_profile"] is False  # anonymous, no PII
    assert props["dashdown_version"]
    assert props["os"] == platform.system()
    assert props["arch"] == platform.machine()
    assert props["extra"] == "x"


def test_capture_noop_when_disabled(tele, sent, monkeypatch):
    monkeypatch.setenv("DASHDOWN_TELEMETRY_KEY", "phc_test")
    monkeypatch.setenv("DO_NOT_TRACK", "1")
    tele.capture("cli_serve")
    assert sent == []


def test_capture_respects_project_opt_out(tele, sent, tmp_path, monkeypatch):
    monkeypatch.setenv("DASHDOWN_TELEMETRY_KEY", "phc_test")
    proj = _project(tmp_path, "title: X\ntelemetry:\n  enabled: false\n")
    tele.capture("cli_serve", project_path=proj)
    assert sent == []


def test_capture_is_throttled(tele, sent, monkeypatch):
    monkeypatch.setenv("DASHDOWN_TELEMETRY_KEY", "phc_test")
    tele.capture("cli_serve")
    tele.capture("cli_serve")  # within 24h ⇒ collapsed
    assert len(sent) == 1
    # a *different* event is not throttled by the first
    tele.capture("cli_build")
    assert len(sent) == 2


def test_post_payload_swallows_network_errors(tele, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("network down")

    monkeypatch.setattr(tele.requests, "post", boom)
    # must not raise — telemetry can never break a command
    tele._post_payload({"event": "cli_serve"})
