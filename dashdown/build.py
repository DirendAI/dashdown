"""Static site export — the ``dashdown build`` command.

Pre-renders every page to a standalone HTML file and executes each page's
queries once at build time, writing the results as JSON snapshots. The emitted
site has no server: the same frontend JS that talks to ``/_dashdown/api/data``
on the dev server instead reads the pre-rendered JSON (see ``readBuildConfig``
in ``static/core.js``). The output can be hosted on any static host (Netlify,
Vercel, Cloudflare Pages, GitHub Pages, S3, ...).

All links, assets, and data URLs are emitted **root-relative** (no leading
slash) and resolved against a ``<base href>`` that a tiny inline script computes
at load time from the page's known depth. The result works served from any
directory, under any URL sub-path, regardless of whether the host adds a
trailing slash, and even opened straight off disk — no configuration required.

Dynamic ``[slug]`` detail pages are pre-rendered when the template opts in with a
``static_paths`` frontmatter block — a query whose rows enumerate the route param
values (the ``getStaticPaths`` pattern). Each concrete page is rendered with its
params, and its queries are snapshotted **per record** (the client reads the
right one via a ``data_url`` baked into the page's query defs). A template without
``static_paths`` is skipped, exactly as before.

Limitations (inherent to a serverless snapshot):
- Queries run with their **default** parameters; interactive filters that depend
  on server-side SQL substitution won't re-query — the snapshot is fixed.
- A dynamic ``[slug]`` page is exported only for the rows its ``static_paths``
  query returns; without that block it's skipped (no enumerable URL set).
- ``<Ask />`` commentary is baked once per ask id; on a dynamic page that means
  the first rendered record's params, not one snapshot per record.
- Live data sources must be reachable, with credentials, at build time.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import shutil
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, select_autoescape

from dashdown.llm import (
    AskDef,
    generate_answer,
    relevant_params,
    resolve_model_name,
    unavailable_notice,
)
from dashdown.project import (
    Project,
    load_project,
    build_breadcrumbs,
    format_config_json,
    resolve_logo_url,
)
from dashdown.render.pipeline import (
    RenderedPage,
    render_page,
    get_query_def,
    get_python_query_def,
    serialize_result,
    _substitute_params,
)
from dashdown.python_query import run_python_query
from dashdown.server import _render_nav_html, _json_default

log = logging.getLogger(__name__)

_PKG_DIR = Path(__file__).parent
_TEMPLATES_DIR = _PKG_DIR / "templates"
_STATIC_DIR = _PKG_DIR / "static"


@dataclass
class BuildResult:
    out_dir: Path
    pages: list[str] = field(default_factory=list)
    queries: list[tuple[str, str]] = field(default_factory=list)  # (connector, name)
    failed_pages: list[tuple[str, str]] = field(default_factory=list)  # (url, error)
    failed_queries: list[tuple[str, str, str]] = field(default_factory=list)  # connector, name, error
    asks: list[str] = field(default_factory=list)  # ask ids baked
    failed_asks: list[tuple[str, str, str]] = field(default_factory=list)  # id, query, error


def page_depth(app_url: str) -> int:
    """Number of path segments below the site root (``/`` -> 0, ``/a/b`` -> 2)."""
    rel = app_url.strip("/")
    return len(rel.split("/")) if rel else 0


def root_link(target_app_url: str) -> str:
    """Root-relative href (no leading slash) to a page's ``index.html``.

    Resolved against the runtime ``<base href>``: ``/`` -> ``index.html``,
    ``/a/b`` -> ``a/b/index.html``.
    """
    rel = target_app_url.strip("/")
    return "index.html" if not rel else f"{rel}/index.html"


def base_script(depth: int) -> str:
    """A static relative ``<base>`` plus a script that refines it to the exact
    absolute site root for this page's ``depth``.

    Two parts, and the order matters:

    1. A **static** ``<base href="../…">`` (one ``../`` per depth; ``./`` at the
       root). This is plain HTML, so the browser's *preload scanner* — which
       speculatively fetches the root-relative ``<link>``/``<script>`` assets that
       follow, before any script runs — resolves them against the right root for
       the common trailing-slash / sub-path serving. Without it, the scanner
       resolves ``_dashdown/static/…`` against a deep page URL (``/a/b/``) and
       every subpage 404s those assets (then silently refetches once the base is
       set) — console-spamming and wasteful.
    2. A script that strips a trailing ``index.html``, forces a trailing slash,
       then walks up ``depth`` segments and pins ``<base>.href`` to the resulting
       absolute root. This nails the one case the relative base can't: a
       no-trailing-slash URL (``/a/b``), where the browser's notion of "current
       directory" drops the last segment. It runs before the *real* parser
       reaches the asset tags, so the authoritative fetch always uses the precise
       root; only that rare case can incur the old double-fetch.

    On the dev server ``base_script`` is unset (assets use absolute ``/`` paths),
    so this is static-build only. Must precede any ``<link>``/``<script>``.
    """
    rel = "./" if depth <= 0 else "../" * depth
    return (
        f'<base href="{rel}">'
        "<script>(function(){var n=%d,p=location.pathname;"
        "p=p.replace(/index\\.html$/,'');"
        "if(p.charAt(p.length-1)!=='/')p+='/';"
        "for(var i=0;i<n;i++)p=p.replace(/[^/]*\\/$/,'');"
        "var b=document.getElementsByTagName('base')[0];"
        "if(b)b.href=location.origin+p;})();</script>"
    ) % depth


def _output_file(app_url: str, out_dir: Path) -> Path:
    """The ``index.html`` file an app URL is written to."""
    rel = app_url.strip("/")
    return out_dir / "index.html" if not rel else out_dir / rel / "index.html"


def _nav_with_hrefs(nodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Deep-copy a nav tree, adding a root-relative ``href`` to each node while
    keeping its canonical ``url`` (used for active-state matching)."""
    out: list[dict[str, Any]] = []
    for n in nodes:
        new = dict(n)
        new["href"] = root_link(n.get("url", "/"))
        if n.get("children"):
            new["children"] = _nav_with_hrefs(n["children"])
        out.append(new)
    return out


