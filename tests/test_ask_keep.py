"""Tests for "keep this answer on the page" (build_kept_markdown + POST /api/ask/keep).

An operator likes a runtime ask answer and clicks "Keep on this page"; the answer
is appended to that page's markdown as a **live** section (components re-query on
every visit; the authored `<Ask>` re-answers). The security spine is that the
client is never trusted: every name in the kept payload is re-validated against the
live catalog (:func:`build_kept_markdown`) before it can land in a `.md` file.

Reuses the project fixtures from tests/test_ask_engine.py.
"""
from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from dashdown import ask_engine
from dashdown.ask_engine import build_kept_markdown
from dashdown.project import load_project
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


def _make_lib_project(root: Path, *, llm: bool = True, extra_yaml: str = "") -> None:
    """A project with a CSV source and a `by_region` library query (from test_ask_engine)."""
    (root / "pages").mkdir()
    (root / "data").mkdir()
    (root / "queries").mkdir()
    yaml = "title: Ask Keep Test\n"
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


# --------------------------------------------------------------------------- #
# build_kept_markdown (pure) — the re-validation + rendering
# --------------------------------------------------------------------------- #
class TestBuildKeptMarkdown:
    def test_query_kind_emits_chart_table_and_ask(self, tmp_path):
        proj = tmp_path / "proj"
        proj.mkdir()
        _make_lib_project(proj)
        project = load_project(proj)
        try:
            resolved = {
                "kind": "query",
                "provenance": "named query 'by_region'",
                "query_name": "by_region",
                "detail": {"name": "by_region", "params": {}},
            }
            section = build_kept_markdown(
                project,
                'revenue by "region"?',
                resolved,
                {"type": "bar", "x": "region", "y": "total"},
            )
        finally:
            project.close()
        # Heading (question preserved) + a dated provenance comment.
        assert '\n## revenue by "region"?\n' in section
        assert "<!-- kept from an ask answer" in section
        assert "named query 'by_region'" in section
        # Chart + table + Ask, all keyed on the named query.
        assert '<BarChart data={by_region} x="region" y="total"' in section
        assert "<Table data={by_region} />" in section
        assert "<Ask data={by_region}" in section
        # The double quotes in the question are escaped inside attribute values.
        assert 'title="revenue by &quot;region&quot;?"' in section
        assert 'ask="revenue by &quot;region&quot;?"' in section
        # ...but the markdown heading keeps the raw question.
        assert '## revenue by "region"?' in section

    def test_query_kind_without_chart_still_emits_table_and_ask(self, tmp_path):
        proj = tmp_path / "proj"
        proj.mkdir()
        _make_lib_project(proj)
        project = load_project(proj)
        try:
            section = build_kept_markdown(
                project,
                "total revenue",
                {"kind": "query", "detail": {"name": "by_region"}},
                None,
            )
        finally:
            project.close()
        assert "Chart" not in section
        assert "<Table data={by_region} />" in section
        assert "<Ask data={by_region}" in section

    def test_chart_xy_escaping(self, tmp_path):
        # A malicious x/y can't break out of the attribute: < / > stripped, " escaped.
        proj = tmp_path / "proj"
        proj.mkdir()
        _make_lib_project(proj)
        project = load_project(proj)
        try:
            section = build_kept_markdown(
                project,
                "q",
                {"kind": "query", "detail": {"name": "by_region"}},
                {"type": "bar", "x": 'a"/><script>', "y": "total"},
            )
        finally:
            project.close()
        assert "<script>" not in section
        assert "&quot;" in section
        assert 'x="a&quot;/script"' in section

    def test_sql_kind_rejected(self, tmp_path):
        proj = tmp_path / "proj"
        proj.mkdir()
        _make_lib_project(proj)
        project = load_project(proj)
        try:
            with pytest.raises(ValueError, match="raw SQL|sql"):
                build_kept_markdown(
                    project, "q", {"kind": "sql", "detail": {"sql": "SELECT 1"}}, None
                )
        finally:
            project.close()

    def test_off_catalog_query_rejected(self, tmp_path):
        proj = tmp_path / "proj"
        proj.mkdir()
        _make_lib_project(proj)
        project = load_project(proj)
        try:
            with pytest.raises(ValueError, match="ghost"):
                build_kept_markdown(
                    project, "q", {"kind": "query", "detail": {"name": "ghost"}}, None
                )
        finally:
            project.close()

    def test_empty_question_rejected(self, tmp_path):
        proj = tmp_path / "proj"
        proj.mkdir()
        _make_lib_project(proj)
        project = load_project(proj)
        try:
            with pytest.raises(ValueError):
                build_kept_markdown(
                    project, "   ", {"kind": "query", "detail": {"name": "by_region"}}, None
                )
        finally:
            project.close()

    @needs_bsl
    def test_semantic_kind_happy(self, tmp_path):
        project = load_project(_semantic_project(tmp_path))
        try:
            resolved = {
                "kind": "semantic",
                "provenance": "semantic: sales.revenue by region",
                "detail": {
                    "model": "sales",
                    "metric": "revenue",
                    "by": "region",
                    "grain": None,
                    "filters": {},
                },
            }
            section = build_kept_markdown(
                project, "revenue by region", resolved, {"type": "bar", "x": "region", "y": "revenue"}
            )
        finally:
            project.close()
        # Semantic charts reference metric/by, never data/x/y.
        assert "metric={sales.revenue}" in section
        assert "by={sales.region}" in section
        assert "data={" not in section
        assert "<BarChart" in section
        assert "<Ask metric={sales.revenue} by={sales.region}" in section
        assert 'title="revenue by region"' in section

    @needs_bsl
    def test_semantic_grain_attr_emitted(self, tmp_path):
        project = load_project(_semantic_project(tmp_path))
        try:
            resolved = {
                "kind": "semantic",
                "provenance": "semantic",
                "detail": {
                    "model": "sales",
                    "metric": "revenue",
                    "by": "order_date",
                    "grain": "month",
                    "filters": {},
                },
            }
            section = build_kept_markdown(
                project, "revenue over time", resolved,
                {"type": "line", "x": "order_date", "y": "revenue", "sort_by": "order_date"},
            )
        finally:
            project.close()
        assert "metric={sales.revenue}" in section
        assert "by={sales.order_date}" in section
        assert 'grain="month"' in section
        assert "<LineChart" in section

    @needs_bsl
    def test_semantic_off_catalog_metric_rejected(self, tmp_path):
        project = load_project(_semantic_project(tmp_path))
        try:
            resolved = {
                "kind": "semantic",
                "detail": {"model": "sales", "metric": "ghost", "by": "region"},
            }
            with pytest.raises(ValueError, match="re-validation|ghost"):
                build_kept_markdown(project, "q", resolved, None)
        finally:
            project.close()


