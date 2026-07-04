"""Visual verification — the ``dashdown screenshot`` command.

`dashdown check` confirms a page *renders* server-side, but charts draw
**client-side** with ECharts, so render success ≠ "the chart actually painted."
This closes that gap: drive headless Chromium over the page (the same engine and
the same ``window.__dashdownPrintReady`` handshake ``pdf.py`` relies on), capture
a PNG, and report a machine-readable verdict — how many chart canvases drew vs
stayed blank, plus any browser console errors. So a coding agent (even a
text-only one) can confirm its change *looks* right.

It reuses ``pdf.py``'s headless plumbing (Playwright import, the throwaway HTTP
server, the navigate+handshake helper) and the same optional ``dashdown-md[pdf]``
extra — no new dependency. Unlike PDF export it renders the **interactive** view
(``window.__dashdownCapture``, not ``__dashdownPrint``): no print cover, no
vertical-grid reflow — the page as a viewer sees it.

Three sources, in priority order:
  1. ``server_url`` — capture a page on an already-running ``dashdown serve``.
  2. ``dist_dir`` — serve an existing static build and capture it.
  3. default — build the static site, serve it over ``127.0.0.1``, capture it.
"""
from __future__ import annotations

import logging
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from dashdown.pdf import _goto_ready, _page_url, _require_playwright, _serve

log = logging.getLogger(__name__)

_SHOT_DEP_HINT = (
    "Screenshot needs the `pdf` extra (Playwright). Install it and the browser:\n"
    "    pip install 'dashdown-md[pdf]'\n"
    "    playwright install chromium"
)

# A desktop viewport so the rendered page matches the wide, sidebar-visible
# layout a viewer sees (DaisyUI collapses to the mobile hamburger under 768px).
_DEFAULT_WIDTH = 1280
_DEFAULT_HEIGHT = 800

# Count chart placeholders that drew a canvas/svg vs. stayed blank vs. surfaced
# an error, and tally page-wide server-rendered error cards. Run in the page
# after the readiness handshake. An empty-result chart still draws a canvas (the
# "No data" message), so it counts as drawn — blank means it never painted.
# Scoped to the ECharts container (like print.js::chartsRendered): the chart
# root can carry other SVGs (the `explain` button + its footer's AI badge),
# which must not make a never-painted chart count as drawn.
_SIGNAL_JS = """
() => {
  const charts = Array.from(document.querySelectorAll('[data-async-component="chart"]'));
  let drawn = 0, blank = 0, errored = 0;
  for (const el of charts) {
    if (el.querySelector('.dashdown-chart-container canvas, .dashdown-chart-container svg')) drawn++;
    else if (el.querySelector('.alert-error, .dashdown-error')) errored++;
    else blank++;
  }
  return {
    charts_total: charts.length,
    charts_drawn: drawn,
    charts_blank: blank,
    charts_errored: errored,
    error_cards: document.querySelectorAll('.dashdown-error').length,
  };
}
"""


@dataclass
class ShotResult:
    """The outcome of a screenshot: the PNG path + a render verdict."""

    out_file: Path
    charts_total: int = 0
    charts_drawn: int = 0
    charts_blank: int = 0
    charts_errored: int = 0
    error_cards: int = 0
    console_errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        """True when nothing visibly failed: no blank charts, no chart-level or
        server-rendered error cards. (Console errors are reported but don't fail
        the verdict — a stray favicon 404 or third-party log shouldn't.)"""
        return self.charts_blank == 0 and self.charts_errored == 0 and self.error_cards == 0


def _shot_output(page_url: str) -> str:
    """The default PNG filename for a page URL (``/`` → ``index.png``,
    ``/a/b`` → ``a-b.png``) — one flat file, friendlier than nested dirs."""
    rel = page_url.strip("/")
    return "index.png" if not rel else rel.replace("/", "-") + ".png"