# A dynamic route segment, e.g. `[channel]` in `/detail-pages/[channel]`.
_ROUTE_PARAM_RE = re.compile(r"\[(\w+)\]")
# Characters kept verbatim in a snapshot filename; everything else collapses to
# `-`. The filename also carries a hash, so this is for readability, not identity.
_SLUG_SAFE_RE = re.compile(r"[^A-Za-z0-9._-]+")


def _route_param_names(app_url: str) -> list[str]:
    """The `[param]` names in a dynamic page's canonical URL (in order)."""
    return _ROUTE_PARAM_RE.findall(app_url)


def _concrete_url(app_url: str, params: dict[str, str]) -> str:
    """Fill a dynamic URL's `[param]` segments from `params`
    (`/detail-pages/[channel]` + {channel: pip} -> `/detail-pages/pip`)."""
    out = app_url
    for k, v in params.items():
        out = out.replace(f"[{k}]", v)
    return out


def _params_key(params: dict[str, str]) -> tuple:
    """Hashable, order-independent key for a params dict (snapshot dedup)."""
    return tuple(sorted(params.items()))


def _snapshot_rel_path(connector: str, name: str, params: dict[str, str]) -> str:
    """Root-relative path of a query's JSON snapshot.

    With no params this is the original ``_dashdown/data/<connector>/<name>.json``
    (so plain pages and existing builds are byte-for-byte unchanged). With route
    params (a detail page) a per-record suffix is appended — a readable slug of
    the values plus a short hash for collision-free uniqueness — so two records of
    one template get distinct files instead of clobbering a single one."""
    base = f"_dashdown/data/{connector}/{name}"
    if not params:
        return f"{base}.json"
    canon = "&".join(f"{k}={params[k]}" for k in sorted(params))
    digest = hashlib.sha1(canon.encode("utf-8")).hexdigest()[:10]
    readable = "-".join(
        _SLUG_SAFE_RE.sub("-", str(params[k])).strip("-") or "x"
        for k in sorted(params)
    )[:48]
    return f"{base}__{readable}-{digest}.json" if readable else f"{base}__{digest}.json"


