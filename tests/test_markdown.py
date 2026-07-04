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


# --- Fenced query definitions -------------------------------------------------
#
# The editor-friendly query syntax: ```sql <name> [attrs…] defines a query
# (first info-string token after the language = name, rest = the same attr
# grammar as :::query); a plain ```sql fence stays a highlighted code sample.


def test_fence_query_is_captured_and_stripped():
    html, queries, _fm = parse_markdown(
        "```sql deals_count ttl=220 connector=secondary live interval=3\n"
        "SELECT count(*) FROM deals\n"
        "```"
    )
    assert len(queries) == 1
    q = queries[0]
    assert q.name == "deals_count"
    assert q.connector == "secondary"
    assert q.sql == "SELECT count(*) FROM deals"
    assert q.cache_ttl == 220
    assert q.live is True
    assert q.interval == 3
    # Like :::query, a definition emits nothing.
    assert "deals" not in html
    assert "dashdown-code" not in html


def test_fence_query_defaults():
    _html_out, queries, _fm = parse_markdown("```sql q1\nSELECT 1\n```")
    q = queries[0]
    # No connector= parses as unresolved (""); render_page / load_project fill
    # in the project's default source (see default_connector_name).
    assert q.connector == ""
    assert q.cache_ttl is None and q.live is False and q.interval is None


def test_fence_query_cache_ttl_wins_over_ttl_alias():
    _html_out, queries, _fm = parse_markdown(
        "```sql q1 ttl=5 cache_ttl=10\nSELECT 1\n```"
    )
    assert queries[0].cache_ttl == 10


def test_fence_query_dax_language():
    _html_out, queries, _fm = parse_markdown(
        "```dax top_products connector=fabric\nEVALUATE TOPN(5, Products)\n```"
    )
    assert queries[0].name == "top_products"
    assert queries[0].connector == "fabric"
    assert queries[0].sql.startswith("EVALUATE")


def test_fence_query_show_registers_and_renders():
    html, queries, _fm = parse_markdown(
        "```sql taught_query connector=main show\nSELECT 42\n```"
    )
    assert len(queries) == 1
    assert queries[0].name == "taught_query"
    # `show` keeps the block visible as an ordinary highlighted sql sample…
    assert 'data-lang="sql"' in html
    assert "42" in html
    # …with the info-string attrs stripped from the rendered output.
    assert "taught_query" not in html
    assert "show" not in html


def test_plain_sql_fence_stays_a_display_block():
    html, queries, _fm = parse_markdown("```sql\nSELECT 'display only'\n```")
    assert queries == []
    assert 'data-lang="sql"' in html
    assert "display only" in html


def test_fence_query_invalid_name_raises():
    # A token that can't be a query name is a typo, not a display block —
    # silent fallback would hide it.
    with pytest.raises(ValueError, match="query name"):
        parse_markdown("```sql {.line-numbers}\nSELECT 1\n```")


def test_fence_query_example_inside_outer_fence_is_not_registered():
    # The standard four-backtick trick documents the syntax itself: the inner
    # block is content of the outer fence, so nothing registers.
    html, queries, _fm = parse_markdown(
        "````markdown\n```sql deals_count connector=main\nSELECT 1\n```\n````"
    )
    assert queries == []
    assert "deals_count" in html


def test_plain_sql_fence_inside_query_container_still_feeds_the_container():
    # The pre-existing editor-highlighting workaround: a plain ```sql fence
    # *inside* :::query contributes its content as the container's SQL and
    # registers no query of its own.
    _html_out, queries, _fm = parse_markdown(
        ":::query name=wrapped connector=main\n```sql\nSELECT 7\n```\n:::"
    )
    assert len(queries) == 1
    assert queries[0].name == "wrapped"
    assert queries[0].sql == "SELECT 7"


def test_query_container_accepts_ttl_alias():
    _html_out, queries, _fm = parse_markdown(
        ":::query name=q ttl=90\nSELECT 1\n:::"
    )
    assert queries[0].cache_ttl == 90


