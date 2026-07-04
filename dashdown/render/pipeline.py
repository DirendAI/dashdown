"""End-to-end page render pipeline."""
from __future__ import annotations

import hashlib
import html
import json
import logging
import math
import re
import time
from collections import OrderedDict
from decimal import Decimal
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from dashdown.components.base import RenderContext
from dashdown.data.base import Connector, QueryResult
from dashdown.data.registry import default_connector_name, no_default_error
from dashdown.render.components import render_components, _error_card
from dashdown.render.markdown import parse_markdown, expand_includes, QuerySpec

log = logging.getLogger(__name__)

_PARAM_RE = re.compile(r"\$\{(\w+)\}")

# Multi-select `IN (...)` expansion (multi-select Dropdown). A `${param}` that is
# the sole content of a parenthesised list directly following the SQL `IN`
# keyword expands a comma-separated value into a quoted, escaped literal list:
#   WHERE region IN (${region})  +  region="East,West"
#     -> WHERE region IN ('East', 'West')
# `_IN_BEFORE_RE` matches an `IN (` (word-boundaried, so `JOIN`/`MAIN` don't
# qualify) immediately before the placeholder; `_IN_AFTER_RE` matches the
# closing `)` immediately after. Each item is escaped exactly like a single
# quoted value (`'` -> `''`), so the per-item escaping matches the scalar path.
_IN_BEFORE_RE = re.compile(r"\bIN\s*\(\s*$", re.IGNORECASE)
_IN_AFTER_RE = re.compile(r"^\s*\)")

# Hard ceiling on the number of values expanded into a single `IN (...)` list.
# Options come from a dropdown's finite distinct values; a longer list only
# arrives via a crafted URL, so it's a DoS guard — extras are dropped.
MAX_IN_VALUES = 1000

# Default TTL (seconds) for both server-side result cache and Cache-Control headers.
DEFAULT_CACHE_TTL = 60

# Poll cadence (seconds) for live queries when no `interval` is given, and the
# floor any interval is clamped to (a too-small interval would hammer the
# connector — connections also serialize on the per-connector lock, so
# sub-second polling buys nothing).
DEFAULT_STREAM_INTERVAL = 5
MIN_STREAM_INTERVAL = 1

# Global query definition cache: (query_name, connector_name) -> (sql, default_params, cache_ttl)
# Persists across requests; same name+connector assumed identical across pages.
_query_def_cache: dict[tuple[str, str], tuple[str, dict[str, str], int | None]] = {}

# Live-query registry: (query_name, connector_name) -> poll interval (seconds).
# Only live (`:::query … live`) queries appear here; the WS endpoint refuses to
# stream anything absent from this map. Kept separate from _query_def_cache so
# the (sql, params, cache_ttl) tuple shape the data/ask paths unpack is unchanged.
_stream_def_cache: dict[tuple[str, str], int] = {}

# Keys (name, connector) currently registered from the shared query library
# (`queries/**/*.{sql,dax}`, registered at project load — see
# `register_library_queries`). Tracked separately from inline `:::query`
# registrations so that, on a dev-server reload, a renamed/deleted query file
# can be evicted from the module-global caches without leaving a stale ghost.
_library_keys: set[tuple[str, str]] = set()

# Python-query registry: (query_name, connector_name) -> the loaded
# PythonQuerySpec (callable + metadata). The data API / poller / build check
# this map FIRST and run the Python runner; absent → the existing SQL path. Kept
# separate from `_query_def_cache` so its (sql, params, cache_ttl) tuple shape —
# which every SQL/ask path unpacks — is untouched. Specs are stored opaquely
# (typed `Any`) so this module needs no import of `python_query` (avoids a cycle:
# python_query imports `_substitute_params`/`serialize_value` from here).
_python_def_cache: dict[tuple[str, str], Any] = {}

# Keys (name, connector) currently registered from `queries/**/*.py`, evicted on
# reload exactly like `_library_keys` (a renamed/deleted .py leaves no ghost).
_python_library_keys: set[tuple[str, str]] = set()

# Hard ceiling on server-side result cache entries. Params arrive from the
# client verbatim, so distinct cache keys are unbounded (crafted URLs, crawlers)
# and expired entries are only removed when their exact key is read again — an
# unbounded dict grows until the process dies. LRU keeps memory flat: hot keys
# survive on every read, junk keys evict other junk.
MAX_CACHED_RESULTS = 1024

# Server-side query result cache: (query_name, connector_name, params_key) -> (result, expiry_time)
_result_cache: OrderedDict[tuple, tuple[QueryResult, float]] = OrderedDict()


def register_query_def(
    name: str,
    connector: str,
    sql: str,
    params: dict[str, str],
    cache_ttl: int | None = None,
    live: bool = False,
    interval: int | None = None,
) -> None:
    """Register a query definition in the global cache.

    When ``live`` is set, also record the (clamped) poll interval in the stream
    registry so the WebSocket endpoint will stream it; otherwise ensure any
    stale live registration for this key is cleared.
    """
    _query_def_cache[(name, connector)] = (sql, params, cache_ttl)
    if live:
        iv = interval if interval is not None else DEFAULT_STREAM_INTERVAL
        _stream_def_cache[(name, connector)] = max(MIN_STREAM_INTERVAL, iv)
    else:
        _stream_def_cache.pop((name, connector), None)


def get_stream_interval(name: str, connector: str) -> int | None:
    """Poll interval (seconds) for a live query, or None if it isn't live."""
    return _stream_def_cache.get((name, connector))


