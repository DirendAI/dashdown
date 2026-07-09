"""Desktop sidebar collapse (Stage 20): ``parse_sidebar_config`` unit coverage
plus ``TestClient`` integration that the collapse control renders on normal pages,
honors the ``layout.sidebar`` config (default seed + ``toggle: false``), and is
absent in chrome-less embeds."""
from __future__ import annotations

import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from dashdown.project import parse_sidebar_config
from dashdown.server import create_app


def _make_project(tmp: Path) -> Path:
    (tmp / "pages").mkdir()
    (tmp / "data").mkdir()
    (tmp / "data" / "sales.csv").write_text(
        "region,amount\nNorth,10\nSouth,20\n", encoding="utf-8"
    )
    (tmp / "sources.yaml").write_text(
        "main:\n  type: csv\n  directory: data\n", encoding="utf-8"
    )
    (tmp / "dashdown.yaml").write_text("title: Test\ntheme: light\n", encoding="utf-8")
    # Two pages → the nav is worth showing (the single-page auto-hide is tested
    # separately).
    (tmp / "pages" / "index.md").write_text("# Home\n\nHello.\n", encoding="utf-8")
    (tmp / "pages" / "about.md").write_text("# About\n\nMore.\n", encoding="utf-8")
    return tmp


def _make_single_page_project(tmp: Path) -> Path:
    proj = _make_project(tmp)
    (proj / "pages" / "about.md").unlink()  # back down to a lone index.md
    return proj


@pytest.fixture
def tmp_project():
    with tempfile.TemporaryDirectory() as d:
        yield Path(d)


def _write_yaml(proj: Path, body: str) -> None:
    (proj / "dashdown.yaml").write_text(
        "title: Test\ntheme: light\n" + body, encoding="utf-8"
    )


# --------------------------------------------------------------------------- #
# unit: parse_sidebar_config
# --------------------------------------------------------------------------- #
def test_parse_sidebar_config_defaults():
    d = parse_sidebar_config(None)
    assert d.collapsed is False
    assert d.toggle is True
    assert d.show_single_page is False
    assert d.hidden is False


def test_parse_sidebar_config_values():
    cfg = parse_sidebar_config(
        {"collapsed": True, "toggle": False, "show_single_page": True, "hidden": True}
    )
    assert cfg.collapsed is True
    assert cfg.toggle is False
    assert cfg.show_single_page is True
    assert cfg.hidden is True

    # Partial block: only the given key changes, the others keep their defaults.
    partial = parse_sidebar_config({"collapsed": True})
    assert partial.toggle is True
    assert partial.show_single_page is False
    assert partial.hidden is False


def test_parse_sidebar_config_malformed_fails_at_startup():
    # Same fail-at-startup policy as auth:/search:.
    for bad in (
        [],
        "nope",
        {"collapsed": "yes"},
        {"toggle": 1},
        {"show_single_page": 0},
        {"hidden": "true"},
    ):
        with pytest.raises(ValueError):
            parse_sidebar_config(bad)


# --------------------------------------------------------------------------- #
# integration: rendered chrome
# --------------------------------------------------------------------------- #
def test_toggle_present_by_default_and_open(tmp_project):
    proj = _make_project(tmp_project)
    html = TestClient(create_app(proj)).get("/").text
    # The desktop collapse control ships…
    assert "dashdown-sidebar-toggle" in html
    # …and the default seed is open (false → not collapsed on first visit).
    assert "var collapsed = sc === null ? false :" in html


def test_toggle_omitted_when_disabled(tmp_project):
    proj = _make_project(tmp_project)
    _write_yaml(proj, "layout:\n  sidebar:\n    toggle: false\n")
    html = TestClient(create_app(proj)).get("/").text
    assert "dashdown-sidebar-toggle" not in html


def test_collapsed_default_seeds_class(tmp_project):
    proj = _make_project(tmp_project)
    _write_yaml(proj, "layout:\n  sidebar:\n    collapsed: true\n")
    html = TestClient(create_app(proj)).get("/").text
    # First-visit seed flips to collapsed (a saved localStorage value still wins
    # at runtime — that's the `sc === null ?` guard).
    assert "var collapsed = sc === null ? true :" in html
    # The toggle is still there so the reader can re-open the nav.
    assert "dashdown-sidebar-toggle" in html


