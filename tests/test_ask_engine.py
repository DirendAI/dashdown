"""Tests for the runtime ask engine (dashdown/ask_engine.py + POST /api/ask + CLI).

Layers, mirroring tests/test_ask.py: a scriptable ``FakeAdapter`` injected via
``app.state.project.llm_adapter`` (first ``complete`` returns the resolution JSON,
the second returns the answer text), the resolution ladder (semantic / library
query / sql-gated / none), server-side chart inference, the answer cache, the
JSONL ask log, and the ``dashdown ask`` CLI command.
"""
from __future__ import annotations

import json
import shutil
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from dashdown import ask_engine
from dashdown.ask_engine import (
    build_ask_catalog,
    infer_chart_shape,
    normalize_question,
    parse_resolution,
)
from dashdown.cli import app as cli_app
from dashdown.data.base import QueryResult
from dashdown.llm import LLMAdapter, LLMConfig
from dashdown.project import load_project
from dashdown.render import pipeline
from dashdown.render.pipeline import serialize_result
from dashdown.server import create_app

runner = CliRunner()

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
    """Scriptable per-call adapter: returns the queued replies in order (first the
    resolution JSON, then the answer text), then falls back to the last reply."""

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
    yaml = "title: Ask Engine Test\n"
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


# --------------------------------------------------------------------------- #
# Catalog + resolution parsing (pure)
# --------------------------------------------------------------------------- #
def test_catalog_lists_library_queries(tmp_path):
    proj = tmp_path / "proj"
    proj.mkdir()
    _make_lib_project(proj)
    project = load_project(proj)
    try:
        catalog = build_ask_catalog(project)
    finally:
        project.close()
    names = [q["name"] for q in catalog["queries"]]
    assert "by_region" in names
    (entry,) = [q for q in catalog["queries"] if q["name"] == "by_region"]
    assert entry["params"] == ["region"]
    assert entry["description"] == "Revenue by region"
    assert entry["connector"] == "main"


def test_parse_resolution_tolerates_markdown_fences(tmp_path):
    proj = tmp_path / "proj"
    proj.mkdir()
    _make_lib_project(proj)
    project = load_project(proj)
    try:
        fenced = '```json\n{"kind": "query", "name": "by_region", "params": {}}\n```'
        res = parse_resolution(fenced, project, allow_sql=False)
    finally:
        project.close()
    assert res.kind == "query"
    assert res.name == "by_region"


def test_parse_resolution_invalid_json_is_none(tmp_path):
    proj = tmp_path / "proj"
    proj.mkdir()
    _make_lib_project(proj)
    project = load_project(proj)
    try:
        res = parse_resolution("I think you want the sales table.", project, allow_sql=False)
    finally:
        project.close()
    assert res.kind == "none"
    assert res.reason


def test_parse_resolution_unknown_query_is_none(tmp_path):
    proj = tmp_path / "proj"
    proj.mkdir()
    _make_lib_project(proj)
    project = load_project(proj)
    try:
        res = parse_resolution('{"kind":"query","name":"ghost"}', project, allow_sql=False)
    finally:
        project.close()
    assert res.kind == "none"
    assert "ghost" in res.reason


# --------------------------------------------------------------------------- #
# Chart inference (mirrors chart.js::resolveAutoConfig)
# --------------------------------------------------------------------------- #
class TestChartInference:
    # infer_chart_shape takes the *serialized* payload (what the browser sees),
    # so tests serialize exactly like answer_question does.
    @staticmethod
    def _shape(columns, rows):
        return infer_chart_shape(serialize_result(QueryResult(columns=columns, rows=rows)))

    def test_temporal_x_is_line_sorted_by_x(self):
        # Temporal charts carry sort_by=x — the server ships a concrete type,
        # which skips the client's resolveAutoConfig (where auto time series
        # get their sort), so the hint must ride the config.
        shape = self._shape(
            ["week", "revenue"],
            [["2026-01-01", 10], ["2026-01-08", 20], ["2026-01-15", 15]],
        )
        assert shape == {"type": "line", "x": "week", "y": "revenue", "sort_by": "week"}

    def test_categorical_x_is_bar(self):
        shape = self._shape(
            ["region", "total"], [["North", 100], ["South", 200], ["West", 50]]
        )
        assert shape == {"type": "bar", "x": "region", "y": "total"}

    def test_numeric_x_is_scatter(self):
        shape = self._shape(["spend", "revenue"], [[10, 100], [20, 150], [30, 90]])
        assert shape == {"type": "scatter", "x": "spend", "y": "revenue"}

    def test_single_value_is_no_chart(self):
        assert self._shape(["total"], [[42]]) is None

    def test_single_row_breakdown_is_no_chart(self):
        # One row is a headline, even with a category column.
        assert self._shape(["region", "total"], [["North", 42]]) is None

    def test_no_numeric_column_is_no_chart(self):
        assert (
            self._shape(["region", "manager"], [["North", "Ann"], ["South", "Bob"]])
            is None
        )