def register_library_queries(specs: dict[str, QuerySpec]) -> None:
    """Register the shared query library into the module-global caches.

    Called from ``load_project`` with the parsed ``queries/`` set. Library
    queries are registered with **empty default params** — every ``${param}``
    value (filter values *and* a dynamic ``[slug]`` page's route params) arrives
    with the data request at fetch time, never from the global cache. (Route
    params travel with the request because the page emits them to the client,
    which merges them into every data/ask/WS request — see ``render_page``.)

    To keep dev-server reloads clean, every previously-registered library key is
    evicted first, so a renamed or deleted query file leaves **no stale ghost**
    in the cache. Inline ``:::query`` registrations (which are re-applied on
    every page render) are untouched, beyond the documented global-by-name
    shadowing if a page and a library file share a (name, connector).
    """
    global _library_keys
    for key in _library_keys:
        _query_def_cache.pop(key, None)
        _stream_def_cache.pop(key, None)

    new_keys: set[tuple[str, str]] = set()
    for spec in specs.values():
        register_query_def(
            spec.name,
            spec.connector,
            spec.sql,
            {},
            spec.cache_ttl,
            live=spec.live,
            interval=spec.interval,
        )
        new_keys.add((spec.name, spec.connector))
    _library_keys = new_keys


def register_python_query_def(
    name: str,
    connector: str,
    spec: Any,
    live: bool = False,
    interval: int | None = None,
) -> None:
    """Register one Python query into the Python-query cache.

    Stores the ``PythonQuerySpec`` (opaque here) keyed by ``(name, connector)`` and,
    when ``live``, records the clamped poll interval in ``_stream_def_cache`` so the
    WS endpoint streams it — exactly the ``register_query_def`` flow, minus the SQL
    tuple. A non-live (re)registration clears any stale live entry for the key.
    """
    _python_def_cache[(name, connector)] = spec
    if live:
        iv = interval if interval is not None else DEFAULT_STREAM_INTERVAL
        _stream_def_cache[(name, connector)] = max(MIN_STREAM_INTERVAL, iv)
    else:
        _stream_def_cache.pop((name, connector), None)


def get_python_query_def(name: str, connector: str) -> Any | None:
    """Return the registered ``PythonQuerySpec`` for ``(name, connector)``, or None.

    The data API / poller / build call this **first**; ``None`` means "not a Python
    query, try the SQL path" (``get_query_def``)."""
    return _python_def_cache.get((name, connector))


def register_python_library_queries(specs: dict[str, Any]) -> None:
    """Register the ``queries/**/*.py`` set into the module-global Python cache.

    Called from ``load_project`` with the loaded Python-query specs. Mirrors
    :func:`register_library_queries`: every previously-registered Python key is
    evicted first, so a renamed/deleted ``.py`` leaves **no stale ghost** on a
    dev-server reload.
    """
    global _python_library_keys
    for key in _python_library_keys:
        _python_def_cache.pop(key, None)
        _stream_def_cache.pop(key, None)

    new_keys: set[tuple[str, str]] = set()
    for spec in specs.values():
        register_python_query_def(
            spec.name, spec.connector, spec, live=spec.live, interval=spec.interval
        )
        new_keys.add((spec.name, spec.connector))
    _python_library_keys = new_keys


def payload_digest(payload: dict[str, Any]) -> str:
    """Stable digest of a serialized query payload, for stream change-detection.

    The WS poll loop sends a new snapshot only when this digest changes, so an
    unchanged result doesn't re-bill the wire. ``sort_keys`` keeps it stable
    across dict ordering; ``default=str`` tolerates any stray non-JSON cell."""
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()


def get_query_def(name: str, connector: str) -> tuple[str, dict[str, str], int | None] | None:
    """Get query definition by name and connector."""
    return _query_def_cache.get((name, connector))


def _freeze_params(params: dict[str, str]) -> tuple:
    return tuple(sorted(params.items()))


def get_cached_result(name: str, connector: str, params: dict[str, str]) -> QueryResult | None:
    """Return a cached result if it exists and has not expired.

    A hit refreshes the entry's LRU position so results readers keep asking for
    outlive one-off keys when the cache is full.
    """
    key = (name, connector, _freeze_params(params))
    entry = _result_cache.get(key)
    if entry is None:
        return None
    result, expiry = entry
    if time.monotonic() > expiry:
        # pop, not del: query threads race here and the key may already be gone
        _result_cache.pop(key, None)
        return None
    try:
        _result_cache.move_to_end(key)
    except KeyError:
        pass  # concurrently evicted; the result in hand is still valid
    return result


def cache_result(
    name: str, connector: str, params: dict[str, str], result: QueryResult, ttl: int
) -> None:
    """Store a query result in the server-side cache with a TTL.

    The cache is a bounded LRU (``MAX_CACHED_RESULTS``): overwriting a key
    refreshes its position, and inserting past the cap evicts from the
    least-recently-used end.
    """
    key = (name, connector, _freeze_params(params))
    _result_cache[key] = (result, time.monotonic() + ttl)
    _result_cache.move_to_end(key)
    while len(_result_cache) > MAX_CACHED_RESULTS:
        _result_cache.popitem(last=False)


# Matches the first <h1>…</h1> in rendered body HTML so the page header
# (description subtitle + "updated" stamp) can be grafted around it.
_H1_RE = re.compile(r"<h1\b[^>]*>.*?</h1>", re.DOTALL | re.IGNORECASE)


