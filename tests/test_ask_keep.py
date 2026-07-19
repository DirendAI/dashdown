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
from dashdown.ask_engine import build_kept_markdown, find_kept_sections
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
            section, keep_id = build_kept_markdown(
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
        # Machine-parseable marker pair wraps the whole section (id is 8 hex).
        assert len(keep_id) == 8 and all(c in "0123456789abcdef" for c in keep_id)
        assert f"<!-- dashdown:keep id={keep_id} kind=query · " in section
        assert f"<!-- /dashdown:keep id={keep_id} -->" in section
        # The opening marker sits *before* the heading (so a delete removes both).
        assert section.index(f"<!-- dashdown:keep id={keep_id}") < section.index("## ")
        # No triple-newline runs in a single kept section.
        assert "\n\n\n" not in section

    def test_query_kind_without_chart_still_emits_table_and_ask(self, tmp_path):
        proj = tmp_path / "proj"
        proj.mkdir()
        _make_lib_project(proj)
        project = load_project(proj)
        try:
            section, keep_id = build_kept_markdown(
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
            section, keep_id = build_kept_markdown(
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
            section, keep_id = build_kept_markdown(
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
            section, keep_id = build_kept_markdown(
                project, "revenue over time", resolved,
                {"type": "line", "x": "order_date", "y": "revenue", "sort_by": "order_date"},
            )
        finally:
            project.close()
        assert "metric={sales.revenue}" in section
        assert "by={sales.order_date}" in section
        assert 'grain="month"' in section
        assert "<LineChart" in section

    def test_client_provenance_never_lands_in_comment(self, tmp_path):
        # The client's `resolved.provenance` is untrusted text — a crafted value
        # must never appear in the kept-from comment; the server rebuilds it from
        # the re-validated resolution instead.
        proj = tmp_path / "proj"
        proj.mkdir()
        _make_lib_project(proj)
        project = load_project(proj)
        try:
            resolved = {
                "kind": "query",
                "provenance": "--><script>x</script>",  # malicious client value
                "query_name": "by_region",
                "detail": {"name": "by_region"},
            }
            section, keep_id = build_kept_markdown(project, "q", resolved, None)
        finally:
            project.close()
        assert "--><script>x</script>" not in section
        assert "<script>" not in section
        # The comment carries the server-derived provenance (the query rung's).
        assert "<!-- kept from an ask answer · named query 'by_region' ·" in section

    def test_comment_safe_escape_neutralizes_provenance(self, tmp_path):
        # Even a server-derived provenance is run through a comment-safe escape as
        # defense in depth: `--`/`<`/`>` can't survive to break the comment.
        proj = tmp_path / "proj"
        proj.mkdir()
        _make_lib_project(proj)
        project = load_project(proj)
        try:
            section, keep_id = build_kept_markdown(
                project,
                "q",
                {"kind": "query", "detail": {"name": "by_region"}},
                None,
            )
        finally:
            project.close()
        # Three balanced comments — the open marker, the human kept-from line, and
        # the close marker — no early `-->` smuggled in by the provenance.
        assert section.count("<!--") == 3
        assert section.count("-->") == 3

    @needs_bsl
    def test_semantic_keep_with_filters_notes_dropped(self, tmp_path):
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
                    "filters": {"region": ["North"]},
                },
            }
            section, keep_id = build_kept_markdown(
                project, "revenue by region", resolved,
                {"type": "bar", "x": "region", "y": "revenue"},
            )
        finally:
            project.close()
        # Same disclosure the list rung emits when it drops filters.
        assert "filters not carried over" in section
        # The provenance is the *validated* resolution's, naming the filter.
        assert "where region" in section

    @needs_bsl
    def test_semantic_keep_without_filters_no_note(self, tmp_path):
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
            section, keep_id = build_kept_markdown(
                project, "revenue by region", resolved,
                {"type": "bar", "x": "region", "y": "revenue"},
            )
        finally:
            project.close()
        assert "filters not carried over" not in section

    @needs_bsl
    def test_semantic_keep_with_only_date_range_notes_dropped(self, tmp_path):
        # A date-scoped semantic answer (no dimension filters) still discloses the
        # dropped scope — semantic resolutions carry a date range.
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
                    "date_start": "2020-01-01",
                    "date_end": "2020-12-31",
                },
            }
            section, keep_id = build_kept_markdown(
                project, "revenue by region in 2020", resolved,
                {"type": "bar", "x": "region", "y": "revenue"},
            )
        finally:
            project.close()
        assert "filters not carried over" in section

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
# find_kept_sections — the reader half of the marker format
# --------------------------------------------------------------------------- #
class TestFindKeptSections:
    def _section(self, project, question: str) -> tuple[str, str]:
        return build_kept_markdown(
            project, question, {"kind": "query", "detail": {"name": "by_region"}}, None
        )

    def test_roundtrip(self, tmp_path):
        proj = tmp_path / "proj"
        proj.mkdir()
        _make_lib_project(proj)
        project = load_project(proj)
        try:
            section, keep_id = self._section(project, "revenue by region")
        finally:
            project.close()
        page = "# Home\n" + section
        found = find_kept_sections(page)
        assert len(found) == 1
        sec = found[0]
        assert sec.id == keep_id
        assert sec.kind == "query"
        # The span covers marker → heading → components → close marker verbatim.
        span = page[sec.start : sec.end]
        assert span.startswith(f"<!-- dashdown:keep id={keep_id}")
        assert span.rstrip().endswith(f"<!-- /dashdown:keep id={keep_id} -->")
        assert "## revenue by region" in span

    def test_two_sections_in_order(self, tmp_path):
        proj = tmp_path / "proj"
        proj.mkdir()
        _make_lib_project(proj)
        project = load_project(proj)
        try:
            s1, id1 = self._section(project, "first question")
            s2, id2 = self._section(project, "second question")
        finally:
            project.close()
        assert id1 != id2  # ids are unique per keep
        page = "# Home\n" + s1 + "\n" + s2
        found = find_kept_sections(page)
        assert [s.id for s in found] == [id1, id2]
        assert found[0].start < found[0].end <= found[1].start < found[1].end
        # Splicing the first section out by its span deletes exactly it.
        remaining = page[: found[0].start] + page[found[0].end :]
        assert "first question" not in remaining
        assert "second question" in remaining

    def test_unclosed_marker_ignored(self, tmp_path):
        proj = tmp_path / "proj"
        proj.mkdir()
        _make_lib_project(proj)
        project = load_project(proj)
        try:
            good, good_id = self._section(project, "kept and closed")
        finally:
            project.close()
        # An opening marker with no matching close is silently skipped; a valid
        # (closed) section alongside it is still found.
        page = (
            "# Home\n"
            "<!-- dashdown:keep id=deadbeef kind=query · orphan · 2026-07-18 -->\n"
            "## orphan with no close\n"
            "<Table data={by_region} />\n" + good
        )
        found = find_kept_sections(page)
        assert [s.id for s in found] == [good_id]

    def test_malformed_marker_ignored(self):
        # A marker whose id isn't 8 hex chars doesn't match the format authority.
        page = (
            "<!-- dashdown:keep id=NOTHEX kind=query · x · 2026-07-18 -->\n"
            "## bad\n"
            "<!-- /dashdown:keep id=NOTHEX -->\n"
        )
        assert find_kept_sections(page) == []

    def test_ids_unique_across_many(self, tmp_path):
        proj = tmp_path / "proj"
        proj.mkdir()
        _make_lib_project(proj)
        project = load_project(proj)
        try:
            ids = {self._section(project, f"q{i}")[1] for i in range(50)}
        finally:
            project.close()
        assert len(ids) == 50


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

    def test_dynamic_slug_file_page_is_400(self, tmp_path):
        # A `[id].md` page is dynamic — a kept block would apply to every slug, so
        # the endpoint refuses it (400, "dynamic").
        proj = self._proj(tmp_path)
        (proj / "pages" / "[id].md").write_text("# Item\n", encoding="utf-8")
        app = create_app(proj)
        client = TestClient(app)
        r = client.post(
            "/_dashdown/api/ask/keep",
            json={
                "question": "q",
                "resolved": {"kind": "query", "detail": {"name": "by_region"}},
                "chart": None,
                "path": "/anything",
            },
        )
        assert r.status_code == 400
        assert "dynamic" in r.json()["detail"]

    def test_dynamic_slug_dir_page_is_400(self, tmp_path):
        # The nested `[id]/index.md` directory form is dynamic too.
        proj = self._proj(tmp_path)
        (proj / "pages" / "foo" / "[id]").mkdir(parents=True)
        (proj / "pages" / "foo" / "[id]" / "index.md").write_text(
            "# Item\n", encoding="utf-8"
        )
        app = create_app(proj)
        client = TestClient(app)
        r = client.post(
            "/_dashdown/api/ask/keep",
            json={
                "question": "q",
                "resolved": {"kind": "query", "detail": {"name": "by_region"}},
                "chart": None,
                "path": "/foo/bar",
            },
        )
        assert r.status_code == 400
        assert "dynamic" in r.json()["detail"]

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
        body1 = r1.json()
        assert body1["ok"] is True
        assert body1["path"] == "/"
        # Response carries the generated keep id + the new content fingerprint.
        keep_id = body1["id"]
        assert len(keep_id) == 8 and all(c in "0123456789abcdef" for c in keep_id)
        assert isinstance(body1["token"], str) and body1["token"]

        content = page.read_text(encoding="utf-8")
        assert content.startswith(original.rstrip("\n"))
        assert "## revenue by region" in content
        assert "<BarChart data={by_region}" in content
        assert "<Table data={by_region} />" in content
        assert "<Ask data={by_region}" in content
        # Both markers landed in the file, keyed on the returned id.
        assert f"<!-- dashdown:keep id={keep_id} kind=query · " in content
        assert f"<!-- /dashdown:keep id={keep_id} -->" in content
        # The response token matches the on-disk content fingerprint.
        import hashlib as _hashlib

        assert body1["token"] == _hashlib.sha1(content.encode("utf-8")).hexdigest()
        # Exactly one blank line between the original body and the new marker+heading.
        assert "# Home\n\n<!-- dashdown:keep id=" in content

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
        assert "## another question" in content2
        # One blank line separates the first section's close marker from the second
        # section's open marker (both keeps landed as marker-wrapped sections).
        assert content2.count("<!-- dashdown:keep id=") == 2
        assert content2.count("<!-- /dashdown:keep id=") == 2
        # No triple-newline runs crept in.
        assert "\n\n\n" not in content2
