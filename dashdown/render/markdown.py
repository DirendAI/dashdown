"""Markdown setup with the :::query container directive."""
from __future__ import annotations

import html as _html
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from markdown_it import MarkdownIt
from mdit_py_plugins.anchors import anchors_plugin
from mdit_py_plugins.container import container_plugin
from mdit_py_plugins.deflist import deflist_plugin
from mdit_py_plugins.footnote import footnote_plugin
from mdit_py_plugins.tasklists import tasklists_plugin
from pygments import highlight as _pyg_highlight
from pygments.formatters import HtmlFormatter as _HtmlFormatter
from pygments.lexers import get_lexer_by_name as _get_lexer_by_name
from pygments.util import ClassNotFound as _ClassNotFound

from dashdown.render.attrs import parse_attrs

# Pygments formatter with no wrapper markup — we emit our own <pre><code> shell
# (see `highlight_code`) so the class names and structure stay under our control.
_PYG_FORMATTER = _HtmlFormatter(nowrap=True)


def highlight_code(code: str, lang: str, _attrs: str = "") -> str:
    """Syntax-highlight a fenced code block to **static HTML** via Pygments.

    Wired into ``MarkdownIt`` as the ``highlight`` option. markdown-it uses the
    returned string verbatim when it starts with ``<pre``, so we own the markup:
    every block is a ``<pre class="dashdown-code">`` shell carrying a ``data-lang``
    attribute, highlighted blocks add the ``highlight`` class and Pygments token
    spans inside ``<code>``.

    Unknown / absent languages fall back to an escaped plain block in the *same*
    shell, so all code blocks look consistent. ``mermaid`` is special-cased to an
    explicit ``dashdown-mermaid`` marker block (never highlighted) that the client
    ``mermaid.js`` upgrades to an SVG diagram. Highlighting is server-side, so the
    output is static and works in ``dashdown build`` exports and embeds with no
    client JS — and no flash of unhighlighted code.
    """
    # The fence content carries a trailing newline; drop it so the <pre> has no
    # dangling blank line (internal newlines are preserved by Pygments).
    text = code[:-1] if code.endswith("\n") else code
    # Mermaid: never highlight — emit a marker block that the client
    # (static/components/mermaid.js) upgrades to an SVG diagram. Kept as a
    # `<pre class="dashdown-code dashdown-mermaid" data-lang="mermaid">` shell so it
    # degrades to readable source when JS is unavailable, and so the seam is
    # explicit rather than relying on `mermaid` not being a Pygments lexer.
    if lang == "mermaid":
        return (
            '<pre class="dashdown-code dashdown-mermaid" data-lang="mermaid">'
            f"<code>{_html.escape(text)}</code></pre>"
        )
    lexer = None
    if lang:
        try:
            lexer = _get_lexer_by_name(lang)
        except _ClassNotFound:
            lexer = None
    lang_attr = f' data-lang="{_html.escape(lang, quote=True)}"' if lang else ""
    if lexer is None:
        return (
            f'<pre class="dashdown-code"{lang_attr}>'
            f"<code>{_html.escape(text)}</code></pre>"
        )
    inner = _pyg_highlight(text, lexer, _PYG_FORMATTER).rstrip("\n")
    return (
        f'<pre class="dashdown-code highlight"{lang_attr}>'
        f"<code>{inner}</code></pre>"
    )


# Admonition / callout containers: `:::note`, `:::tip`, `:::info`, `:::warning`,
# `:::danger`. They reuse the same `:::` colon-fence machinery as `:::query`
# (markdown-it's container plugin), so authors get GitHub-style call-outs without
# a new syntax. Text after the keyword becomes the title (`:::warning Heads up`),
# else the kind's capitalized name is used.
_CALLOUT_KINDS = ("note", "tip", "info", "warning", "danger")


def _callout_validate(kind: str):
    def validate(params: str, *args) -> bool:
        return params.strip().split(" ", 1)[0] == kind

    return validate


def _make_callout_render(kind: str):
    default_title = kind.capitalize()

    def render(self, tokens, idx, options, env):
        tok = tokens[idx]
        if tok.nesting == 1:
            after = tok.info.strip()[len(kind):].strip()
            title = _html.escape(after) if after else default_title
            return (
                f'<div class="dashdown-callout dashdown-callout-{kind}">'
                f'<p class="dashdown-callout-title">{title}</p>'
            )
        return "</div>"

    return render

_FRONTMATTER_RE = re.compile(r"\A---\s*\n(.*?\n)---\s*\n", re.DOTALL)

# `{% include 'partials/foo.md' %}` — path is relative to the project root.
_INCLUDE_RE = re.compile(r"\{%\s*include\s+(['\"])(?P<path>.+?)\1\s*%\}")

_MAX_INCLUDE_DEPTH = 16