# The filter-row slot: a static, content-aligned flex row — controls (left),
# active-filter chips, search (right), and a "Filters" drawer button —
# followed by the (initially hidden) right off-canvas drawer itself.
# filter_bar.js routes the page's filter components between the inline slots
# and the drawer body: up to 3 controls inline, overflow to the drawer; narrow
# viewports and `filters: drawer` frontmatter (data-filter-mode="drawer") send
# everything to the drawer. Chips stay inline in every mode. Emitted into
# body_html (not the page template) so the filters travel with the page content
# — e.g. if a page is ever embedded without the app chrome — and lands directly
# below the page header.
def _filter_bar_slot_html(mode: str = "auto") -> str:
    return (
        f'<div class="dashdown-filter-bar" data-filter-mode="{mode}">'
        '<div class="dashdown-filter-bar-row flex flex-wrap items-center gap-x-3 gap-y-2">'
        '<div class="dashdown-filter-bar-container" id="dashdown-filter-bar-container"></div>'
        # Active filters without their own control show as dismissible chips;
        # "Clear all" appears whenever any filter is active.
        '<div class="dashdown-filter-chips flex flex-wrap items-center gap-2" '
        'x-data x-show="$store.filterChips && $store.filterChips.anyActive()" x-cloak>'
        '<template x-for="chip in ($store.filterChips ? $store.filterChips.list() : [])" :key="chip.key">'
        '<span class="badge badge-ghost gap-1 py-3 font-medium">'
        '<span class="opacity-60" x-text="chip.label + \':\'"></span>'
        '<span x-text="chip.display"></span>'
        '<button type="button" class="opacity-50 hover:opacity-100" '
        ':aria-label="\'Remove \' + chip.label + \' filter\'" '
        '@click="$store.filterChips.clear(chip.keys)">✕</button>'
        "</span>"
        "</template>"
        '<button type="button" class="text-xs link link-hover opacity-60 hover:opacity-100" '
        '@click="$store.filterChips.clearAll()">Clear all</button>'
        "</div>"
        '<div class="dashdown-filter-bar-search ml-auto" id="dashdown-filter-bar-search"></div>'
        # Drawer trigger: `hidden` covers the pre-Alpine paint, then
        # filter_bar.js drops it and the x-show takes over — visible while the
        # drawer is the page's only filter surface (narrow viewport /
        # `filters: drawer`), otherwise only once one of its filters is
        # active. Badge counts active drawer filters.
        '<button type="button" class="dashdown-filter-drawer-btn" '
        'id="dashdown-filter-drawer-btn" hidden x-data '
        'x-show="$store.filterDrawer && $store.filterDrawer.buttonVisible()" '
        '@click="$store.filterDrawer.toggle()" '
        'aria-controls="dashdown-filter-drawer" '
        ':aria-expanded="$store.filterDrawer ? $store.filterDrawer.open : false">'
        '<svg class="dashdown-filter-pill-icon" fill="none" stroke="currentColor" '
        'stroke-width="2" viewBox="0 0 24 24" aria-hidden="true">'
        '<path stroke-linecap="round" stroke-linejoin="round" '
        'd="M3 4.5h18l-7 8.5v6l-4 2v-8l-7-8.5z"/></svg>'
        "Filters"
        '<span class="dashdown-filter-drawer-count" '
        'x-show="$store.filterDrawer && $store.filterDrawer.activeCount() > 0" '
        'x-text="$store.filterDrawer ? $store.filterDrawer.activeCount() : 0" x-cloak></span>'
        "</button>"
        "</div>"
        # The off-canvas drawer: fixed backdrop + right panel. Esc / backdrop
        # click / ✕ close it; focus moves to the close button on open.
        '<div class="dashdown-filter-drawer" id="dashdown-filter-drawer" x-data '
        'x-show="$store.filterDrawer && $store.filterDrawer.open" x-cloak '
        '@keydown.escape.window="$store.filterDrawer.close()" '
        'x-effect="if ($store.filterDrawer && $store.filterDrawer.open) '
        "$nextTick(() => { const c = $el.querySelector('.dashdown-filter-drawer-close'); "
        'if (c) c.focus(); })">'
        '<div class="dashdown-filter-drawer-backdrop" '
        '@click="$store.filterDrawer.close()"></div>'
        '<aside class="dashdown-filter-drawer-panel" role="dialog" aria-modal="true" '
        'aria-label="Filters">'
        '<div class="dashdown-filter-drawer-header">'
        "<span>Filters</span>"
        '<button type="button" class="dashdown-filter-drawer-close" '
        'aria-label="Close filters" @click="$store.filterDrawer.close()">✕</button>'
        "</div>"
        '<div class="dashdown-filter-drawer-body" id="dashdown-filter-drawer-body"></div>'
        "</aside>"
        "</div>"
        "</div>"
    )


def _insert_filter_bar_slot(body_html: str, mode: str = "auto") -> str:
    """Insert the filter-row slot right after the body's first ``<h1>``.

    Runs *before* ``_page_header_html``: the later header graft replaces only
    the H1 itself, so the slot ends up directly below the full page-header
    block (title → description → filter row → content, per the table mockup).
    Bodies with no H1 get the slot prepended.
    """
    slot = _filter_bar_slot_html(mode)
    m = _H1_RE.search(body_html)
    if m:
        return body_html[: m.end()] + slot + body_html[m.end() :]
    return slot + body_html


# PDF + Embed page actions: a right-aligned button cluster rendered on every
# non-embed page, on the breadcrumb line (the template places it; see
# RenderedPage.page_actions_html). print.js::initPdfButton and
# embed_ui.js::initEmbedUI bind these element IDs, and the mobile-hide CSS
# targets them.
_PDF_ACTION_BUTTON = (
    '<button type="button" id="dashdown-pdf-btn" '
    'class="btn btn-ghost btn-sm gap-1" title="Export this page as PDF">'
    '<svg xmlns="http://www.w3.org/2000/svg" width="18" height="18" viewBox="0 0 24 24" '
    'fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" '
    'stroke-linejoin="round" aria-hidden="true">'
    '<path d="M12 3v12m0 0l-4-4m4 4l4-4M4 17v2a2 2 0 002 2h12a2 2 0 002-2v-2"/></svg>'
    "PDF</button>"
)
_EMBED_ACTION_BUTTON = (
    '<button type="button" id="dashdown-embed-btn" '
    'class="btn btn-ghost btn-sm gap-1" title="Embed this page">'
    '<svg xmlns="http://www.w3.org/2000/svg" width="18" height="18" viewBox="0 0 24 24" '
    'fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" '
    'stroke-linejoin="round" aria-hidden="true">'
    '<polyline points="16 18 22 12 16 6"/><polyline points="8 6 2 12 8 18"/></svg>'
    "Embed</button>"
)


def _page_actions_html(embed_enabled: bool) -> str:
    """The right-aligned page-action cluster: PDF always, Embed when enabled."""
    buttons = _PDF_ACTION_BUTTON
    if embed_enabled:
        buttons += _EMBED_ACTION_BUTTON
    return f'<div class="dashdown-page-actions">{buttons}</div>'


