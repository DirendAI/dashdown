"""Presentation PDF export — the ``dashdown pdf`` command.

Renders a project to a polished, "presentation-quality" PDF (one file per page),
driving headless Chromium over the **static export** (`dashdown build`). The
static site draws every chart with client-side ECharts, so only a real browser
can rasterize it — `weasyprint`/HTML→PDF (no JS) would emit blank charts. Hence
Playwright Chromium, shipped as the optional ``dashdown-md[pdf]`` extra.

Flow:
  1. Build the static site (or reuse an existing ``dist/`` via ``dist_dir``).
  2. Serve the build over a throwaway ``127.0.0.1`` HTTP server (the export
     ``fetch()``es its data JSON, which the browser blocks over ``file://``).
  3. For each page, load it with ``window.__dashdownPrint`` set (a Playwright
     init script) so the page dresses itself for print (``print.js`` adds the
     ``dashdown-print`` class, injects the gradient cover, and exposes a
     readiness signal).
  4. Wait for ``window.__dashdownPrintReady`` — the chart-render handshake, since
     ECharts draws asynchronously and printing too early yields blank canvases.
  5. ``page.pdf(...)`` with the project's orientation/format and a high
     ``device_scale_factor`` for crisp output.
"""
from __future__ import annotations

import contextlib
import functools
import logging
import re
import tempfile
import threading
from dataclasses import dataclass, field
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from dashdown.build import build_site
from dashdown.project import load_project

log = logging.getLogger(__name__)

# Generous ceiling — print.js self-caps its own wait (MAX_WAIT_MS) and then
# signals ready anyway, so this only trips if the page never boots at all.
_READY_TIMEOUT_MS = 30_000

# Page sizes in mm (portrait). The headless viewport is set to the printable
# width (page minus L/R margins) so fixed-size ECharts canvases render at the
# final page width instead of overflowing it. The margins give every page
# header/footer breathing room.
_PAGE_MM = {
    "A4": (210, 297),
    "A3": (297, 420),
    "LETTER": (215.9, 279.4),
    "LEGAL": (215.9, 355.6),
}
_MARGIN_LR_MM = 12
_MARGIN_TB_MM = 14
_PDF_MARGIN = {
    "top": f"{_MARGIN_TB_MM}mm",
    "bottom": f"{_MARGIN_TB_MM}mm",
    "left": f"{_MARGIN_LR_MM}mm",
    "right": f"{_MARGIN_LR_MM}mm",
}


def _print_geometry(fmt: str, orientation: str) -> dict:
    """Browser viewport (CSS px) matching a page format's printable area, so
    charts lay out at the final print width."""
    w_mm, h_mm = _PAGE_MM.get(fmt.strip().upper(), _PAGE_MM["A4"])
    if orientation == "landscape":
        w_mm, h_mm = h_mm, w_mm
    to_px = lambda mm: round(mm * 96 / 25.4)  # noqa: E731
    return {
        "width": to_px(w_mm - 2 * _MARGIN_LR_MM),
        "height": max(to_px(h_mm - 2 * _MARGIN_TB_MM), 600),
    }

_MISSING_DEP_HINT = (
    "PDF export needs the `pdf` extra (Playwright + pypdf). Install it and the "
    "browser:\n"
    "    pip install 'dashdown-md[pdf]'\n"
    "    playwright install chromium"
)


@dataclass
class PdfResult:
    out_dir: Path
    pdfs: list[tuple[str, Path]] = field(default_factory=list)  # (page url, file)
    failed: list[tuple[str, str]] = field(default_factory=list)  # (page url, error)
    combined: Path | None = None  # single merged deck, when --combine


def _require_playwright(hint: str = _MISSING_DEP_HINT):
    """Import Playwright lazily with a friendly install hint (like the connector
    extras) so a core install without the ``pdf`` extra fails clearly. ``hint``
    lets a sibling feature (``dashdown screenshot``) tailor the message."""
    try:
        from playwright.sync_api import sync_playwright  # noqa: PLC0415
    except ImportError as e:  # pragma: no cover - exercised via the hint message
        raise RuntimeError(hint) from e
    return sync_playwright


def _goto_ready(page, url: str, *, wait_until: str = "load"):
    """Navigate ``page`` to ``url`` and block on the chart-render handshake.

    The shared navigate+handshake step for every headless path (PDF export,
    the live-server endpoint, ``dashdown screenshot``): ECharts draws
    asynchronously, so we wait for ``window.__dashdownPrintReady`` — print.js
    flips it once data has loaded and every chart canvas exists (time-boxed, so
    a stuck/live component can't hang us). Returns the navigation ``Response``
    (or ``None``).

    The HTTP status is checked **before** the readiness wait: a 404/500 page
    ships no Dashdown JS, so the flag would never flip and we'd burn the full
    timeout — instead fail fast with the status (e.g. a bad ``--server`` URL).
    """
    response = page.goto(url, wait_until=wait_until)
    if response is not None and response.status >= 400:
        raise ValueError(f"page returned HTTP {response.status}: {url}")
    page.wait_for_function(
        "window.__dashdownPrintReady === true", timeout=_READY_TIMEOUT_MS
    )
    return response


