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

import html as _htmllib
import re
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