def _render_global_date_control(cfg: Any, ctx: RenderContext, embed: bool) -> str:
    """Render the project-wide global date filter as a ``DateRange`` control.

    Reuses the ``DateRange`` component (so it shares the preset math, URL sync and
    pill styling): a persisted control bound to the configured ``start_param`` /
    ``end_param`` and seeded by the configured ``default`` preset. In the header
    (non-embed) it opts out of the filter bar (``filter_bar=False``) so
    filter_bar.js leaves it in place; in embed mode it stays a normal filter so
    the bar picks it up.
    """
    from dashdown.components.base import get_component

    dr = get_component("DateRange")
    if dr is None:  # pragma: no cover - DateRange is a built-in, always registered
        return ""
    attrs = {
        "name": "date",
        "label": cfg.label,
        "presets": cfg.presets,
        "start_param": cfg.start_param,
        "end_param": cfg.end_param,
        "persist": True,
        "default": cfg.default or "",
        "filter_bar": bool(embed),
    }
    return dr.render(attrs, ctx, None)


def _page_header_html(body_html: str, frontmatter: dict[str, Any]) -> str:
    """Graft a page-header block around the body's first ``<h1>``.

    Driven by frontmatter:
    - ``description:`` → a muted subtitle rendered directly under the H1.
    - ``updated: true`` → an "Updated <time>" stamp, filled client-side from the
      data-fetch time (``page_header.js``); ``updated: <text/date>`` renders that
      value verbatim instead. Absent/false → no stamp.

    When neither is set the body is returned unchanged, so existing pages are
    untouched. The H1 itself is preserved (only wrapped); if the body has no
    ``<h1>`` the header is prepended.
    """
    description = frontmatter.get("description")
    updated = frontmatter.get("updated")

    desc_html = ""
    if description not in (None, "", False):
        desc_html = (
            f'<p class="dashdown-page-description">{html.escape(str(description))}</p>'
        )

    updated_html = ""
    if updated is True:
        # Auto: filled by page_header.js once query data lands.
        updated_html = (
            '<span class="dashdown-page-updated" data-dashdown-updated hidden>'
            'Updated <span class="dashdown-updated-time"></span></span>'
        )
    elif updated not in (None, "", False):
        updated_html = (
            f'<span class="dashdown-page-updated">Updated '
            f'{html.escape(str(updated))}</span>'
        )

    if not desc_html and not updated_html:
        return body_html

    m = _H1_RE.search(body_html)
    heading = m.group(0) if m else ""
    header = (
        '<div class="dashdown-page-header">'
        f'<div class="dashdown-page-heading">{heading}{desc_html}</div>'
        f"{updated_html}"
        "</div>"
    )
    if m:
        return body_html[: m.start()] + header + body_html[m.end() :]
    return header + body_html


@dataclass
class RenderedPage:
    body_html: str
    datasets: dict[str, dict[str, Any]]  # name -> {columns, rows}
    errors: list[str]
    frontmatter: dict[str, Any] = field(default_factory=dict)
    query_defs: dict[str, dict[str, Any]] = field(default_factory=dict)
    # AskDefs registered by this page's <Ask /> blocks (see dashdown/llm.py);
    # the static build bakes one commentary snapshot per def.
    ask_defs: list[Any] = field(default_factory=list)
    # The project-wide global date filter control (GlobalDateFilterConfig),
    # rendered for the sticky app header. Empty when disabled, in a static build,
    # or in embed mode (where it's injected into body_html as a filter instead).
    global_date_html: str = ""
    # PDF + Embed page-action buttons, rendered by the template onto the
    # breadcrumb line (right-aligned). Empty in embed mode; the Embed button is
    # present only when the project's embed.enabled (passed as `embed_enabled`).
    page_actions_html: str = ""
    # Route params captured from a dynamic `[slug]` page's URL (e.g.
    # `/teams/Brazil` -> {"team": "Brazil"}). Emitted to the client as the
    # `#dashdown-route-params` script so it merges them into every data/ask/WS
    # request, making each record's request URL unique — without them two slugs
    # of one template share a cacheable, param-less data URL and the browser
    # serves the first record's response for the second. Empty for static pages.
    route_params: dict[str, str] = field(default_factory=dict)


# --- Author asset URLs (images / downloadable files) ----------------------------
# A page can reference two kinds of asset: a **shared** file under the project's
# `assets/` dir (`/assets/foo.png`) or a **co-located** file sitting next to the
# page's `.md` under `pages/` (a relative `diagram.png` / `files/data.zip`). The
# problem is that no single written form resolves correctly everywhere: the dev
# server has no `<base>`, so a nested page needs an *absolute* URL, while a static
# build emits *root-relative* URLs resolved against a runtime `<base>` (so it works
# under sub-path hosting). So we normalize author asset refs at render time to the
# right shape for the target. Conservative by construction: only `/assets/...` and
# relative refs that resolve to an existing non-`.md` file under `pages/` are
# touched — page links, external URLs, anchors, data URIs and framework
# (`/_dashdown/...`) URLs are left exactly as written.
_ASSET_ATTR_RE = re.compile(r'\b(src|href)\s*=\s*(["\'])(.*?)\2', re.IGNORECASE)
_ASSET_SKIP_PREFIXES = (
    "#", "data:", "http://", "https://", "//", "mailto:", "tel:",
    "javascript:", "blob:", "/_dashdown/",
)