def _merge_pdfs(parts: list[Path], out_file: Path) -> None:
    """Concatenate ``parts`` (in order) into a single ``out_file`` deck."""
    try:
        from pypdf import PdfWriter  # noqa: PLC0415
    except ImportError as e:  # pragma: no cover - exercised via the hint message
        raise RuntimeError(_MISSING_DEP_HINT) from e
    writer = PdfWriter()
    for part in parts:
        writer.append(str(part))
    with out_file.open("wb") as fh:
        writer.write(fh)
    writer.close()


def _slug(text: str, fallback: str) -> str:
    """A filesystem-safe slug for the combined deck's filename."""
    s = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return s or fallback


def _pdf_output(app_url: str, out_dir: Path) -> Path:
    """The ``.pdf`` file a page URL maps to, mirroring the build layout
    (``/`` -> ``index.pdf``, ``/a/b`` -> ``a/b.pdf``)."""
    rel = app_url.strip("/")
    return out_dir / "index.pdf" if not rel else out_dir / f"{rel}.pdf"


def _page_url(app_url: str, base: str) -> str:
    """The HTTP URL of a page in the local preview server (trailing slash so the
    server resolves the directory ``index.html`` and the page's ``<base>`` depth
    math sees the right path)."""
    rel = app_url.strip("/")
    return f"{base}/" if not rel else f"{base}/{rel}/"


@contextlib.contextmanager
def _serve(directory: Path):
    """Serve ``directory`` over a background ``127.0.0.1`` HTTP server, yielding
    the base URL. The static export reads its data JSON via ``fetch()``, which
    the browser blocks over ``file://`` — a real origin sidesteps that."""
    handler = functools.partial(_QuietHandler, directory=str(directory))
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


class _QuietHandler(SimpleHTTPRequestHandler):
    """SimpleHTTPRequestHandler that doesn't spam the console with request logs."""

    def log_message(self, *args) -> None:  # noqa: D102
        pass


def export_pdf(
    project_root: Path,
    out_dir: Path,
    *,
    pages: list[str] | None = None,
    dist_dir: Path | None = None,
    combine: bool = True,
    orientation: str = "portrait",
    fmt: str = "A4",
    scale: float = 1.0,
) -> PdfResult:
    """Render ``project_root`` to PDF under ``out_dir``.

    ``pages`` restricts the export to the given page URLs (default: all static
    pages, in nav order). ``dist_dir`` reuses an existing static build instead of
    producing a fresh one (the caller guarantees it is up to date). ``combine``
    (the default) merges every page into a single deck — ``<title>.pdf`` for the
    whole project, or ``<page>.pdf`` when a single page is selected; set it False
    to write one file per page instead. ``orientation`` (portrait|landscape),
    ``fmt`` (page size) and ``scale`` are page-geometry knobs passed to Chromium.
    """
    if orientation not in ("portrait", "landscape"):
        raise ValueError("orientation must be 'portrait' or 'landscape'")
    if not 0.1 <= scale <= 2.0:
        raise ValueError("scale must be between 0.1 and 2.0")

    project_root = project_root.resolve()
    out_dir = out_dir.resolve()

    project = load_project(project_root)
    try:
        title = project.config.title
        all_pages = [u for u in project.list_pages() if "[" not in u]
    finally:
        project.close()

    if pages:
        wanted = {("/" + p.strip("/")).rstrip("/") or "/" for p in pages}
        targets = [u for u in all_pages if u in wanted]
        missing = wanted - set(targets)
        if missing:
            raise ValueError(f"no such page(s): {', '.join(sorted(missing))}")
    else:
        targets = all_pages

    out_dir.mkdir(parents=True, exist_ok=True)

    # Build a fresh static site unless the caller hands us one to reuse.
    tmp: tempfile.TemporaryDirectory | None = None
    if dist_dir is not None:
        dist = dist_dir.resolve()
        if not (dist / "index.html").exists() and not any(dist.glob("**/index.html")):
            raise ValueError(f"{dist} is not a static build (no index.html)")
    else:
        tmp = tempfile.TemporaryDirectory(prefix="dashdown-pdf-")
        dist = Path(tmp.name) / "site"
        log.info("Building static export for PDF…")
        build_site(project_root, dist)

    result = PdfResult(out_dir=out_dir)
    pages_tmp: tempfile.TemporaryDirectory | None = None
    try:
        if combine:
            # Render each page to a scratch dir, then merge — the per-page files
            # are an implementation detail the caller never sees. A whole-project
            # deck is named after the project; a single selected page keeps the
            # page's own name (so `--page /sales` → sales.pdf, not <project>.pdf).
            pages_tmp = tempfile.TemporaryDirectory(prefix="dashdown-pdf-pages-")
            _render_all(targets, dist, Path(pages_tmp.name), orientation, fmt, scale, result)
            if result.pdfs:
                if len(targets) == 1:
                    combined = _pdf_output(targets[0], out_dir)
                    combined.parent.mkdir(parents=True, exist_ok=True)
                else:
                    combined = out_dir / f"{_slug(title, project_root.name)}.pdf"
                _merge_pdfs([p for _, p in result.pdfs], combined)
                result.combined = combined
                log.info("Combined deck → %s", combined)
        else:
            _render_all(targets, dist, out_dir, orientation, fmt, scale, result)
    finally:
        if pages_tmp is not None:
            pages_tmp.cleanup()
        if tmp is not None:
            tmp.cleanup()
    return result


