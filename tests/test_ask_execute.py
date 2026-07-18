"""Tests for Tier-1 answer refinement on the runtime ask engine:

  * ``execute_spec`` + ``POST /_dashdown/api/ask/execute`` — the answer-panel chips
    that re-run an *edited* semantic spec without an LLM resolution call, optionally
    with one LLM call for commentary.
  * follow-up context — ``POST /_dashdown/api/ask`` with a ``previous`` block so a
    refinement resolves as a delta of the prior resolution.
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
# Follow-up context — POST /api/ask with a `previous` block
# --------------------------------------------------------------------------- #
class TestFollowUpContext:
    def test_previous_block_reaches_resolver_prompt(self, tmp_path):
        fake = FakeAdapter(
            '{"kind": "query", "name": "by_region", "params": {}}',
            "Only paid.",
        )
        client = _client(tmp_path, fake=fake)
        r = client.post(
            "/_dashdown/api/ask",
            json={
                "question": "only paid channels",
                "previous": {
                    "question": "revenue by channel",
                    "resolved": {"kind": "semantic", "detail": {"metric": "revenue"}},
                },
            },
        )
        assert r.status_code == 200, r.text
        # The resolver call's user prompt carries the follow-up context.
        _system, user = fake.calls[0]
        assert "previously asked" in user
        assert "revenue by channel" in user

    def test_no_previous_omits_context(self, tmp_path):
        fake = FakeAdapter(
            '{"kind": "query", "name": "by_region", "params": {}}', "Ans."
        )
        client = _client(tmp_path, fake=fake)
        client.post("/_dashdown/api/ask", json={"question": "revenue by region"})
        _system, user = fake.calls[0]
        assert "previously asked" not in user

    def test_same_question_different_previous_are_distinct_cache_entries(self, tmp_path):
        # Same new-question text under two different previous contexts must not
        # collide — both are cache misses (2 LLM calls each → 4 total).
        fake = FakeAdapter(
            '{"kind": "query", "name": "by_region", "params": {}}', "A.",
            '{"kind": "query", "name": "by_region", "params": {}}', "B.",
        )
        client = _client(tmp_path, fake=fake)
        r1 = client.post(
            "/_dashdown/api/ask",
            json={"question": "same follow up", "previous": {"question": "first context"}},
        )
        assert r1.json()["cached"] is False
        r2 = client.post(
            "/_dashdown/api/ask",
            json={"question": "same follow up", "previous": {"question": "second context"}},
        )
        assert r2.json()["cached"] is False
        assert len(fake.calls) == 4
