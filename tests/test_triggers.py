"""Tests for triggers + actions (the Push surface, backlog B4).

Layers, bottom-up:

- the condition grammar (``parse_condition``) — every operator, both subjects,
  and malformed strings raising (never ``eval``);
- ``evaluate`` edge cases — value/rows breach, empty + non-numeric results;
- ``load_triggers`` — happy path, malformed YAML, unknown action type, ``${ENV}``
  expansion, enabled/interval defaults, min-interval rejection;
- the ``webhook`` / ``slack`` actions over a monkeypatched ``urlopen``;
- ``TriggerRunner._handle_frame`` transition/cooldown logic, driven with an
  injected clock (no real sleeps);
- an end-to-end run: a tmp CSV project + a trigger firing a recording ``FakeAction``
  through the real poll loop under ``TestClient``'s lifespan.
"""
import json
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from dashdown.actions import (
    Action,
    build_action,
    format_slack_message,
    register_action,
)
from dashdown.data.base import QueryResult
from dashdown.render import pipeline
from dashdown.server import create_app
from dashdown.streaming import hub as stream_hub
from dashdown.triggers import (
    TriggerRunner,
    TriggerSpec,
    _TriggerState,
    evaluate,
    load_triggers,
    parse_condition,
    parse_trigger,
)


# --------------------------------------------------------------------------- #
# A recording action, registered once for the integration test.
# --------------------------------------------------------------------------- #
_RECORDED: list[dict] = []


@register_action("fake")
class FakeAction(Action):
    def fire(self, event: dict) -> None:
        _RECORDED.append(event)


@pytest.fixture(autouse=True)
def _clear_caches():
    """Query defs / stream registry / result cache / pollers are module-global."""
    pipeline._query_def_cache.clear()
    pipeline._stream_def_cache.clear()
    pipeline._result_cache.clear()
    stream_hub.reset()
    _RECORDED.clear()
    yield
    pipeline._query_def_cache.clear()
    pipeline._stream_def_cache.clear()
    pipeline._result_cache.clear()
    stream_hub.reset()
    _RECORDED.clear()


# --------------------------------------------------------------------------- #
# Condition grammar
# --------------------------------------------------------------------------- #
class TestParseCondition:
    @pytest.mark.parametrize(
        "op,expected",
        [("<", "<"), ("<=", "<="), (">", ">"), (">=", ">="), ("==", "=="), ("!=", "!=")],
    )
    def test_all_operators(self, op, expected):
        c = parse_condition(f"value {op} 5")
        assert c.subject == "value"
        assert c.op == expected
        assert c.threshold == 5.0

    def test_rows_subject(self):
        c = parse_condition("rows >= 10")
        assert c.subject == "rows"
        assert c.op == ">="
        assert c.threshold == 10.0

    def test_float_threshold(self):
        c = parse_condition("value < 0.12")
        assert c.threshold == pytest.approx(0.12)

    def test_negative_threshold(self):
        c = parse_condition("value < -3")
        assert c.threshold == -3.0

    def test_whitespace_tolerant(self):
        c = parse_condition("  value<5  ")
        assert c.subject == "value" and c.op == "<" and c.threshold == 5.0

    @pytest.mark.parametrize(
        "bad",
        [
            "value ~ 5",          # unknown operator
            "foo < 5",            # unknown subject
            "value < abc",        # non-numeric threshold
            "value <",            # missing threshold
            "< 5",                # missing subject
            "",                   # empty
            "value < 5 or 1=1",   # trailing junk (no eval!)
            "1 < 2",              # not subject-based
        ],
    )
    def test_malformed_raises(self, bad):
        with pytest.raises(ValueError):
            parse_condition(bad)

    def test_non_string_raises(self):
        with pytest.raises(ValueError):
            parse_condition(5)  # type: ignore[arg-type]