def _resolve_asset_ref(
    url: str, page_dir: str, pages_dir: Path | None, static_build: bool, attr: str = "src"
) -> str | None:
    """Return the rewritten URL, or None to leave the original untouched."""
    u = url.strip()
    if not u or u.startswith(_ASSET_SKIP_PREFIXES):
        return None
    # Keep any ?query / #fragment, match only on the path part.
    cut = len(u)
    for ch in "?#":
        i = u.find(ch)
        if i != -1:
            cut = min(cut, i)
    path_part, suffix = u[:cut], u[cut:]
    if not path_part:
        return None
    # (A) Shared assets/ file. Works as-is on the dev server (mounted at /assets);
    # a static build needs it root-relative so the <base> resolves it under any
    # hosting depth.
    if path_part.startswith("/assets/"):
        return path_part[1:] + suffix if static_build else None
    # (A2) Absolute internal *page* link, e.g. `[x](/detail-pages)` or
    # `/queries#params`. On the dev server it's correct as-is (served at the origin
    # root). A static build resolves every URL against a relative `<base>` so it
    # works under sub-path hosting (project GitHub Pages); an absolute `/route`
    # bypasses the `<base>` and 404s, so rewrite it to the same root-relative
    # `<route>/index.html` the nav uses (see build.root_link). `href` only — an
    # image/script `src` is never a page route.
    if path_part.startswith("/"):
        if not static_build or attr.lower() != "href":
            return None
        route = path_part.strip("/")
        return ("index.html" if not route else f"{route}/index.html") + suffix
    # (B) Co-located page asset: a relative ref to a real non-.md file next to the
    # page under pages/. Resolve it and confine to pages/ (traversal guard).
    if pages_dir is None:
        return None
    pages_root = pages_dir.resolve()
    try:
        target = (pages_dir / page_dir / path_part).resolve()
    except (OSError, ValueError, RuntimeError):
        return None
    if not target.is_relative_to(pages_root):
        return None
    if not target.is_file() or target.suffix.lower() == ".md":
        return None
    rel = target.relative_to(pages_root).as_posix()
    return (rel if static_build else "/" + rel) + suffix


def _rewrite_asset_urls(
    body_html: str, *, page_dir: str | None, pages_dir: Path | None, static_build: bool
) -> str:
    page_dir = page_dir or ""

    def repl(m: re.Match[str]) -> str:
        new = _resolve_asset_ref(m.group(3), page_dir, pages_dir, static_build, m.group(1))
        if new is None:
            return m.group(0)
        return f"{m.group(1)}={m.group(2)}{new}{m.group(2)}"

    return _ASSET_ATTR_RE.sub(repl, body_html)


