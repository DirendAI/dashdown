#!/usr/bin/env python3
"""Generate the scaffolded coding-agent guide from the ``docs/`` project.

Release-only tooling (like ``tooling/build-assets.mjs``): ``pip install`` users never
run it. The ``docs/`` project is the single source of truth — it already documents
every component, connector, and feature, page-by-page.

This emits **a map plus per-topic shards** (progressive disclosure), not one monolith:

- ``dashdown/scaffold/AGENTS.md`` — the *map*: a preamble, a one-screen cheat-sheet, and
  a table of contents linking each reference shard. ~300 lines, read first.
- ``dashdown/scaffold/references/<topic>.md`` — one shard per top-level entry in
  ``docs/pages/`` (a directory rolls up all its pages; a file is its own shard), in
  sidebar order. A coding agent loads only the shard a task needs instead of the whole
  ~50k-token guide.

So an agent editing a chart reads ``references/components.md`` alone; one configuring auth
reads ``references/authentication.md``. The map tells it which.

It also emits ``docs/llms.txt`` (the network-fetchable map) and ``docs/llms-full.txt`` (the
whole manual in one file) — the llms.txt convention — which ``dashdown build docs`` serves
from the static-build root.

Run from the repo root:  ``python tooling/gen-agent-docs.py``

It walks ``docs/pages/**`` in sidebar order, drops the frontmatter, the live query
definitions (fenced ``` ```sql name ``` blocks and legacy ``:::query`` — their dangling SQL
is meaningless out of context; display-only fenced code and ``:::note``-style callouts are
kept, they teach syntax), and the Jinja ``{% include %}`` lines, then concatenates the
prose into each shard.
"""
from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DOCS_ROOT = REPO_ROOT / "docs"
DOCS_PAGES = DOCS_ROOT / "pages"
SCAFFOLD_DIR = REPO_ROOT / "dashdown" / "scaffold"
# Where the shards are *stored* in the package: dotless, so setuptools' `scaffold/**/*`
# glob ships them (a hidden `.references/` would be skipped — same reason `.claude` is
# stored as `claude/`, see dashdown/agent_targets.py).
REFERENCES_SUBDIR = "references"  # relative to SCAFFOLD_DIR (the package copy)
# Where the shards *land* in a user's project — hidden, to keep the project root clean.
# `cli.py::_agent_doc_files` renames `references/` → `.references/` on install, so the
# map/skill links must point here, not at the storage subdir.
REFERENCES_LINK_SUBDIR = ".references"

# Reuse the framework's own frontmatter splitter so the parse matches the renderer.
sys.path.insert(0, str(REPO_ROOT))
from dashdown.catalog import build_catalog  # noqa: E402
from dashdown.render.markdown import parse_fence_query, split_frontmatter  # noqa: E402

CATALOG_SLUG = "catalog"  # the registry-introspected reference shard (not a docs/ page)

_FENCE_RE = re.compile(r"^\s*(`{3,}|~{3,})")
_QUERY_OPEN_RE = re.compile(r"^\s*:::query\b")
_JINJA_RE = re.compile(r"^\s*\{%.*%\}\s*$")
_CLOSE_RE = re.compile(r"^\s*:::\s*$")

# Lines that do not start an opening prose paragraph (for the TOC lede).
_NON_PROSE_PREFIXES = ("#", ">", "|", "-", "*", "+", ":::", "```", "~~~", "<", "{", "!", "<!--")


def _is_fence_query(info: str) -> bool:
    """Whether a fence info string defines a live query (``sql name …``)."""
    try:
        return parse_fence_query(info, "") is not None
    except ValueError:
        return False


def _closes(line: str, opening: str) -> bool:
    """CommonMark close test: same fence char, run at least as long as the opener."""
    m = _FENCE_RE.match(line)
    return bool(m) and m.group(1)[0] == opening[0] and len(m.group(1)) >= len(opening)