# --------------------------------------------------------------------------- #
# evaluate
# --------------------------------------------------------------------------- #
class TestEvaluate:
    def test_value_breached(self):
        breached, obs = evaluate(parse_condition("value < 0.12"), QueryResult(["v"], [[0.1]]))
        assert breached is True and obs == pytest.approx(0.1)

    def test_value_not_breached(self):
        breached, obs = evaluate(parse_condition("value < 0.12"), QueryResult(["v"], [[0.5]]))
        assert breached is False and obs == pytest.approx(0.5)

    def test_value_from_string_cell_coerced(self):
        breached, obs = evaluate(parse_condition("value > 100"), QueryResult(["v"], [["250"]]))
        assert breached is True and obs == 250.0

    def test_rows_breached(self):
        breached, obs = evaluate(parse_condition("rows >= 3"), QueryResult(["v"], [[1], [2], [3]]))
        assert breached is True and obs == 3

    def test_rows_zero_on_empty(self):
        breached, obs = evaluate(parse_condition("rows == 0"), QueryResult(["v"], []))
        assert breached is True and obs == 0

    def test_empty_value_not_breached(self):
        # Empty result for a `value` condition → not breached, no crash.
        breached, obs = evaluate(parse_condition("value < 999"), QueryResult(["v"], []))
        assert breached is False and obs == 0

    def test_non_numeric_value_not_breached(self):
        breached, obs = evaluate(parse_condition("value < 999"), QueryResult(["v"], [["hello"]]))
        assert breached is False and obs == 0

    def test_none_cell_not_breached(self):
        breached, _ = evaluate(parse_condition("value < 999"), QueryResult(["v"], [[None]]))
        assert breached is False

    def test_warns_once_per_condition(self, caplog):
        cond = parse_condition("value < 5")
        empty = QueryResult(["v"], [])
        import logging

        with caplog.at_level(logging.WARNING, logger="dashdown.triggers"):
            evaluate(cond, empty)
            evaluate(cond, empty)
            evaluate(cond, empty)
        warnings = [r for r in caplog.records if "empty or non-numeric" in r.getMessage()]
        assert len(warnings) == 1  # latched after the first


# --------------------------------------------------------------------------- #
# load_triggers / parse_trigger
# --------------------------------------------------------------------------- #
def _write_trigger(dir_: Path, name: str, body: str) -> None:
    dir_.mkdir(parents=True, exist_ok=True)
    (dir_ / f"{name}.yml").write_text(body, encoding="utf-8")