def render_page(
    source: str,
    connectors: dict[str, Connector],
    params: dict[str, str] | None = None,
    current_path: str = "/",
    include_base: Path | None = None,
    page_dir: str | None = None,
    static_build: bool = False,
    library: dict[str, QuerySpec] | None = None,
    python_library: dict[str, Any] | None = None,
    semantic_models: dict[str, Any] | None = None,
    global_date: Any = None,
    embed: bool = False,
    embed_enabled: bool = False,
    filter_debounce: int = 300,
) -> RenderedPage:
    params = params or {}
    library = library or {}
    python_library = python_library or {}
    semantic_models = semantic_models or {}
    # Inline `{% include 'partials/...' %}` before parsing so partials can carry
    # their own queries and components.
    source = expand_includes(source, include_base)
    body_html, local_specs, frontmatter = parse_markdown(source)
    # A spec without an explicit `connector=` parses with an empty connector —
    # resolve it here, where the project's sources are in hand (`default: true`
    # flag → sole source). Several unflagged sources make an unqualified query
    # ambiguous — fail loudly rather than guess. With *zero* sources there is
    # nothing to disambiguate; the empty connector surfaces downstream as the
    # ordinary unknown-connector error on data fetch.
    default_connector = default_connector_name(connectors)
    for s in local_specs:
        if not s.connector:
            if default_connector is None and len(connectors) > 1:
                raise no_default_error(f"query '{s.name}'")
            s.connector = default_connector or ""
    local_names = {s.name for s in local_specs}

    # Full async mode: don't execute ANY queries server-side
    # All queries (including dropdowns) will be fetched client-side
    results: dict[str, QueryResult] = {}
    errors: list[str] = []
    error_blocks: list[str] = []

    # Seed name -> connector with the shared library so a component that binds a
    # connector at render time (e.g. <Ask />) resolves a referenced *library*
    # query correctly. A page-local :::query of the same name takes precedence
    # (precedence: local -> library). Extra (unreferenced) library entries are
    # inert here — they never reach the client, which only sees `query_defs`.
    query_connectors = {name: spec.connector for name, spec in library.items()}
    # Python library queries bind their connector the same way, so a referenced
    # Python query resolves the right connector at render time too.
    query_connectors.update(
        {name: spec.connector for name, spec in python_library.items()}
    )
    query_connectors.update({s.name: s.connector for s in local_specs})

    # Create empty context - no query data on server. render_components records
    # which queries the page's components reference (DataRefs) into
    # ctx.referenced_queries, which we resolve against the library below.
    ctx = RenderContext(
        queries=results,
        params=params,
        current_path=current_path,
        static_build=static_build,
        query_connectors=query_connectors,
        semantic_models=semantic_models,
        filter_debounce=filter_debounce,
        default_connector=default_connector or "",
    )
    body_html = render_components(body_html, ctx)

    # Effective query set = page-local :::query specs + referenced library
    # queries (precedence local -> library; a truly unknown name is left to the
    # existing client-side 404 path). So a referenced library query lands in the
    # global cache, in `query_connectors`, and in the client `query_defs` —
    # making it indistinguishable from an inline query downstream.
    referenced_library = [
        library[name]
        for name in sorted(ctx.referenced_queries)
        if name not in local_names and name in library
    ]
    query_specs = list(local_specs) + referenced_library

    # A `data={name}` DataRef can also resolve to a Python library query — same
    # local→library precedence, after SQL (a name is unique across both, enforced
    # at load). These are already globally registered in
    # `_python_def_cache` at project load (so the data API resolves them with zero
    # render-time work); render_page only needs to surface them in the client
    # `query_defs` (connector + live/interval — **never the Python source**, which
    # stays server-side exactly like SQL). They don't participate in the global
    # date scan / SQL registration below (no `.sql` body).
    referenced_python = [
        python_library[name]
        for name in sorted(ctx.referenced_queries)
        if name not in local_names
        and name not in library
        and name in python_library
    ]

    # First-class semantic metric references. A component with
    # `metric={model.metric} by={model.dim}` recorded a SemanticRef on
    # ctx.semantic_refs during the scan. Compile each into a *synthetic*
    # PythonQuerySpec and register it in the same `_python_def_cache` — so the
    # data API / poller / static build resolve it with no special-casing (it's
    # just another Python query downstream). Re-registered every render with a
    # deterministic name, so it overwrites identically (like an inline
    # `register_query_def`); the synthetic spec carries no SQL/Python source to
    # the client — only its connector reaches `query_defs` below.
    semantic_refs = list(ctx.semantic_refs.values())
    if semantic_refs:
        from dashdown.semantic import build_semantic_spec

        for ref in semantic_refs:
            spec = build_semantic_spec(semantic_models, ref, connectors)
            register_python_query_def(spec.name, spec.connector, spec)

    # Register query definitions in the global cache so the separate data-API
    # request can look the SQL back up by name. Registered with **empty** default
    # params — even on a dynamic `[slug]` page. The route params (`${slug}` etc.)
    # travel with each *data request* instead: this page emits them to the client
    # (`#dashdown-route-params`, see `route_params` below), which merges them into
    # every data/ask/WS request URL. So the global cache never holds per-record
    # values — which two concurrent requests for different slugs would otherwise
    # clobber, serving one record's data for another — and each record's request
    # URL + cache keys are unique.
    for spec in query_specs:
        register_query_def(
            spec.name,
            spec.connector,
            spec.sql,
            {},
            spec.cache_ttl,
            live=spec.live,
            interval=spec.interval,
        )
    # Project-wide global date filter (GlobalDateFilterConfig): one date-range
    # control. Shown **only on pages whose effective queries actually reference
    # it** — i.e. use `${start_param}`/`${end_param}` in their SQL — so a control
    # that couldn't change anything never appears (e.g. a docs page or a page with
    # only non-date queries). The selection still persists across navigation, so
    # it stays applied when you reach a date-aware page. Static builds omit it
    # (their snapshots are fixed, like any `is_filter` control). Placement is
    # embed-driven:
    #   - normal: returned on RenderedPage.global_date_html for the template to
    #     render into the **sticky app header** (always reachable, even scrolled
    #     to the bottom of a long page);
    #   - embed: the app header is omitted, so it's injected into the body as an
    #     ordinary filter (forcing the bar to appear) and routed into the filter
    #     bar by filter_bar.js.
    global_date_html = ""
    if global_date is not None and getattr(global_date, "enabled", False) and not static_build:
        date_placeholders = (
            f"${{{global_date.start_param}}}",
            f"${{{global_date.end_param}}}",
        )
        page_uses_date = any(
            ph in spec.sql for spec in query_specs for ph in date_placeholders
        )
        # A semantic chart whose model has a time_dimension also responds to the
        # date range (the compiler maps date_start/date_end onto it), so show the
        # control when the page has any such metric reference.
        if not page_uses_date and semantic_refs:
            page_uses_date = any(
                getattr(semantic_models.get(ref.model), "time_dimension", None)
                for ref in semantic_refs
            )
        if page_uses_date:
            control_html = _render_global_date_control(global_date, ctx, embed)
            if embed:
                body_html = control_html + body_html
                # Embeds omit the app header, so the global date rides the bar.
                # (The control was rendered with filter_bar=True, which already
                # set has_bar_filters; set both explicitly for clarity.)
                ctx.has_filters = True
                ctx.has_bar_filters = True
            else:
                global_date_html = control_html

    # The filter-row slot is grafted below the (about-to-be-grafted) page header
    # only when a control opts INTO the top bar (`bar` / `filter_bar=true`).
    # Filters render **inline where authored** by default, so a page of purely
    # inline controls gets no top chrome (bar/chips/clear-all/drawer) at all.
    # Static builds never set has_bar_filters because their filter controls are
    # stripped. `filters: drawer` frontmatter forces every bar-routed control into
    # the off-canvas drawer.
    if ctx.has_bar_filters:
        filter_mode = (
            "drawer"
            if str(frontmatter.get("filters", "")).strip().lower() == "drawer"
            else "auto"
        )
        body_html = _insert_filter_bar_slot(body_html, filter_mode)
    body_html = _page_header_html(body_html, frontmatter)

    # PDF / Embed page actions ride the breadcrumb line (the template renders this
    # alongside the breadcrumbs — see page.html). Returned as a ready-to-emit
    # string like global_date_html; empty in embed mode (chrome-less, as the
    # header was). Embed button is gated on the project's embed.enabled.
    page_actions_html = "" if embed else _page_actions_html(embed_enabled)

    if error_blocks:
        body_html = "\n".join(error_blocks) + "\n" + body_html

    # Normalize author asset URLs (co-located page assets + shared /assets refs) to
    # the form that resolves on this target — absolute on the dev server, root-
    # relative for a static build. See `_rewrite_asset_urls`.
    pages_dir = (include_base / "pages") if include_base is not None else None
    body_html = _rewrite_asset_urls(
        body_html, page_dir=page_dir, pages_dir=pages_dir, static_build=static_build
    )

    # Empty datasets for async mode
    datasets = {}
    
    # Store query definitions for client-side async loading.
    # Query SQL is never emitted here — it stays server-side and is never shipped
    # to the browser. The client references queries by name; the data API looks
    # the SQL back up in `_query_def_cache` and runs it. Only the connector (and
    # cache_ttl / live hints) reach the page source.
    # cache_ttl is only emitted when explicitly set so the client knows the TTL.
    def _query_def_item(s: QuerySpec) -> dict[str, Any]:
        d: dict[str, Any] = {"connector": s.connector}
        if s.cache_ttl is not None:
            d["cache_ttl"] = s.cache_ttl
        # Tells the client which queries to open a live WS for, and how fast the
        # server polls (so it can hint UI). Omitted under a static build — there
        # is no server to stream from, so the export reads from fixed snapshots.
        if s.live and not static_build:
            d["live"] = True
            d["interval"] = s.interval if s.interval is not None else DEFAULT_STREAM_INTERVAL
        # Per-widget "filtered by" indicator: surface the *names* of the
        # `${param}` placeholders this query references so the client can show which
        # active filters affect a widget. Only the names ship — never the SQL (param
        # names are already visible as filter/URL keys, so they're not sensitive).
        # The global-date `${date_start}`/`${date_end}` are ordinary SQL params, so
        # they're captured here for free. A Python query has no SQL to scan (its
        # params are an opaque runtime dict), so it advertises `params_unknown`
        # instead and the client falls back to a vaguer "may be filtered" badge.
        sql = getattr(s, "sql", None)
        if sql is not None:
            d["params"] = sorted(set(_PARAM_RE.findall(sql)))
        else:
            d["params_unknown"] = True
        return d

    query_defs = {s.name: _query_def_item(s) for s in query_specs}
    # Python library queries share the same client def shape (connector +
    # live/interval); `_query_def_item` only reads attributes a PythonQuerySpec
    # also carries. The Python source is never emitted (there's no `sql` field).
    query_defs.update({s.name: _query_def_item(s) for s in referenced_python})
    # Semantic synthetic queries: only the connector reaches the client (the
    # compiled SQL stays server-side, looked back up by name in
    # `_python_def_cache`). The client fetches them via the normal data API.
    # `params` is the model's filterable dimensions (+ global-date params when it
    # has a time dimension) — the keys `build_filters` matches on, so the
    # "filtered by" badge is accurate for semantic charts too. The model's SQL
    # still never ships, only these param names.
    if semantic_refs:
        from dashdown.semantic import semantic_filter_params

        for ref in semantic_refs:
            d: dict[str, Any] = {"connector": ref.connector}
            handle = semantic_models.get(ref.model)
            if handle is not None:
                d["params"] = semantic_filter_params(handle)
            query_defs[ref.query_name] = d

    return RenderedPage(
        body_html=body_html,
        datasets=datasets,
        errors=errors,
        frontmatter=frontmatter,
        query_defs=query_defs,
        ask_defs=list(ctx.ask_defs),
        global_date_html=global_date_html,
        page_actions_html=page_actions_html,
        route_params=dict(params),
    )