def _strip_body(body: str) -> str:
    """Drop live query definitions and Jinja includes, keep prose + display code."""
    out: list[str] = []
    fence: str | None = None  # marker of the code fence being kept, or None
    skip_fence: str | None = None  # marker of the fenced query being dropped, or None
    in_query = False
    for line in body.splitlines():
        if skip_fence is not None:
            if _closes(line, skip_fence):
                skip_fence = None
            continue
        if fence is not None:
            out.append(line)
            if _closes(line, fence):
                fence = None
            continue
        if in_query:
            if _CLOSE_RE.match(line):
                in_query = False
            continue
        m = _FENCE_RE.match(line)
        if m:
            marker = m.group(1)
            info = line.strip()[len(marker):]
            # A live fenced query definition (```sql name …) is page plumbing,
            # stripped exactly like the legacy :::query form below. Display-only
            # fences (plain ```sql, ````markdown examples) are kept.
            if _is_fence_query(info):
                skip_fence = marker
            else:
                fence = marker
                out.append(line)
            continue
        if _QUERY_OPEN_RE.match(line):
            in_query = True
            continue
        if _JINJA_RE.match(line):
            continue
        out.append(line)
    # Collapse 3+ blank lines left behind by stripped blocks down to one.
    return re.sub(r"\n{3,}", "\n\n", "\n".join(out)).strip()


def _frontmatter(md_file: Path) -> dict:
    fm, _ = split_frontmatter(md_file.read_text(encoding="utf-8"))
    return fm or {}


def _position(md_file: Path) -> int:
    return int(_frontmatter(md_file).get("sidebar_position", 9999))


def _title(md_file: Path) -> str:
    fm = _frontmatter(md_file)
    title = fm.get("title") or fm.get("sidebar_label")
    if title:
        return str(title)
    return md_file.stem.replace("-", " ").replace("_", " ").title()


def _lede(md_file: Path) -> str:
    """First sentence of a page's opening prose paragraph — a one-line TOC hint."""
    _, body = split_frontmatter(md_file.read_text(encoding="utf-8"))
    para: list[str] = []
    fence: str | None = None  # skip fenced code blocks wholesale (e.g. a leading example)
    for raw in _strip_body(body).splitlines():
        line = raw.strip()
        if fence is not None:
            if line.startswith(fence):
                fence = None
            continue
        m = _FENCE_RE.match(raw)
        if m:
            fence = m.group(1)[0] * 3
            continue
        if not para:
            if not line or line.startswith(_NON_PROSE_PREFIXES):
                continue
            para.append(line)
        elif line:
            para.append(line)
        else:
            break  # blank line ends the opening paragraph
    if not para:
        return ""
    text = re.sub(r"\s+", " ", " ".join(para))
    sentence = re.split(r"(?<=[.!?])\s+", text)[0].strip().rstrip(".")
    if len(sentence) > 160:  # keep the TOC tidy; trim at a word boundary
        sentence = sentence[:160].rsplit(" ", 1)[0] + "…"
    return sentence


def _ordered_pages(directory: Path) -> list[Path]:
    """Yield .md files under ``directory`` in sidebar order, depth-first.

    A directory is placed at its ``index.md``'s position; its ``index.md`` is
    emitted first, then its remaining files and subdirectories interleaved by
    position — mirroring the sidebar the docs render.
    """
    pages: list[Path] = []
    index = directory / "index.md"
    if index.exists():
        pages.append(index)

    children: list[tuple[int, Path]] = []
    for child in directory.iterdir():
        if child.is_dir():
            child_index = child / "index.md"
            pos = _position(child_index) if child_index.exists() else 9999
            children.append((pos, child))
        elif child.suffix == ".md" and child.name != "index.md":
            children.append((_position(child), child))

    for _, child in sorted(children, key=lambda t: (t[0], t[1].name)):
        if child.is_dir():
            pages.extend(_ordered_pages(child))
        else:
            pages.append(child)
    return pages