class TestLoadTriggers:
    def test_absent_dir_is_empty(self, tmp_path):
        assert load_triggers(tmp_path / "nope") == {}

    def test_happy_path(self, tmp_path):
        d = tmp_path / "triggers"
        _write_trigger(
            d,
            "repeat-rate",
            "query: kpi.repeat_rate\n"
            "interval: 300\n"
            'when: "value < 0.12"\n'
            'message: "Repeat rate slipped"\n'
            "cooldown: 3600\n"
            "actions:\n"
            "  - type: webhook\n"
            "    url: https://example.com/hook\n",
        )
        specs = load_triggers(d)
        assert set(specs) == {"repeat-rate"}
        spec = specs["repeat-rate"]
        assert spec.name == "repeat-rate"        # name = file stem
        assert spec.query == "kpi.repeat_rate"
        assert spec.interval == 300
        assert spec.cooldown == 3600
        assert spec.condition.subject == "value"
        assert spec.enabled is True              # default
        assert len(spec.actions) == 1

    def test_enabled_and_interval_defaults(self, tmp_path):
        d = tmp_path / "triggers"
        _write_trigger(d, "t", "query: q\nwhen: 'rows > 0'\n")
        spec = load_triggers(d)["t"]
        assert spec.enabled is True
        assert spec.interval == 300  # DEFAULT_TRIGGER_INTERVAL

    def test_disabled_flag(self, tmp_path):
        d = tmp_path / "triggers"
        _write_trigger(d, "t", "query: q\nwhen: 'rows > 0'\nenabled: false\n")
        assert load_triggers(d)["t"].enabled is False

    def test_missing_query_raises(self, tmp_path):
        d = tmp_path / "triggers"
        _write_trigger(d, "t", "when: 'value < 1'\n")
        with pytest.raises(ValueError):
            load_triggers(d)

    def test_missing_when_raises(self, tmp_path):
        d = tmp_path / "triggers"
        _write_trigger(d, "t", "query: q\n")
        with pytest.raises(ValueError):
            load_triggers(d)

    def test_bad_condition_raises(self, tmp_path):
        d = tmp_path / "triggers"
        _write_trigger(d, "t", "query: q\nwhen: 'totally bogus'\n")
        with pytest.raises(ValueError):
            load_triggers(d)

    def test_interval_below_min_raises(self, tmp_path):
        d = tmp_path / "triggers"
        _write_trigger(d, "t", "query: q\nwhen: 'rows > 0'\ninterval: 2\n")
        with pytest.raises(ValueError):
            load_triggers(d)

    def test_unknown_action_type_raises(self, tmp_path):
        d = tmp_path / "triggers"
        _write_trigger(
            d, "t",
            "query: q\nwhen: 'rows > 0'\nactions:\n  - type: carrier-pigeon\n",
        )
        with pytest.raises(ValueError):
            load_triggers(d)

    def test_invalid_yaml_raises(self, tmp_path):
        d = tmp_path / "triggers"
        _write_trigger(d, "t", "query: q\n  when: : :\n :bad\n")
        with pytest.raises(ValueError):
            load_triggers(d)

    def test_duplicate_stem_raises(self, tmp_path):
        d = tmp_path / "triggers"
        d.mkdir()
        (d / "t.yml").write_text("query: q\nwhen: 'rows > 0'\n", encoding="utf-8")
        (d / "t.yaml").write_text("query: q\nwhen: 'rows > 0'\n", encoding="utf-8")
        with pytest.raises(ValueError):
            load_triggers(d)

    def test_env_expansion_in_action_config(self, tmp_path, monkeypatch):
        monkeypatch.setenv("TRIGGER_HOOK_URL", "https://secret.example/hook")
        d = tmp_path / "triggers"
        _write_trigger(
            d, "t",
            "query: q\nwhen: 'rows > 0'\n"
            "actions:\n  - type: webhook\n    url: ${TRIGGER_HOOK_URL}\n",
        )
        spec = load_triggers(d)["t"]
        assert spec.actions[0].config["url"] == "https://secret.example/hook"

    def test_disabled_trigger_skips_action_build(self, tmp_path, monkeypatch):
        # A disabled trigger must load even when its action names an unset env
        # var (or an unknown type): actions are only built when the trigger is
        # live, so a scaffolded/example trigger ships enabled:false cleanly.
        monkeypatch.delenv("TRIGGER_HOOK_URL_UNSET", raising=False)
        d = tmp_path / "triggers"
        _write_trigger(
            d, "t",
            "query: q\nwhen: 'rows > 0'\nenabled: false\n"
            "actions:\n  - type: webhook\n    url: ${TRIGGER_HOOK_URL_UNSET}\n",
        )
        spec = load_triggers(d)["t"]
        assert spec.enabled is False
        assert spec.actions == []

    def test_disabled_trigger_still_validates_action_structure(self, tmp_path):
        # Only ${ENV} resolution is deferred for disabled triggers; a typo'd
        # action type must fail at load regardless of the enabled flag.
        d = tmp_path / "triggers"
        _write_trigger(
            d, "t",
            "query: q\nwhen: 'rows > 0'\nenabled: false\n"
            "actions:\n  - type: nope\n",
        )
        with pytest.raises(ValueError, match="unknown action type"):
            load_triggers(d)

    def test_unset_env_in_action_raises(self, tmp_path, monkeypatch):
        monkeypatch.delenv("DEFINITELY_UNSET_TRIGGER_VAR", raising=False)
        d = tmp_path / "triggers"
        _write_trigger(
            d, "t",
            "query: q\nwhen: 'rows > 0'\n"
            "actions:\n  - type: webhook\n    url: ${DEFINITELY_UNSET_TRIGGER_VAR}\n",
        )
        with pytest.raises(ValueError):
            load_triggers(d)


# --------------------------------------------------------------------------- #
# Actions
# --------------------------------------------------------------------------- #
class _FakeResp:
    def read(self):
        return b""

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