def _enumerate_static_paths(
    project: Project, app_url: str, md_path: Path, result: BuildResult
) -> list[dict[str, str]]:
    """Resolve a dynamic page's ``static_paths`` frontmatter into route params,
    one dict per concrete page to pre-render (the ``getStaticPaths`` pattern).

    Opt-in frontmatter on the ``[slug]`` page::

        static_paths:
          connector: main          # optional, default: the project's default source
          query: SELECT DISTINCT channel FROM downloads

    The query runs at build time; each row supplies the route params **by column
    name** (a ``[channel]`` route needs a ``channel`` column; extra columns are
    ignored, so the list page's own overview query can double as the source).
    Rows with an empty value or a ``/`` in a param (can't be one URL segment) are
    skipped. Absent ``static_paths`` -> ``[]`` (the page is skipped, as before);
    a malformed block or a failing query records a failed page and returns ``[]``."""
    from dashdown.render.markdown import parse_frontmatter

    fm = parse_frontmatter(md_path.read_text(encoding="utf-8"))
    spec = fm.get("static_paths")
    if spec is None:
        return []
    if not isinstance(spec, dict):
        result.failed_pages.append(
            (app_url, "static_paths must be a mapping with a 'query' key")
        )
        return []
    sql = spec.get("query")
    if not isinstance(sql, str) or not sql.strip():
        result.failed_pages.append(
            (app_url, "static_paths.query must be a non-empty SQL string")
        )
        return []
    connector_name = str(spec.get("connector") or project.default_connector or "")
    connector = project.connectors.get(connector_name)
    if connector is None:
        result.failed_pages.append(
            (app_url, f"static_paths connector '{connector_name}' not found")
        )
        return []

    param_names = _route_param_names(app_url)
    try:
        qr = connector.query(sql)
    except Exception as e:  # noqa: BLE001
        log.warning("static_paths query for %s failed: %s", app_url, e)
        result.failed_pages.append(
            (app_url, f"static_paths query failed: {type(e).__name__}: {e}")
        )
        return []

    missing = [pn for pn in param_names if pn not in qr.columns]
    if missing:
        result.failed_pages.append(
            (
                app_url,
                f"static_paths query is missing column(s) {missing} for route "
                f"param(s) {param_names}",
            )
        )
        return []

    out: list[dict[str, str]] = []
    seen: set[tuple] = set()
    skipped = 0
    for row in qr.rows:
        rowmap = {c: v for c, v in zip(qr.columns, row)}
        params: dict[str, str] = {}
        ok = True
        for pn in param_names:
            v = rowmap[pn]
            sval = "" if v is None else str(v)
            if sval == "" or "/" in sval:
                ok = False
                break
            params[pn] = sval
        if not ok:
            skipped += 1
            continue
        key = _params_key(params)
        if key in seen:
            continue
        seen.add(key)
        out.append(params)
    if skipped:
        # No silent caps: a value that can't be a URL segment is dropped loudly.
        log.warning(
            "static_paths for %s skipped %d row(s) with an empty or '/'-containing value",
            app_url,
            skipped,
        )
    return out


def build_site(
    project_root: Path, out_dir: Path, *, only_pages: list[str] | None = None
) -> BuildResult:
    """Render ``project_root`` into a static site under ``out_dir``.

    ``only_pages`` restricts the **page render + query snapshots** to the given
    page URLs (the chrome — nav, search index, assets — is still complete). Used
    by ``dashdown screenshot`` to build just the page it captures, so unrelated
    pages' queries (e.g. a slow/flaky external API on another page) never run.
    """
    project_root = project_root.resolve()
    out_dir = out_dir.resolve()

    # Refuse to write into the project itself — a stray --out would otherwise
    # delete the user's source on the rmtree below.
    if out_dir == project_root or (out_dir / "dashdown.yaml").exists():
        raise ValueError(
            f"Refusing to build into {out_dir}: it looks like the project "
            f"directory, not an output directory. Choose a separate --out."
        )

    # Wall-clock start of the whole build (project load included) — the "built in
    # <N>" half of the provenance footer measures against this.
    started_at = time.perf_counter()
    project = load_project(project_root)
    try:
        return _build(project, out_dir, only_pages=only_pages, started_at=started_at)
    finally:
        project.close()


# Filled into the "built in <N>" footer at render time and swapped for the real,
# formatted duration in one final pass once the total build time is known (a page
# can't know it while rendering — the render *is* most of the build). Distinct
# enough never to collide with page content; carries no HTML-special chars so it
# survives Jinja autoescaping verbatim.
_BUILD_DURATION_PLACEHOLDER = "__DASHDOWN_BUILD_DURATION__"