@dataclass
class Shard:
    """One per-topic reference: a top-level dir (rolled up) or a single top-level page."""

    slug: str  # filename stem under references/, e.g. "components"
    title: str  # human title for the TOC, e.g. "Components"
    lede: str  # one-line hint for the TOC
    position: int  # sidebar position, for TOC ordering
    pages: list[Path]  # source pages, in sidebar order


def _shards() -> list[Shard]:
    """Build one shard per top-level entry in ``docs/pages/`` (``index.md`` excluded).

    The top-level ``index.md`` is the docs home — it becomes the AGENTS.md map's basis,
    not a shard. Everything else: a directory rolls up all its pages; a file stands alone.
    """
    shards: list[Shard] = []
    for child in sorted(DOCS_PAGES.iterdir(), key=lambda p: p.name):
        if child.is_dir():
            index = child / "index.md"
            if not index.exists():
                continue
            shards.append(
                Shard(child.name, _title(index), _lede(index), _position(index), _ordered_pages(child))
            )
        elif child.suffix == ".md" and child.name != "index.md":
            shards.append(Shard(child.stem, _title(child), _lede(child), _position(child), [child]))
    return sorted(shards, key=lambda s: (s.position, s.slug))


def _render_shard(shard: Shard) -> str:
    """Concatenate a shard's source pages into one reference document."""
    banner = (
        f"<!-- AUTO-GENERATED from docs/pages/ by tooling/gen-agent-docs.py — do not edit. -->\n"
        f"<!-- Topic: {shard.slug}. Regenerate with: python tooling/gen-agent-docs.py -->"
    )
    parts: list[str] = [banner]
    for page in shard.pages:
        _, body = split_frontmatter(page.read_text(encoding="utf-8"))
        stripped = _strip_body(body)
        if not stripped:
            continue
        rel = page.relative_to(REPO_ROOT).as_posix()
        parts.append(f"\n<!-- source: {rel} -->\n\n{stripped}\n")
    return "\n".join(parts).rstrip() + "\n"


# --- The catalog (references/catalog.md) ------------------------------------------------
# Registry-introspected, not derived from docs/pages: the same data `dashdown components`
# prints, in file-readable form. Sharing one `build_catalog()` source means a new component
# attribute or connector config key shows up in both with no hand edit.


def _cell(text: str) -> str:
    """Escape a value for a Markdown table cell (no pipes/newlines)."""
    return text.replace("|", "\\|").replace("\n", " ").strip()


def _render_catalog() -> str:
    catalog = build_catalog()
    components = catalog["components"]
    connectors = catalog["connectors"]

    banner = (
        "<!-- AUTO-GENERATED from the component/connector registries by "
        "tooling/gen-agent-docs.py — do not edit. -->\n"
        "<!-- Topic: catalog. Regenerate with: python tooling/gen-agent-docs.py -->"
    )
    lines = [
        banner,
        "",
        "# Component & connector catalog",
        "",
        "Introspected straight from the registries — the **same data `dashdown components` "
        "prints**, in file-readable form. A new component attribute or connector config key "
        "appears here automatically (it is recovered from the source, not hand-written), so "
        "this can't drift. Prefer running `dashdown components` when you have a shell; read "
        "this shard when you don't.",
        "",
        f"## Components ({len(components)})",
        "",
        "`*` marks a **filter** component (it writes a `${param}` and is stripped from static "
        "builds). Charts share a common attribute set via the shared chart helper.",
        "",
        "| Component | Attributes | Summary |",
        "|---|---|---|",
    ]
    for row in components:
        name = f"`{row['name']}`" + (" \\*" if row["is_filter"] else "")
        attrs = ", ".join(f"`{a}`" for a in row["attrs"]) or "—"
        lines.append(f"| {name} | {attrs} | {_cell(row['summary'])} |")

    lines += [
        "",
        f"## Connectors ({len(connectors)})",
        "",
        "`type:` in `sources.yaml`. Install the listed extra before using a non-core "
        "connector; config keys support `${ENV_VAR}` expansion.",
        "",
        "| Type | Install | Config keys | Summary |",
        "|---|---|---|---|",
    ]
    for row in connectors:
        install = (
            f"`pip install 'dashdown-md[{row['extra']}]'`" if row["extra"] else "core"
        )
        keys = ", ".join(f"`{k}`" for k in row["config_keys"]) or "—"
        lines.append(f"| `{row['type']}` | {install} | {keys} | {_cell(row['summary'])} |")

    return "\n".join(lines).rstrip() + "\n"


