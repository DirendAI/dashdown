"""Tests for the per-page PDF / Embed action buttons.

The PDF and Embed buttons were moved out of the app header into a right-aligned
cluster on the **breadcrumb line** (the template renders
``RenderedPage.page_actions_html`` alongside the breadcrumbs), so they show on
*every* page — including a top-level page that has no breadcrumbs. Embed is
gated on the project's ``embed.enabled`` (passed as ``embed_enabled``); both are
omitted entirely in embed mode (chrome-less).
"""
from __future__ import annotations

import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from dashdown.render.pipeline import render_page
from dashdown.server import create_app

PLAIN = "# Sales\n\nSome prose.\n"


def _render(page=PLAIN, **kwargs):
    return render_page(page, {}, **kwargs)


# --------------------------------------------------------------------------- #
# unit: the page_actions_html field
# --------------------------------------------------------------------------- #
def test_pdf_action_always_present():
    html = _render().page_actions_html
    assert 'id="dashdown-pdf-btn"' in html
    assert "dashdown-page-actions" in html


def test_embed_button_gated_by_embed_enabled():
    off = _render(embed_enabled=False).page_actions_html
    assert 'id="dashdown-pdf-btn"' in off
    assert 'id="dashdown-embed-btn"' not in off

    on = _render(embed_enabled=True).page_actions_html
    assert 'id="dashdown-pdf-btn"' in on
    assert 'id="dashdown-embed-btn"' in on


def test_actions_empty_in_embed_mode():
    # Chrome-less embed: no PDF/Embed affordances at all (matches the old header,
    # which was omitted in embeds).
    assert _render(embed=True, embed_enabled=True).page_actions_html == ""


# --------------------------------------------------------------------------- #
# integration: placement on the breadcrumb line, via the full template
# --------------------------------------------------------------------------- #
def _make_project(tmp: Path, extra_yaml: str = "") -> Path:
    (tmp / "pages").mkdir()
    # Top-level page (no breadcrumbs).
    (tmp / "pages" / "index.md").write_text("# Home\n\nWelcome.\n", encoding="utf-8")
    # Nested page (gets breadcrumbs: length > 1).
    (tmp / "pages" / "reports").mkdir()
    (tmp / "pages" / "reports" / "q1.md").write_text("# Q1\n\nNumbers.\n", encoding="utf-8")
    (tmp / "dashdown.yaml").write_text("title: Test\ntheme: light\n" + extra_yaml, encoding="utf-8")
    return tmp


_EMBED_ON = "embed:\n  enabled: true\n"


@pytest.fixture
def project():
    with tempfile.TemporaryDirectory() as d:
        yield _make_project(Path(d), _EMBED_ON)


def _topbar(html: str) -> str:
    """The .dashdown-page-topbar block (open tag → its closing </div>)."""
    start = html.index('class="dashdown-page-topbar')
    # The topbar wraps an optional <nav> + the actions <div>; grab a generous
    # slice and assert on its contents rather than balancing tags.
    return html[start : html.index("dashdown-prose")]


def test_actions_on_breadcrumb_line_top_level_page(project):
    client = TestClient(create_app(project))
    html = client.get("/").text
    # Topbar present even with no breadcrumbs, carrying the actions.
    bar = _topbar(html)
    assert 'id="dashdown-pdf-btn"' in bar
    assert 'id="dashdown-embed-btn"' in bar  # embed enabled in this project
    # No breadcrumbs on a top-level page.
    assert "dashdown-breadcrumbs" not in bar


def test_actions_share_breadcrumb_line_on_nested_page(project):
    client = TestClient(create_app(project))
    html = client.get("/reports/q1").text
    bar = _topbar(html)
    # Breadcrumbs and the actions are in the same topbar.
    assert "dashdown-breadcrumbs" in bar
    assert 'id="dashdown-pdf-btn"' in bar


def test_buttons_not_in_header(project):
    client = TestClient(create_app(project))
    html = client.get("/").text
    header = html[html.index("dashdown-header") : html.index("dashdown-layout")]
    assert "dashdown-pdf-btn" not in header
    assert "dashdown-embed-btn" not in header


def test_embed_render_omits_actions(project):
    client = TestClient(create_app(project))
    html = client.get("/?_embed=1").text
    assert "dashdown-page-topbar" not in html
    assert "dashdown-pdf-btn" not in html
    assert "dashdown-embed-btn" not in html