def _format_build_duration(seconds: float) -> str:
    """Human-readable build duration for the provenance footer."""
    if seconds < 1:
        return f"{seconds * 1000:.0f}ms"
    if seconds < 60:
        return f"{seconds:.1f}s"
    minutes, secs = divmod(int(round(seconds)), 60)
    return f"{minutes}m {secs:02d}s"


def _build(
    project: Project,
    out_dir: Path,
    *,
    only_pages: list[str] | None = None,
    started_at: float | None = None,
) -> BuildResult:
    result = BuildResult(out_dir=out_dir)

    if out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True)

    # Copy framework static assets + the user's project assets folder.
    shutil.copytree(_STATIC_DIR, out_dir / "_dashdown" / "static")
    if project.assets_dir.is_dir():
        shutil.copytree(project.assets_dir, out_dir / "assets")

    # Copy co-located page assets: any non-.md file living under pages/ (images,
    # PDFs, downloads a page references relatively). The pages/ tree is mirrored
    # into the output root so a page's relative ref — rewritten to a root-relative
    # URL by render_page — resolves against the runtime <base>. The .md sources
    # themselves are never copied.
    if project.pages_dir.is_dir():
        for f in project.pages_dir.rglob("*"):
            if not f.is_file() or f.suffix.lower() == ".md":
                continue
            if f.name.startswith(".") or "__pycache__" in f.parts:
                continue
            dst = out_dir / f.relative_to(project.pages_dir)
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(f, dst)

    # Publish llms.txt / llms-full.txt at the site root if the project ships them (the
    # docs project generates these via tooling/gen-agent-docs.py). The llms.txt convention
    # is a root-served map at /llms.txt plus the whole manual at /llms-full.txt — agent-
    # friendly hosts fetch them directly; copying root → root keeps that path.
    for fname in ("llms.txt", "llms-full.txt"):
        src = project.root / fname
        if src.is_file():
            shutil.copyfile(src, out_dir / fname)

    # Copy custom components' colocated frontend assets (js/css only — never the
    # .py source). Mirrors how the dev server serves /_dashdown/components; the
    # template emits a <link>/<script> per asset, resolved against the <base>.
    for rel in project.component_js + project.component_css:
        dst = out_dir / "_dashdown" / "components" / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(project.components_dir / rel, dst)

    # Bake the full-text search index next to the data snapshots (the live
    # `/_dashdown/api/search-index` has no equivalent in a static export; the
    # client reads this file instead — see search.py / site_search.js).
    from dashdown.search import build_search_index

    (out_dir / "_dashdown" / "search-index.json").write_text(
        json.dumps(build_search_index(project), default=_json_default),
        encoding="utf-8",
    )

    env = Environment(
        loader=FileSystemLoader(str(_TEMPLATES_DIR)),
        autoescape=select_autoescape(["html"]),
    )
    env.globals["_render_nav"] = _render_nav_html
    page_template = env.get_template("page.html")

    app_nav = project.nav_tree()
    nav_static = _nav_with_hrefs(app_nav)  # root-relative; same for every page
    exported: set[tuple[str, str]] = set()
    exported_asks: set[str] = set()

    # Escape hatch (same as the dev server): a project's assets/custom.css is
    # copied into the export by the assets copytree above, so link it if present.
    # Root-relative, resolved against the runtime <base> like every other asset.
    custom_css_url = (
        "assets/custom.css"
        if (project.assets_dir / "custom.css").is_file()
        else None
    )

    # Snapshot timestamp, shared by two stamps: the page-header "Updated" stamp
    # (frontmatter `updated: true`) and the per-page "Generated <time>" build
    # footer below. The live data fetch resolves to a fixed JSON, so the build
    # time is the meaningful moment, not the viewing time. `built_at` is the
    # canonical UTC ISO (also fed to the client as `builtAt`); `built_at_display`
    # is a readable no-JS fallback for the footer (page_header.js localizes the
    # <time> to the viewer when JS is on).
    built_dt = datetime.now(timezone.utc)
    built_at = built_dt.isoformat()
    built_at_display = built_dt.strftime("%b %d, %Y at %H:%M UTC")

    # Shared render context every page needs — passed to `_emit_page`.
    ctx = _PageCtx(
        project=project,
        page_template=page_template,
        out_dir=out_dir,
        app_nav=app_nav,
        nav_static=nav_static,
        custom_css_url=custom_css_url,
        built_at=built_at,
        built_at_display=built_at_display,
        built_duration_display=_BUILD_DURATION_PLACEHOLDER,
        exported=exported,
        exported_asks=exported_asks,
    )

    page_urls = project.list_pages()
    if only_pages is not None:
        # Render (and thus snapshot queries for) only the requested page(s); the
        # nav/search/assets above stay complete so the page's chrome is intact.
        wanted = {("/" + p.strip("/")).rstrip("/") or "/" for p in only_pages}
        page_urls = [u for u in page_urls if u in wanted]

    for app_url in page_urls:
        md_path, params = project.page_path(app_url)
        if md_path is None:
            continue

        if "[" in app_url:
            # Dynamic `[slug]` template: pre-render one concrete page per row of
            # its `static_paths` enumeration query. No such block -> skipped.
            slugs = _enumerate_static_paths(project, app_url, md_path, result)
            if not slugs:
                log.info("Skipping dynamic page %s (no static_paths)", app_url)
                continue
            log.info("Pre-rendering %d page(s) for %s", len(slugs), app_url)
            # All records of one template render the SAME body (prose isn't
            # templated — the slug only flows into SQL), so render it once and
            # reuse it: for 100+ records that turns 100 markdown+component renders
            # into one, the bulk of a large detail build's CPU. `shared` is None
            # only when the body bakes in the page URL (e.g. <Table detail_slug>),
            # in which case each record renders itself (correct, just slower).
            shared = _render_shared_body(project, app_url, md_path, slugs[0])
            for slug_params in slugs:
                _emit_page(
                    ctx,
                    _concrete_url(app_url, slug_params),
                    md_path,
                    slug_params,
                    result,
                    rendered=shared,
                )
            continue

        _emit_page(ctx, app_url, md_path, params, result)

    # Now that every page is written, the total build time is known — swap the
    # duration placeholder in each emitted page for the real, formatted value.
    # A no-op when the build wasn't timed (defensive; `build_site` always is).
    if started_at is not None:
        duration = _format_build_duration(time.perf_counter() - started_at)
        for app_url in result.pages:
            out_file = _output_file(app_url, out_dir)
            html = out_file.read_text(encoding="utf-8")
            if _BUILD_DURATION_PLACEHOLDER in html:
                out_file.write_text(
                    html.replace(_BUILD_DURATION_PLACEHOLDER, duration),
                    encoding="utf-8",
                )

    return result