@pytest.fixture
def captured_post(monkeypatch):
    captured: dict = {}

    def fake_urlopen(req, timeout=None):
        captured["url"] = req.full_url
        captured["data"] = req.data
        captured["content_type"] = req.get_header("Content-type")
        captured["method"] = req.get_method()
        captured["timeout"] = timeout
        return _FakeResp()

    monkeypatch.setattr("dashdown.actions.urllib.request.urlopen", fake_urlopen)
    return captured


class TestActions:
    def test_webhook_posts_event_json(self, captured_post):
        action = build_action({"type": "webhook", "url": "https://example.com/hook"})
        action.fire({"trigger": "t", "value": 1, "message": "m"})
        assert captured_post["url"] == "https://example.com/hook"
        assert captured_post["method"] == "POST"
        assert captured_post["content_type"] == "application/json"
        assert captured_post["timeout"] == 10
        body = json.loads(captured_post["data"])
        assert body["trigger"] == "t" and body["value"] == 1

    def test_webhook_requires_url(self):
        # An empty url only errors at fire (config could carry an env that's
        # present-but-empty); build succeeds, fire raises.
        action = build_action({"type": "webhook", "url": ""})
        with pytest.raises(ValueError):
            action.fire({"trigger": "t"})

    def test_slack_posts_text(self, captured_post):
        action = build_action({"type": "slack", "webhook_url": "https://hooks.slack.com/x"})
        action.fire(
            {
                "trigger": "t",
                "message": "Repeat rate slipped",
                "when": "value < 0.12",
                "value": 0.1,
                "rows_count": 3,
                "fired_at": "2026-07-17T09:07:00+00:00",
            }
        )
        body = json.loads(captured_post["data"])
        assert set(body) == {"text"}
        text = body["text"]
        assert "Repeat rate slipped" in text
        assert "value < 0.12" in text
        assert "0.1" in text  # current value
        assert "3" in text    # row count
        assert "2026-07-17" in text  # fired_at

    def test_slack_requires_webhook_url(self):
        action = build_action({"type": "slack"})
        with pytest.raises(ValueError):
            action.fire({"trigger": "t"})

    def test_env_expansion_at_build(self, monkeypatch):
        monkeypatch.setenv("MY_HOOK", "https://secret/hook")
        action = build_action({"type": "webhook", "url": "${MY_HOOK}"})
        assert action.config["url"] == "https://secret/hook"

    def test_unknown_type_raises(self):
        with pytest.raises(ValueError):
            build_action({"type": "nope"})

    def test_missing_type_raises(self):
        with pytest.raises(ValueError):
            build_action({"url": "x"})

    def test_format_slack_message_without_message(self):
        text = format_slack_message({"trigger": "t", "value": 1, "rows_count": 0})
        assert "Trigger 't'" in text


# --------------------------------------------------------------------------- #
# TriggerRunner._handle_frame — transition + cooldown (no real sleeps)
# --------------------------------------------------------------------------- #
def _runner() -> TriggerRunner:
    return TriggerRunner(SimpleNamespace(triggers={}, connectors={}, default_connector=None))


def _state(when: str, *, cooldown=None) -> _TriggerState:
    spec = TriggerSpec(
        name="t",
        query="q",
        when=when,
        condition=parse_condition(when),
        message="breached",
        cooldown=cooldown,
    )
    return _TriggerState(spec=spec, key=None, queue=None)  # type: ignore[arg-type]


_BREACH = {"columns": ["v"], "rows": [[5]]}    # value 5
_CLEAR = {"columns": ["v"], "rows": [[50]]}    # value 50


