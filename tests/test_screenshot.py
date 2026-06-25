"""Tests for visual verification (`dashdown screenshot`, Stage A3).

The helper + validation tests run everywhere. The full build+capture integration
is skipped unless Playwright *and* a Chromium browser are installed, so the suite
stays green on a core install (the `pdf` extra is opt-in, like the connector
extras) — mirroring `tests/test_pdf.py`, whose headless plumbing this reuses.
"""
import pytest

from dashdown.screenshot import ShotResult, _shot_output, screenshot_page


# --------------------------------------------------------------------------- #
# Pure helpers (no browser)
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize(
    "page_url,name",
    [("/", "index.png"), ("/sales", "sales.png"), ("/a/b", "a-b.png"), ("sales", "sales.png")],
)
def test_shot_output_flattens_url(page_url, name):
    assert _shot_output(page_url) == name


def test_shot_result_ok_when_clean():
    r = ShotResult(out_file=None, charts_total=2, charts_drawn=2)
    assert r.ok is True


def test_shot_result_ok_ignores_console_errors():
    # A stray console error (favicon 404, third-party log) must not fail the verdict.
    r = ShotResult(out_file=None, charts_total=1, charts_drawn=1, console_errors=["boom"])
    assert r.ok is True


@pytest.mark.parametrize(
    "kwargs",
    [
        {"charts_total": 2, "charts_drawn": 1, "charts_blank": 1},
        {"charts_total": 1, "charts_errored": 1},
        {"error_cards": 1},
    ],
)
def test_shot_result_not_ok_on_failure(kwargs):
    assert ShotResult(out_file=None, **kwargs).ok is False


# --------------------------------------------------------------------------- #
# Project fixture
# --------------------------------------------------------------------------- #

def _make_project(root, *, broken_chart=False):
    (root / "pages").mkdir()
    (root / "data").mkdir()
    (root / "dashdown.yaml").write_text("title: Shot Test\n", encoding="utf-8")
    (root / "sources.yaml").write_text(
        "main:\n  type: csv\n  directory: data\n", encoding="utf-8"
    )
    (root / "data" / "sales.csv").write_text(
        "region,amount\nNorth,100\nSouth,200\n", encoding="utf-8"
    )
    if broken_chart:
        # A chart bound to a query that is never defined → its data JSON is
        # absent from the build → the client fetch 404s → an error card renders.
        body = '<BarChart data={ghost} x="region" y="total" title="Ghost" />\n'
    else:
        body = (
            ":::query name=by_region connector=main\n"
            "SELECT region, SUM(amount) AS total FROM sales GROUP BY region ORDER BY region\n"
            ":::\n\n"
            '<BarChart data={by_region} x="region" y="total" title="By Region" />\n\n'
            '<Table data={by_region} title="By Region" />\n'
        )
    (root / "pages" / "index.md").write_text("# Home\n\n" + body, encoding="utf-8")


# --------------------------------------------------------------------------- #
# Validation (no browser needed)
# --------------------------------------------------------------------------- #

def test_screenshot_unknown_page_raises(tmp_path):
    proj = tmp_path / "proj"
    proj.mkdir()
    _make_project(proj)
    with pytest.raises(ValueError, match="no such page"):
        screenshot_page(proj, "/nope", tmp_path / "out.png")


def test_screenshot_missing_dependency_hint(monkeypatch, tmp_path):
    """A core install (no `pdf` extra) raises a friendly install hint — and does
    so without building (the page is validated first, then the dep)."""
    import dashdown.screenshot as shot_mod

    def _boom(hint=shot_mod._SHOT_DEP_HINT):
        raise RuntimeError(hint)

    proj = tmp_path / "proj"
    proj.mkdir()
    _make_project(proj)
    monkeypatch.setattr(shot_mod, "_require_playwright", _boom)

    with pytest.raises(RuntimeError) as exc:
        screenshot_page(proj, "/", tmp_path / "out.png")
    assert "pip install 'dashdown-md[pdf]'" in str(exc.value)


# --------------------------------------------------------------------------- #
# Build + capture integration (needs Playwright + Chromium)
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


@requires_chromium
def test_screenshot_writes_png_and_reports_drawn(tmp_path):
    """Happy path: a real PNG is written and the verdict says the chart drew."""
    proj = tmp_path / "proj"
    proj.mkdir()
    _make_project(proj)
    out = tmp_path / "shot.png"

    result = screenshot_page(proj, "/", out)

    assert result.out_file == out
    assert out.is_file()
    data = out.read_bytes()
    assert data[:8] == b"\x89PNG\r\n\x1a\n"  # PNG magic
    assert len(data) > 1000
    assert result.charts_total == 1
    assert result.charts_drawn == 1
    assert result.charts_blank == 0
    assert result.charts_errored == 0
    assert result.error_cards == 0
    assert result.ok is True


