"""Full-text search index (Stage 17): unit coverage of the index builder plus
``TestClient`` integration for the live endpoint and the static-build snapshot."""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from dashdown.build import build_site
from dashdown.project import load_project
from dashdown.search import (
    build_search_index,
    get_cached_search_index,
    index_page,
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

    (tmp / "pages" / "index.md").write_text(
        "---\ntitle: Welcome\n---\n\n"
        "# Welcome\n\nIntro prose about widgets.\n\n"
        "## Charts\n\nLine charts and bar charts.\n\n"
        "<SiteSearch />\n",
        encoding="utf-8",
    )
    (tmp / "pages" / "guide.md").write_text(
        "# Guide\n\n"
        ":::query name=q connector=main\n"
        "SELECT region, SUM(amount) AS uniquesqltoken FROM sales GROUP BY region\n"
        ":::\n\n"
        "## Connectors\n\nHook up Postgres and BigQuery.\n\n"
        "<Table data={q} />\n",
        encoding="utf-8",
    )
    # Dynamic page — excluded from the index (no enumerable URL).
    (tmp / "pages" / "[slug].md").write_text("# Dynamic ${slug}\n", encoding="utf-8")
    return tmp


@pytest.fixture
def tmp_project():
    with tempfile.TemporaryDirectory() as d:
        yield Path(d)


# --------------------------------------------------------------------------- #
# unit: index_page
# --------------------------------------------------------------------------- #
def test_index_page_uses_frontmatter_title_and_collects_headings():
    src = "---\ntitle: My Page\n---\n\n# H1\n\n## Section One\n\nbody text\n"
    entry = index_page("/x", src)
    assert entry["url"] == "/x"
    assert entry["title"] == "My Page"
    ids = [h["id"] for h in entry["headings"]]
    texts = [h["text"] for h in entry["headings"]]
    # h2/h3 are anchored by the markdown pipeline; the id is reused for deep links.
    assert "section-one" in ids
    assert "Section One" in texts
    # Heading text has no trailing permalink "#".
    assert all(not t.endswith("#") for t in texts)
    assert "body text" in entry["text"]


def test_index_page_falls_back_to_h1_then_url():
    assert index_page("/a", "# Just An H1\n\ntext")["title"] == "Just An H1"
    assert index_page("/foo/bar", "plain paragraph, no heading")["title"] == "Bar"


def test_index_page_excludes_query_sql():
    src = (
        "# Q\n\n:::query name=q connector=main\n"
        "SELECT secretcolumn FROM t\n:::\n\nvisible prose\n"
    )
    entry = index_page("/q", src)
    assert "secretcolumn" not in entry["text"]
    assert "visible prose" in entry["text"]


# --------------------------------------------------------------------------- #
# unit: build_search_index over a real project
# --------------------------------------------------------------------------- #
def test_build_search_index_covers_pages_and_skips_dynamic(tmp_project):
    proj = load_project(_make_project(tmp_project))
    index = build_search_index(proj)
    urls = {e["url"] for e in index}
    assert "/" in urls and "/guide" in urls
    assert all("[" not in u for u in urls)  # dynamic [slug] excluded

    guide = next(e for e in index if e["url"] == "/guide")
    assert guide["title"] == "Guide"
    assert "Postgres" in guide["text"]
    # SQL from the :::query block never enters the index.
    assert "uniquesqltoken" not in guide["text"]


# --------------------------------------------------------------------------- #
# integration: the live endpoint + the static-build snapshot
# --------------------------------------------------------------------------- #
def test_search_index_endpoint(tmp_project):
    client = TestClient(create_app(_make_project(tmp_project)))
    resp = client.get("/_dashdown/api/search-index")
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, list)
    assert {e["url"] for e in data} >= {"/", "/guide"}


def test_static_build_writes_search_index(tmp_project):
    proj_dir = _make_project(tmp_project)
    out = tmp_project / "dist"
    build_site(proj_dir, out)

    index_file = out / "_dashdown" / "search-index.json"
    assert index_file.is_file()
    data = json.loads(index_file.read_text(encoding="utf-8"))
    assert {e["url"] for e in data} >= {"/", "/guide"}


def test_header_search_present_and_omitted_in_embed(tmp_project):
    """The header (centered) + mobile-menu search ship on every normal page, but
    not in chrome-less embed mode (where the whole header/sidebar is omitted)."""
    embed_yaml = "embed:\n  enabled: true\n  frame_ancestors:\n    - https://h.example\n"
    proj = _make_project(tmp_project)
    (proj / "dashdown.yaml").write_text(
        "title: Test\ntheme: light\n" + embed_yaml, encoding="utf-8"
    )
    client = TestClient(create_app(proj))

    normal = client.get("/guide").text
    assert "dashdown-header-search" in normal  # centered header box
    assert "dashdown-sidebar-search" in normal  # mobile menu box

    embedded = client.get("/guide?_embed=1").text
    assert "dashdown-header-search" not in embedded
    assert "dashdown-sidebar-search" not in embedded