# A current_path no real page would use; if it shows up in a rendered body, that
# body bakes in the page URL and can't be shared across a template's records.
_ROUTE_SENTINEL = "/__dashdown_route_sentinel_9f3a7c__"


def _page_dir(project: Project, md_path: Path) -> str:
    """The page's directory under ``pages/`` as POSIX ("" at the root), so
    ``render_page`` can resolve co-located asset refs relative to the page."""
    try:
        rel = md_path.parent.relative_to(project.pages_dir).as_posix()
    except ValueError:
        return ""
    return "" if rel == "." else rel


def _render_shared_body(
    project: Project, app_url: str, md_path: Path, sample_params: dict[str, str]
) -> "RenderedPage | None":
    """Render a dynamic template once, for reuse across all its records.

    Returns the rendered page when its body is independent of the per-record URL
    (the common case) so ``_emit_page`` can skip re-rendering each record. Returns
    ``None`` — falling back to per-record rendering — when rendering fails (let
    ``_emit_page`` surface the error per record) or when the body embeds the page
    URL, detected by a sentinel ``current_path`` appearing in the output (e.g. a
    ``<Table detail_slug>`` whose links are ``{current path}/{value}``). ``params``
    don't affect the rendered body (they only substitute into SQL at fetch time),
    so the sample row's params are safe to render with."""
    try:
        source = md_path.read_text(encoding="utf-8")
        rendered = render_page(
            source,
            project.connectors,
            params=sample_params,
            current_path=_ROUTE_SENTINEL,
            include_base=project.root,
            page_dir=_page_dir(project, md_path),
            static_build=True,
            library=project.queries,
            python_library=project.python_queries,
            semantic_models=project.semantic_models,
        )
    except Exception:  # noqa: BLE001 — re-rendered (and recorded) per record
        return None
    if _ROUTE_SENTINEL in rendered.body_html:
        return None
    return rendered