def _render_all(
    targets: list[str],
    dist: Path,
    out_dir: Path,
    orientation: str,
    fmt: str,
    scale: float,
    result: PdfResult,
) -> None:
    sync_playwright = _require_playwright()
    landscape = orientation == "landscape"

    with sync_playwright() as pw, _serve(dist) as base_url:
        browser = pw.chromium.launch()
        try:
            # Viewport = printable page width so ECharts canvases lay out at the
            # final width (no right-edge overflow); high device_scale_factor →
            # crisp output.
            context = browser.new_context(
                device_scale_factor=2,
                viewport=_print_geometry(fmt, orientation),
            )
            page = context.new_page()
            # Set the print flag before any page script runs (print.js reads it).
            page.add_init_script("window.__dashdownPrint = true;")

            for app_url in targets:
                out_file = _pdf_output(app_url, out_dir)
                out_file.parent.mkdir(parents=True, exist_ok=True)
                try:
                    _goto_ready(page, _page_url(app_url, base_url), wait_until="networkidle")
                    page.pdf(
                        path=str(out_file),
                        format=fmt,
                        landscape=landscape,
                        scale=scale,
                        margin=_PDF_MARGIN,
                        print_background=True,
                        prefer_css_page_size=False,
                    )
                    result.pdfs.append((app_url, out_file))
                    log.info("PDF %s → %s", app_url, out_file)
                except Exception as e:  # noqa: BLE001
                    log.warning("PDF failed for %s: %s", app_url, e)
                    result.failed.append((app_url, f"{type(e).__name__}: {e}"))
        finally:
            browser.close()


def render_url_pdf(
    url: str,
    *,
    orientation: str = "portrait",
    fmt: str = "A4",
    scale: float = 1.0,
    http_credentials: dict | None = None,
    extra_headers: dict | None = None,
) -> bytes:
    """Render a single, already-served URL to PDF bytes via headless Chromium.

    The live-server "Export PDF" endpoint uses this against its own running page
    (so it reflects the current data + filters in that URL), giving the **same**
    Chromium output as the `dashdown pdf` CLI — which instead renders the static
    build via ``_render_all``. ``http_credentials`` / ``extra_headers`` let the
    headless browser satisfy the server's own auth (Basic / api_key).
    """
    if orientation not in ("portrait", "landscape"):
        raise ValueError("orientation must be 'portrait' or 'landscape'")
    if not 0.1 <= scale <= 2.0:
        raise ValueError("scale must be between 0.1 and 2.0")

    sync_playwright = _require_playwright()
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        try:
            context = browser.new_context(
                device_scale_factor=2,
                viewport=_print_geometry(fmt, orientation),
                http_credentials=http_credentials,
            )
            if extra_headers:
                context.set_extra_http_headers(extra_headers)
            page = context.new_page()
            page.add_init_script("window.__dashdownPrint = true;")
            # `load`, not `networkidle`: a *live* dev-server page holds an open
            # SSE live-reload connection, so the network never goes idle. The
            # readiness flag (inside _goto_ready) is the real "data + charts
            # drawn" signal.
            _goto_ready(page, url, wait_until="load")
            return page.pdf(
                format=fmt,
                landscape=(orientation == "landscape"),
                scale=scale,
                margin=_PDF_MARGIN,
                print_background=True,
                prefer_css_page_size=False,
            )
        finally:
            browser.close()