def test_normalize_question_collapses_whitespace():
    assert normalize_question("  Which  Region\nLeads? ") == "which region leads?"


# --------------------------------------------------------------------------- #
# Endpoint — the resolution ladder end to end
# --------------------------------------------------------------------------- #
class TestAskEndpoint:
    def test_disabled_llm_returns_notice(self, tmp_path):
        client = _client(tmp_path, fake=None, llm=False)
        r = client.post("/_dashdown/api/ask", json={"question": "revenue by region"})
        assert r.status_code == 200
        body = r.json()
        assert body["answer_html"] == ""
        assert "no LLM provider is configured" in body["notice"]

    def test_disabled_ask_returns_notice(self, tmp_path):
        client = _client(
            tmp_path, fake=FakeAdapter(), extra_yaml="ask:\n  enabled: false\n"
        )
        r = client.post("/_dashdown/api/ask", json={"question": "revenue by region"})
        assert r.status_code == 200
        assert "disabled" in r.json()["notice"]

    def test_empty_question_is_400(self, tmp_path):
        client = _client(tmp_path, fake=FakeAdapter())
        assert client.post("/_dashdown/api/ask", json={"question": "  "}).status_code == 400
        assert client.post("/_dashdown/api/ask", json={}).status_code == 400

    def test_library_query_resolution_with_params(self, tmp_path):
        fake = FakeAdapter(
            '{"kind": "query", "name": "by_region", "params": {"region": "North"}}',
            "Revenue comes only from **North** here.",
        )
        client = _client(tmp_path, fake=fake)
        r = client.post("/_dashdown/api/ask", json={"question": "revenue in the north"})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["resolved"]["kind"] == "query"
        assert body["resolved"]["query_name"] == "by_region"
        assert body["resolved"]["connector"] == "main"
        assert "by_region" in body["resolved"]["provenance"]
        # The param substituted → only the North row reached the result.
        assert body["columns"] == ["region", "total"]
        assert len(body["rows"]) == 1
        assert body["rows"][0][0] == "North"
        assert "<strong>North</strong>" in body["answer_html"]
        assert body["cached"] is False
        # Two LLM calls: resolve, then answer.
        assert len(fake.calls) == 2

    def test_multi_row_result_infers_chart(self, tmp_path):
        fake = FakeAdapter(
            '{"kind": "query", "name": "by_region", "params": {}}',
            "North leads.",
        )
        client = _client(tmp_path, fake=fake)
        r = client.post("/_dashdown/api/ask", json={"question": "revenue by region"})
        body = r.json()
        assert body["chart"] == {"type": "bar", "x": "region", "y": "total"}
        assert len(body["rows"]) == 3

    def test_allow_sql_off_rejects_sql_kind(self, tmp_path):
        fake = FakeAdapter('{"kind": "sql", "sql": "SELECT 1"}')
        client = _client(tmp_path, fake=fake)
        r = client.post("/_dashdown/api/ask", json={"question": "raw please"})
        assert r.status_code == 200
        body = r.json()
        assert body["resolved"]["kind"] == "none"
        assert body["columns"] is None
        assert body["chart"] is None
        assert "allow_sql" in body["answer_html"] or "allow_sql" in body["answer_text"]
        # No second (answer) LLM call for a `none` resolution.
        assert len(fake.calls) == 1

    def test_allow_sql_on_runs_raw_sql(self, tmp_path):
        fake = FakeAdapter(
            '{"kind": "sql", "sql": "SELECT region, SUM(amount) AS total '
            "FROM sales GROUP BY region ORDER BY total DESC\"}",
            "South is the largest.",
        )
        client = _client(tmp_path, fake=fake, extra_yaml="ask:\n  allow_sql: true\n")
        r = client.post("/_dashdown/api/ask", json={"question": "revenue by region raw"})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["resolved"]["kind"] == "sql"
        assert len(body["rows"]) == 3
        assert len(fake.calls) == 2

    def test_invalid_json_degrades_to_none(self, tmp_path):
        fake = FakeAdapter("Sorry, I cannot help with that.")
        client = _client(tmp_path, fake=fake)
        r = client.post("/_dashdown/api/ask", json={"question": "??"})
        assert r.status_code == 200
        body = r.json()
        assert body["resolved"]["kind"] == "none"
        assert body["answer_text"]  # the model's reason rides in the answer
        assert len(fake.calls) == 1

    def test_none_is_cached_only_briefly(self, tmp_path):
        # A "none" may be a transient resolver misroute; caching it for the full
        # cache_ttl would pin a valid question as unanswerable for an hour.
        fake = FakeAdapter("garbage", "garbage")
        client = _client(tmp_path, fake=fake)
        client.post("/_dashdown/api/ask", json={"question": "flaky question"})
        entries = list(ask_engine._answer_cache.values())
        assert len(entries) == 1
        _, expiry = entries[0]
        assert expiry - time.monotonic() <= ask_engine.NONE_ANSWER_TTL + 1

    def test_non_object_body_is_400_not_422(self, tmp_path):
        # The endpoint owns its malformed-body contract (400 + message), so the
        # body param must accept arbitrary JSON instead of letting FastAPI 422.
        client = _client(tmp_path, fake=FakeAdapter("x"))
        for payload in ("just a string", [1, 2, 3], None):
            r = client.post("/_dashdown/api/ask", json=payload)
            assert r.status_code == 400, r.text


    def test_cache_hit_skips_llm(self, tmp_path):
        fake = FakeAdapter(
            '{"kind": "query", "name": "by_region", "params": {}}',
            "North leads.",
        )
        client = _client(tmp_path, fake=fake)
        r1 = client.post("/_dashdown/api/ask", json={"question": "revenue by region"})
        assert r1.json()["cached"] is False
        assert len(fake.calls) == 2
        # Same question → served from the answer cache, no new LLM calls.
        r2 = client.post("/_dashdown/api/ask", json={"question": "revenue by region"})
        assert r2.json()["cached"] is True
        assert len(fake.calls) == 2

    def test_refresh_bypasses_cache(self, tmp_path):
        fake = FakeAdapter(
            '{"kind": "query", "name": "by_region", "params": {}}',
            "North leads.",
            '{"kind": "query", "name": "by_region", "params": {}}',
            "North still leads.",
        )
        client = _client(tmp_path, fake=fake)
        client.post("/_dashdown/api/ask", json={"question": "revenue by region"})
        r = client.post(
            "/_dashdown/api/ask",
            json={"question": "revenue by region", "refresh": True},
        )
        assert r.json()["cached"] is False
        assert len(fake.calls) == 4

    def test_query_execution_failure_is_500(self, tmp_path):
        # A library query whose name resolves but whose SQL is broken → 500.
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
        r = client.post("/_dashdown/api/ask", json={"question": "boom"})
        assert r.status_code == 500

    def test_llm_failure_is_502(self, tmp_path):
        class BoomAdapter(FakeAdapter):
            def complete(self, system, prompt):
                raise RuntimeError("rate limited")

        client = _client(tmp_path, fake=BoomAdapter())
        r = client.post("/_dashdown/api/ask", json={"question": "revenue by region"})
        assert r.status_code == 502
        assert "rate limited" in r.json()["detail"]

    def test_ask_log_written(self, tmp_path):
        fake = FakeAdapter(
            '{"kind": "query", "name": "by_region", "params": {}}',
            "North leads.",
        )
        proj = tmp_path / "proj"
        proj.mkdir()
        _make_lib_project(proj)
        app = create_app(proj)
        app.state.project.llm_adapter = fake
        client = TestClient(app)
        client.post("/_dashdown/api/ask", json={"question": "revenue by region"})
        log_path = proj / ".dashdown" / "ask_log.jsonl"
        assert log_path.is_file()
        (line,) = log_path.read_text(encoding="utf-8").splitlines()
        entry = json.loads(line)
        assert entry["kind"] == "query"
        assert entry["rows"] == 3
        assert entry["question"] == "revenue by region"
        assert "provenance" in entry and "duration_ms" in entry

    def test_ask_log_opt_out(self, tmp_path):
        fake = FakeAdapter(
            '{"kind": "query", "name": "by_region", "params": {}}',
            "North leads.",
        )
        proj = tmp_path / "proj"
        proj.mkdir()
        _make_lib_project(proj, extra_yaml="ask:\n  log: false\n")
        app = create_app(proj)
        app.state.project.llm_adapter = fake
        client = TestClient(app)
        client.post("/_dashdown/api/ask", json={"question": "revenue by region"})
        assert not (proj / ".dashdown" / "ask_log.jsonl").exists()