def expand_includes(
    source: str,
    base_dir: Path | None,
    _stack: tuple[str, ...] = (),
) -> str:
    """Expand ``{% include 'path.md' %}`` directives, inlining file contents.

    Paths are resolved relative to ``base_dir`` (the project root). Included
    files are themselves expanded, so partials can include other partials.

    Guards:
    - Path traversal: a resolved path that escapes ``base_dir`` raises ``ValueError``.
    - Cycles: a file that (transitively) includes itself raises ``ValueError``.
    - Missing files raise ``ValueError`` with the offending path.

    When ``base_dir`` is ``None`` the source is returned unchanged (includes are
    a project-level feature; there is no root to resolve against otherwise).
    """
    if base_dir is None or "{%" not in source:
        return source

    base = Path(base_dir).resolve()

    def _replace(m: re.Match[str]) -> str:
        rel = m.group("path").strip()
        target = (base / rel).resolve()
        try:
            target.relative_to(base)
        except ValueError:
            raise ValueError(f"include path escapes project root: {rel!r}")
        if not target.is_file():
            raise ValueError(f"include file not found: {rel!r}")
        key = str(target)
        if key in _stack:
            chain = " -> ".join([*_stack, key])
            raise ValueError(f"circular include detected: {chain}")
        text = target.read_text(encoding="utf-8")
        return expand_includes(text, base, _stack + (key,))

    if len(_stack) > _MAX_INCLUDE_DEPTH:
        raise ValueError("include nesting too deep (possible cycle)")

    return _INCLUDE_RE.sub(_replace, source)


@dataclass
class QuerySpec:
    name: str
    connector: str
    sql: str
    cache_ttl: int | None = None
    # Real-time streaming. `live` opts the query into the WebSocket poll path
    # (`/_dashdown/ws/data/{name}`); `interval` is the poll cadence in seconds
    # (None → server default). Parsed off `:::query name=… live interval=5`.
    live: bool = False
    interval: int | None = None
    # Optional human description, set only by shared-library query files
    # (`queries/**/*.{sql,dax}` frontmatter). Carried on Project.queries for
    # introspection / a generated query reference; doesn't affect execution.
    description: str | None = None


def _container_validate(params: str, *args) -> bool:
    return params.strip().startswith("query")


def _make_container_render(queries: list[QuerySpec]):
    """Returns a render function for the `query` container.

    On the opening token we parse the attributes and capture the SQL from the
    enclosed paragraph / code block; we emit nothing in the HTML output.
    """

    def render(self, tokens, idx, options, env):
        tok = tokens[idx]
        if tok.nesting == 1:
            # Opening tag: gather child text until closing tag of same level.
            params = tok.info.strip()
            # Strip leading "query" word.
            after = params[len("query") :].strip()
            attrs = parse_attrs(" " + after) if after else {}
            name = attrs.get("name")
            if not name or not isinstance(name, str):
                raise ValueError(":::query block requires a `name` attribute")
            connector = attrs.get("connector") or "main"

            # Collect text content from the inner tokens.
            sql_parts: list[str] = []
            depth = 1
            j = idx + 1
            while j < len(tokens) and depth > 0:
                t = tokens[j]
                if t.type == "container_query_open":
                    depth += 1
                elif t.type == "container_query_close":
                    depth -= 1
                    if depth == 0:
                        break
                if t.content:
                    sql_parts.append(t.content)
                elif t.children:
                    for c in t.children:
                        if c.content:
                            sql_parts.append(c.content)
                j += 1
            sql = "\n".join(p for p in sql_parts if p).strip()
            queries.append(QuerySpec(name=name, connector=str(connector), sql=sql))
        return ""  # do not emit anything

    return render


def build_md(queries_sink: list[QuerySpec]) -> MarkdownIt:
    md = MarkdownIt(
        "commonmark",
        {
            "html": True,
            "linkify": True,
            "typographer": False,
            # Server-side syntax highlighting for fenced code (Pygments).
            "highlight": highlight_code,
        },
    )
    md.enable("table")
    # GitHub-flavored niceties on top of CommonMark: ~~strikethrough~~, `- [ ]`
    # task lists, footnotes, definition lists, and slugged heading anchors (with a
    # hover permalink). These are page-markdown only — `render_markdown_text`
    # (untrusted LLM output) deliberately stays minimal and raw-HTML-free.
    md.enable("strikethrough")
    md.use(footnote_plugin)
    md.use(deflist_plugin)
    md.use(tasklists_plugin)
    # Anchor section headings (h2/h3) only — the h1 is the page title and gets
    # its own page-header treatment in the pipeline, so it stays plain.
    md.use(anchors_plugin, min_level=2, max_level=3, permalink=True, permalinkSymbol="#")
    md.use(
        container_plugin,
        name="query",
        validate=_container_validate,
        render=_make_container_render(queries_sink),
    )
    # Admonition/callout containers (`:::note` … `:::danger`). Each is its own
    # validated container; none collide with `:::query` (distinct first words).
    for kind in _CALLOUT_KINDS:
        md.use(
            container_plugin,
            name=kind,
            validate=_callout_validate(kind),
            render=_make_callout_render(kind),
        )
    # Suppress rendering of inner tokens for query containers by stripping them
    # after parse; simpler: override renderer to no-op for tokens inside.
    return md