def serialize_value(v: Any) -> Any:
    """Coerce one cell to a JSON-safe value.

    Shared by the live data API (`server.py`) and the static build
    (`build.py`) so both emit byte-identical query payloads.
    """
    if v is None:
        return None
    # DuckDB-backed connectors (csv/duckdb/excel/sheets) return DECIMAL columns —
    # and any arithmetic with a decimal literal, e.g. `SUM(x) * 0.12` — as Python
    # `Decimal`, which is not JSON-serializable. (The DB-API connectors pre-clean
    # their own values; the DuckDB family relies on this shared seam.)
    if isinstance(v, Decimal):
        v = float(v)
    # np.float64 is a `float` subclass, so this also catches NaN/inf from a
    # pandas/Arrow-returned Python query.
    if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
        return None
    if hasattr(v, "isoformat"):
        return v.isoformat()
    if hasattr(v, "strftime"):
        return str(v)
    # numpy / pandas scalar (np.int64, np.bool_, np.datetime64, …) returned by a
    # Python query — coerce to a native Python value via `.item()` so JSON
    # serialization doesn't choke. Guarded so it never fires on str/bytes (no
    # `.item()`) or ordinary Python scalars (likewise). A coerced datetime then
    # takes the isoformat path.
    if hasattr(v, "item") and not isinstance(v, (str, bytes, bytearray)):
        try:
            nv = v.item()
        except Exception:  # noqa: BLE001 - defensive; fall back to the raw value
            return v
        return nv.isoformat() if hasattr(nv, "isoformat") else nv
    return v


def serialize_result(result: QueryResult) -> dict[str, Any]:
    """Turn a ``QueryResult`` into the ``{columns, rows}`` JSON shape the
    frontend expects, with every cell run through :func:`serialize_value`."""
    rows = [[serialize_value(cell) for cell in row] for row in result.rows]
    return {"columns": result.columns, "rows": rows}


def _expand_in_list(value: str) -> str:
    """Expand a comma-separated multi-select value into a quoted SQL literal list.

    Used only inside an ``IN (...)`` context (see ``_substitute_params``). Each
    item is trimmed, empties dropped, and escaped exactly like a single quoted
    value (``'`` -> ``''``) so the per-item injection guarantee is identical to
    the scalar path. The list is capped at ``MAX_IN_VALUES``. An empty/blank
    value yields ``NULL`` — ``IN (NULL)`` is valid SQL that matches nothing, so a
    multi-select query keeps its author-written "all" guard (e.g.
    ``'${x}' = '' OR col IN (${x})``) syntactically valid when nothing is picked.
    """
    items = [v.strip() for v in value.split(",")]
    items = [v for v in items if v != ""]
    if not items:
        return "NULL"
    if len(items) > MAX_IN_VALUES:
        items = items[:MAX_IN_VALUES]
    return ", ".join("'" + v.replace("'", "''") + "'" for v in items)


_OPTIONS_COLUMN_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
DEFAULT_OPTIONS_LIMIT = 50
MAX_OPTIONS_LIMIT = 200