# --------------------------------------------------------------------------- #
# Template context — ask_enabled threaded into page()
# --------------------------------------------------------------------------- #
class TestAskEnabledTemplate:
    def test_page_renders_with_ask_enabled(self, tmp_path):
        # The template treats ask_enabled default-false; here we only assert the
        # page renders (200) with the llm block on — the frontend markup is
        # another agent's scope, so we don't assert on specific ask-box HTML.
        client = _client(tmp_path, fake=FakeAdapter())
        assert client.get("/").status_code == 200


# --------------------------------------------------------------------------- #
# Semantic resolution (needs the BSL/Ibis extra) — end to end through execution
# --------------------------------------------------------------------------- #
def _semantic_project(tmp_path: Path) -> Path:
    dst = tmp_path / "sem_proj"
    shutil.copytree(
        _SEMANTIC_EXAMPLE,
        dst,
        ignore=shutil.ignore_patterns("__pycache__", "*.duckdb*", "sources.yaml"),
    )
    (dst / "sources.yaml").write_text("main:\n  type: csv\n  directory: data\n")
    cfg = (dst / "dashdown.yaml").read_text()
    cfg += "\nllm:\n  provider: mistral\n  api_key: dummy\n"
    (dst / "dashdown.yaml").write_text(cfg)
    return dst