@dataclass
class _PageCtx:
    """The per-build context `_emit_page` needs, bundled so dynamic-page
    enumeration and ordinary pages share one render path."""

    project: Project
    page_template: Any
    out_dir: Path
    app_nav: list[dict[str, Any]]
    nav_static: list[dict[str, Any]]
    custom_css_url: str | None
    built_at: str
    built_at_display: str
    built_duration_display: str
    exported: set[tuple]
    exported_asks: set[str]


def _emit_page(
    ctx: _PageCtx,
    app_url: str,
    md_path: Path,
    params: dict[str, str],
    result: BuildResult,
    rendered: "RenderedPage | None" = None,
) -> None:
    """Render one concrete page to ``<app_url>/index.html`` and snapshot its queries.

    ``params`` are the page's route params — empty for a normal page, the captured
    ``[slug]`` values for a dynamic detail page. Both the query snapshots and the
    ``data_url`` baked into each query def are keyed by those params, so two
    records of one template get distinct JSON files and the client fetches the
    right one (no shared, param-less data URL — the static-build analogue of the
    detail-page cross-contamination bug).

    ``rendered`` lets a dynamic template's records reuse one shared body render
    (see ``_render_shared_body``) — the markdown/component pass is identical
    across records, so this skips re-running it per record. Only the per-record
    work (query snapshots, breadcrumbs, the page-URL-aware bits) still runs."""
    project = ctx.project
    if rendered is None:
        try:
            source = md_path.read_text(encoding="utf-8")
            rendered = render_page(
                source,
                project.connectors,
                params=params,
                current_path=app_url,
                include_base=project.root,
                page_dir=_page_dir(project, md_path),
                static_build=True,  # omit filter controls — the snapshot is fixed
                library=project.queries,
                python_library=project.python_queries,
                semantic_models=project.semantic_models,
            )
        except Exception as e:  # noqa: BLE001
            log.exception("Render failed for %s", app_url)
            result.failed_pages.append((app_url, f"{type(e).__name__}: {e}"))
            return

    # Snapshot each query with THIS page's params, and remember the per-record
    # JSON path so the client fetches that record's data (not a shared file).
    # Dedup on (connector, name, params) so unrelated pages don't re-run a query.
    data_urls: dict[str, str] = {}
    for name, qdef in rendered.query_defs.items():
        connector_name = qdef.get("connector", "main")
        data_urls[name] = _snapshot_rel_path(connector_name, name, params)
        key = (connector_name, name, _params_key(params))
        if key in ctx.exported:
            continue
        ctx.exported.add(key)
        _export_query(project, name, connector_name, params, ctx.out_dir, result)

    # Generate the commentary for each <Ask /> block once and bake it as a JSON
    # snapshot (ask.js reads it in static mode). Deterministic ids dedupe repeats
    # across pages — including across a dynamic page's records (see module docs).
    for ask in rendered.ask_defs:
        if ask.id in ctx.exported_asks:
            continue
        ctx.exported_asks.add(ask.id)
        _export_ask(project, ask, params, ctx.out_dir, result)

    # Tell the client where each query's snapshot lives (root-relative, resolved
    # against the <base>). Plain pages get the unchanged connector/name path.
    query_defs = {
        name: {**qdef, "data_url": data_urls[name]}
        for name, qdef in rendered.query_defs.items()
    }

    # All URLs are root-relative, resolved against the <base> the inline script
    # sets from this page's depth.
    page_title = rendered.frontmatter.get("title", md_path.stem)
    breadcrumbs = build_breadcrumbs(app_url, ctx.app_nav, page_title)
    for crumb in breadcrumbs:
        crumb["url"] = root_link(crumb["url"])

    html = ctx.page_template.render(
        title=project.config.title,
        page_title=page_title,
        body_html=rendered.body_html,
        datasets_json=json.dumps(rendered.datasets, default=_json_default),
        query_defs_json=json.dumps(query_defs, default=_json_default),
        nav_tree=ctx.nav_static,
        pages=project.list_pages(),
        current=app_url,  # canonical, for nav active-state
        breadcrumbs=breadcrumbs,
        asset_prefix="",
        home_href=root_link("/"),
        base_script=base_script(page_depth(app_url)),
        build_config_json=json.dumps(
            {"static": True, "dataBase": "_dashdown/data", "builtAt": ctx.built_at}
        ),
        # `dashdown build` provenance footer (build-only — the dev server leaves
        # these unset, so the live app never shows it). `built_at` is the machine-
        # readable <time datetime>; `built_at_display` the no-JS fallback text.
        built_at=ctx.built_at,
        built_at_display=ctx.built_at_display,
        # A placeholder while rendering; the final pass swaps it for the real
        # total-build duration once every page is written (see `_build`).
        built_duration_display=ctx.built_duration_display,
        # Root-relative like every other asset URL; the <base> resolves it.
        logo_url=resolve_logo_url(project.config.branding.logo, prefix=""),
        favicon_url=resolve_logo_url(project.config.branding.favicon, prefix=""),
        branding_json=(
            json.dumps({"palette": project.config.branding.palette})
            if project.config.branding.palette
            else None
        ),
        format_json=format_config_json(project.config.format),
        custom_css_url=ctx.custom_css_url,
        # Colocated custom-component assets (copied into _dashdown/components
        # above); the template emits a <link>/<script type=module> per asset,
        # root-relative and resolved against the page's <base>.
        component_js=project.component_js,
        component_css=project.component_css,
        # Static builds never render the global date control (filters can't
        # re-query a fixed snapshot); the header slot stays empty.
        global_date_html="",
        # PDF page action (the Embed button is off in static builds — no live
        # token endpoint — since render_page defaults embed_enabled=False).
        page_actions_html=rendered.page_actions_html,
        # The built-in search box works in static exports too (it reads the
        # baked search-index.json), so it honors the same `search:` toggle.
        search_enabled=project.config.search.enabled,
        search_placeholder=project.config.search.placeholder,
        search_max_results=project.config.search.max_results,
        # Desktop sidebar collapse: the static export ships the chrome and runs
        # Alpine, so the toggle works the same; localStorage persists the
        # reader's choice per-browser.
        sidebar_collapsed=project.config.sidebar.collapsed,
        sidebar_toggle=project.config.sidebar.toggle,
        # Single-page project → omit the nav + menu buttons (unless forced on).
        show_sidebar=(
            project.config.sidebar.show_single_page
            or project.navigable_page_count() > 1
        ),
    )

    out_file = _output_file(app_url, ctx.out_dir)
    out_file.parent.mkdir(parents=True, exist_ok=True)
    out_file.write_text(html, encoding="utf-8")
    result.pages.append(app_url)