def _capture(
    sync_playwright,
    url: str,
    out_file: Path,
    *,
    full_page: bool,
    width: int,
    height: int,
) -> ShotResult:
    """Drive headless Chromium to ``url`` in capture mode, write the PNG, and
    collect the render verdict. The shared browser work for all three sources."""
    console_errors: list[str] = []

    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        try:
            context = browser.new_context(
                viewport={"width": width, "height": height},
                device_scale_factor=1,
            )
            page = context.new_page()

            def _on_console(msg) -> None:
                if msg.type == "error":
                    console_errors.append(msg.text)

            page.on("console", _on_console)
            page.on("pageerror", lambda exc: console_errors.append(str(exc)))

            # Capture mode (interactive view), not print — set before page
            # scripts. _goto_ready fails fast on a 404 (e.g. a bad --server URL).
            page.add_init_script("window.__dashdownCapture = true;")
            _goto_ready(page, url, wait_until="load")

            signal = page.evaluate(_SIGNAL_JS)
            out_file.parent.mkdir(parents=True, exist_ok=True)
            page.screenshot(path=str(out_file), full_page=full_page)
        finally:
            browser.close()

    return ShotResult(out_file=out_file, console_errors=console_errors, **signal)


def screenshot_page(
    project_root: Path,
    page_url: str,
    out_file: Path,
    *,
    dist_dir: Path | None = None,
    server_url: str | None = None,
    full_page: bool = False,
    width: int = _DEFAULT_WIDTH,
    height: int = _DEFAULT_HEIGHT,
) -> ShotResult:
    """Capture ``page_url`` of ``project_root`` to ``out_file`` (a PNG).

    With ``server_url`` the page is captured from an already-running server (no
    build). Otherwise the project is rendered to a static site — ``dist_dir``
    reuses an existing build, else a fresh one is built — served locally, and
    captured. Returns a :class:`ShotResult` with the render verdict.
    """
    project_root = project_root.resolve()
    out_file = out_file.resolve()
    canonical = ("/" + page_url.strip("/")).rstrip("/") or "/"

    # Source 1: an already-running server. No project load / build — just shoot
    # the URL; a 404 is caught by the HTTP-status check in _capture.
    if server_url is not None:
        sync_playwright = _require_playwright(_SHOT_DEP_HINT)
        url = _page_url(canonical, server_url.rstrip("/"))
        log.info("Capturing %s from running server…", url)
        return _capture(
            sync_playwright, url, out_file, full_page=full_page, width=width, height=height
        )

    # Sources 2 & 3 need the page to exist + a static build to serve. Validate
    # the page first (cheap, no browser) so an unknown page fails clearly even on
    # a core install; then require Playwright before the (slow) build.
    from dashdown.build import build_site
    from dashdown.project import load_project

    project = load_project(project_root)
    try:
        pages = [u for u in project.list_pages() if "[" not in u]
    finally:
        project.close()
    if canonical not in pages:
        raise ValueError(
            f"no such page: {canonical} (available: {', '.join(sorted(pages)) or 'none'})"
        )

    sync_playwright = _require_playwright(_SHOT_DEP_HINT)

    tmp: tempfile.TemporaryDirectory | None = None
    if dist_dir is not None:
        dist = dist_dir.resolve()
        if not (dist / "index.html").exists() and not any(dist.glob("**/index.html")):
            raise ValueError(f"{dist} is not a static build (no index.html)")
    else:
        tmp = tempfile.TemporaryDirectory(prefix="dashdown-shot-")
        dist = Path(tmp.name) / "site"
        log.info("Building static export for screenshot…")
        # Build only the page we're capturing — so unrelated pages' queries
        # (e.g. a slow/flaky external API elsewhere in the project) never run.
        build_site(project_root, dist, only_pages=[canonical])

    try:
        with _serve(dist) as base_url:
            url = _page_url(canonical, base_url)
            log.info("Capturing %s…", canonical)
            return _capture(
                sync_playwright, url, out_file, full_page=full_page, width=width, height=height
            )
    finally:
        if tmp is not None:
            tmp.cleanup()