# --------------------------------------------------------------------------- #
# Endpoint — POST /_dashdown/api/ask/keep
# --------------------------------------------------------------------------- #
class TestKeepEndpoint:
    def _proj(self, tmp_path: Path, **kw) -> Path:
        proj = tmp_path / "proj"
        proj.mkdir()
        _make_lib_project(proj, **kw)
        return proj

    def test_non_dev_app_is_403(self, tmp_path):
        app = create_app(self._proj(tmp_path), dev=False)
        client = TestClient(app)
        r = client.post(
            "/_dashdown/api/ask/keep",
            json={
                "question": "revenue by region",
                "resolved": {"kind": "query", "detail": {"name": "by_region"}},
                "chart": {"type": "bar", "x": "region", "y": "total"},
                "path": "/",
            },
        )
        assert r.status_code == 403

    def test_unknown_page_is_404(self, tmp_path):
        app = create_app(self._proj(tmp_path))
        client = TestClient(app)
        r = client.post(
            "/_dashdown/api/ask/keep",
            json={
                "question": "q",
                "resolved": {"kind": "query", "detail": {"name": "by_region"}},
                "chart": None,
                "path": "/nope",
            },
        )
        assert r.status_code == 404

    def test_malformed_body_is_400(self, tmp_path):
        app = create_app(self._proj(tmp_path))
        client = TestClient(app)
        # non-object body
        assert client.post("/_dashdown/api/ask/keep", json="hi").status_code == 400
        # missing question
        assert (
            client.post(
                "/_dashdown/api/ask/keep",
                json={"resolved": {"kind": "query", "detail": {"name": "by_region"}}, "path": "/"},
            ).status_code
            == 400
        )
        # missing path
        assert (
            client.post(
                "/_dashdown/api/ask/keep",
                json={"question": "q", "resolved": {"kind": "query", "detail": {"name": "by_region"}}},
            ).status_code
            == 400
        )
        # unkeepable (sql) kind → build_kept_markdown ValueError → 400
        assert (
            client.post(
                "/_dashdown/api/ask/keep",
                json={"question": "q", "resolved": {"kind": "sql", "detail": {}}, "path": "/"},
            ).status_code
            == 400
        )

    def test_happy_path_appends_and_second_keep_appends_cleanly(self, tmp_path):
        proj = self._proj(tmp_path)
        page = proj / "pages" / "index.md"
        original = page.read_text(encoding="utf-8")
        app = create_app(proj)
        client = TestClient(app)

        r1 = client.post(
            "/_dashdown/api/ask/keep",
            json={
                "question": "revenue by region",
                "resolved": {
                    "kind": "query",
                    "provenance": "named query 'by_region'",
                    "detail": {"name": "by_region"},
                },
                "chart": {"type": "bar", "x": "region", "y": "total"},
                "path": "/",
            },
        )
        assert r1.status_code == 200, r1.text
        assert r1.json() == {"ok": True, "path": "/"}

        content = page.read_text(encoding="utf-8")
        assert content.startswith(original.rstrip("\n"))
        assert "## revenue by region" in content
        assert "<BarChart data={by_region}" in content
        assert "<Table data={by_region} />" in content
        assert "<Ask data={by_region}" in content
        # Exactly one blank line between the original body and the new heading.
        assert "# Home\n\n## revenue by region" in content

        # Second keep appends again cleanly (one blank line before the next section).
        r2 = client.post(
            "/_dashdown/api/ask/keep",
            json={
                "question": "another question",
                "resolved": {"kind": "query", "detail": {"name": "by_region"}},
                "chart": None,
                "path": "/",
            },
        )
        assert r2.status_code == 200, r2.text
        content2 = page.read_text(encoding="utf-8")
        assert content2.count("## ") == 2
        assert "\n\n## another question" in content2
        # No triple-newline runs crept in.
        assert "\n\n\n" not in content2
