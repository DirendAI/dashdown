"""Tests for Tier-1 answer refinement on the runtime ask engine:

  * ``execute_spec`` + ``POST /_dashdown/api/ask/execute`` — the answer-panel chips
    that re-run an *edited* semantic spec without an LLM resolution call, optionally
    with one LLM call for commentary.
  * session context — ``POST /_dashdown/api/ask`` with a ``history`` list so a
    refinement resolves in the context of the whole session.
  * ``semantic_options`` on a semantic answer payload.

Mirrors tests/test_ask_engine.py's patterns (scriptable ``FakeAdapter``, the
``_client`` helper, the autouse cache-clearing fixture, ``needs_bsl``).
"""
from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from dashdown import ask_engine
from dashdown.llm import LLMAdapter, LLMConfig
from dashdown.render import pipeline
from dashdown.server import create_app

_SEMANTIC_EXAMPLE = Path(__file__).parent / "fixtures" / "semantic_first_class"

_bsl_installed = True
try:  # the semantic extra
    import boring_semantic_layer  # noqa: F401
    import ibis  # noqa: F401
except ImportError:  # pragma: no cover
    _bsl_installed = False

needs_bsl = pytest.mark.skipif(not _bsl_installed, reason="requires dashdown-md[semantic]")


@pytest.fixture(autouse=True)
def _clear_caches():
    """Answer cache + query/def caches are module-global; isolate every test."""

    def _clear():
        ask_engine._answer_cache.clear()
        ask_engine._rate_marks.clear()
        pipeline._query_def_cache.clear()
        pipeline._result_cache.clear()
        pipeline._python_def_cache.clear()
        pipeline._stream_def_cache.clear()
        pipeline._library_keys.clear()
        pipeline._python_library_keys.clear()

    _clear()
    yield
    _clear()


class FakeAdapter(LLMAdapter):
    """Scriptable per-call adapter: returns queued replies in order, then falls
    back to the last reply."""

    def __init__(self, *replies: str):
        super().__init__(LLMConfig(provider="mistral", api_key="test"))
        self.replies = list(replies)
        self.calls: list[tuple[str, str]] = []

    def complete(self, system: str, prompt: str) -> str:
        self.calls.append((system, prompt))
        if self.replies:
            return self.replies.pop(0)
        return ""


# --------------------------------------------------------------------------- #
# Project fixtures
# --------------------------------------------------------------------------- #
def _make_lib_project(root: Path, *, llm: bool = True, extra_yaml: str = "") -> None:
    """A project with a CSV source and a `by_region` library query."""
    (root / "pages").mkdir()
    (root / "data").mkdir()
    (root / "queries").mkdir()
    yaml = "title: Ask Execute Test\n"
    if llm:
        yaml += "llm:\n  provider: mistral\n  api_key: dummy\n"
    yaml += extra_yaml
    (root / "dashdown.yaml").write_text(yaml, encoding="utf-8")
    (root / "sources.yaml").write_text(
        "main:\n  type: csv\n  directory: data\n", encoding="utf-8"
    )
    (root / "data" / "sales.csv").write_text(
        "region,amount\nNorth,100\nSouth,200\nWest,50\n", encoding="utf-8"
    )
    (root / "queries" / "by_region.sql").write_text(
        "---\ndescription: Revenue by region\n---\n"
        "SELECT region, SUM(amount) AS total FROM sales\n"
        "WHERE (region = '${region}' OR '${region}' = '')\n"
        "GROUP BY region ORDER BY total DESC\n",
        encoding="utf-8",
    )
    (root / "pages" / "index.md").write_text("# Home\n", encoding="utf-8")


def _client(tmp_path: Path, fake: FakeAdapter | None = None, **kw) -> TestClient:
    proj = tmp_path / "proj"
    proj.mkdir()
    _make_lib_project(proj, **kw)
    app = create_app(proj)
    app.state.project.llm_adapter = fake
    return TestClient(app)


def _semantic_client(tmp_path: Path, fake: FakeAdapter, extra_yaml: str = "") -> TestClient:
    dst = tmp_path / "sem_proj"
    shutil.copytree(
        _SEMANTIC_EXAMPLE,
        dst,
        ignore=shutil.ignore_patterns("__pycache__", "*.duckdb*", "sources.yaml"),
    )
    (dst / "sources.yaml").write_text("main:\n  type: csv\n  directory: data\n")
    cfg = (dst / "dashdown.yaml").read_text()
    cfg += "\nllm:\n  provider: mistral\n  api_key: dummy\n" + extra_yaml
    (dst / "dashdown.yaml").write_text(cfg)
    app = create_app(dst)
    app.state.project.llm_adapter = fake
    return TestClient(app)


_SEMANTIC_SPEC = {
    "kind": "semantic",
    "model": "sales",
    "metric": "revenue",
    "by": "region",
    "grain": None,
    "filters": {},
}