def parse_markdown(source: str) -> tuple[str, list[QuerySpec], dict[str, Any]]:
    """Parse markdown, returning (html, queries, frontmatter)."""
    frontmatter: dict[str, Any] = {}
    body = source
    m = _FRONTMATTER_RE.match(source)
    if m:
        try:
            frontmatter = yaml.safe_load(m.group(1)) or {}
        except yaml.YAMLError:
            frontmatter = {}
        body = source[m.end() :]

    queries: list[QuerySpec] = []
    md = build_md(queries)
    tokens = md.parse(body)

    # Remove tokens enclosed by container_query_open/close (inclusive) so we
    # don't render SQL as a paragraph in the output.
    cleaned: list = []
    skip_depth = 0
    for tok in tokens:
        if tok.type == "container_query_open":
            skip_depth += 1
            continue
        if tok.type == "container_query_close":
            skip_depth -= 1
            continue
        if skip_depth == 0:
            cleaned.append(tok)

    # We still need queries to be populated; do a second pass that walks the
    # original token stream just to collect them (the render function above is
    # only triggered during HTML rendering, so call it via env-less rendering).
    queries.clear()
    _collect_queries(tokens, queries)

    html = md.renderer.render(cleaned, md.options, {})
    return html, queries, frontmatter


def _collect_queries(tokens, sink: list[QuerySpec]) -> None:
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        if tok.type == "container_query_open":
            params = tok.info.strip()
            after = params[len("query") :].strip()
            attrs = parse_attrs(" " + after) if after else {}
            name = attrs.get("name")
            if not name or not isinstance(name, str):
                raise ValueError(":::query block requires a `name` attribute")
            connector = attrs.get("connector") or "main"

            sql_parts: list[str] = []
            depth = 1
            j = i + 1
            while j < len(tokens) and depth > 0:
                t = tokens[j]
                if t.type == "container_query_open":
                    depth += 1
                elif t.type == "container_query_close":
                    depth -= 1
                    if depth == 0:
                        break
                if t.content:
                    sql_parts.append(t.content)
                elif t.children:
                    for c in t.children:
                        if c.content:
                            sql_parts.append(c.content)
                j += 1
            cache_ttl_raw = attrs.get("cache_ttl")
            cache_ttl: int | None = (
                int(cache_ttl_raw)
                if isinstance(cache_ttl_raw, (int, float)) and not isinstance(cache_ttl_raw, bool)
                else None
            )
            # Bare `live` parses to True; `interval=N` coerces to int (like cache_ttl).
            live = bool(attrs.get("live"))
            interval_raw = attrs.get("interval")
            interval: int | None = (
                int(interval_raw)
                if isinstance(interval_raw, (int, float)) and not isinstance(interval_raw, bool)
                else None
            )
            sink.append(
                QuerySpec(
                    name=name,
                    connector=str(connector),
                    sql="\n".join(p for p in sql_parts if p).strip(),
                    cache_ttl=cache_ttl,
                    live=live,
                    interval=interval,
                )
            )
            i = j + 1
            continue
        i += 1


# Renderer for untrusted markdown (LLM answers). Unlike page markdown,
# raw HTML is disabled so model output can't inject script/markup — it gets
# escaped and shown as text instead.
_TEXT_MD: MarkdownIt | None = None


def render_markdown_text(text: str) -> str:
    """Render plain (untrusted) markdown to HTML — no raw HTML, no directives."""
    global _TEXT_MD
    if _TEXT_MD is None:
        _TEXT_MD = MarkdownIt("commonmark", {"html": False, "linkify": True})
        _TEXT_MD.enable("table")
    return _TEXT_MD.render(text or "")


def split_frontmatter(source: str) -> tuple[dict[str, Any], str]:
    """Split a ``---``-fenced YAML frontmatter block from the body.

    Returns ``(frontmatter, body)``. With no frontmatter (or malformed YAML)
    the frontmatter is ``{}`` and the body is the source unchanged. Shared by
    page parsing and the query-library loader (which reuses the same
    frontmatter+body shape over a ``.sql``/``.dax`` file)."""
    m = _FRONTMATTER_RE.match(source)
    if not m:
        return {}, source
    try:
        fm = yaml.safe_load(m.group(1)) or {}
    except yaml.YAMLError:
        fm = {}
    if not isinstance(fm, dict):
        fm = {}
    return fm, source[m.end():]


def parse_frontmatter(source: str) -> dict[str, Any]:
    """Extract only the YAML frontmatter from a markdown source (fast)."""
    return split_frontmatter(source)[0]