def test_fence_and_container_queries_collect_in_document_order():
    _html_out, queries, _fm = parse_markdown(
        "```sql first\nSELECT 1\n```\n\n"
        ":::query name=second\nSELECT 2\n:::\n\n"
        "```sql third\nSELECT 3\n```"
    )
    assert [q.name for q in queries] == ["first", "second", "third"]


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


def test_render_markdown_text_renders_tables():
    # Tables are the one extension the answer renderer enables — the <Ask />
    # system prompt invites small comparison tables, so this stays load-bearing.
    out = render_markdown_text("| a | b |\n| - | - |\n| 1 | 2 |")
    assert "<table>" in out and "<th>a</th>" in out and "<td>2</td>" in out


# --- PascalCase component blocks survive internal blank lines ---------------
#
# CommonMark's HTML block (type 7) ends at the first blank line, which used to
# chop a `<Grid>` apart when its children were blank-line-separated (and lose an
# indented child to an escaped code block). `_component_block` swallows the whole
# balanced component instead. See `render/markdown.py::_component_block`.


def test_component_block_keeps_blank_separated_children_intact():
    html = _html(
        "<Grid cols=2>\n"
        "<Counter data={q} column=\"c\" />\n"
        "\n"
        "<Counter data={q} column=\"c\" />\n"
        "</Grid>\n"
    )
    # Both children stay as real component tags (not escaped, not code blocks).
    assert html.count("<Counter ") == 2
    assert "&lt;Counter" not in html
    assert "<pre>" not in html


def test_component_block_keeps_many_blank_separated_children():
    # Not limited to two children: the rule consumes through the matching close
    # tag, so any number of blank-separated children stay intact.
    children = "\n\n".join("<Counter data={q} column=\"c\" />" for _ in range(6))
    html = _html(f"<Grid cols=3>\n{children}\n</Grid>\n")
    assert html.count("<Counter ") == 6
    assert "&lt;Counter" not in html
    assert "<pre>" not in html


def test_component_block_keeps_indented_blank_separated_children():
    # An indented child after a blank line previously became a `<pre><code>`
    # block with the tag escaped — the component was silently lost.
    html = _html(
        "<Grid cols=2>\n"
        "\t<Counter data={q} />\n"
        "\n"
        "\t<Counter data={q} />\n"
        "</Grid>\n"
    )
    assert html.count("<Counter ") == 2
    assert "&lt;Counter" not in html
    assert "<pre><code>" not in html


def test_component_block_handles_nesting():
    html = _html(
        "<Grid cols=2>\n"
        "<Grid cols=1>\n"
        "<Counter data={q} />\n"
        "</Grid>\n"
        "\n"
        "<Counter data={q} />\n"
        "</Grid>\n"
    )
    assert html.count("<Grid ") == 2
    assert html.count("</Grid>") == 2
    assert html.count("<Counter ") == 2


def test_self_closing_components_stay_separate_blocks():
    # Two top-level self-closing components with a blank line between them must
    # not be merged — each is its own block.
    html = _html("<Counter data={q} />\n\n<Counter data={q} />\n")
    assert html.count("<Counter ") == 2


def test_inline_component_with_content_still_renders_inner_markdown():
    # `<Ask …>text</Ask>` on one line is inline usage: the paragraph rule owns
    # it so the inner markdown still renders. The block rule must not hijack it.
    html = _html("<Ask data={q}>Which region **leads**?</Ask>\n")
    assert "<strong>leads</strong>" in html


def test_component_examples_inside_fenced_code_are_untouched():
    # A `<Grid>` shown as a code sample must stay escaped source, not get parsed.
    html = _html("```markdown\n<Grid cols=2>\n<Counter data={q} />\n</Grid>\n```\n")
    assert "&lt;Grid cols=2&gt;" in html


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