@needs_bsl
class TestSemanticResolution:
    def test_semantic_happy_path(self, tmp_path):
        fake = FakeAdapter(
            '{"kind": "semantic", "model": "sales", "metric": "revenue", '
            '"by": "region", "filters": {}, "date_start": "", "date_end": ""}',
            "**North** drives the most revenue.",
        )
        app = create_app(_semantic_project(tmp_path))
        app.state.project.llm_adapter = fake
        client = TestClient(app)

        r = client.post(
            "/_dashdown/api/ask",
            json={"question": "which region drives revenue?"},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["resolved"]["kind"] == "semantic"
        assert body["resolved"]["query_name"].startswith("_sem.")
        assert body["resolved"]["connector"] == "main"
        assert body["resolved"]["detail"]["metric"] == "revenue"
        # revenue by region → categorical x → bar.
        assert body["chart"]["type"] == "bar"
        assert "<strong>North</strong>" in body["answer_html"]
        assert len(fake.calls) == 2

    def test_grain_on_categorical_by_is_dropped(self, tmp_path):
        # A hallucinated grain on a non-time dimension must not 500 — it's
        # soft-dropped (same forgiveness as unknown filter keys) and the
        # resolution still routes.
        fake = FakeAdapter(
            '{"kind": "semantic", "model": "sales", "metric": "revenue", '
            '"by": "region", "grain": "month", "filters": {}}',
            "North leads.",
        )
        app = create_app(_semantic_project(tmp_path))
        app.state.project.llm_adapter = fake
        client = TestClient(app)
        r = client.post(
            "/_dashdown/api/ask", json={"question": "revenue by region monthly"}
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["resolved"]["kind"] == "semantic"
        assert body["resolved"]["detail"]["grain"] is None

    def test_unknown_metric_is_none(self, tmp_path):
        fake = FakeAdapter(
            '{"kind": "semantic", "model": "sales", "metric": "ghost", "by": "region"}'
        )
        app = create_app(_semantic_project(tmp_path))
        app.state.project.llm_adapter = fake
        client = TestClient(app)
        r = client.post("/_dashdown/api/ask", json={"question": "ghost metric"})
        assert r.status_code == 200
        assert r.json()["resolved"]["kind"] == "none"
        assert len(fake.calls) == 1


# --------------------------------------------------------------------------- #
# CLI: `dashdown ask`
# --------------------------------------------------------------------------- #
class TestAskCLI:
    def test_ask_prints_provenance_table_and_answer(self, tmp_path, monkeypatch):
        proj = tmp_path / "proj"
        proj.mkdir()
        _make_lib_project(proj)
        fake = FakeAdapter(
            '{"kind": "query", "name": "by_region", "params": {}}',
            "North leads the pack.",
        )
        import dashdown.project as project_mod

        monkeypatch.setattr(project_mod, "create_adapter", lambda cfg: fake)
        res = runner.invoke(cli_app, ["ask", "revenue by region", "-p", str(proj)])
        assert res.exit_code == 0, res.stdout + res.stderr
        assert "Provenance:" in res.stderr
        assert "by_region" in res.stderr
        # Result table + answer land on stdout.
        assert "region" in res.stdout and "total" in res.stdout
        assert "North leads the pack." in res.stdout

    def test_ask_json_output(self, tmp_path, monkeypatch):
        proj = tmp_path / "proj"
        proj.mkdir()
        _make_lib_project(proj)
        fake = FakeAdapter(
            '{"kind": "query", "name": "by_region", "params": {}}',
            "North leads.",
        )
        import dashdown.project as project_mod

        monkeypatch.setattr(project_mod, "create_adapter", lambda cfg: fake)
        res = runner.invoke(
            cli_app, ["ask", "revenue by region", "-p", str(proj), "--json"]
        )
        assert res.exit_code == 0, res.stdout + res.stderr
        payload = json.loads(res.stdout)
        assert payload["resolved"]["kind"] == "query"
        assert payload["chart"]["type"] == "bar"

    def test_ask_disabled_llm_exits_nonzero(self, tmp_path):
        proj = tmp_path / "proj"
        proj.mkdir()
        _make_lib_project(proj, llm=False)
        res = runner.invoke(cli_app, ["ask", "revenue by region", "-p", str(proj)])
        assert res.exit_code == 1
        assert "no LLM provider" in res.stderr
