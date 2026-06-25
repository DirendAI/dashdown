"""Tests for presentation PDF export (`dashdown pdf`, Stage 16b).

The helper + validation tests run everywhere. The full build+pdf integration is
skipped unless Playwright *and* a Chromium browser are installed, so the suite
stays green on a core install (the `pdf` extra is opt-in, like the connector
extras). Page geometry (orientation/format/scale) is a per-export CLI option,
not project config.
"""
import contextlib
import socket
import threading
import time

import pytest

from dashdown.pdf import _pdf_output, _page_url, export_pdf


# --------------------------------------------------------------------------- #
# Argument validation
# --------------------------------------------------------------------------- #

def test_export_pdf_rejects_bad_orientation(tmp_path):
    with pytest.raises(ValueError):
        export_pdf(tmp_path, tmp_path / "out", orientation="sideways")


def test_export_pdf_rejects_bad_scale(tmp_path):
    with pytest.raises(ValueError):
        export_pdf(tmp_path, tmp_path / "out", scale=3.0)


# --------------------------------------------------------------------------- #
# Path helpers (pure functions)
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize(
    "app_url,rel",
    [("/", "index.pdf"), ("/sales", "sales.pdf"), ("/a/b", "a/b.pdf")],
)
def test_pdf_output_mirrors_layout(app_url, rel, tmp_path):
    assert _pdf_output(app_url, tmp_path) == tmp_path / rel


@pytest.mark.parametrize(
    "app_url,expected",
    [
        ("/", "http://127.0.0.1:9/"),
        ("/sales", "http://127.0.0.1:9/sales/"),
        ("/a/b", "http://127.0.0.1:9/a/b/"),
    ],
)
def test_page_url(app_url, expected):
    assert _page_url(app_url, "http://127.0.0.1:9") == expected


# --------------------------------------------------------------------------- #
# Build + PDF integration (needs Playwright + Chromium)
# --------------------------------------------------------------------------- #

def _chromium_available() -> bool:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return False
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch()
            browser.close()
        return True
    except Exception:
        return False


requires_chromium = pytest.mark.skipif(
    not _chromium_available(),
    reason="Playwright + Chromium not installed (the `pdf` extra is opt-in)",
)


def _make_project(root):
    (root / "pages").mkdir()
    (root / "data").mkdir()
    (root / "dashdown.yaml").write_text("title: PDF Test\n", encoding="utf-8")
    (root / "sources.yaml").write_text(
        "main:\n  type: csv\n  directory: data\n", encoding="utf-8"
    )
    (root / "data" / "sales.csv").write_text(
        "region,amount\nNorth,100\nSouth,200\n", encoding="utf-8"
    )
    (root / "pages" / "index.md").write_text(
        "# Home\n\n"
        ":::query name=by_region connector=main\n"
        "SELECT region, SUM(amount) AS total FROM sales GROUP BY region ORDER BY region\n"
        ":::\n\n"
        '<BarChart data={by_region} x="region" y="total" title="By Region" />\n\n'
        '<Table data={by_region} title="By Region" />\n',
        encoding="utf-8",
    )


def test_pdf_missing_dependency_hint(monkeypatch, tmp_path):
    """When Playwright isn't importable, export raises a friendly install hint."""
    import dashdown.pdf as pdf_mod

    def _boom():
        raise RuntimeError(pdf_mod._MISSING_DEP_HINT)

    proj = tmp_path / "proj"
    proj.mkdir()
    _make_project(proj)
    monkeypatch.setattr(pdf_mod, "_require_playwright", _boom)

    with pytest.raises(RuntimeError) as exc:
        export_pdf(proj, tmp_path / "pdf", dist_dir=None)
    assert "pip install 'dashdown-md[pdf]'" in str(exc.value)


@requires_chromium
def test_export_pdf_default_is_single_page_deck(tmp_path):
    """Default = combined. A single-page project produces one deck named after
    the page (index.pdf), no per-page files alongside it."""
    proj = tmp_path / "proj"
    proj.mkdir()
    _make_project(proj)
    out = tmp_path / "pdf"

    result = export_pdf(proj, out)

    assert result.failed == []
    # Single page → combined deck keeps the page's own name.
    assert result.combined == out / "index.pdf"
    pdf_file = result.combined
    assert pdf_file.is_file()
    data = pdf_file.read_bytes()
    assert data[:5] == b"%PDF-"
    assert len(data) > 1000
    assert list(out.glob("*.pdf")) == [pdf_file]


@requires_chromium
def test_export_pdf_separate_one_file_per_page(tmp_path):
    """--separate (combine=False) writes one file per page, no combined deck."""
    proj = tmp_path / "proj"
    proj.mkdir()
    _make_project(proj)
    out = tmp_path / "pdf"

    result = export_pdf(proj, out, combine=False)

    assert result.failed == []
    assert result.combined is None
    assert ("/", out / "index.pdf") in result.pdfs
    assert (out / "index.pdf").is_file()