def _export_query(
    project: Project,
    name: str,
    connector_name: str,
    params: dict[str, str],
    out_dir: Path,
    result: BuildResult,
) -> None:
    """Run one query with ``params`` substituted and write its JSON snapshot.

    ``params`` are the rendering page's route params (empty for a normal page);
    they take precedence over any registered defaults, so a dynamic detail page's
    query runs for *that* record. The file path mirrors the ``data_url`` the page
    hands the client (``_snapshot_rel_path``)."""
    data_path = out_dir / _snapshot_rel_path(connector_name, name, params)
    data_path.parent.mkdir(parents=True, exist_ok=True)

    def _write_error(msg: str) -> None:
        result.failed_queries.append((connector_name, name, msg))
        data_path.write_text(
            json.dumps({"columns": [], "rows": [], "query": name, "error": msg}),
            encoding="utf-8",
        )

    def _write_ok(qr) -> None:
        payload = serialize_result(qr)
        payload["query"] = name
        data_path.write_text(
            json.dumps(payload, default=_json_default), encoding="utf-8"
        )
        result.queries.append((connector_name, name))

    # A Python query (queries/*.py) snapshots through its runner, once with this
    # page's route params (the same `error`-on-failure contract as SQL).
    py_spec = get_python_query_def(name, connector_name)
    if py_spec is not None:
        try:
            qr = run_python_query(py_spec, dict(params), project.connectors)
        except Exception as e:  # noqa: BLE001
            log.warning("Python query '%s' failed during build: %s", name, e)
            _write_error(f"{type(e).__name__}: {e}")
            return
        _write_ok(qr)
        return

    query_def = get_query_def(name, connector_name)
    connector = project.connectors.get(connector_name)

    if query_def is None:
        _write_error(f"Query '{name}' not registered for connector '{connector_name}'")
        return
    if connector is None:
        _write_error(f"Connector '{connector_name}' not found")
        return

    sql, default_params, _ttl = query_def
    final_sql = _substitute_params(sql, {**default_params, **params})
    try:
        qr = connector.query(final_sql)
    except Exception as e:  # noqa: BLE001
        log.warning("Query '%s' (%s) failed during build: %s", name, connector_name, e)
        _write_error(f"{type(e).__name__}: {e}")
        return

    _write_ok(qr)