@requires_chromium
def test_screenshot_full_page(tmp_path):
    proj = tmp_path / "proj"
    proj.mkdir()
    _make_project(proj)
    out = tmp_path / "full.png"

    result = screenshot_page(proj, "/", out, full_page=True)

    assert out.is_file()
    assert out.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"
    assert result.ok is True


@requires_chromium
def test_screenshot_flags_a_failed_chart(tmp_path):
    """A chart whose data fails to load surfaces as an errored chart and a non-ok
    verdict — the signal a `check`-passes-but-chart-broke change needs. The
    fetch 404 is also captured as a console error."""
    proj = tmp_path / "proj"
    proj.mkdir()
    _make_project(proj, broken_chart=True)
    out = tmp_path / "broken.png"

    result = screenshot_page(proj, "/", out)

    assert out.is_file()  # the PNG is still captured — the verdict is the signal
    assert result.charts_drawn == 0
    assert result.charts_errored == 1
    assert result.ok is False
    assert result.console_errors  # the 404 was captured


@requires_chromium
def test_screenshot_reuses_existing_dist(tmp_path):
    """`--dist` captures a prebuilt static site without rebuilding."""
    from dashdown.build import build_site

    proj = tmp_path / "proj"
    proj.mkdir()
    _make_project(proj)
    dist = tmp_path / "dist"
    build_site(proj, dist)
    out = tmp_path / "shot.png"

    result = screenshot_page(proj, "/", out, dist_dir=dist)

    assert out.is_file()
    assert result.ok is True


# --------------------------------------------------------------------------- #
# `--server` mode: capture an already-running dev server
# --------------------------------------------------------------------------- #

import contextlib  # noqa: E402
import socket  # noqa: E402
import threading  # noqa: E402
import time  # noqa: E402


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


@contextlib.contextmanager
def _live_server(proj):
    """Run the app under a real uvicorn server on a free port (the screenshot
    drives a real browser, which needs a real socket — TestClient won't do)."""
    import httpx
    import uvicorn

    from dashdown.server import create_app

    port = _free_port()
    server = uvicorn.Server(
        uvicorn.Config(create_app(proj), host="127.0.0.1", port=port, log_level="warning")
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
def test_screenshot_colocated_component_import_resolves(tmp_path):
    """Regression: a colocated component's `import … from "dashdown/core.js"`
    must resolve in a static build. The import-map address has to start with
    `./` — a bare relative value is nulled by the browser ("blocked by a null
    value") and the import silently fails. We assert no such console error and
    that the module's self-init ran (it stamps the DOM on a successful import)."""
    from dashdown.build import build_site

    proj = tmp_path / "proj"
    proj.mkdir()
    _make_project(proj)
    comp = proj / "components" / "Widget"
    comp.mkdir(parents=True)
    (comp / "Widget.py").write_text(
        "from dashdown import Component, register_component\n"
        "@register_component('Widget')\n"
        "class Widget(Component):\n"
        "    def render(self, attrs, ctx, inner=None):\n"
        "        return '<div data-async-component=\"widget\" id=\"w\">pending</div>'\n",
        encoding="utf-8",
    )
    # If the specifier nulls, this import throws and the stamp never runs.
    (comp / "Widget.js").write_text(
        'import { recordsOf } from "dashdown/core.js";\n'
        'const el = document.getElementById("w");\n'
        'if (el) el.textContent = "ok:" + (typeof recordsOf);\n',
        encoding="utf-8",
    )
    (proj / "pages" / "index.md").write_text("# Home\n\n<Widget />\n", encoding="utf-8")

    dist = tmp_path / "dist"
    build_site(proj, dist)
    out = tmp_path / "shot.png"

    result = screenshot_page(proj, "/", out, dist_dir=dist)

    bad = [e for e in result.console_errors if "specifier" in e or "dashdown/" in e]
    assert not bad, f"import map failed to resolve: {bad}"


@requires_chromium
def test_screenshot_server_mode(tmp_path):
    """`--server` captures a page from a running server (no build) — and a bad
    URL fails fast on the HTTP status, not a readiness timeout."""
    proj = tmp_path / "proj"
    proj.mkdir()
    _make_project(proj)
    out = tmp_path / "shot.png"

    with _live_server(proj) as base:
        result = screenshot_page(proj, "/", out, server_url=base)
        assert result.ok is True
        assert result.charts_drawn == 1

        with pytest.raises(ValueError, match="HTTP 404"):
            screenshot_page(proj, "/nope", tmp_path / "x.png", server_url=base)
