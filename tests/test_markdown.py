"""Tests for the rich-markdown rendering features (Stage 14a).

Covers the syntax-highlighting `highlight` callback, the GitHub-flavored
extensions wired into `build_md` (strikethrough, task lists, footnotes,
definition lists, heading anchors), the `:::note`/`:::warning` callout
containers, and that none of this leaks into the untrusted `render_markdown_text`
path or breaks `:::query` parsing.
"""
from pathlib import Path

import pytest

import dashdown
from dashdown.render.markdown import (
    highlight_code,
    parse_markdown,
    render_markdown_text,
)

_PKG_DIR = Path(dashdown.__file__).parent
_REPO_ROOT = _PKG_DIR.parent


def _html(src: str) -> str:
    html, _queries, _fm = parse_markdown(src)
    return html


# --- Syntax highlighting ----------------------------------------------------

def test_highlight_known_language_emits_token_spans():
    html = _html("```python\ndef f():\n    return 1\n```")
    assert 'class="dashdown-code highlight"' in html
    assert 'data-lang="python"' in html
    # `def` is a keyword -> Pygments short class `k`.
    assert '<span class="k">def</span>' in html


def test_highlight_unknown_language_falls_back_to_plain_block():
    html = _html("```nosuchlang\nplain text\n```")
    assert 'class="dashdown-code"' in html
    # No highlighting applied, so no token spans / highlight class.
    assert "highlight" not in html
    assert "plain text" in html


def test_fence_without_language_renders_plain_code_block():
    html = _html("```\njust code\n```")
    assert 'class="dashdown-code"' in html
    assert "data-lang" not in html
    assert "just code" in html


def test_mermaid_fence_is_a_plain_tagged_block_not_highlighted():
    # Stage 14b: a mermaid fence renders as a plain marker block (never
    # highlighted) carrying the `dashdown-mermaid` class + data-lang="mermaid"
    # so the client (mermaid.js) can upgrade it to an SVG diagram.
    html = _html("```mermaid\ngraph TD; A-->B;\n```")
    assert 'data-lang="mermaid"' in html
    assert "dashdown-mermaid" in html
    # Not highlighted: no Pygments token spans / highlight class.
    assert "highlight" not in html
    # Source is preserved (HTML-escaped) so the client can re-read it.
    assert "graph TD" in html


def test_mermaid_fence_source_is_escaped():
    # The diagram source is HTML-escaped in the <pre>; the browser decodes it
    # back to raw source via textContent before handing it to Mermaid.
    html = _html("```mermaid\ngraph TD; A-->B & <x>;\n```")
    assert "<x>" not in html
    assert "&lt;x&gt;" in html
    assert "--&gt;" in html  # the `-->` edge is escaped, not raw markup


def test_highlight_escapes_html_in_code():
    out = highlight_code("<script>alert(1)</script>\n", "")
    assert "<script>" not in out
    assert "&lt;script&gt;" in out


def test_highlight_no_trailing_blank_line():
    out = highlight_code("x = 1\n", "python")
    # The fence's trailing newline must not become a dangling blank line.
    assert "\n</code>" not in out


# --- GitHub-flavored extensions ---------------------------------------------

def test_strikethrough():
    assert "<s>gone</s>" in _html("~~gone~~")


def test_task_list_renders_checkboxes():
    html = _html("- [x] done\n- [ ] todo")
    assert "task-list-item" in html
    assert 'type="checkbox"' in html
    assert "checked" in html


def test_footnotes():
    html = _html("A claim.[^1]\n\n[^1]: The source.")
    assert 'class="footnote-ref"' in html
    assert 'class="footnotes"' in html
    assert "The source." in html


def test_definition_list():
    html = _html("Term\n: The definition.")
    assert "<dl>" in html
    assert "<dt>Term</dt>" in html
    assert "<dd>The definition.</dd>" in html


def test_heading_gets_anchor_id_and_permalink():
    html = _html("## Some Heading")
    assert 'id="some-heading"' in html
    assert 'class="header-anchor"' in html
    assert 'href="#some-heading"' in html


# --- Callout containers -----------------------------------------------------

@pytest.mark.parametrize("kind", ["note", "tip", "info", "warning", "danger"])
def test_callout_kinds_render(kind):
    html = _html(f":::{kind}\nBody text.\n:::")
    assert f'dashdown-callout dashdown-callout-{kind}' in html
    # Default title is the capitalized kind.
    assert kind.capitalize() in html
    assert "Body text." in html


def test_callout_custom_title():
    html = _html(":::warning Heads up\nBe careful.\n:::")
    assert "dashdown-callout-warning" in html
    assert "Heads up" in html


def test_callout_inner_markdown_is_rendered():
    html = _html(":::note\nSome **bold** text.\n:::")
    assert "<strong>bold</strong>" in html


def test_callout_title_is_escaped():
    html = _html(":::note <script>x</script>\nbody\n:::")
    assert "<script>x</script>" not in html
    assert "&lt;script&gt;" in html


# --- Coexistence with :::query ----------------------------------------------

def test_query_still_parsed_alongside_new_containers():
    src = (
        ":::note\nHello\n:::\n\n"
        ":::query name=sales connector=main\nSELECT 1\n:::\n\n"
        ":::warning\nCareful\n:::"
    )
    html, queries, _fm = parse_markdown(src)
    assert len(queries) == 1
    assert queries[0].name == "sales"
    assert queries[0].sql == "SELECT 1"
    # The SQL must not leak into the rendered HTML.
    assert "SELECT 1" not in html
    # ...but the callouts around it render fine.
    assert "dashdown-callout-note" in html
    assert "dashdown-callout-warning" in html


# --- Untrusted text path stays locked down ----------------------------------

def test_render_markdown_text_escapes_raw_html():
    out = render_markdown_text("Hello <script>alert(1)</script>")
    assert "<script>" not in out
    assert "&lt;script&gt;" in out


def test_render_markdown_text_has_no_page_extensions():
    # The LLM-answer renderer is deliberately minimal: no strikethrough,
    # no callouts, no highlighting shell.
    out = render_markdown_text("~~x~~")
    assert "<s>" not in out
    out2 = render_markdown_text("```python\ndef f(): pass\n```")
    assert "dashdown-code" not in out2


# --- Mermaid bundle is vendored offline (Stage 14b) -------------------------

def test_mermaid_bundle_is_vendored():
    """The Mermaid bundle ships locally (no CDN), like echarts/alpine/world.json.

    It's the self-contained IIFE build that exposes globalThis.mermaid, so the
    client can lazy-load it with a plain <script> tag.
    """
    bundle = _PKG_DIR / "static" / "vendor" / "mermaid.min.js"
    assert bundle.is_file(), "static/vendor/mermaid.min.js missing — run tooling `npm run build`"
    # Sanity: it's the real (multi-MB) bundle, not a stub.
    assert bundle.stat().st_size > 500_000
    assert 'globalThis["mermaid"]' in bundle.read_text(encoding="utf-8", errors="ignore")[-200:]


def test_build_tooling_copies_mermaid():
    """`npm run build` (build-assets.mjs) vendors the Mermaid bundle, so the
    committed file above stays reproducible from node_modules on the next bump."""
    tooling = _REPO_ROOT / "tooling"
    if not tooling.is_dir():  # tooling isn't shipped in the wheel
        pytest.skip("tooling/ not present (release-only)")
    assert "mermaid" in (tooling / "package.json").read_text()
    assert "mermaid.min.js" in (tooling / "build-assets.mjs").read_text()