# --- The map (AGENTS.md) ----------------------------------------------------------------

PREAMBLE = """\
# Dashdown — authoring guide for coding agents

This file is auto-generated from the Dashdown documentation and bundled into every
project scaffolded with `dashdown new`. It is **tool-agnostic**: any coding agent
that reads `AGENTS.md` (Claude Code, Cursor, Codex, …) can use it to help author this
dashboard. Do not edit by hand — regenerate with `python tooling/gen-agent-docs.py`
against the `docs/` project.

Dashdown renders Markdown files (with embedded SQL and `<Component />` tags) as
interactive analytics dashboards: no JavaScript to write, no frontend toolchain. You write
`.md` under `pages/`, point the CLI at the folder (`dashdown serve .`), and get a
live dashboard.

## How to use this guide

This is a **map**, not the whole manual. Skim the cheat-sheet below, then open **only the
one reference shard** your task needs (see the index) — each shard under `.references/` is the
full, flattened docs for one topic. Don't read every shard; that's the token cost this
structure exists to avoid.

> **Concepts from the references, facts from the CLI.** Don't guess a component's attributes
> or a connector's config keys — ask the tool. `dashdown components` prints a dense,
> introspected catalog (every component + its attrs, every connector + its config keys);
> `dashdown check` tells you if the project still renders; `dashdown query` shows real data.
> These answer factual lookups far cheaper than re-reading prose. See "The CLI loop" below.
"""

CHEAT_SHEET = """\
## Cheat-sheet

A **page** is Markdown under `pages/**/*.md`: prose + fenced query blocks + `<Component />`
tags. Queries are collected at render and run **in the browser** — never server-side at
render time, so a page ships instantly and fetches its data after.

### A page is a query plus components

````markdown
```sql sales
SELECT month, region, SUM(amount) AS amount
FROM orders GROUP BY month, region ORDER BY month
```

<LineChart data={sales} x="month" y="amount" series="region" title="Sales" />
<Table data={sales} />
````

- ` ```sql <name> [connector=…] [ttl=60] [live] [interval=5] ` — the first word after the
  language is the query **name**; `connector` is a key in `sources.yaml` (omit it to use
  the project's default source). The SQL is collected, not run at render. A plain
  ` ```sql ` fence with nothing after the language is an ordinary display-only code
  sample. (The legacy `:::query name=…` container form still works.)
- A query can instead live once in `queries/<name>.sql` (or `.py` for Python) and be
  referenced by name from any page — see `.references/queries.md`.
- `data={query_name}` wires a component to a result; `column="col"` picks one column.

### Parameters & filters (the security-critical bit)

- `${param}` in SQL is filled from filter/route values. It is **always** substituted as a
  quoted string literal (context-aware `'`→`''` / `"`→`""` / `IN (…)` expansion), so a value
  like `1 OR 1=1` is inert. **Never** build SQL by string-concatenating a value yourself.
- Filter controls write those params: `<Dropdown name="region" data={q} column="region" />`,
  `<Search name="q" />`, `<DateRange />`, `<Toggle name="active" />`. The project-wide date
  filter uses `${date_start}` / `${date_end}` by convention.

### Most-used components (run `dashdown components` for the full attr list)

- **Charts** share `data={} x="" y="" [series=""] [title=""]`: `<LineChart>` `<BarChart>`
  `<PieChart>` `<ScatterChart>` (+ box plot, heatmap, sankey, gauge, map, radar, treemap,
  funnel, … — all in `.references/components.md`). Multiple series: `y="a,b"` **or** `series=`.
- `<Counter data={q} column="amount" label="Revenue" />` — one big KPI number.
- `<Value>` — an inline metric. `<Table data={q} />` — sortable, CSV-exportable grid.
- `<Grid cols=2>…</Grid>` — lay widgets side by side.
- With a `semantic/` model, components can take **metrics** instead of a query:
  `<BarChart metric={sales.revenue} by={sales.region} />` — see `.references/semantic-layer.md`.
"""