def _export_ask(
    project: Project,
    ask: AskDef,
    params: dict[str, str],
    out_dir: Path,
    result: BuildResult,
) -> None:
    """Generate one <Ask /> commentary with ``params`` substituted and bake the JSON.

    Snapshots live under ``_dashdown/data/_ask/`` — the leading underscore
    keeps the directory clear of connector names. An absent/misconfigured
    ``llm:`` block bakes a ``notice`` payload ("commentary not available")
    without counting as a failure — a keyless build is expected to succeed.
    A real failure (missing extra, provider/query error) writes an ``error``
    payload the ask card renders as a muted note; the build doesn't abort.
    Ask ids dedupe across pages, so on a dynamic page the first record's
    ``params`` win (the static client has no per-record ask path — see the
    module docstring)."""
    ask_path = out_dir / "_dashdown" / "data" / "_ask" / f"{ask.id}.json"
    ask_path.parent.mkdir(parents=True, exist_ok=True)

    # A multi-query ask reads several queries; report them all on failure.
    display_queries = ", ".join(name for name, _ in ask.queries)

    def _write_error(msg: str) -> None:
        result.failed_asks.append((ask.id, display_queries, msg))
        ask_path.write_text(
            json.dumps({"ask_id": ask.id, "html": "", "error": msg}),
            encoding="utf-8",
        )

    if not project.config.llm.enabled:
        log.info("Ask '%s': LLM not configured — baking a notice payload", ask.id)
        ask_path.write_text(
            json.dumps(
                {
                    "ask_id": ask.id,
                    "html": "",
                    "notice": unavailable_notice(project.config.llm),
                }
            ),
            encoding="utf-8",
        )
        return

    # Resolve each data source like the live endpoint: a Python / semantic query
    # (synthetic PythonQuerySpec) runs its callable; the SQL path is the fallback.
    # So <Ask metric={model.metric} /> and queries/*.py sources bake too. A
    # multi-query ask executes every referenced query, and the prompt params
    # are the union of each query's contribution (all params for a Python body,
    # the SQL-substituted subset otherwise) — same as the live endpoint.
    query_results = []
    prompt_params: dict[str, str] = {}
    for query_name, connector_name in ask.queries:
        py_spec = get_python_query_def(query_name, connector_name)
        if py_spec is not None:
            try:
                qr = run_python_query(py_spec, dict(params), project.connectors)
            except Exception as e:  # noqa: BLE001
                log.warning("Ask '%s' (%s) failed during build: %s", ask.id, query_name, e)
                _write_error(f"{type(e).__name__}: {e}")
                return
            prompt_params.update(params)
        else:
            query_def = get_query_def(query_name, connector_name)
            if query_def is None:
                _write_error(
                    f"Query '{query_name}' not registered for connector '{connector_name}'"
                )
                return
            connector = project.connectors.get(connector_name)
            if connector is None:
                _write_error(f"Connector '{connector_name}' not found")
                return

            sql, default_params, _ttl = query_def
            final_sql = _substitute_params(sql, {**default_params, **params})
            try:
                qr = connector.query(final_sql)
            except Exception as e:  # noqa: BLE001
                log.warning("Ask '%s' (%s) failed during build: %s", ask.id, query_name, e)
                _write_error(f"{type(e).__name__}: {e}")
                return
            prompt_params.update(relevant_params(sql, {**default_params, **params}))
        query_results.append(qr)

    try:
        adapter = project.get_llm_adapter()
        html, text = generate_answer(ask, query_results, adapter, prompt_params)
    except Exception as e:  # noqa: BLE001
        log.warning("Ask '%s' (%s) failed during build: %s", ask.id, display_queries, e)
        _write_error(f"{type(e).__name__}: {e}")
        return

    ask_path.write_text(
        json.dumps(
            {
                "ask_id": ask.id,
                "html": html,
                # Raw answer text: lets the static client replay the answer as
                # a typewriter (escaped plain text) before swapping in `html`.
                "text": text,
                "model": resolve_model_name(project.config.llm),
            },
            default=_json_default,
        ),
        encoding="utf-8",
    )
    result.asks.append(ask.id)
