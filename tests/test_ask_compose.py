"""Tests for the compose surface — instruction → typed plan → validated markdown.

`build_composed_markdown` is plan-compilation with the same trust standing as
the keep flow (model output → file), so these tests are security-relevant:
every name must re-validate against the live catalog, free text must be escaped
and capped, and a failing entry must drop with a reason (never a 500, never a
silent difference between preview and apply).

Reuses the project fixtures from tests/test_ask_engine.py.
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from dashdown import ask_compose, ask_engine
from dashdown.ask_compose import build_composed_markdown, compose_plan
from dashdown.ask_engine import find_kept_sections
from dashdown.project import load_project
from dashdown.render import pipeline
from dashdown.server import create_app

from tests.test_ask_engine import FakeAdapter, _make_lib_project

try:
    import boring_semantic_layer  # noqa: F401

    _bsl_installed = True
except ImportError:
    _bsl_installed = False

needs_bsl = pytest.mark.skipif(not _bsl_installed, reason="requires dashdown-md[semantic]")


@pytest.fixture(autouse=True)
def _clear_caches():
    """Def caches are module-global; isolate every test (mirrors test_ask_engine)."""

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


_SEMANTIC_EXAMPLE = Path(__file__).parent / "fixtures" / "semantic_first_class"


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


def _lib_project(tmp_path: Path) -> Path:
    proj = tmp_path / "proj"
    proj.mkdir()
    _make_lib_project(proj)
    return proj


# --------------------------------------------------------------------------- #
# build_composed_markdown — pure plan compilation
# --------------------------------------------------------------------------- #
class TestBuildComposedMarkdown:
    def test_query_table_entry(self, tmp_path):
        project = load_project(_lib_project(tmp_path))
        try:
            plan = {"sections": [{"element": "table", "query": "by_region"}]}
            section, keep_id, dropped = build_composed_markdown(
                project, "add the revenue table", plan
            )
        finally:
            project.close()
        assert "<Table data={by_region} />" in section
        assert dropped == []
        assert f"<!-- dashdown:keep id={keep_id} kind=composed · " in section
        assert "compose: add the revenue table" in section
        assert f"<!-- /dashdown:keep id={keep_id} -->" in section
        # The written marker pair is readable by the standard section reader.
        found = find_kept_sections("body\n" + section)
        assert len(found) == 1 and found[0].kind == "composed"

    def test_query_chart_entry_is_auto_chart(self, tmp_path):
        project = load_project(_lib_project(tmp_path))
        try:
            plan = {
                "sections": [
                    {"element": "chart", "query": "by_region", "title": "Revenue"}
                ]
            }
            section, _, dropped = build_composed_markdown(project, "add a chart", plan)
        finally:
            project.close()
        assert '<Chart auto data={by_region} title="Revenue" />' in section
        assert dropped == []

    def test_title_heading_and_prose(self, tmp_path):
        project = load_project(_lib_project(tmp_path))
        try:
            plan = {
                "title": "Key metrics",
                "sections": [
                    {"element": "heading", "text": "Overview"},
                    {"element": "prose", "text": "Revenue across regions."},
                    {"element": "table", "query": "by_region"},
                ],
            }
            section, _, dropped = build_composed_markdown(project, "add stuff", plan)
        finally:
            project.close()
        assert "\n## Key metrics\n" in section
        assert "\n### Overview\n" in section
        assert "Revenue across regions." in section
        assert dropped == []
        # Blocks are blank-line separated so prose and components never glue.
        assert "\n\n### Overview" in section

    def test_free_text_is_angle_escaped(self, tmp_path):
        project = load_project(_lib_project(tmp_path))
        try:
            plan = {
                "title": 'x <script>alert("1")</script>',
                "sections": [
                    {"element": "prose", "text": "a <LineChart data={x} /> b"},
                    {"element": "table", "query": "by_region"},
                ],
            }
            section, _, _ = build_composed_markdown(project, "q", plan)
        finally:
            project.close()
        assert "<script>" not in section
        assert "<LineChart data={x}" not in section
        assert "&lt;script&gt;" in section
        assert "&lt;LineChart" in section

    def test_prose_is_capped(self, tmp_path):
        project = load_project(_lib_project(tmp_path))
        try:
            plan = {
                "sections": [
                    {"element": "prose", "text": "x" * 2000},
                    {"element": "table", "query": "by_region"},
                ]
            }
            section, _, _ = build_composed_markdown(project, "q", plan)
        finally:
            project.close()
        prose_line = next(
            line for line in section.splitlines() if line.startswith("xxx")
        )
        assert len(prose_line) <= ask_compose.MAX_PROSE_CHARS + 1  # +ellipsis

    def test_unknown_query_entry_dropped_with_reason(self, tmp_path):
        project = load_project(_lib_project(tmp_path))
        try:
            plan = {
                "sections": [
                    {"element": "table", "query": "ghost"},
                    {"element": "table", "query": "by_region"},
                ]
            }
            section, _, dropped = build_composed_markdown(project, "q", plan)
        finally:
            project.close()
        assert "<Table data={by_region} />" in section
        assert "ghost" not in section
        assert len(dropped) == 1
        assert "ghost" in dropped[0]["reason"]

    def test_unknown_element_dropped(self, tmp_path):
        project = load_project(_lib_project(tmp_path))
        try:
            plan = {
                "sections": [
                    {"element": "iframe", "src": "https://evil"},
                    {"element": "table", "query": "by_region"},
                ]
            }
            section, _, dropped = build_composed_markdown(project, "q", plan)
        finally:
            project.close()
        assert "iframe" not in section
        assert len(dropped) == 1

    def test_all_entries_dropped_raises(self, tmp_path):
        project = load_project(_lib_project(tmp_path))
        try:
            plan = {"sections": [{"element": "table", "query": "ghost"}]}
            with pytest.raises(ValueError, match="no plan entry survived"):
                build_composed_markdown(project, "q", plan)
        finally:
            project.close()

    def test_model_error_plan_raises_with_reason(self, tmp_path):
        project = load_project(_lib_project(tmp_path))
        try:
            with pytest.raises(ValueError, match="only revenue data"):
                build_composed_markdown(
                    project, "q", {"error": "only revenue data is available"}
                )
        finally:
            project.close()

    def test_section_cap_drops_overflow(self, tmp_path):
        project = load_project(_lib_project(tmp_path))
        try:
            entries = [
                {"element": "table", "query": "by_region"}
                for _ in range(ask_compose.MAX_PLAN_SECTIONS + 3)
            ]
            section, _, dropped = build_composed_markdown(
                project, "q", {"sections": entries}
            )
        finally:
            project.close()
        assert section.count("<Table") == ask_compose.MAX_PLAN_SECTIONS
        assert len(dropped) == 3
        assert "capped" in dropped[0]["reason"]

    def test_empty_instruction_rejected(self, tmp_path):
        project = load_project(_lib_project(tmp_path))
        try:
            with pytest.raises(ValueError, match="empty instruction"):
                build_composed_markdown(
                    project, "  ", {"sections": [{"element": "table", "query": "by_region"}]}
                )
        finally:
            project.close()

    @needs_bsl
    def test_kpi_row_emits_grid_of_counters(self, tmp_path):
        project = load_project(_semantic_project(tmp_path))
        try:
            plan = {"sections": [{"element": "kpi_row", "metrics": ["sales.revenue"]}]}
            section, _, dropped = build_composed_markdown(project, "kpis", plan)
        finally:
            project.close()
        assert "<Grid cols=1>" in section
        assert '<Counter metric={sales.revenue} label="Revenue" />' in section
        assert dropped == []

    @needs_bsl
    def test_semantic_chart_categorical_defaults_to_bar(self, tmp_path):
        project = load_project(_semantic_project(tmp_path))
        try:
            plan = {
                "sections": [
                    {"element": "chart", "model": "sales", "metric": "revenue", "by": "region"}
                ]
            }
            section, _, _ = build_composed_markdown(project, "q", plan)
        finally:
            project.close()
        assert "<BarChart metric={sales.revenue} by={sales.region} />" in section

    @needs_bsl
    def test_semantic_off_catalog_metric_dropped(self, tmp_path):
        project = load_project(_semantic_project(tmp_path))
        try:
            plan = {
                "sections": [
                    {"element": "chart", "model": "sales", "metric": "ghost", "by": "region"},
                    {"element": "kpi_row", "metrics": ["sales.revenue"]},
                ]
            }
            section, _, dropped = build_composed_markdown(project, "q", plan)
        finally:
            project.close()
        assert "ghost" not in section
        assert len(dropped) == 1


# --------------------------------------------------------------------------- #
# compose_plan — the constrained LLM call + self-repair
# --------------------------------------------------------------------------- #
class TestComposePlan:
    def _project(self, tmp_path, fake):
        project = load_project(_lib_project(tmp_path))
        project.llm_adapter = fake
        return project

    def test_happy_path(self, tmp_path):
        plan = {"sections": [{"element": "table", "query": "by_region"}]}
        fake = FakeAdapter(json.dumps(plan))
        project = self._project(tmp_path, fake)
        try:
            out = compose_plan(project, "add the revenue table")
        finally:
            project.close()
        assert out == plan
        assert len(fake.calls) == 1
        system, user = fake.calls[0]
        assert "by_region" in user  # the catalog rode along
        assert "Instruction: add the revenue table" in user

    def test_self_repair_retries_once(self, tmp_path):
        plan = {"sections": [{"element": "table", "query": "by_region"}]}
        fake = FakeAdapter("not json at all", json.dumps(plan))
        project = self._project(tmp_path, fake)
        try:
            out = compose_plan(project, "add the table")
        finally:
            project.close()
        assert out == plan
        assert len(fake.calls) == 2
        assert "previous response was not a valid plan" in fake.calls[1][1]

    def test_unusable_after_repair_raises(self, tmp_path):
        fake = FakeAdapter("garbage", "still garbage")
        project = self._project(tmp_path, fake)
        try:
            with pytest.raises(ValueError, match="usable compose plan"):
                compose_plan(project, "add the table")
        finally:
            project.close()

    def test_model_refusal_carries_reason(self, tmp_path):
        fake = FakeAdapter(json.dumps({"error": "the catalog has no churn data"}))
        project = self._project(tmp_path, fake)
        try:
            with pytest.raises(ValueError, match="no churn data"):
                compose_plan(project, "add churn")
        finally:
            project.close()


# --------------------------------------------------------------------------- #
# Endpoints — POST /api/ask/compose (preview) + /api/ask/compose/apply (write)
# --------------------------------------------------------------------------- #
class TestComposeEndpoints:
    PLAN = {"sections": [{"element": "table", "query": "by_region"}]}

    def _app(self, tmp_path, fake=None, dev=True, **kw):
        proj = tmp_path / "proj"
        proj.mkdir()
        _make_lib_project(proj, **kw)
        app = create_app(proj, dev=dev)
        app.state.project.llm_adapter = fake
        return app, proj

    def test_non_dev_is_403(self, tmp_path):
        app, _ = self._app(tmp_path, dev=False)
        r = TestClient(app).post(
            "/_dashdown/api/ask/compose",
            json={"instruction": "add the table", "path": "/"},
        )
        assert r.status_code == 403

    def test_preview_returns_section_without_writing(self, tmp_path):
        fake = FakeAdapter(json.dumps(self.PLAN))
        app, proj = self._app(tmp_path, fake)
        before = (proj / "pages" / "index.md").read_text(encoding="utf-8")
        r = TestClient(app).post(
            "/_dashdown/api/ask/compose",
            json={"instruction": "add the revenue table", "path": "/"},
        )
        assert r.status_code == 200
        data = r.json()
        assert data["plan"] == self.PLAN
        assert "<Table data={by_region} />" in data["section"]
        assert data["dropped"] == []
        # Preview never writes.
        assert (proj / "pages" / "index.md").read_text(encoding="utf-8") == before

    def test_apply_writes_marker_wrapped_section(self, tmp_path):
        app, proj = self._app(tmp_path)
        r = TestClient(app).post(
            "/_dashdown/api/ask/compose/apply",
            json={
                "instruction": "add the revenue table",
                "plan": self.PLAN,
                "path": "/",
                "position": "end",
            },
        )
        assert r.status_code == 200
        data = r.json()
        assert data["ok"] is True
        content = (proj / "pages" / "index.md").read_text(encoding="utf-8")
        assert "<Table data={by_region} />" in content
        assert f"<!-- dashdown:keep id={data['id']} kind=composed" in content
        sections = find_kept_sections(content)
        assert len(sections) == 1 and sections[0].kind == "composed"

    def test_apply_top_position(self, tmp_path):
        app, proj = self._app(tmp_path)
        r = TestClient(app).post(
            "/_dashdown/api/ask/compose/apply",
            json={
                "instruction": "add it on top",
                "plan": self.PLAN,
                "path": "/",
                "position": "top",
            },
        )
        assert r.status_code == 200
        content = (proj / "pages" / "index.md").read_text(encoding="utf-8")
        # The page's H1 stays above the composed section.
        assert content.index("# Home") < content.index("<Table data={by_region} />")

    def test_apply_revalidates_against_live_catalog(self, tmp_path):
        app, proj = self._app(tmp_path)
        r = TestClient(app).post(
            "/_dashdown/api/ask/compose/apply",
            json={
                "instruction": "add it",
                "plan": {"sections": [{"element": "table", "query": "ghost"}]},
                "path": "/",
            },
        )
        assert r.status_code == 400
        assert "ghost" in r.json()["detail"]
        assert "ghost" not in (proj / "pages" / "index.md").read_text(encoding="utf-8")

    def test_notice_when_llm_unconfigured(self, tmp_path):
        app, _ = self._app(tmp_path, llm=False)
        r = TestClient(app).post(
            "/_dashdown/api/ask/compose",
            json={"instruction": "add the table", "path": "/"},
        )
        assert r.status_code == 200
        assert "notice" in r.json()

    def test_llm_transport_failure_is_502(self, tmp_path):
        class BoomAdapter(FakeAdapter):
            def complete(self, system, prompt):
                raise RuntimeError("connection reset")

        app, _ = self._app(tmp_path, BoomAdapter())
        r = TestClient(app).post(
            "/_dashdown/api/ask/compose",
            json={"instruction": "add the table", "path": "/"},
        )
        assert r.status_code == 502

    def test_malformed_bodies_are_400(self, tmp_path):
        app, _ = self._app(tmp_path)
        client = TestClient(app)
        assert (
            client.post("/_dashdown/api/ask/compose", json={"path": "/"}).status_code
            == 400
        )
        assert (
            client.post(
                "/_dashdown/api/ask/compose/apply",
                json={"instruction": "x", "plan": "not a dict", "path": "/"},
            ).status_code
            == 400
        )
        assert (
            client.post(
                "/_dashdown/api/ask/compose/apply",
                json={
                    "instruction": "x",
                    "plan": self.PLAN,
                    "path": "/",
                    "position": "middle",
                },
            ).status_code
            == 400
        )

    def test_rate_limit_429(self, tmp_path):
        fake = FakeAdapter(json.dumps(self.PLAN))
        app, _ = self._app(tmp_path, fake, extra_yaml="ask:\n  rate_limit: 1\n")
        client = TestClient(app)
        ok = client.post(
            "/_dashdown/api/ask/compose",
            json={"instruction": "add the table", "path": "/"},
        )
        assert ok.status_code == 200
        again = client.post(
            "/_dashdown/api/ask/compose",
            json={"instruction": "add the table again", "path": "/"},
        )
        assert again.status_code == 429