CLI_LOOP = """\
## The CLI loop — verify your work, don't guess

These answer factual questions and confirm a change cheaper than re-reading docs. After
editing a page, run `check`; before wiring a connector, `query` it.

```bash
dashdown check                       # config loads + every page renders? (queries never run)
dashdown connectors --test           # each connector reachable? (probes SELECT 1)
dashdown query "SELECT * FROM t LIMIT 5" -c main   # inspect real data / schema (-f json|csv)
dashdown components                  # dense, introspected attr catalog for every component
dashdown components --connectors     # config keys + install extra per connector type
dashdown metric --list               # semantic metrics & dimensions, if a semantic/ model exists
dashdown serve .                     # run the dev server with live reload (http://127.0.0.1:8000)
dashdown build . --out dist          # static export; dashdown pdf .  → presentation PDF
dashdown screenshot /page            # PNG + verdict: did the chart canvases draw? (needs [pdf])
```

Typical loop: **read** the relevant `.references/<topic>.md` for the concept → **edit** the
page/query/config → **`dashdown check`** it renders → **`dashdown query`/`connectors --test`**
the data is real → **`dashdown serve`** to see it. (Charts draw client-side, so `check`
confirms render, not paint — **`dashdown screenshot <page>`** captures a PNG and reports whether
the chart canvases actually drew, exiting non-zero if any failed.)
"""


def _render_map(shards: list[Shard]) -> str:
    toc_lines = ["## Reference index", "", "Open the one shard your task needs:", ""]
    toc_lines.append(
        f"- [Catalog]({REFERENCES_LINK_SUBDIR}/{CATALOG_SLUG}.md) — every component's attributes "
        "+ every connector's config keys, introspected from the registries "
        "(the `dashdown components` data; **facts, not prose**)"
    )
    for shard in shards:
        href = f"{REFERENCES_LINK_SUBDIR}/{shard.slug}.md"
        hint = f" — {shard.lede}" if shard.lede else ""
        toc_lines.append(f"- [{shard.title}]({href}){hint}")
    toc = "\n".join(toc_lines) + "\n"
    return "\n".join([PREAMBLE, "---\n", CHEAT_SHEET, "---\n", toc, "---\n", CLI_LOOP]).rstrip() + "\n"


# --- llms.txt / llms-full.txt (published on the docs static build) ----------------------
# The llms.txt convention: a network-fetchable map at the site root (`/llms.txt`) linking
# each topic page, plus `/llms-full.txt` with the entire manual in one file. Generated from
# the same ordered `docs/pages/` walk, committed under `docs/`, and copied to the static
# build root by `build.py` so an agent-friendly host serves them without a special step.


def _page_url(shard: Shard) -> str:
    """Root-relative docs URL for a shard's topic page (resolves against the site root)."""
    return f"/{shard.slug}"