class TestHandleFrame:
    def test_fires_on_transition_only(self):
        r = _runner()
        st = _state("value < 10")
        assert r._handle_frame(st, _BREACH, now=0) is not None   # clear → breach
        assert r._handle_frame(st, _BREACH, now=1) is None       # still breached
        assert r._handle_frame(st, _BREACH, now=99) is None      # no cooldown → quiet

    def test_no_fire_when_never_breached(self):
        r = _runner()
        st = _state("value < 10")
        assert r._handle_frame(st, _CLEAR, now=0) is None
        assert st.breached is False

    def test_breach_clear_breach_fires_twice(self):
        r = _runner()
        st = _state("value < 10")
        fires = 0
        for now, frame in [(0, _BREACH), (1, _BREACH), (2, _CLEAR), (3, _BREACH)]:
            if r._handle_frame(st, frame, now=now) is not None:
                fires += 1
        assert fires == 2  # first breach + re-breach after clearing

    def test_cooldown_refires_while_breached(self):
        r = _runner()
        st = _state("value < 10", cooldown=100)
        assert r._handle_frame(st, _BREACH, now=0) is not None    # transition
        assert r._handle_frame(st, _BREACH, now=50) is None       # within cooldown
        assert r._handle_frame(st, _BREACH, now=100) is not None  # cooldown elapsed
        assert r._handle_frame(st, _BREACH, now=150) is None
        assert r._handle_frame(st, _BREACH, now=200) is not None

    def test_event_payload_shape(self):
        r = _runner()
        st = _state("value < 10")
        event = r._handle_frame(st, _BREACH, now=0)
        assert event is not None
        assert event["trigger"] == "t"
        assert event["message"] == "breached"
        assert event["when"] == "value < 10"
        assert event["value"] == 5.0
        assert event["rows_count"] == 1
        assert event["columns"] == ["v"]
        assert event["sample_rows"] == [{"v": 5}]
        assert "fired_at" in event

    def test_sample_rows_capped(self):
        r = _runner()
        st = _state("rows > 0")
        frame = {"columns": ["v"], "rows": [[i] for i in range(25)]}
        event = r._handle_frame(st, frame, now=0)
        assert event is not None
        assert len(event["sample_rows"]) == 10  # MAX_SAMPLE_ROWS


# --------------------------------------------------------------------------- #
# parse_trigger fixed params
# --------------------------------------------------------------------------- #
class TestParseTriggerParams:
    def test_params_coerced_to_strings(self):
        spec = parse_trigger(
            {"query": "q", "when": "rows > 0", "params": {"region": "EU", "n": 5}}, "t"
        )
        assert spec.params == {"region": "EU", "n": "5"}

    def test_bad_params_raises(self):
        with pytest.raises(ValueError):
            parse_trigger({"query": "q", "when": "rows > 0", "params": [1, 2]}, "t")


# --------------------------------------------------------------------------- #
# End-to-end: a real project + poll loop firing FakeAction under TestClient
# --------------------------------------------------------------------------- #
def _make_trigger_project(root: Path, *, when: str, interval: int = 5, val: int = 5) -> None:
    (root / "pages").mkdir()
    (root / "pages" / "index.md").write_text("# Home\n", encoding="utf-8")
    (root / "data").mkdir()
    (root / "data" / "metrics.csv").write_text(
        f"metric,val\nrepeat,{val}\n", encoding="utf-8"
    )
    (root / "queries").mkdir()
    (root / "queries" / "kpi.sql").write_text(
        "---\nconnector: main\n---\nSELECT val FROM metrics LIMIT 1\n",
        encoding="utf-8",
    )
    (root / "triggers").mkdir()
    (root / "triggers" / "kpi-watch.yml").write_text(
        "query: kpi\n"
        f"interval: {interval}\n"
        f'when: "{when}"\n'
        'message: "kpi breached"\n'
        "actions:\n  - type: fake\n",
        encoding="utf-8",
    )
    (root / "dashdown.yaml").write_text("title: Trig\n", encoding="utf-8")
    (root / "sources.yaml").write_text(
        "main:\n  type: csv\n  directory: data\n", encoding="utf-8"
    )