def build_options_sql(
    inner_sql: str,
    column: str,
    search: str = "",
    limit: int = DEFAULT_OPTIONS_LIMIT,
) -> str:
    """Wrap a query's SQL into a DISTINCT-values lookup for a ``<Combobox>``.

    This is the **only** new SQL surface the searchable filter adds, so it stays
    injection-safe by the same rules as :func:`_substitute_params`:

    - ``column`` must be a bare identifier (``^[A-Za-z_][A-Za-z0-9_]*$``) — anything
      else raises ``ValueError``, so it can't smuggle SQL — and is double-quoted in
      the output.
    - ``search`` is embedded as a single-quoted string literal with ``'`` doubled
      (``'`` → ``''``), exactly like a quoted ``${param}``; it can only ever be data,
      never code.
    - ``inner_sql`` is the page query *after* ``_substitute_params`` has already run,
      wrapped verbatim as a subquery (trailing ``;`` stripped so the wrap is valid).

    Selects up to ``limit`` (clamped to ``MAX_OPTIONS_LIMIT``) distinct, non-null
    values of the column, optionally narrowed by a case-insensitive substring match.
    A search ranks **prefix matches first** (typing ``num`` surfaces ``numpy`` above
    ``abnum``), case-insensitively alphabetical within each band — which also puts an
    exact match first, since a string sorts before anything it prefixes. SQL
    connectors only — the wrap is meaningless for a non-SQL backend (e.g. DAX).
    """
    if not _OPTIONS_COLUMN_RE.match(column or ""):
        raise ValueError(f"invalid column name for options: {column!r}")
    try:
        n = int(limit)
    except (TypeError, ValueError):
        n = DEFAULT_OPTIONS_LIMIT
    n = max(1, min(n, MAX_OPTIONS_LIMIT))

    col = f'"{column}"'
    inner = inner_sql.strip().rstrip(";").strip()
    where = [f"{col} IS NOT NULL"]
    distinct = f"SELECT DISTINCT CAST({col} AS VARCHAR) AS value\nFROM (\n{inner}\n) AS _dd_opt\n"
    if search:
        esc = search.replace("'", "''")
        where.append(f"CAST({col} AS VARCHAR) ILIKE '%' || '{esc}' || '%'")
        # Prefix matches rank first. The ranking lives in an extra outer layer
        # because `SELECT DISTINCT … ORDER BY <expr not in the select list>` is
        # rejected by Postgres.
        return (
            f"SELECT value FROM (\n"
            f"{distinct}"
            f"WHERE {' AND '.join(where)}\n"
            f") AS _dd_vals\n"
            f"ORDER BY CASE WHEN value ILIKE '{esc}' || '%' THEN 0 ELSE 1 END, LOWER(value), value\n"
            f"LIMIT {n}"
        )
    return (
        f"{distinct}"
        f"WHERE {' AND '.join(where)}\n"
        f"ORDER BY value\n"
        f"LIMIT {n}"
    )


def _substitute_params(sql: str, params: dict[str, str]) -> str:
    """Replace ``${name}`` placeholders in SQL with parameter values.

    Values are properly escaped to prevent SQL injection.

    This approach:
    - For placeholders already inside single quotes ('${param}'): escapes ' in place
    - For placeholders already inside double quotes ("${param}"): escapes " in place
      (DAX string literals are double-quoted; "" is the escape there, as it is for
      ANSI SQL quoted identifiers)
    - For a placeholder that is the whole content of an ``IN (...)`` list
      (``IN (${param})``): expands a comma-separated value into a quoted literal
      list (multi-select Dropdown) — each item escaped as above
    - For placeholders NOT inside quotes (${param}): wraps value in single quotes
    - This prevents injection in all contexts

    DuckDB will automatically cast quoted numeric/date strings to the appropriate type.

    Examples:
        Input: "WHERE name = '${name}'", {"name": "O'Reilly"}
        Output: "WHERE name = 'O''Reilly'"

        Input: 'VAR v = "${type}"', {"type": 'say "hi" loud'}
        Output: 'VAR v = "say ""hi"" loud"'

        Input: "WHERE id = ${id}", {"id": "123"}
        Output: "WHERE id = '123'"

        Input: "WHERE id = ${id}", {"id": "1 OR 1=1"}
        Output: "WHERE id = '1 OR 1=1'"  # Safe - treated as string literal

    Args:
        sql: The SQL string with ${name} placeholders
        params: Dictionary of parameter name -> value

    Returns:
        SQL string with placeholders replaced by properly escaped/quoted values
    """
    # Find all placeholders and their positions
    # For each, check if it's surrounded by quotes
    result_parts = []
    last_end = 0
    
    for m in _PARAM_RE.finditer(sql):
        start, end = m.span()
        key = m.group(1)
        
        # Add text before this match
        result_parts.append(sql[last_end:start])
        
        value = params.get(key, "")
        if not isinstance(value, str):
            value = str(value)
        
        # Check if placeholder is surrounded by single or double quotes
        before = sql[max(0, start - 1):start]
        after = sql[end:min(len(sql), end + 1)]
        in_squotes = before == "'" and after == "'"
        in_dquotes = before == '"' and after == '"'
        # `IN (${param})` — the placeholder is the entire parenthesised list
        # directly after the SQL `IN` keyword. The surrounding `(`/`)` are part
        # of the SQL (not consumed here), so the expansion drops in between them.
        in_list = (
            not in_squotes
            and not in_dquotes
            and _IN_BEFORE_RE.search(sql[:start]) is not None
            and _IN_AFTER_RE.match(sql[end:]) is not None
        )

        if in_list:
            # Multi-select: expand "a,b,c" -> 'a', 'b', 'c' (each escaped).
            result_parts.append(_expand_in_list(value))
        elif in_squotes:
            # Placeholder is inside single quotes: escape ' in place, keep the
            # surrounding quotes from the SQL
            # Input: '${param}' -> Output: 'escaped_value'
            result_parts.append(value.replace("'", "''"))
        elif in_dquotes:
            # Placeholder is inside double quotes (DAX string literal or SQL
            # quoted identifier): escape " in place
            # Input: "${param}" -> Output: "escaped_value"
            result_parts.append(value.replace('"', '""'))
        else:
            # Placeholder is NOT inside quotes: wrap in quotes and escape
            # Input: ${param} -> Output: 'escaped_value'
            result_parts.append("'" + value.replace("'", "''") + "'")
        
        last_end = end
    
    # Add remaining text after last match
    result_parts.append(sql[last_end:])
    
    return "".join(result_parts)