def build_llms_outputs() -> dict[str, str]:
    """Return ``{relative_path: content}`` for the llms.txt files, relative to ``docs/``.

    ``llms.txt`` is the *map* (an H1, a one-line summary, then a link per topic page);
    ``llms-full.txt`` is the whole manual in one file (the registry catalog + every shard).
    Pure, like ``build_outputs()`` — so a freshness test can compare against the committed
    ``docs/llms.txt`` / ``docs/llms-full.txt``.
    """
    shards = _shards()
    home = DOCS_PAGES / "index.md"
    title = _title(home) if home.exists() else "Dashdown"
    # A stable one-liner for the blockquote — the home page's opening sentence is bold
    # (markdown `**…**`) and spans a sentence break, which the prose `_lede` heuristic
    # can't cleanly extract, so summarize directly.
    summary = (
        "Dashdown turns Markdown files — with embedded SQL and component tags — into "
        "interactive analytics dashboards"
    )

    # llms.txt — the map: links to each topic page.
    map_lines = [
        f"# {title}",
        "",
        f"> {summary}.",
        "",
        "Dashdown renders Markdown (with embedded SQL and `<Component />` tags) as "
        "interactive analytics dashboards. The links below are the documentation, one page "
        "per topic. For the entire manual in a single file, fetch `/llms-full.txt`.",
        "",
        "## Documentation",
        "",
    ]
    for shard in shards:
        hint = f": {shard.lede}" if shard.lede else ""
        map_lines.append(f"- [{shard.title}]({_page_url(shard)}){hint}")
    map_lines += [
        "",
        "## Full text",
        "",
        "- [Complete documentation](/llms-full.txt): every topic concatenated into one file.",
    ]
    llms_txt = "\n".join(map_lines).rstrip() + "\n"

    # llms-full.txt — the monolith: the catalog plus every topic shard, in order.
    full_parts = [
        f"# {title} — complete documentation\n\n> {summary}.\n\n"
        "Auto-generated from the Dashdown docs by tooling/gen-agent-docs.py. This is the "
        "whole manual in one file; `/llms.txt` is the per-topic map.",
        _render_catalog(),
    ]
    full_parts += [_render_shard(shard) for shard in shards]
    llms_full = "\n\n".join(part.rstrip() for part in full_parts).rstrip() + "\n"

    return {"llms.txt": llms_txt, "llms-full.txt": llms_full}


def build_outputs() -> dict[str, str]:
    """Return ``{relative_path: content}`` for every generated file under ``scaffold/``.

    Pure (reads ``docs/``, writes nothing) so a freshness test can compare the committed
    artifacts against a fresh generation. Keys are POSIX-relative to ``dashdown/scaffold/``:
    ``"AGENTS.md"`` and ``"references/<slug>.md"``.
    """
    shards = _shards()
    outputs: dict[str, str] = {"AGENTS.md": _render_map(shards)}
    outputs[f"{REFERENCES_SUBDIR}/{CATALOG_SLUG}.md"] = _render_catalog()
    for shard in shards:
        outputs[f"{REFERENCES_SUBDIR}/{shard.slug}.md"] = _render_shard(shard)
    return outputs


def main() -> int:
    if not DOCS_PAGES.is_dir():
        print(f"error: {DOCS_PAGES} not found (run from the repo root)", file=sys.stderr)
        return 1

    outputs = build_outputs()
    refs_dir = SCAFFOLD_DIR / REFERENCES_SUBDIR
    refs_dir.mkdir(parents=True, exist_ok=True)

    # Evict stale shards: a renamed/deleted docs topic must not leave a ghost reference.
    expected = {p for p in outputs if p.startswith(f"{REFERENCES_SUBDIR}/")}
    for existing in refs_dir.glob("*.md"):
        rel = f"{REFERENCES_SUBDIR}/{existing.name}"
        if rel not in expected:
            existing.unlink()
            print(f"removed stale {existing.relative_to(REPO_ROOT)}")

    total = 0
    for rel, content in outputs.items():
        dest = SCAFFOLD_DIR / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(content, encoding="utf-8")
        total += len(content.encode("utf-8"))
    print(
        f"wrote AGENTS.md + {len(outputs) - 1} reference shard(s) under "
        f"{(SCAFFOLD_DIR / REFERENCES_SUBDIR).relative_to(REPO_ROOT)} ({total:,} bytes total)"
    )

    # Publish the llms.txt map + monolith onto the docs project (build.py serves them
    # from the static-build root). Skipped on a packaged install with no docs/ source.
    if DOCS_ROOT.is_dir():
        for rel, content in build_llms_outputs().items():
            (DOCS_ROOT / rel).write_text(content, encoding="utf-8")
        print(f"wrote llms.txt + llms-full.txt under {DOCS_ROOT.relative_to(REPO_ROOT)}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
