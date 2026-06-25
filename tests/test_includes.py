"""Tests for Markdown partials/includes (Stage 5)."""
import pytest

from dashdown.render.markdown import expand_includes
from dashdown.render.pipeline import render_page


def test_none_base_returns_source_unchanged():
    src = "{% include 'partials/x.md' %}"
    assert expand_includes(src, None) == src


def test_no_directive_returns_source_unchanged(tmp_path):
    src = "# Hello\n\nNo includes here."
    assert expand_includes(src, tmp_path) == src


def test_basic_include(tmp_path):
    (tmp_path / "partials").mkdir()
    (tmp_path / "partials" / "kpi.md").write_text("**KPI row**", encoding="utf-8")
    out = expand_includes("Top\n\n{% include 'partials/kpi.md' %}\n\nBottom", tmp_path)
    assert "**KPI row**" in out
    assert "{%" not in out


def test_double_quotes_supported(tmp_path):
    (tmp_path / "p.md").write_text("INNER", encoding="utf-8")
    out = expand_includes('{% include "p.md" %}', tmp_path)
    assert out == "INNER"


def test_nested_includes(tmp_path):
    (tmp_path / "a.md").write_text("A {% include 'b.md' %}", encoding="utf-8")
    (tmp_path / "b.md").write_text("B", encoding="utf-8")
    out = expand_includes("{% include 'a.md' %}", tmp_path)
    assert out == "A B"


def test_sibling_includes_same_partial_not_a_cycle(tmp_path):
    (tmp_path / "leaf.md").write_text("L", encoding="utf-8")
    out = expand_includes(
        "{% include 'leaf.md' %} {% include 'leaf.md' %}", tmp_path
    )
    assert out == "L L"


def test_missing_file_raises(tmp_path):
    with pytest.raises(ValueError, match="not found"):
        expand_includes("{% include 'nope.md' %}", tmp_path)


def test_path_traversal_blocked(tmp_path):
    secret = tmp_path.parent / "secret.md"
    secret.write_text("SECRET", encoding="utf-8")
    proj = tmp_path / "proj"
    proj.mkdir()
    with pytest.raises(ValueError, match="escapes project root"):
        expand_includes("{% include '../secret.md' %}", proj)


def test_circular_include_raises(tmp_path):
    (tmp_path / "a.md").write_text("{% include 'b.md' %}", encoding="utf-8")
    (tmp_path / "b.md").write_text("{% include 'a.md' %}", encoding="utf-8")
    with pytest.raises(ValueError, match="circular include"):
        expand_includes("{% include 'a.md' %}", tmp_path)


def test_render_page_expands_includes(tmp_path):
    (tmp_path / "partials").mkdir()
    (tmp_path / "partials" / "frag.md").write_text(
        "## Included heading", encoding="utf-8"
    )
    page = render_page(
        "# Page\n\n{% include 'partials/frag.md' %}",
        connectors={},
        include_base=tmp_path,
    )
    assert "Included heading" in page.body_html


def test_included_query_is_registered(tmp_path):
    """A partial carrying a :::query block contributes its query def."""
    (tmp_path / "partials").mkdir()
    (tmp_path / "partials" / "q.md").write_text(
        ":::query name=from_partial connector=main\n"
        "```sql\nSELECT 1\n```\n"
        ":::\n",
        encoding="utf-8",
    )
    page = render_page(
        "# Page\n\n{% include 'partials/q.md' %}",
        connectors={},
        include_base=tmp_path,
    )
    assert "from_partial" in page.query_defs
