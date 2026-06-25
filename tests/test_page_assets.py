"""Page assets — co-located files next to a page (.md) and shared /assets refs.

Covers the three seams: render-time URL rewriting (pipeline), dev-server serving
(server), and the static-build copy + rewrite (build).
"""
from __future__ import annotations

import json

import pytest

from dashdown.render.pipeline import (
    _resolve_asset_ref,
    _rewrite_asset_urls,
    render_page,
)


# --------------------------------------------------------------------------- #
# URL resolution unit tests
# --------------------------------------------------------------------------- #
def _pages(tmp_path):
    pages = tmp_path / "pages"
    (pages / "topics").mkdir(parents=True)
    (pages / "topics" / "chart.png").write_bytes(b"PNG")
    (pages / "diagram.png").write_bytes(b"PNG")
    (pages / "topics" / "other.md").write_text("# Other", encoding="utf-8")
    return pages


def test_assets_ref_rewrites_only_in_build(tmp_path):
    pages = _pages(tmp_path)
    # dev: /assets works as-is (mounted at /assets) -> untouched
    assert _resolve_asset_ref("/assets/a.pdf", "", pages, static_build=False) is None
    # build: root-relative so the <base> resolves it under sub-path hosting
    assert _resolve_asset_ref("/assets/a.pdf", "", pages, static_build=True) == "assets/a.pdf"


def test_colocated_relative_ref(tmp_path):
    pages = _pages(tmp_path)
    # from pages/topics/<page>, "chart.png" is a sibling file
    assert _resolve_asset_ref("chart.png", "topics", pages, static_build=False) == "/topics/chart.png"
    assert _resolve_asset_ref("chart.png", "topics", pages, static_build=True) == "topics/chart.png"
    # from the root page, "diagram.png" is at the pages root
    assert _resolve_asset_ref("diagram.png", "", pages, static_build=False) == "/diagram.png"


def test_query_and_fragment_preserved(tmp_path):
    pages = _pages(tmp_path)
    assert _resolve_asset_ref("diagram.png?v=2", "", pages, static_build=True) == "diagram.png?v=2"
    assert _resolve_asset_ref("/assets/a.pdf#p=3", "", pages, static_build=True) == "assets/a.pdf#p=3"


def test_absolute_page_link_href_rewritten_in_build(tmp_path):
    # An absolute internal page link bypasses the build's relative <base> and 404s
    # under sub-path hosting, so a static build rewrites it (href only) to the same
    # root-relative "<route>/index.html" the nav uses. The dev server keeps it as-is.
    pages = _pages(tmp_path)
    R = _resolve_asset_ref
    assert R("/getting-started", "topics", pages, static_build=True, attr="href") == "getting-started/index.html"
    assert R("/queries#params", "topics", pages, static_build=True, attr="href") == "queries/index.html#params"
    assert R("/", "", pages, static_build=True, attr="href") == "index.html"
    # dev server: an absolute page link is correct as-is
    assert R("/getting-started", "topics", pages, static_build=False, attr="href") is None
    # an image/script src is never turned into a page link, even in a build
    assert R("/getting-started", "topics", pages, static_build=True, attr="src") is None
    # framework + external links stay skipped even as href
    assert R("/_dashdown/static/core.js", "topics", pages, static_build=True, attr="href") is None
    assert R("https://x.com/p", "topics", pages, static_build=True, attr="href") is None


@pytest.mark.parametrize(
    "url",
    [
        "https://example.com/x.png",
        "//cdn.example.com/x.png",
        "#section",
        "data:image/png;base64,AAAA",
        "mailto:a@b.com",
        "/_dashdown/static/core.js",
        "/getting-started",          # page link as <img src> — left alone (href is rewritten, see above)
        "missing.png",               # relative but no such file
        "other.md",                  # relative .md (a page link, not an asset)
    ],
)
def test_refs_left_untouched(tmp_path, url):
    pages = _pages(tmp_path)
    assert _resolve_asset_ref(url, "topics", pages, static_build=True) is None


def test_traversal_is_blocked(tmp_path):
    pages = _pages(tmp_path)
    (tmp_path / "secret.txt").write_bytes(b"SECRET")
    assert _resolve_asset_ref("../secret.txt", "topics", pages, static_build=True) is None
    assert _resolve_asset_ref("../../secret.txt", "topics", pages, static_build=True) is None


def test_rewrite_over_html(tmp_path):
    pages = _pages(tmp_path)
    html = (
        '<img src="chart.png"><a href="missing.png">x</a><img src="/assets/a.png">'
        '<a href="/detail-pages">d</a><a href="/queries#p">q</a>'
    )
    out = _rewrite_asset_urls(html, page_dir="topics", pages_dir=pages, static_build=True)
    assert 'src="topics/chart.png"' in out
    assert 'href="missing.png"' in out                 # untouched
    assert 'src="assets/a.png"' in out
    assert 'href="detail-pages/index.html"' in out     # absolute page link -> build form
    assert 'href="queries/index.html#p"' in out        # fragment preserved