# --------------------------------------------------------------------------- #
# execute_spec — the chip path (no LLM) and commentary path (one LLM call)
# --------------------------------------------------------------------------- #
@needs_bsl
class TestExecuteSpec:
    def test_commentary_false_never_calls_llm(self, tmp_path):
        fake = FakeAdapter()  # scripted with nothing — must never be called
        client = _semantic_client(tmp_path, fake)
        r = client.post(
            "/_dashdown/api/ask/execute",
            json={"question": "revenue by region", "spec": _SEMANTIC_SPEC},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert fake.calls == []  # zero LLM calls
        assert body["answer_html"] == ""
        assert body["answer_text"] == ""
        assert body["annotations"] == []
        assert body["cached"] is False
        # Data + chart + provenance + semantic_options all present.
        assert len(body["rows"]) == 4
        assert body["chart"]["type"] == "bar"
        assert body["resolved"]["kind"] == "semantic"
        assert "sales.revenue" in body["resolved"]["provenance"]
        opts = body["semantic_options"]
        assert opts["model"] == "sales"
        assert "revenue" in opts["measures"]
        assert "region" in opts["dimensions"]

    def test_commentary_true_makes_exactly_one_llm_call(self, tmp_path):
        # Only the answer call — no resolution call (the spec is client-built).
        fake = FakeAdapter("**North** leads.")
        client = _semantic_client(tmp_path, fake)
        r = client.post(
            "/_dashdown/api/ask/execute",
            json={
                "question": "revenue by region",
                "spec": _SEMANTIC_SPEC,
                "commentary": True,
            },
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert len(fake.calls) == 1
        assert "<strong>North</strong>" in body["answer_html"]
        assert isinstance(body["annotations"], list)
        assert body["semantic_options"]["model"] == "sales"

    def test_invalid_metric_is_400(self, tmp_path):
        fake = FakeAdapter()
        client = _semantic_client(tmp_path, fake)
        bad = {**_SEMANTIC_SPEC, "metric": "ghost"}
        r = client.post(
            "/_dashdown/api/ask/execute",
            json={"question": "ghost metric", "spec": bad},
        )
        assert r.status_code == 400
        assert "ghost" in r.json()["detail"]
        assert fake.calls == []

    def test_non_semantic_kind_is_400(self, tmp_path):
        client = _semantic_client(tmp_path, FakeAdapter())
        r = client.post(
            "/_dashdown/api/ask/execute",
            json={
                "question": "x",
                "spec": {"kind": "query", "name": "by_region"},
            },
        )
        assert r.status_code == 400

    def test_missing_spec_is_400(self, tmp_path):
        client = _semantic_client(tmp_path, FakeAdapter())
        r = client.post("/_dashdown/api/ask/execute", json={"question": "x"})
        assert r.status_code == 400

    def test_empty_question_is_400(self, tmp_path):
        client = _semantic_client(tmp_path, FakeAdapter())
        r = client.post(
            "/_dashdown/api/ask/execute",
            json={"question": "  ", "spec": _SEMANTIC_SPEC},
        )
        assert r.status_code == 400

    def test_spec_cache_distinguishes_specs_and_replays(self, tmp_path):
        # Same question, two different specs → two distinct cache entries (both
        # miss). Same question + same spec twice → the second is a cache hit with
        # no new LLM call.
        fake = FakeAdapter("Answer A.", "Answer B.")
        client = _semantic_client(tmp_path, fake)
        spec_a = _SEMANTIC_SPEC
        spec_b = {**_SEMANTIC_SPEC, "by": "status"}

        r1 = client.post(
            "/_dashdown/api/ask/execute",
            json={"question": "same q", "spec": spec_a, "commentary": True},
        )
        assert r1.json()["cached"] is False
        r2 = client.post(
            "/_dashdown/api/ask/execute",
            json={"question": "same q", "spec": spec_b, "commentary": True},
        )
        assert r2.json()["cached"] is False
        assert len(fake.calls) == 2  # two distinct entries, two answer calls

        # Repeat spec_a with the same question → cache hit, no third call.
        r3 = client.post(
            "/_dashdown/api/ask/execute",
            json={"question": "same q", "spec": spec_a, "commentary": True},
        )
        assert r3.json()["cached"] is True
        assert len(fake.calls) == 2

    def test_commentary_true_consumes_rate_limit(self, tmp_path):
        # rate_limit: 1 — a commentary=true execute burns the budget, so a
        # following distinct ask (resolution) 429s.
        fake = FakeAdapter(
            "Commentary.",  # the execute_spec answer call
            '{"kind": "semantic", "model": "sales", "metric": "revenue", "by": "region"}',
            "Ask answer.",
        )
        client = _semantic_client(tmp_path, fake, extra_yaml="ask:\n  rate_limit: 1\n")
        r1 = client.post(
            "/_dashdown/api/ask/execute",
            json={"question": "revenue by region", "spec": _SEMANTIC_SPEC, "commentary": True},
        )
        assert r1.status_code == 200, r1.text
        calls_after = len(fake.calls)
        r2 = client.post(
            "/_dashdown/api/ask", json={"question": "a different question"}
        )
        assert r2.status_code == 429
        assert len(fake.calls) == calls_after  # refused before any LLM call

    def test_commentary_false_does_not_consume_rate_limit(self, tmp_path):
        # rate_limit: 1 — two commentary=false executes cost nothing, so a
        # following ask still has its budget.
        fake = FakeAdapter(
            '{"kind": "semantic", "model": "sales", "metric": "revenue", "by": "region"}',
            "Ask answer.",
        )
        client = _semantic_client(tmp_path, fake, extra_yaml="ask:\n  rate_limit: 1\n")
        for _ in range(2):
            r = client.post(
                "/_dashdown/api/ask/execute",
                json={"question": "revenue by region", "spec": _SEMANTIC_SPEC},
            )
            assert r.status_code == 200, r.text
        assert fake.calls == []  # no LLM spend from the chip path
        r = client.post("/_dashdown/api/ask", json={"question": "a fresh ask"})
        assert r.status_code == 200, r.text

    def test_semantic_answer_payload_has_options(self, tmp_path):
        # A normal (LLM-resolved) semantic ask also ships semantic_options.
        fake = FakeAdapter(
            '{"kind": "semantic", "model": "sales", "metric": "revenue", "by": "region"}',
            "North leads.",
        )
        client = _semantic_client(tmp_path, fake)
        r = client.post(
            "/_dashdown/api/ask", json={"question": "revenue by region"}
        )
        assert r.status_code == 200, r.text
        opts = r.json()["semantic_options"]
        assert opts["model"] == "sales"
        assert {"revenue", "orders", "avg_deal"} <= set(opts["measures"])
        assert {"region", "status", "order_date"} <= set(opts["dimensions"])
        assert opts["time_dimension"] == "order_date"
        assert opts["grains"]  # non-empty grain vocabulary


# --------------------------------------------------------------------------- #
# Session context — POST /api/ask with a `history` list
# --------------------------------------------------------------------------- #
class TestSessionContext:
    def test_history_reaches_resolver_prompt(self, tmp_path):
        # A two-entry history → both prior questions + the "session so far" block
        # ride in the resolver call's user prompt.
        fake = FakeAdapter(
            '{"kind": "query", "name": "by_region", "params": {}}',
            "Only paid.",
        )
        client = _client(tmp_path, fake=fake)
        r = client.post(
            "/_dashdown/api/ask",
            json={
                "question": "only paid channels",
                "history": [
                    {
                        "question": "revenue by channel",
                        "resolved": {"kind": "semantic", "detail": {"metric": "revenue"}},
                    },
                    {
                        "question": "which channels grew",
                        "resolved": {"kind": "semantic", "detail": {"metric": "revenue"}},
                    },
                ],
            },
        )
        assert r.status_code == 200, r.text
        # The resolver call's user prompt carries the session block + both questions.
        _system, user = fake.calls[0]
        assert "session so far" in user
        assert "revenue by channel" in user
        assert "which channels grew" in user

    def test_history_reaches_answer_prompt(self, tmp_path):
        # The answer (2nd) call's prompt carries the "earlier questions in this
        # session" parenthetical listing the prior questions.
        fake = FakeAdapter(
            '{"kind": "query", "name": "by_region", "params": {}}',
            "Only paid.",
        )
        client = _client(tmp_path, fake=fake)
        r = client.post(
            "/_dashdown/api/ask",
            json={
                "question": "only paid channels",
                "history": [
                    {"question": "revenue by channel", "resolved": {"kind": "query"}},
                    {"question": "which channels grew", "resolved": {"kind": "query"}},
                ],
            },
        )
        assert r.status_code == 200, r.text
        _system, answer_prompt = fake.calls[1]
        assert "earlier questions in this session" in answer_prompt
        assert "revenue by channel" in answer_prompt
        assert "which channels grew" in answer_prompt

    def test_no_history_omits_context(self, tmp_path):
        fake = FakeAdapter(
            '{"kind": "query", "name": "by_region", "params": {}}', "Ans."
        )
        client = _client(tmp_path, fake=fake)
        client.post("/_dashdown/api/ask", json={"question": "revenue by region"})
        _system, user = fake.calls[0]
        assert "session so far" not in user
        _system, answer_prompt = fake.calls[1]
        assert "earlier questions in this session" not in answer_prompt

    def test_no_history_shares_cache_with_fresh_ask(self, tmp_path):
        # Two no-history asks of the same question → the second is a cache hit
        # (the context discriminator is None both times, so the key is unchanged).
        fake = FakeAdapter(
            '{"kind": "query", "name": "by_region", "params": {}}', "Ans."
        )
        client = _client(tmp_path, fake=fake)
        r1 = client.post("/_dashdown/api/ask", json={"question": "revenue by region"})
        assert r1.json()["cached"] is False
        r2 = client.post("/_dashdown/api/ask", json={"question": "revenue by region"})
        assert r2.json()["cached"] is True
        assert len(fake.calls) == 2  # one resolve + one answer, no re-billing

    def test_same_question_histories_differing_by_detail_are_distinct(self, tmp_path):
        # Same follow-up text, histories differing ONLY in resolved.detail (a chip
        # edit) → two distinct cache entries, both misses (2 LLM calls each → 4).
        fake = FakeAdapter(
            '{"kind": "query", "name": "by_region", "params": {}}', "A.",
            '{"kind": "query", "name": "by_region", "params": {}}', "B.",
        )
        client = _client(tmp_path, fake=fake)
        r1 = client.post(
            "/_dashdown/api/ask",
            json={
                "question": "same follow up",
                "history": [
                    {
                        "question": "revenue by channel",
                        "resolved": {"kind": "semantic", "detail": {"by": "region"}},
                    }
                ],
            },
        )
        assert r1.json()["cached"] is False
        r2 = client.post(
            "/_dashdown/api/ask",
            json={
                "question": "same follow up",
                "history": [
                    {
                        "question": "revenue by channel",
                        "resolved": {"kind": "semantic", "detail": {"by": "status"}},
                    }
                ],
            },
        )
        assert r2.json()["cached"] is False
        assert len(fake.calls) == 4


# --------------------------------------------------------------------------- #
# History sanitization — bounding / narrowing (unit, no server)
# --------------------------------------------------------------------------- #
class TestHistorySanitization:
    def test_keeps_only_last_six(self):
        raw = [{"question": f"q{i}", "resolved": {}} for i in range(9)]
        out = ask_engine._sanitize_history(raw)
        assert [e["question"] for e in out] == ["q3", "q4", "q5", "q6", "q7", "q8"]

    def test_drops_malformed_entries(self):
        raw = [
            {"question": "good", "resolved": {"kind": "query"}},
            {"question": "   "},          # empty question
            {"resolved": {"kind": "x"}},  # no question
            "not a dict",                 # not a dict
            {"question": 42},             # non-str question
        ]
        out = ask_engine._sanitize_history(raw)
        assert len(out) == 1
        assert out[0]["question"] == "good"

    def test_truncates_long_question(self):
        raw = [{"question": "x" * 900, "resolved": {}}]
        out = ask_engine._sanitize_history(raw)
        assert len(out[0]["question"]) == 400

    def test_narrows_resolved_to_kind_and_detail(self):
        raw = [
            {
                "question": "q",
                "resolved": {"kind": "semantic", "detail": {"m": 1}, "extra": "drop me"},
            }
        ]
        out = ask_engine._sanitize_history(raw)
        assert out[0]["resolved"] == {"kind": "semantic", "detail": {"m": 1}}

    def test_non_list_is_empty(self):
        assert ask_engine._sanitize_history(None) == []
        assert ask_engine._sanitize_history({"question": "q"}) == []

    def test_more_than_six_only_last_six_in_prompt(self, tmp_path):
        # Prompt-level: >6 entries → only the last 6 questions appear.
        fake = FakeAdapter(
            '{"kind": "query", "name": "by_region", "params": {}}', "Ans."
        )
        client = _client(tmp_path, fake=fake)
        history = [{"question": f"q{i}", "resolved": {}} for i in range(9)]
        r = client.post(
            "/_dashdown/api/ask",
            json={"question": "latest", "history": history},
        )
        assert r.status_code == 200, r.text
        _system, user = fake.calls[0]
        assert "q0" not in user and "q1" not in user and "q2" not in user
        for i in range(3, 9):
            assert f"q{i}" in user


# --------------------------------------------------------------------------- #
# Forgiving coercion + series (the "by: 'name,channel'" bug) and self-repair
# --------------------------------------------------------------------------- #
@needs_bsl
class TestByCoercionAndSeries:
    def test_comma_joined_by_splits_into_by_and_series(self, tmp_path):
        # The real-world Mistral failure: two groupings packed into `by`.
        # The route must survive: first valid dim → by, second → series.
        fake = FakeAdapter(
            '{"kind": "semantic", "model": "sales", "metric": "revenue", '
            '"by": "region,status", "filters": {}}',
            "Answer.",
        )
        client = _semantic_client(tmp_path, fake)
        r = client.post("/_dashdown/api/ask", json={"question": "revenue by region and status"})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["resolved"]["kind"] == "semantic"
        assert body["resolved"]["detail"]["by"] == "region"
        assert body["resolved"]["detail"]["series"] == "status"
        assert "per status" in body["resolved"]["provenance"]
        # The chart carries the series split for the client + annotations.
        if body["chart"] is not None:
            assert body["chart"]["series_by"].split(".")[-1] == "status"

    def test_list_valued_by_splits_too(self, tmp_path):
        fake = FakeAdapter(
            '{"kind": "semantic", "model": "sales", "metric": "revenue", '
            '"by": ["region", "status"], "filters": {}}',
            "Answer.",
        )
        client = _semantic_client(tmp_path, fake)
        r = client.post("/_dashdown/api/ask", json={"question": "split it"})
        body = r.json()
        assert body["resolved"]["detail"]["by"] == "region"
        assert body["resolved"]["detail"]["series"] == "status"

    def test_explicit_series_field(self, tmp_path):
        fake = FakeAdapter(
            '{"kind": "semantic", "model": "sales", "metric": "revenue", '
            '"by": "region", "series": "status", "filters": {}}',
            "Answer.",
        )
        client = _semantic_client(tmp_path, fake)
        body = client.post("/_dashdown/api/ask", json={"question": "by region per status"}).json()
        assert body["resolved"]["detail"]["by"] == "region"
        assert body["resolved"]["detail"]["series"] == "status"

    def test_invalid_series_is_dropped_not_fatal(self, tmp_path):
        fake = FakeAdapter(
            '{"kind": "semantic", "model": "sales", "metric": "revenue", '
            '"by": "region", "series": "ghost", "filters": {}}',
            "Answer.",
        )
        client = _semantic_client(tmp_path, fake)
        body = client.post("/_dashdown/api/ask", json={"question": "x"}).json()
        assert body["resolved"]["kind"] == "semantic"
        assert body["resolved"]["detail"]["series"] is None

    def test_metric_comma_takes_first_valid(self, tmp_path):
        fake = FakeAdapter(
            '{"kind": "semantic", "model": "sales", "metric": "revenue,orders", '
            '"by": "region", "filters": {}}',
            "Answer.",
        )
        client = _semantic_client(tmp_path, fake)
        body = client.post("/_dashdown/api/ask", json={"question": "x"}).json()
        assert body["resolved"]["detail"]["metric"] == "revenue"

    def test_all_unknown_by_is_still_none(self, tmp_path):
        # Forgiveness has a floor: when NOTHING in `by` is a real dimension the
        # route degrades (and, with the fallback exhausted, stays none).
        fake = FakeAdapter(
            '{"kind": "semantic", "model": "sales", "metric": "revenue", '
            '"by": "ghost,phantom", "filters": {}}',
        )
        client = _semantic_client(tmp_path, fake)
        body = client.post("/_dashdown/api/ask", json={"question": "x"}).json()
        assert body["resolved"]["kind"] == "none"

    def test_execute_spec_accepts_series(self, tmp_path):
        fake = FakeAdapter("never called")
        client = _semantic_client(tmp_path, fake)
        spec = dict(_SEMANTIC_SPEC, series="status")
        r = client.post(
            "/_dashdown/api/ask/execute",
            json={"question": "q", "spec": spec, "commentary": False},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["resolved"]["detail"]["series"] == "status"
        assert fake.calls == []


@needs_bsl
class TestSelfRepair:
    def test_invalid_resolution_gets_one_retry(self, tmp_path):
        # First resolver reply is off-catalog → one corrective retry (with the
        # error quoted back) → valid → answered. 3 calls total.
        fake = FakeAdapter(
            '{"kind": "semantic", "model": "sales", "metric": "ghost_metric"}',
            '{"kind": "semantic", "model": "sales", "metric": "revenue", '
            '"by": "region", "filters": {}}',
            "Repaired answer.",
        )
        client = _semantic_client(tmp_path, fake)
        r = client.post("/_dashdown/api/ask", json={"question": "revenue by region"})
        body = r.json()
        assert body["resolved"]["kind"] == "semantic"
        assert len(fake.calls) == 3
        # The repair prompt quotes the validation error.
        assert "previous response was invalid" in fake.calls[1][1]
        assert "ghost_metric" in fake.calls[1][1]

    def test_still_invalid_after_retry_degrades_to_none(self, tmp_path):
        fake = FakeAdapter("garbage one", "garbage two")
        client = _semantic_client(tmp_path, fake)
        body = client.post("/_dashdown/api/ask", json={"question": "x"}).json()
        assert body["resolved"]["kind"] == "none"
        assert len(fake.calls) == 2  # resolve + one repair, no answer call

    def test_explicit_none_is_not_retried(self, tmp_path):
        # An honest "I can't answer this" must not be re-billed.
        fake = FakeAdapter('{"kind": "none", "reason": "not in catalog"}')
        client = _semantic_client(tmp_path, fake)
        body = client.post("/_dashdown/api/ask", json={"question": "weather?"}).json()
        assert body["resolved"]["kind"] == "none"
        assert len(fake.calls) == 1


# --------------------------------------------------------------------------- #
# The "list" resolution rung — detail/list questions off a semantic model
# --------------------------------------------------------------------------- #
def _list_reply(**over) -> str:
    import json

    obj = {
        "kind": "list",
        "model": "sales",
        "columns": ["region", "status", "order_date"],
        "order_by": "order_date",
        "desc": True,
        "limit": 10,
    }
    obj.update(over)
    return json.dumps(obj)


@needs_bsl
class TestListResolution:
    def test_routes_executes_and_newest_first(self, tmp_path):
        fake = FakeAdapter(_list_reply(limit=10), "Recent orders shown.")
        client = _semantic_client(tmp_path, fake)
        r = client.post(
            "/_dashdown/api/ask", json={"question": "show me the last 10 orders"}
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["resolved"]["kind"] == "list"
        assert "list:" in body["resolved"]["provenance"]
        assert "newest first" in body["resolved"]["provenance"]
        rows = body["rows"]
        cols = body["columns"]
        assert 0 < len(rows) <= 10
        # order_date column resolves to a canonical `sales.order_date` spelling.
        date_col = next(c for c in cols if c.split(".")[-1] == "order_date")
        dates = [row[cols.index(date_col)] for row in rows]
        assert dates[0] >= dates[-1]  # desc by the time dimension
        # The answer prose rides along (the table is the deliverable).
        assert body["answer_text"]

    def test_no_semantic_options_on_list(self, tmp_path):
        # Chips are the aggregate editor — a list answer omits semantic_options.
        fake = FakeAdapter(_list_reply(), "Answer.")
        client = _semantic_client(tmp_path, fake)
        body = client.post(
            "/_dashdown/api/ask", json={"question": "list orders"}
        ).json()
        assert body["resolved"]["kind"] == "list"
        assert "semantic_options" not in body

    def test_columns_comma_joined_coerces(self, tmp_path):
        # A single comma-joined `columns` entry splits into both dimensions.
        fake = FakeAdapter(
            _list_reply(columns=["region,status"], order_by="region"), "Answer."
        )
        client = _semantic_client(tmp_path, fake)
        body = client.post(
            "/_dashdown/api/ask", json={"question": "list region and status"}
        ).json()
        assert body["resolved"]["kind"] == "list"
        assert body["resolved"]["detail"]["columns"] == ["region", "status"]

    def test_all_unknown_columns_degrades_to_none(self, tmp_path):
        # No valid column left → none (and, self-repair exhausted, stays none).
        fake = FakeAdapter(
            _list_reply(columns=["ghost", "phantom"], order_by="ghost"),
            _list_reply(columns=["ghost", "phantom"], order_by="ghost"),
        )
        client = _semantic_client(tmp_path, fake)
        body = client.post(
            "/_dashdown/api/ask", json={"question": "list ghosts"}
        ).json()
        assert body["resolved"]["kind"] == "none"

    def test_order_by_falls_back_to_time_dimension(self, tmp_path):
        # An invalid order_by soft-falls-back to the model's time dimension when
        # it's among the selected columns.
        fake = FakeAdapter(_list_reply(order_by="not_a_dim"), "Answer.")
        client = _semantic_client(tmp_path, fake)
        body = client.post(
            "/_dashdown/api/ask", json={"question": "list orders"}
        ).json()
        assert body["resolved"]["kind"] == "list"
        assert body["resolved"]["detail"]["order_by"] == "order_date"

    def test_limit_clamped_low(self, tmp_path):
        fake = FakeAdapter(_list_reply(limit=0), "Answer.")
        client = _semantic_client(tmp_path, fake)
        body = client.post(
            "/_dashdown/api/ask", json={"question": "list orders"}
        ).json()
        assert body["resolved"]["detail"]["limit"] == 1

    def test_limit_clamped_high(self, tmp_path):
        fake = FakeAdapter(_list_reply(limit=10_000), "Answer.")
        client = _semantic_client(tmp_path, fake)
        body = client.post(
            "/_dashdown/api/ask", json={"question": "list orders"}
        ).json()
        assert body["resolved"]["detail"]["limit"] == 500

    def test_joined_dimension_in_columns(self, tmp_path):
        # `manager` lives in the joined geo model — a list can project it.
        fake = FakeAdapter(
            _list_reply(columns=["manager", "order_date"], order_by="order_date"),
            "Answer.",
        )
        client = _semantic_client(tmp_path, fake)
        body = client.post(
            "/_dashdown/api/ask", json={"question": "which managers, recently"}
        ).json()
        assert body["resolved"]["kind"] == "list", body["resolved"]
        cols = body["columns"]
        assert any(c.split(".")[-1] == "manager" for c in cols)
        assert len(body["rows"]) > 0

    def test_filters_shrink_rows(self, tmp_path):
        # A status filter narrows the row set vs. the unfiltered list.
        fake = FakeAdapter(
            _list_reply(limit=500), "a",
            _list_reply(limit=500, filters={"status": ["Won"]}), "b",
        )
        client = _semantic_client(tmp_path, fake)
        unfiltered = client.post(
            "/_dashdown/api/ask", json={"question": "all orders"}
        ).json()
        filtered = client.post(
            "/_dashdown/api/ask", json={"question": "won orders"}
        ).json()
        assert filtered["resolved"]["detail"]["filters"] == {"status": ["Won"]}
        assert 0 < len(filtered["rows"]) < len(unfiltered["rows"])

    def test_self_repair_on_invalid_model(self, tmp_path):
        # A bad model is invalid → one corrective retry → valid list → answered.
        fake = FakeAdapter(
            _list_reply(model="ghost_model"),
            _list_reply(),
            "Repaired answer.",
        )
        client = _semantic_client(tmp_path, fake)
        body = client.post(
            "/_dashdown/api/ask", json={"question": "list orders"}
        ).json()
        assert body["resolved"]["kind"] == "list"
        assert len(fake.calls) == 3
        assert "previous response was invalid" in fake.calls[1][1]

    def test_backend_without_list_support_degrades_to_none(self, tmp_path, monkeypatch):
        # A backend whose build_list_spec raises NotImplementedError degrades the
        # answer to none (never a 500) — no billable answer call.
        from dashdown.semantic import IbisBackend

        def _unsupported(self, *a, **k):
            raise NotImplementedError(
                "the 'ibis' semantic backend does not support list queries yet"
            )

        monkeypatch.setattr(IbisBackend, "build_list_spec", _unsupported)
        fake = FakeAdapter(_list_reply())
        client = _semantic_client(tmp_path, fake)
        body = client.post(
            "/_dashdown/api/ask", json={"question": "list orders"}
        ).json()
        assert body["resolved"]["kind"] == "none"
        assert "does not support list" in body["answer_text"]
        assert len(fake.calls) == 1  # resolve only, no answer call

    def test_keep_writes_list(self, tmp_path):
        # A list answer is now keepable — the keep endpoint re-validates the detail
        # and appends a `<List …>` section to the page (v1: no filters carried).
        client = _semantic_client(tmp_path, FakeAdapter())
        page = client.app.state.project.root / "pages" / "keep_here.md"
        page.write_text("# Keep\n", encoding="utf-8")
        r = client.post(
            "/_dashdown/api/ask/keep",
            json={
                "question": "last 10 orders",
                "resolved": {
                    "kind": "list",
                    "provenance": "list: sales — region",
                    "detail": {
                        "model": "sales",
                        "columns": ["region", "order_date"],
                        "order_by": "order_date",
                        "desc": True,
                        "limit": 10,
                        "filters": {},
                    },
                },
                "chart": None,
                "path": "/keep_here",
            },
        )
        assert r.status_code == 200, r.text
        md = page.read_text(encoding="utf-8")
        assert "## last 10 orders" in md
        assert '<List model="sales"' in md
        assert 'columns="region, order_date"' in md
        assert 'order_by="order_date"' in md
        assert "desc" in md
        assert "limit=10" in md
        # No filters were set → the comment carries no "filters not carried over".
        assert "filters not carried over" not in md

    def test_keep_list_notes_dropped_filters(self, tmp_path):
        # A list answer that carried a filter is kept, but the comment flags that
        # the filter isn't carried into the file (v1).
        client = _semantic_client(tmp_path, FakeAdapter())
        page = client.app.state.project.root / "pages" / "keep_filtered.md"
        page.write_text("# Keep\n", encoding="utf-8")
        r = client.post(
            "/_dashdown/api/ask/keep",
            json={
                "question": "won orders",
                "resolved": {
                    "kind": "list",
                    "provenance": "list: sales — region",
                    "detail": {
                        "model": "sales",
                        "columns": ["region", "status"],
                        "order_by": "region",
                        "desc": False,
                        "limit": 10,
                        "filters": {"status": ["Won"]},
                    },
                },
                "chart": None,
                "path": "/keep_filtered",
            },
        )
        assert r.status_code == 200, r.text
        md = page.read_text(encoding="utf-8")
        assert '<List model="sales"' in md
        assert "desc=false" in md  # descending default overridden explicitly
        assert "filters not carried over" in md

    def test_keep_list_off_catalog_columns_raise(self, tmp_path):
        # A list whose columns are all off-catalog fails re-validation → 400.
        client = _semantic_client(tmp_path, FakeAdapter())
        (client.app.state.project.root / "pages" / "keep_bad.md").write_text(
            "# Keep\n", encoding="utf-8"
        )
        r = client.post(
            "/_dashdown/api/ask/keep",
            json={
                "question": "ghosts",
                "resolved": {
                    "kind": "list",
                    "provenance": "list: sales — ghost",
                    "detail": {
                        "model": "sales",
                        "columns": ["ghost", "phantom"],
                        "order_by": "ghost",
                        "desc": True,
                        "limit": 10,
                        "filters": {},
                    },
                },
                "chart": None,
                "path": "/keep_bad",
            },
        )
        assert r.status_code == 400
        assert "re-validation" in r.json()["detail"]

    def test_keep_still_refuses_sql(self, tmp_path):
        # A raw-sql answer stays unkeepable (no named artifact).
        client = _semantic_client(tmp_path, FakeAdapter())
        (client.app.state.project.root / "pages" / "keep_sql.md").write_text(
            "# Keep\n", encoding="utf-8"
        )
        r = client.post(
            "/_dashdown/api/ask/keep",
            json={
                "question": "raw",
                "resolved": {"kind": "sql", "detail": {"sql": "SELECT 1"}},
                "chart": None,
                "path": "/keep_sql",
            },
        )
        assert r.status_code == 400
        assert "raw SQL has no named source" in r.json()["detail"]


@needs_bsl
def test_keep_markdown_emits_series(tmp_path):
    from dashdown.ask_engine import build_kept_markdown
    from dashdown.project import load_project

    dst = tmp_path / "sem_proj"
    shutil.copytree(
        _SEMANTIC_EXAMPLE,
        dst,
        ignore=shutil.ignore_patterns("__pycache__", "*.duckdb*", "sources.yaml"),
    )
    (dst / "sources.yaml").write_text("main:\n  type: csv\n  directory: data\n")
    proj = load_project(dst)
    try:
        md, _keep_id = build_kept_markdown(
            proj,
            "Revenue by region per status?",
            {
                "kind": "semantic",
                "provenance": "semantic: sales.revenue by region per status",
                "detail": {
                    "model": "sales",
                    "metric": "revenue",
                    "by": "region",
                    "series": "status",
                    "grain": None,
                    "filters": {},
                },
            },
            {"type": "bar", "x": "sales.region", "y": "sales.revenue",
             "series_by": "sales.status"},
        )
    finally:
        proj.close()
    assert "series={sales.status}" in md
    assert "by={sales.region}" in md
    assert "<Ask metric={sales.revenue} by={sales.region} series={sales.status}" in md


# --------------------------------------------------------------------------- #
# SQL rung: schema hint + SELECT-only guard
# --------------------------------------------------------------------------- #
class TestSqlRungArming:
    def test_schema_hint_in_prompt_only_when_allow_sql(self, tmp_path):
        fake = FakeAdapter(
            '{"kind": "query", "name": "by_region", "params": {}}', "A.",
        )
        client = _client(tmp_path, fake=fake, extra_yaml="ask:\n  allow_sql: true\n")
        client.post("/_dashdown/api/ask", json={"question": "schema probe on"})
        assert "sql_tables" in fake.calls[0][1]
        # The CSV project's DuckDB view shows up with its columns.
        assert "sales" in fake.calls[0][1]

    def test_no_schema_hint_when_sql_disabled(self, tmp_path):
        fake = FakeAdapter(
            '{"kind": "query", "name": "by_region", "params": {}}', "A.",
        )
        client = _client(tmp_path, fake=fake)
        client.post("/_dashdown/api/ask", json={"question": "schema probe off"})
        assert "sql_tables" not in fake.calls[0][1]

    def test_non_select_sql_is_rejected_with_repair(self, tmp_path):
        # A mutating statement is a validation failure: one self-repair retry,
        # then none. Nothing executes.
        fake = FakeAdapter(
            '{"kind": "sql", "sql": "DROP TABLE sales"}',
            '{"kind": "sql", "sql": "DELETE FROM sales"}',
        )
        client = _client(tmp_path, fake=fake, extra_yaml="ask:\n  allow_sql: true\n")
        body = client.post("/_dashdown/api/ask", json={"question": "drop it"}).json()
        assert body["resolved"]["kind"] == "none"
        assert "SELECT" in body["answer_text"]
        # The data survived: an honest query still answers.
        fake2 = FakeAdapter(
            '{"kind": "sql", "sql": "WITH t AS (SELECT region FROM sales) '
            'SELECT * FROM t"}',
            "Fine.",
        )
        second = tmp_path / "second"
        second.mkdir()
        client2 = _client(second, fake=fake2, extra_yaml="ask:\n  allow_sql: true\n")
        body2 = client2.post("/_dashdown/api/ask", json={"question": "regions raw"}).json()
        assert body2["resolved"]["kind"] == "sql"
        assert len(body2["rows"]) == 3


# --------------------------------------------------------------------------- #
# Chart preference: "as a funnel" is presentation, never a refusal
# --------------------------------------------------------------------------- #
class TestChartPref:
    def test_funnel_pref_overrides_inferred_bar(self, tmp_path):
        fake = FakeAdapter(
            '{"kind": "query", "name": "by_region", "params": {}, "chart": "funnel"}',
            "Regions as a funnel.",
        )
        client = _client(tmp_path, fake=fake)
        body = client.post(
            "/_dashdown/api/ask", json={"question": "funnel of revenue by region"}
        ).json()
        assert body["resolved"]["kind"] == "query"
        assert body["resolved"]["detail"]["chart"] == "funnel"
        assert body["chart"]["type"] == "funnel"
        # Same x/y roles; ordering hint dropped (funnel sorts itself).
        assert body["chart"]["x"] == "region" and body["chart"]["y"] == "total"
        assert "sort_by" not in body["chart"]

    def test_invalid_pref_is_soft_dropped(self, tmp_path):
        fake = FakeAdapter(
            '{"kind": "query", "name": "by_region", "params": {}, "chart": "hologram"}',
            "A.",
        )
        client = _client(tmp_path, fake=fake)
        body = client.post("/_dashdown/api/ask", json={"question": "hologram pls"}).json()
        assert body["chart"]["type"] == "bar"  # inference kept
        assert "chart" not in body["resolved"]["detail"]

    @needs_bsl
    def test_funnel_pref_dropped_on_series_split(self, tmp_path):
        # funnel can't express a split — the pref is dropped, pie would be kept.
        fake = FakeAdapter(
            '{"kind": "semantic", "model": "sales", "metric": "revenue", '
            '"by": "region", "series": "status", "chart": "funnel"}',
            "Split.",
        )
        client = _semantic_client(tmp_path, fake)
        body = client.post("/_dashdown/api/ask", json={"question": "x"}).json()
        assert body["chart"]["type"] == "bar"
        assert body["chart"]["series_by"]

    @needs_bsl
    def test_pie_pref_survives_series_as_facets(self, tmp_path):
        fake = FakeAdapter(
            '{"kind": "semantic", "model": "sales", "metric": "revenue", '
            '"by": "region", "series": "status", "chart": "pie"}',
            "Facets.",
        )
        client = _semantic_client(tmp_path, fake)
        body = client.post("/_dashdown/api/ask", json={"question": "pies"}).json()
        assert body["chart"]["type"] == "pie"

    @needs_bsl
    def test_execute_spec_chart_pref_round_trip(self, tmp_path):
        fake = FakeAdapter("never")
        client = _semantic_client(tmp_path, fake)
        spec = dict(_SEMANTIC_SPEC, chart="pie")
        body = client.post(
            "/_dashdown/api/ask/execute",
            json={"question": "q", "spec": spec, "commentary": False},
        ).json()
        assert body["chart"]["type"] == "pie"
        assert body["resolved"]["detail"]["chart"] == "pie"
        assert fake.calls == []

    @needs_bsl
    def test_keep_maps_funnel_component(self, tmp_path):
        import shutil as _sh

        from dashdown.ask_engine import build_kept_markdown
        from dashdown.project import load_project

        dst = tmp_path / "sem_proj"
        _sh.copytree(
            _SEMANTIC_EXAMPLE, dst,
            ignore=_sh.ignore_patterns("__pycache__", "*.duckdb*", "sources.yaml"),
        )
        (dst / "sources.yaml").write_text("main:\n  type: csv\n  directory: data\n")
        proj = load_project(dst)
        try:
            md, _keep_id = build_kept_markdown(
                proj,
                "Revenue funnel by region?",
                {
                    "kind": "semantic",
                    "provenance": "semantic: sales.revenue by region",
                    "detail": {"model": "sales", "metric": "revenue", "by": "region",
                               "grain": None, "filters": {}},
                },
                {"type": "funnel", "x": "sales.region", "y": "sales.revenue"},
            )
        finally:
            proj.close()
        assert "<FunnelChart " in md

    def test_prompt_offers_chart_pref(self):
        from dashdown.ask_engine import RESOLVER_SYSTEM_PROMPT

        assert "funnel" in RESOLVER_SYSTEM_PROMPT
        assert "never a reason to refuse" in RESOLVER_SYSTEM_PROMPT


# --------------------------------------------------------------------------- #
# Staged SSE streaming — POST /api/ask with stream:true
# --------------------------------------------------------------------------- #
class RaisingAdapter(LLMAdapter):
    """An adapter whose every completion raises — to exercise the streamed
    error path (an LLM failure after SSE headers are sent)."""

    def __init__(self):
        super().__init__(LLMConfig(provider="mistral", api_key="test"))
        self.calls = 0

    def complete(self, system: str, prompt: str) -> str:
        self.calls += 1
        raise RuntimeError("boom")


def _parse_sse(text: str) -> list[tuple[str, dict | None]]:
    """Parse an SSE body into an ordered list of ``(event, data)`` frames."""
    import json as _json

    events: list[tuple[str, dict | None]] = []
    for frame in text.split("\n\n"):
        frame = frame.strip()
        if not frame:
            continue
        event = None
        data_lines: list[str] = []
        for line in frame.split("\n"):
            if line.startswith("event:"):
                event = line[len("event:") :].strip()
            elif line.startswith("data:"):
                data_lines.append(line[len("data:") :].strip())
        data = _json.loads("\n".join(data_lines)) if data_lines else None
        events.append((event, data))
    return events


class TestStreaming:
    def test_stream_yields_resolved_then_done(self, tmp_path):
        # A cache-miss stream: `resolved` ships rows+chart with an empty answer;
        # `done` carries the rendered commentary. (Query rung — no bsl needed.)
        fake = FakeAdapter(
            '{"kind": "query", "name": "by_region", "params": {}}',
            "**North** leads the regions.",
        )
        client = _client(tmp_path, fake=fake)
        with client.stream(
            "POST",
            "/_dashdown/api/ask",
            json={"question": "revenue by region", "stream": True},
        ) as r:
            assert r.status_code == 200
            assert "text/event-stream" in r.headers["content-type"]
            body = "".join(r.iter_text())
        events = _parse_sse(body)
        kinds = [e for e, _ in events]
        assert kinds == ["resolved", "done"], kinds

        _, resolved = events[0]
        assert resolved["resolved"]["kind"] == "query"
        assert len(resolved["rows"]) == 3  # North/South/West
        assert resolved["chart"]["type"] == "bar"
        assert resolved["answer_html"] == ""
        assert resolved["answer_text"] == ""
        assert resolved["annotations"] == []
        assert resolved["cached"] is False

        _, done = events[1]
        assert "<strong>North</strong>" in done["answer_html"]
        assert done["answer_text"] == "**North** leads the regions."
        assert done["cached"] is False

    def test_stream_cache_hit_carries_full_answer(self, tmp_path):
        # Prime the cache with a non-stream ask, then stream the same question →
        # `resolved` carries cached:true AND the full answer; no new LLM calls.
        fake = FakeAdapter(
            '{"kind": "query", "name": "by_region", "params": {}}',
            "Cached commentary.",
        )
        client = _client(tmp_path, fake=fake)
        first = client.post(
            "/_dashdown/api/ask", json={"question": "revenue by region"}
        )
        assert first.status_code == 200
        assert first.json()["cached"] is False
        calls_after = len(fake.calls)

        with client.stream(
            "POST",
            "/_dashdown/api/ask",
            json={"question": "revenue by region", "stream": True},
        ) as r:
            assert "text/event-stream" in r.headers["content-type"]
            body = "".join(r.iter_text())
        events = _parse_sse(body)
        assert [e for e, _ in events] == ["resolved", "done"]
        _, resolved = events[0]
        assert resolved["cached"] is True
        assert "Cached commentary." == resolved["answer_text"]
        _, done = events[1]
        assert done["cached"] is True
        assert "Cached commentary." == done["answer_text"]
        assert len(fake.calls) == calls_after  # cache hit — no re-billing

    def test_stream_kind_none(self, tmp_path):
        # An explicit none → `resolved` carries the none payload (reason as the
        # answer), then `done` follows immediately.
        fake = FakeAdapter('{"kind": "none", "reason": "not in the catalog"}')
        client = _client(tmp_path, fake=fake)
        with client.stream(
            "POST",
            "/_dashdown/api/ask",
            json={"question": "the weather?", "stream": True},
        ) as r:
            assert "text/event-stream" in r.headers["content-type"]
            body = "".join(r.iter_text())
        events = _parse_sse(body)
        assert [e for e, _ in events] == ["resolved", "done"]
        _, resolved = events[0]
        assert resolved["resolved"]["kind"] == "none"
        assert resolved["columns"] is None
        assert resolved["chart"] is None
        assert "not in the catalog" in resolved["answer_text"]
        _, done = events[1]
        assert "not in the catalog" in done["answer_text"]

    def test_stream_absent_is_plain_json(self, tmp_path):
        # No stream flag → byte-identical single-JSON behavior (the CLI/tests rely
        # on this). Explicitly assert the content-type is JSON, not event-stream.
        fake = FakeAdapter(
            '{"kind": "query", "name": "by_region", "params": {}}', "Ans.",
        )
        client = _client(tmp_path, fake=fake)
        r = client.post(
            "/_dashdown/api/ask", json={"question": "revenue by region"}
        )
        assert r.status_code == 200
        assert "application/json" in r.headers["content-type"]
        body = r.json()
        assert body["resolved"]["kind"] == "query"
        assert body["answer_text"] == "Ans."

    def test_stream_llm_failure_is_error_event(self, tmp_path):
        # The resolver call raises *after* headers are sent → an `error` event
        # (not a pre-header status code), and no `resolved` is emitted.
        client = _client(tmp_path, fake=RaisingAdapter())
        with client.stream(
            "POST",
            "/_dashdown/api/ask",
            json={"question": "revenue by region", "stream": True},
        ) as r:
            assert r.status_code == 200
            assert "text/event-stream" in r.headers["content-type"]
            body = "".join(r.iter_text())
        events = _parse_sse(body)
        assert [e for e, _ in events] == ["error"], events
        _, err = events[0]
        assert "detail" in err
        assert "LLM request failed" in err["detail"]

    def test_stream_query_failure_is_error_event(self, tmp_path):
        # A resolution onto a library query whose SQL is broken fails during
        # execution — *after* SSE headers are sent, before any `resolved` — so the
        # stream carries exactly one `error` event (never a pre-header 500).
        proj = tmp_path / "proj"
        proj.mkdir()
        _make_lib_project(proj)
        (proj / "queries" / "broken.sql").write_text(
            "SELECT * FROM no_such_table\n", encoding="utf-8"
        )
        app = create_app(proj)
        fake = FakeAdapter('{"kind": "query", "name": "broken", "params": {}}')
        app.state.project.llm_adapter = fake
        client = TestClient(app)
        with client.stream(
            "POST",
            "/_dashdown/api/ask",
            json={"question": "boom", "stream": True},
        ) as r:
            assert r.status_code == 200
            assert "text/event-stream" in r.headers["content-type"]
            body = "".join(r.iter_text())
        events = _parse_sse(body)
        assert [e for e, _ in events] == ["error"], events
        _, err = events[0]
        assert "Query execution failed" in err["detail"]

    def test_stream_rate_limit_is_json_429_before_headers(self, tmp_path):
        # Rate-limit is checked before the stream commits → a plain-JSON 429, not
        # an SSE error (the client falls back on content-type).
        fake = FakeAdapter(
            '{"kind": "query", "name": "by_region", "params": {}}', "A.",
        )
        client = _client(tmp_path, fake=fake, extra_yaml="ask:\n  rate_limit: 1\n")
        # First (non-stream) ask burns the single-slot budget.
        first = client.post("/_dashdown/api/ask", json={"question": "first q"})
        assert first.status_code == 200
        # A distinct streamed ask is refused before any SSE headers.
        r = client.post(
            "/_dashdown/api/ask",
            json={"question": "second q", "stream": True},
        )
        assert r.status_code == 429
        assert "application/json" in r.headers["content-type"]

    def test_prompt_demands_initiative_on_open_questions(self):
        # "Show me some random chart" must trigger a choice, not a refusal —
        # the disposition rule the strict-router prompt was missing.
        from dashdown.ask_engine import RESOLVER_SYSTEM_PROMPT

        assert "underspecified" in RESOLVER_SYSTEM_PROMPT
        assert "CHOOSE, not refuse" in RESOLVER_SYSTEM_PROMPT


# --------------------------------------------------------------------------- #
# Direct-SQL passthrough: typed SQL skips the resolver (attempt-and-fallback)
# --------------------------------------------------------------------------- #
class TestDirectSqlPassthrough:
    def test_typed_sql_skips_resolver(self, tmp_path):
        # Only ONE LLM call (the answer) — the resolver never runs.
        fake = FakeAdapter("Three regions, South leads.")
        client = _client(tmp_path, fake=fake, extra_yaml="ask:\n  allow_sql: true\n")
        body = client.post(
            "/_dashdown/api/ask",
            json={"question": "SELECT region, amount FROM sales ORDER BY amount DESC"},
        ).json()
        assert body["resolved"]["kind"] == "sql"
        assert body["resolved"]["provenance"] == "raw SQL (typed directly)"
        assert len(body["rows"]) == 3
        assert len(fake.calls) == 1  # answer only, no resolver call

    def test_english_starting_with_select_falls_back(self, tmp_path):
        # "select the best region from our data" parses as SQL-ish but fails to
        # execute → silently falls back to the normal resolver path.
        fake = FakeAdapter(
            '{"kind": "query", "name": "by_region", "params": {}}',
            "North leads.",
        )
        client = _client(tmp_path, fake=fake, extra_yaml="ask:\n  allow_sql: true\n")
        body = client.post(
            "/_dashdown/api/ask",
            json={"question": "select the best region from our data please"},
        ).json()
        assert body["resolved"]["kind"] == "query"
        assert len(fake.calls) == 2  # resolver + answer, as normal

    def test_no_passthrough_when_sql_disabled(self, tmp_path):
        # allow_sql off → typed SQL is just a question for the resolver.
        fake = FakeAdapter(
            '{"kind": "query", "name": "by_region", "params": {}}', "A.",
        )
        client = _client(tmp_path, fake=fake)
        body = client.post(
            "/_dashdown/api/ask",
            json={"question": "SELECT * FROM sales"},
        ).json()
        assert body["resolved"]["kind"] == "query"
        assert len(fake.calls) == 2

    def test_typed_sql_streams_staged(self, tmp_path):
        # The passthrough rides the staged protocol like any answer.
        fake = FakeAdapter("Streamed commentary.")
        client = _client(tmp_path, fake=fake, extra_yaml="ask:\n  allow_sql: true\n")
        with client.stream(
            "POST",
            "/_dashdown/api/ask",
            json={"question": "SELECT region FROM sales", "stream": True},
        ) as resp:
            assert resp.headers["content-type"].startswith("text/event-stream")
            raw = b"".join(resp.iter_bytes()).decode("utf-8")
        assert "event: resolved" in raw and "event: done" in raw
        assert "typed directly" in raw