def test_parse_search_config():
    from dashdown.project import parse_search_config

    # Default: enabled, sensible placeholder/max_results.
    d = parse_search_config(None)
    assert d.enabled is True and d.max_results == 8

    cfg = parse_search_config(
        {"enabled": False, "placeholder": "Find…", "max_results": 5}
    )
    assert cfg.enabled is False
    assert cfg.placeholder == "Find…"
    assert cfg.max_results == 5

    # Malformed → fail-at-startup (same policy as auth:/embed:).
    for bad in ([], {"placeholder": "  "}, {"max_results": 0}, {"max_results": True}):
        with pytest.raises(ValueError):
            parse_search_config(bad)


def test_search_block_toggles_header_control(tmp_project):
    proj = _make_project(tmp_project)

    # Default (no `search:` block) → header + menu search present.
    on = TestClient(create_app(proj)).get("/guide").text
    assert "dashdown-header-search" in on
    assert "dashdown-sidebar-search" in on

    # `search: { enabled: false }` → the built-in control is gone…
    (proj / "dashdown.yaml").write_text(
        "title: Test\ntheme: light\nsearch:\n  enabled: false\n", encoding="utf-8"
    )
    off = TestClient(create_app(proj)).get("/guide").text
    assert "dashdown-header-search" not in off
    assert "dashdown-sidebar-search" not in off
    # …but the index endpoint still answers (component/use stays possible).
    assert TestClient(create_app(proj)).get("/_dashdown/api/search-index").status_code == 200


def test_search_config_threads_placeholder_and_max_results(tmp_project):
    proj = _make_project(tmp_project)
    (proj / "dashdown.yaml").write_text(
        "title: Test\ntheme: light\nsearch:\n  placeholder: Find stuff\n  max_results: 3\n",
        encoding="utf-8",
    )
    html = TestClient(create_app(proj)).get("/guide").text
    assert 'placeholder="Find stuff"' in html
    assert '"max_results":3' in html


# --------------------------------------------------------------------------- #
# memoization: the live endpoint caches on page content state
# --------------------------------------------------------------------------- #
def test_get_cached_search_index_does_not_reparse(tmp_project, monkeypatch):
    """A second call with unchanged pages reuses the built index (no re-parse)."""
    import dashdown.search as search_mod

    proj = load_project(_make_project(tmp_project))

    calls = {"n": 0}
    orig = search_mod.parse_markdown

    def counting(src):
        calls["n"] += 1
        return orig(src)

    monkeypatch.setattr(search_mod, "parse_markdown", counting)

    etag1, index1 = get_cached_search_index(proj)
    first = calls["n"]
    assert first > 0

    etag2, index2 = get_cached_search_index(proj)
    assert calls["n"] == first  # no page re-parsed on the cache hit
    assert etag2 == etag1
    assert index2 == index1


def test_get_cached_search_index_reflects_page_edit(tmp_project):
    """Editing a page (bumping its mtime) yields a fresh index + new etag."""
    proj_dir = _make_project(tmp_project)
    proj = load_project(proj_dir)

    etag1, index1 = get_cached_search_index(proj)
    assert "freshtoken" not in next(e["text"] for e in index1 if e["url"] == "/guide")

    guide = proj_dir / "pages" / "guide.md"
    guide.write_text("# Guide\n\nNow with freshtoken prose.\n", encoding="utf-8")
    # Force the mtime forward so the change is visible even on coarse clocks.
    bumped = os.stat(guide).st_mtime_ns + 2_000_000_000
    os.utime(guide, ns=(bumped, bumped))

    etag2, index2 = get_cached_search_index(proj)
    assert etag2 != etag1
    assert "freshtoken" in next(e["text"] for e in index2 if e["url"] == "/guide")


def test_search_index_endpoint_etag_and_304(tmp_project):
    """The endpoint returns an ETag; an If-None-Match round-trip gets a 304."""
    client = TestClient(create_app(_make_project(tmp_project)))

    r1 = client.get("/_dashdown/api/search-index")
    assert r1.status_code == 200
    etag = r1.headers.get("etag")
    assert etag

    r2 = client.get(
        "/_dashdown/api/search-index", headers={"If-None-Match": etag}
    )
    assert r2.status_code == 304
    assert r2.headers.get("etag") == etag
    assert r2.content == b""


def test_site_search_survives_static_build(tmp_project):
    """Unlike filter controls, <SiteSearch> must NOT be stripped from a static
    export (it searches a static snapshot)."""
    proj_dir = _make_project(tmp_project)
    out = tmp_project / "dist"
    build_site(proj_dir, out)
    home = (out / "index.html").read_text(encoding="utf-8")
    assert 'data-async-component="site-search"' in home