def _wait_for(pred, timeout: float = 10.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if pred():
            return True
        time.sleep(0.05)
    return False


class TestRunnerIntegration:
    def test_project_loads_triggers(self, tmp_path):
        from dashdown.project import load_project

        _make_trigger_project(tmp_path, when="value < 100")
        proj = load_project(tmp_path)
        try:
            assert set(proj.triggers) == {"kpi-watch"}
            assert proj.triggers["kpi-watch"].query == "kpi"
        finally:
            proj.close()

    def test_trigger_fires_action_end_to_end(self, tmp_path):
        # value 5 < 100 → breach on the first poll → FakeAction records one event.
        _make_trigger_project(tmp_path, when="value < 100", interval=5, val=5)
        app = create_app(tmp_path)
        with TestClient(app) as client:  # lifespan runs startup → runner starts
            assert client.app.state.trigger_runner is not None
            assert _wait_for(lambda: len(_RECORDED) >= 1)
        assert _RECORDED[0]["trigger"] == "kpi-watch"
        assert _RECORDED[0]["value"] == 5.0
        assert _RECORDED[0]["message"] == "kpi breached"

    def test_non_breaching_trigger_stays_quiet(self, tmp_path):
        # value 5 is NOT < 1 → never breaches → no action, ever.
        _make_trigger_project(tmp_path, when="value < 1", interval=5, val=5)
        app = create_app(tmp_path)
        with TestClient(app) as client:
            # Give the first poll time to run and evaluate (and not fire).
            time.sleep(0.5)
            assert client.app.state.trigger_runner is not None
        assert _RECORDED == []

    def test_no_triggers_means_no_runner(self, tmp_path):
        (tmp_path / "pages").mkdir()
        (tmp_path / "pages" / "index.md").write_text("# Home\n", encoding="utf-8")
        (tmp_path / "dashdown.yaml").write_text("title: T\n", encoding="utf-8")
        (tmp_path / "sources.yaml").write_text(
            "main:\n  type: csv\n  directory: data\n", encoding="utf-8"
        )
        (tmp_path / "data").mkdir()
        app = create_app(tmp_path)
        with TestClient(app) as client:
            assert client.app.state.trigger_runner is None

    def test_joining_existing_poller_replays_latest(self, tmp_path):
        # A trigger that subscribes to a poller which ALREADY holds a breached
        # snapshot must fire from the replay: broadcasts are digest-gated, so a
        # constant value would otherwise never deliver a frame to the late
        # joiner and the alert would never fire.
        import asyncio

        from dashdown.project import load_project
        from dashdown.triggers import TriggerRunner
        from dashdown.streaming import build_query_fetch

        _make_trigger_project(tmp_path, when="value < 100", interval=5, val=5)
        proj = load_project(tmp_path)
        try:
            async def scenario():
                # First subscriber creates the poller and lets it publish once.
                built = build_query_fetch(proj, "kpi", "main", {})
                assert built is not None
                fetch, key = built
                poller, q = stream_hub.subscribe(key, fetch, "kpi", 5)
                assert await asyncio.wait_for(q.get(), timeout=5) is not None
                assert poller.latest is not None

                # Now the runner joins the SAME poller (shared key) — the value
                # never changes again, so only the replay can trigger the fire.
                runner = TriggerRunner(proj)
                runner.start()
                try:
                    for _ in range(100):
                        if _RECORDED:
                            break
                        await asyncio.sleep(0.05)
                finally:
                    runner.stop()
                    stream_hub.unsubscribe(key, q)
                assert _RECORDED and _RECORDED[0]["trigger"] == "kpi-watch"

            asyncio.run(scenario())
        finally:
            proj.close()


class TestSharedPollerInterval:
    def test_faster_subscriber_speeds_up_existing_poller(self):
        # First-subscriber-wins would starve a later, faster subscriber (e.g. a
        # 5s live chart joining a 300s trigger poller). The hub adopts the
        # fastest requested cadence instead.
        import asyncio

        from dashdown.data.base import QueryResult as QR

        async def scenario():
            fetch = lambda: QR(columns=["v"], rows=[[1]])  # noqa: E731
            poller, q1 = stream_hub.subscribe(("q", "c", ()), fetch, "q", 300)
            assert poller.interval == 300
            _, q2 = stream_hub.subscribe(("q", "c", ()), fetch, "q", 5)
            assert poller.interval == 5
            # A slower joiner never slows it back down.
            _, q3 = stream_hub.subscribe(("q", "c", ()), fetch, "q", 60)
            assert poller.interval == 5
            for q in (q1, q2, q3):
                stream_hub.unsubscribe(("q", "c", ()), q)

        asyncio.run(scenario())
