"""Full-text search index over a project's pages.

Builds a lightweight, *client-searchable* index of every concrete page — its
title, section headings (with anchor ids), and plain body text. Served live at
``GET /_dashdown/api/search-index`` (``server.py``) and baked into static
exports as ``_dashdown/search-index.json`` (``build.py``). The browser
(``static/components/site_search.js``) scores entries itself; there is **no**
server-side search execution — mirroring the framework's "queries never run
during page render" stance, the page ships instant and the search box fetches
the index once and ranks locally.

The text is extracted from the *parsed* markdown (``parse_markdown``): the
``:::query`` SQL blocks are already stripped there, so query bodies never leak
into the index, and the h2/h3 anchor ids the page renders are reused verbatim so
a result can deep-link to the matching section.
"""
from __future__ import annotations

import hashlib
import html as _htmllib
import os
import re
from pathlib import Path
from typing import TYPE_CHECKING, Any

from dashdown.render.markdown import parse_markdown

if TYPE_CHECKING:  # avoid an import cycle (project imports render which imports …)
    from dashdown.project import Project

# Strip the permalink anchor the headings plugin injects (``<a class="header-anchor"
# href="#id">#</a>``) before we read heading text, so titles don't end in "#".
_ANCHOR_RE = re.compile(
    r'<a[^>]*class="[^"]*header-anchor[^"]*"[^>]*>.*?</a>', re.DOTALL | re.IGNORECASE
)
# h2/h3 (and any) headings carry an ``id`` from the anchors plugin — capture it so
# results can deep-link to the section.
_HEADING_RE = re.compile(
    r'<h([1-6])[^>]*\sid="([^"]+)"[^>]*>(.*?)</h\1>', re.DOTALL | re.IGNORECASE
)
_H1_RE = re.compile(r"<h1[^>]*>(.*?)</h1>", re.DOTALL | re.IGNORECASE)
_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")

# Keep the per-page payload bounded — enough for ranking + a snippet, without
# shipping a megabyte of prose for a long page.
MAX_TEXT_CHARS = 4000


def _clean(fragment: str) -> str:
    """HTML fragment -> collapsed, entity-decoded plain text."""
    text = _ANCHOR_RE.sub("", fragment)
    text = _TAG_RE.sub(" ", text)
    return _WS_RE.sub(" ", _htmllib.unescape(text)).strip()


def _url_label(app_url: str) -> str:
    if app_url == "/":
        return "Home"
    last = app_url.rstrip("/").rsplit("/", 1)[-1]
    return last.replace("_", " ").replace("-", " ").title()


def index_page(app_url: str, source: str) -> dict[str, Any]:
    """Build one search-index entry from a page's markdown source."""
    html, _queries, fm = parse_markdown(source)

    headings: list[dict[str, str]] = []
    for m in _HEADING_RE.finditer(html):
        text = _clean(m.group(3))
        if text:
            headings.append({"id": m.group(2), "text": text})

    title = str(fm.get("title") or "").strip()
    if not title:
        h1 = _H1_RE.search(html)
        if h1:
            title = _clean(h1.group(1))
    if not title:
        title = headings[0]["text"] if headings else _url_label(app_url)

    text = _clean(html)
    if len(text) > MAX_TEXT_CHARS:
        text = text[:MAX_TEXT_CHARS]

    return {"url": app_url, "title": title, "headings": headings, "text": text}


def build_search_index(project: "Project") -> list[dict[str, Any]]:
    """Index every concrete (non-dynamic) page of ``project``.

    Dynamic ``[slug]`` pages are skipped (no enumerable URL), same as the static
    build. A page that fails to read is skipped rather than aborting the index.
    """
    if not project.pages_dir.is_dir():
        return []

    entries: list[dict[str, Any]] = []
    for app_url in project.list_pages():
        if "[" in app_url:  # dynamic slug page — no concrete URL to link to
            continue
        md_path, _params = project.page_path(app_url)
        if md_path is None:
            continue
        try:
            source = md_path.read_text(encoding="utf-8")
        except OSError:
            continue
        entries.append(index_page(app_url, source))
    return entries


# --------------------------------------------------------------------------- #
# Memoized accessor
# --------------------------------------------------------------------------- #
# The live endpoint (`server.py::get_search_index`) would otherwise re-parse every
# page on each poll. Cache the built index against a cheap content-state key so a
# rebuild happens only when a page is added/removed/renamed or edited. The key is
# content-derived, so a `reload_project` that swaps the Project without changing
# any page reuses the cache, and an edited page (bumped mtime) invalidates it.
_ContentKey = tuple[tuple[str, ...], int]
_index_cache: dict[Path, tuple[_ContentKey, str, list[dict[str, Any]]]] = {}


def _content_state_key(project: "Project") -> _ContentKey:
    """Cheap fingerprint of the page files that feed the index.

    A tuple of ``(sorted page file paths, newest mtime_ns)`` via ``os.stat``: an
    added/removed/renamed page changes the path set and an edited page bumps the
    mtime, so either way the key changes and the index is rebuilt.
    """
    if not project.pages_dir.is_dir():
        return ((), 0)
    paths: list[str] = []
    max_mtime = 0
    for p in sorted(project.pages_dir.rglob("*.md")):
        try:
            st = os.stat(p)
        except OSError:
            continue
        paths.append(str(p))
        if st.st_mtime_ns > max_mtime:
            max_mtime = st.st_mtime_ns
    return (tuple(paths), max_mtime)


def _etag_for_key(key: _ContentKey) -> str:
    """A strong HTTP validator (quoted) derived from the content-state key."""
    digest = hashlib.sha1(repr(key).encode("utf-8")).hexdigest()[:16]
    return f'"{digest}"'


def get_cached_search_index(project: "Project") -> tuple[str, list[dict[str, Any]]]:
    """Return ``(etag, entries)`` for ``project``, memoized on page content state.

    The index is rebuilt only when the set of page files or their newest mtime
    changes; otherwise the previously built entries are served verbatim. The etag
    is a strong validator over the same key, so a client can revalidate with
    ``If-None-Match`` and receive a 304.
    """
    key = _content_state_key(project)
    cached = _index_cache.get(project.root)
    if cached is not None and cached[0] == key:
        return cached[1], cached[2]
    entries = build_search_index(project)
    etag = _etag_for_key(key)
    _index_cache[project.root] = (key, etag, entries)
    return etag, entries