@requires_chromium
def test_print_view_renders_charts_without_errors(tmp_path):
    """Regression guard: the served build must load its data so charts draw —
    catches the `file://` fetch-blocked failure (chart shows "Failed to fetch")
    that a bytes-only PDF assertion would silently pass."""
    from dashdown.build import build_site
    from dashdown.pdf import _serve
    from playwright.sync_api import sync_playwright

    proj = tmp_path / "proj"
    proj.mkdir()
    _make_project(proj)
    dist = tmp_path / "dist"
    build_site(proj, dist)

    with sync_playwright() as pw, _serve(dist) as base:
        browser = pw.chromium.launch()
        page = browser.new_context(device_scale_factor=1).new_page()
        page.add_init_script("window.__dashdownPrint = true;")
        page.goto(f"{base}/", wait_until="networkidle")
        page.wait_for_function("window.__dashdownPrintReady === true", timeout=30000)
        canvases = page.eval_on_selector_all("canvas", "els => els.length")
        errors = page.eval_on_selector_all(".dashdown-error", "els => els.length")
        has_cover = page.eval_on_selector("body", "() => !!document.querySelector('.dashdown-print-cover')")
        browser.close()

    assert canvases >= 1, "chart canvas did not render — data fetch likely failed"
    assert errors == 0, "an error card rendered (data fetch failed)"
    assert has_cover, "print cover page was not injected"


@requires_chromium
def test_export_pdf_page_filter_and_unknown_page(tmp_path):
    proj = tmp_path / "proj"
    proj.mkdir()
    _make_project(proj)
    out = tmp_path / "pdf"

    with pytest.raises(ValueError):
        export_pdf(proj, out, pages=["/nope"])

    result = export_pdf(proj, out, pages=["/"])
    assert [u for u, _ in result.pdfs] == ["/"]


@requires_chromium
def test_export_pdf_whole_project_deck_named_from_title(tmp_path):
    """The whole-project (multi-page) default deck is named from the project
    title and leaves no per-page files behind."""
    proj = tmp_path / "proj"
    proj.mkdir()
    _make_project(proj)
    # A second page so the deck genuinely concatenates more than one.
    (proj / "pages" / "detail.md").write_text(
        "# Detail\n\n"
        ":::query name=rows connector=main\nSELECT * FROM sales\n:::\n\n"
        '<Table data={rows} />\n',
        encoding="utf-8",
    )
    out = tmp_path / "pdf"

    result = export_pdf(proj, out)  # default = combined

    assert result.failed == []
    assert result.combined == out / "pdf-test.pdf"  # slug of "PDF Test"
    assert result.combined.is_file()
    assert result.combined.read_bytes()[:5] == b"%PDF-"
    # Only the combined deck — no per-page files left in the output dir.
    assert list(out.glob("*.pdf")) == [result.combined]

    from pypdf import PdfReader

    # Two pages in → at least two PDF pages out.
    assert len(PdfReader(str(result.combined)).pages) >= 2


# --------------------------------------------------------------------------- #
# Live-server export endpoint  (GET /_dashdown/api/pdf)
# --------------------------------------------------------------------------- #

def _app(proj):
    from dashdown.server import create_app

    return create_app(proj)


def test_pdf_endpoint_unknown_page_404(tmp_path):
    from fastapi.testclient import TestClient

    proj = tmp_path / "proj"
    proj.mkdir()
    _make_project(proj)
    client = TestClient(_app(proj))
    assert client.get("/_dashdown/api/pdf?_path=/nope").status_code == 404


def test_pdf_endpoint_bad_orientation_422(tmp_path):
    from fastapi.testclient import TestClient

    proj = tmp_path / "proj"
    proj.mkdir()
    _make_project(proj)
    client = TestClient(_app(proj))
    r = client.get("/_dashdown/api/pdf?_path=/&_orientation=sideways")
    assert r.status_code == 422


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


@contextlib.contextmanager
def _live_server(proj):
    """Run the app under a real uvicorn server on a free port (the PDF endpoint
    drives a real browser, which needs a real socket — TestClient won't do)."""
    import uvicorn
    import httpx

    port = _free_port()
    server = uvicorn.Server(
        uvicorn.Config(_app(proj), host="127.0.0.1", port=port, log_level="warning")
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{port}"
    try:
        for _ in range(100):  # wait for startup
            try:
                if httpx.get(f"{base}/_dashdown/health", timeout=0.5).status_code == 200:
                    break
            except Exception:
                time.sleep(0.1)
        yield base
    finally:
        server.should_exit = True
        thread.join(timeout=5)


@requires_chromium
def test_pdf_endpoint_renders_page(tmp_path):
    """The live-server endpoint returns a real PDF rendered by the same Chromium
    engine as the CLI (so the in-app button matches `dashdown pdf`)."""
    import httpx

    proj = tmp_path / "proj"
    proj.mkdir()
    _make_project(proj)

    with _live_server(proj) as base:
        r = httpx.get(
            f"{base}/_dashdown/api/pdf?_path=/&_orientation=landscape&_format=A4",
            timeout=60,
        )
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/pdf"
    assert r.headers["content-disposition"] == 'attachment; filename="index.pdf"'
    assert r.content[:5] == b"%PDF-"
    assert len(r.content) > 1000