# --------------------------------------------------------------------------- #
# render_page integration
# --------------------------------------------------------------------------- #
def _render(tmp_path, page_rel, source, *, static_build):
    pages = tmp_path / "pages"
    (pages / "topics").mkdir(parents=True, exist_ok=True)
    (pages / "topics" / "chart.png").write_bytes(b"PNG")
    md = pages / page_rel
    md.parent.mkdir(parents=True, exist_ok=True)
    md.write_text(source, encoding="utf-8")
    return render_page(
        source,
        {},
        current_path="/topics/guide",
        include_base=tmp_path,
        page_dir="topics",
        static_build=static_build,
    )


def test_render_rewrites_markdown_image_and_download(tmp_path):
    src = (
        "# Guide\n\n"
        "![Chart](chart.png)\n\n"
        "[Spec](/assets/spec.pdf)\n\n"
        '<a href="chart.png" download>Download</a>\n'
    )
    dev = _render(tmp_path, "topics/guide.md", src, static_build=False)
    assert 'src="/topics/chart.png"' in dev.body_html
    assert 'href="/assets/spec.pdf"' in dev.body_html       # dev: untouched
    assert 'href="/topics/chart.png"' in dev.body_html      # raw <a download>

    built = _render(tmp_path, "topics/guide.md", src, static_build=True)
    assert 'src="topics/chart.png"' in built.body_html
    assert 'href="assets/spec.pdf"' in built.body_html      # build: root-relative


def test_render_rewrites_absolute_page_link(tmp_path):
    src = "# Guide\n\n[Back to channels](/detail-pages) and [Queries](/queries#params)\n"
    dev = _render(tmp_path, "topics/guide.md", src, static_build=False)
    assert 'href="/detail-pages"' in dev.body_html              # dev: untouched
    built = _render(tmp_path, "topics/guide.md", src, static_build=True)
    assert 'href="detail-pages/index.html"' in built.body_html  # build: sub-path safe
    assert 'href="queries/index.html#params"' in built.body_html


# --------------------------------------------------------------------------- #
# Dev server
# --------------------------------------------------------------------------- #
def _serve_project(tmp_path):
    (tmp_path / "pages" / "assets").mkdir(parents=True)
    (tmp_path / "pages" / "index.md").write_text(
        "# Home\n\n![Logo](assets/logo.svg)\n", encoding="utf-8"
    )
    (tmp_path / "pages" / "assets" / "logo.svg").write_text("<svg/>", encoding="utf-8")
    (tmp_path / "pages" / "report.pdf").write_bytes(b"%PDF-1.4 fake")
    (tmp_path / "dashdown.yaml").write_text("title: T\n", encoding="utf-8")
    return tmp_path


def test_dev_server_serves_colocated_assets(tmp_path):
    from fastapi.testclient import TestClient
    from dashdown.server import create_app

    root = _serve_project(tmp_path)
    client = TestClient(create_app(root))

    # the index page rewrites its relative image ref to an absolute URL
    home = client.get("/").text
    assert 'src="/assets/logo.svg"' in home

    # ...and that URL serves the file
    r = client.get("/assets/logo.svg")
    assert r.status_code == 200 and r.text == "<svg/>"

    r = client.get("/report.pdf")
    assert r.status_code == 200 and r.content == b"%PDF-1.4 fake"

    # the .md source is never served as an asset
    assert client.get("/index.md").status_code == 404
    # unknown path still 404s
    assert client.get("/nope.png").status_code == 404


# --------------------------------------------------------------------------- #
# Static build
# --------------------------------------------------------------------------- #
def test_build_copies_and_rewrites_page_assets(tmp_path):
    from dashdown.build import build_site

    proj = tmp_path / "proj"
    (proj / "pages" / "guide").mkdir(parents=True)
    (proj / "pages" / "index.md").write_text(
        "# Home\n\n![Diagram](diagram.png)\n\n[Data](data.zip)\n", encoding="utf-8"
    )
    (proj / "pages" / "diagram.png").write_bytes(b"PNG")
    (proj / "pages" / "data.zip").write_bytes(b"ZIP")
    (proj / "pages" / ".DS_Store").write_bytes(b"junk")  # dotfile -> skipped
    (proj / "dashdown.yaml").write_text("title: T\n", encoding="utf-8")
    out = tmp_path / "dist"

    build_site(proj, out)

    # assets copied mirroring pages/ tree; .md and dotfiles excluded
    assert (out / "diagram.png").read_bytes() == b"PNG"
    assert (out / "data.zip").read_bytes() == b"ZIP"
    assert not (out / ".DS_Store").exists()
    assert not (out / "index.md").exists()

    # body refs rewritten to root-relative (resolved by the <base>)
    html = (out / "index.html").read_text()
    assert 'src="diagram.png"' in html
    assert 'href="data.zip"' in html
