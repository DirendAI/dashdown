"""Per-page layout: ``parse_layout_config`` / ``resolve_page_layout`` unit
coverage plus ``TestClient`` integration that a page's frontmatter (``width:`` /
``header:``) and the project-wide ``layout:`` defaults control the content-column
width class and the top-header visibility."""
from __future__ import annotations

import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from dashdown.project import (
    LayoutConfig,
    parse_layout_config,
    resolve_page_layout,
)
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
    (tmp / "pages" / "index.md").write_text("# Home\n\nHello.\n", encoding="utf-8")
    (tmp / "pages" / "about.md").write_text("# About\n\nMore.\n", encoding="utf-8")
    return tmp


@pytest.fixture
def tmp_project():
    with tempfile.TemporaryDirectory() as d:
        yield Path(d)


def _write_yaml(proj: Path, body: str) -> None:
    (proj / "dashdown.yaml").write_text(
        "title: Test\ntheme: light\n" + body, encoding="utf-8"
    )


def _write_page(proj: Path, name: str, body: str) -> None:
    (proj / "pages" / name).write_text(body, encoding="utf-8")


# --------------------------------------------------------------------------- #
# unit: parse_layout_config
# --------------------------------------------------------------------------- #
def test_parse_layout_config_defaults():
    d = parse_layout_config(None)
    assert d.width == "l"
    assert d.header is True


def test_parse_layout_config_values():
    cfg = parse_layout_config({"width": "s", "header": False})
    assert cfg.width == "s"
    assert cfg.header is False

    # Partial block: only the given key changes.
    partial = parse_layout_config({"width": "m"})
    assert partial.width == "m"
    assert partial.header is True


def test_parse_layout_config_malformed_fails_at_startup():
    # Fail-at-startup policy, same as sidebar:/auth:.
    for bad in ([], "nope", {"width": "xl"}, {"width": 2}, {"header": "yes"}):
        with pytest.raises(ValueError):
            parse_layout_config(bad)


# --------------------------------------------------------------------------- #
# unit: resolve_page_layout (frontmatter overrides config default)
# --------------------------------------------------------------------------- #
def test_resolve_page_layout_uses_config_default():
    cfg = LayoutConfig(width="m", header=False)
    assert resolve_page_layout({}, cfg) == ("m", False)


def test_resolve_page_layout_frontmatter_overrides():
    cfg = LayoutConfig(width="l", header=True)
    assert resolve_page_layout({"width": "s", "header": False}, cfg) == ("s", False)


def test_resolve_page_layout_ignores_invalid_frontmatter():
    # A bad frontmatter value is lenient: fall back to the config default rather
    # than 500-ing the page.
    cfg = LayoutConfig(width="l", header=True)
    assert resolve_page_layout({"width": "xl", "header": "sure"}, cfg) == ("l", True)


# --------------------------------------------------------------------------- #
# integration: width class
# --------------------------------------------------------------------------- #
def test_default_width_is_l(tmp_project):
    proj = _make_project(tmp_project)
    html = TestClient(create_app(proj)).get("/").text
    assert 'data-page-width="l"' in html
    assert "dashdown-content-col" in html


def test_frontmatter_width_overrides(tmp_project):
    proj = _make_project(tmp_project)
    _write_page(proj, "index.md", "---\nwidth: s\n---\n# Home\n")
    html = TestClient(create_app(proj)).get("/").text
    assert 'data-page-width="s"' in html


def test_config_width_default_and_page_override(tmp_project):
    proj = _make_project(tmp_project)
    _write_yaml(proj, "layout:\n  width: m\n")
    # A page with no frontmatter inherits the config default…
    _write_page(proj, "index.md", "# Home\n")
    # …and a page can still override it.
    _write_page(proj, "about.md", "---\nwidth: s\n---\n# About\n")
    client = TestClient(create_app(proj))
    assert 'data-page-width="m"' in client.get("/").text
    assert 'data-page-width="s"' in client.get("/about").text


# --------------------------------------------------------------------------- #
# integration: header visibility
# --------------------------------------------------------------------------- #
def test_header_shown_by_default(tmp_project):
    proj = _make_project(tmp_project)
    html = TestClient(create_app(proj)).get("/").text
    assert "dashdown-header navbar" in html
    assert "dashdown-no-header" not in html


def test_frontmatter_hides_header(tmp_project):
    proj = _make_project(tmp_project)
    _write_page(proj, "index.md", "---\nheader: false\n---\n# Home\n")
    html = TestClient(create_app(proj)).get("/").text
    assert "dashdown-header navbar" not in html
    # The layout wrapper gains the offset-reclaim class.
    assert "dashdown-no-header" in html


def test_config_hides_header_page_can_reenable(tmp_project):
    proj = _make_project(tmp_project)
    _write_yaml(proj, "layout:\n  header: false\n")
    _write_page(proj, "index.md", "# Home\n")  # inherits config → hidden
    _write_page(proj, "about.md", "---\nheader: true\n---\n# About\n")  # re-enabled
    client = TestClient(create_app(proj))
    assert "dashdown-header navbar" not in client.get("/").text
    assert "dashdown-header navbar" in client.get("/about").text