def test_toggle_absent_in_embed(tmp_project):
    """Chrome-less embed omits the header (and thus the toggle) entirely."""
    proj = _make_project(tmp_project)
    _write_yaml(
        proj,
        "embed:\n  enabled: true\n  frame_ancestors:\n    - https://h.example\n",
    )
    client = TestClient(create_app(proj))
    assert "dashdown-sidebar-toggle" in client.get("/").text
    assert "dashdown-sidebar-toggle" not in client.get("/?_embed=1").text


# --------------------------------------------------------------------------- #
# single-page auto-hide
# --------------------------------------------------------------------------- #
def test_single_page_hides_nav_and_buttons(tmp_project):
    """A project with one navigable page has nothing to navigate to, so the nav,
    the desktop toggle, and the mobile hamburger are all omitted by default."""
    proj = _make_single_page_project(tmp_project)
    html = TestClient(create_app(proj)).get("/").text
    assert "dashdown-sidebar " not in html  # the <aside class="dashdown-sidebar ...">
    assert "dashdown-sidebar-toggle" not in html
    assert "dashdown-mobile-menu-btn" not in html


def test_single_page_shown_when_forced(tmp_project):
    """`sidebar.show_single_page: true` forces the nav on even with one page."""
    proj = _make_single_page_project(tmp_project)
    _write_yaml(proj, "layout:\n  sidebar:\n    show_single_page: true\n")
    html = TestClient(create_app(proj)).get("/").text
    assert "dashdown-sidebar " in html
    assert "dashdown-sidebar-toggle" in html
    assert "dashdown-mobile-menu-btn" in html


def test_multipage_shows_nav_by_default(tmp_project):
    """The common case: >1 page → the nav and its buttons ship without config."""
    proj = _make_project(tmp_project)  # two pages
    html = TestClient(create_app(proj)).get("/").text
    assert "dashdown-sidebar " in html
    assert "dashdown-mobile-menu-btn" in html


def test_navigable_page_count_excludes_dynamic_slug(tmp_project):
    from dashdown.project import load_project

    proj = _make_single_page_project(tmp_project)  # one real page
    # A dynamic [slug] page is excluded from the nav, so it doesn't count.
    (proj / "pages" / "[slug].md").write_text("# Item ${slug}\n", encoding="utf-8")
    assert load_project(proj).navigable_page_count() == 1
    # Still single-page → nav hidden.
    assert "dashdown-sidebar-toggle" not in TestClient(create_app(proj)).get("/").text


# --------------------------------------------------------------------------- #
# sidebar.hidden — drop the nav outright (blog/article-style chrome)
# --------------------------------------------------------------------------- #
def test_hidden_removes_nav_on_multipage(tmp_project):
    """`sidebar.hidden: true` omits the nav and both menu buttons even when
    there are pages to navigate — the blog-style chrome-less case."""
    proj = _make_project(tmp_project)  # two pages
    _write_yaml(proj, "layout:\n  sidebar:\n    hidden: true\n")
    html = TestClient(create_app(proj)).get("/").text
    assert "dashdown-sidebar " not in html
    assert "dashdown-sidebar-toggle" not in html
    assert "dashdown-mobile-menu-btn" not in html


def test_hidden_overrides_show_single_page(tmp_project):
    """`hidden` wins over `show_single_page` — an explicit "never" beats an
    explicit "always"."""
    proj = _make_single_page_project(tmp_project)
    _write_yaml(proj, "layout:\n  sidebar:\n    hidden: true\n    show_single_page: true\n")
    html = TestClient(create_app(proj)).get("/").text
    assert "dashdown-sidebar " not in html
    assert "dashdown-sidebar-toggle" not in html
    assert "dashdown-mobile-menu-btn" not in html


def test_hidden_applies_to_static_build(tmp_project):
    """The same decision flows through `dashdown build` — the static-hosting
    path a chrome-less blog relies on."""
    from dashdown.build import build_site

    proj = _make_project(tmp_project)  # two pages
    _write_yaml(proj, "layout:\n  sidebar:\n    hidden: true\n")
    out = proj / ".dist"  # the CLI's default out dir; allowed by the guard
    build_site(proj, out)
    html = (out / "index.html").read_text(encoding="utf-8")
    assert "dashdown-sidebar " not in html
    assert "dashdown-sidebar-toggle" not in html
    assert "dashdown-mobile-menu-btn" not in html
