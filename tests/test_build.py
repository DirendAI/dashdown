"""Tests for static site export (`dashdown build`, Stage 8b)."""
import json

import pytest

from dashdown.build import (
    build_site,
    page_depth,
    root_link,
    base_script,
    _output_file,
    _nav_with_hrefs,
    _concrete_url,
    _route_param_names,
    _snapshot_rel_path,
)


# --------------------------------------------------------------------------- #
# URL / path helpers (pure functions)
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize(
    "app_url,depth",
    [("/", 0), ("/foo", 1), ("/a/b", 2), ("/a/b/c", 3)],
)
def test_page_depth(app_url, depth):
    assert page_depth(app_url) == depth


@pytest.mark.parametrize(
    "target,expected",
    [
        ("/", "index.html"),
        ("/foo", "foo/index.html"),
        ("/a/b", "a/b/index.html"),
    ],
)
def test_root_link(target, expected):
    assert root_link(target) == expected


def test_base_script_embeds_depth_and_writes_base():
    assert "var n=0," in base_script(0)
    assert "var n=2," in base_script(2)
    # A static relative <base> the preload scanner can act on (one ../ per depth,
    # ./ at the root), then a script that pins it to the precise absolute root.
    assert '<base href="./">' in base_script(0)
    assert '<base href="../../">' in base_script(2)
    assert "b.href=location.origin+p" in base_script(0)


def test_output_file_maps_to_directory_index(tmp_path):
    assert _output_file("/", tmp_path) == tmp_path / "index.html"
    assert _output_file("/foo", tmp_path) == tmp_path / "foo" / "index.html"
    assert _output_file("/a/b", tmp_path) == tmp_path / "a" / "b" / "index.html"


def test_nav_with_hrefs_keeps_canonical_url():
    nav = [
        {"url": "/", "label": "Home"},
        {"url": "/sales", "label": "Sales", "children": [{"url": "/sales/q1", "label": "Q1"}]},
    ]
    out = _nav_with_hrefs(nav)
    # Canonical url preserved (active-state matching); root-relative href added.
    assert out[0]["url"] == "/" and out[0]["href"] == "index.html"
    assert out[1]["href"] == "sales/index.html"
    assert out[1]["children"][0]["href"] == "sales/q1/index.html"
    assert out[1]["children"][0]["url"] == "/sales/q1"
    # original untouched
    assert "href" not in nav[1]["children"][0]


# --------------------------------------------------------------------------- #
# Full build integration
# --------------------------------------------------------------------------- #

def _make_project(root):
    (root / "pages").mkdir()
    (root / "data").mkdir()
    (root / "assets").mkdir()
    (root / "dashdown.yaml").write_text("title: Test Dash\n", encoding="utf-8")
    (root / "sources.yaml").write_text(
        "main:\n  type: csv\n  directory: data\n", encoding="utf-8"
    )
    (root / "data" / "sales.csv").write_text(
        "region,amount\nNorth,100\nSouth,200\n", encoding="utf-8"
    )
    (root / "assets" / "logo.txt").write_text("LOGO", encoding="utf-8")
    (root / "pages" / "index.md").write_text(
        "# Home\n\n"
        ":::query name=by_region connector=main\n"
        "SELECT region, SUM(amount) AS total FROM sales GROUP BY region ORDER BY region\n"
        ":::\n\n"
        '<Table data={by_region} title="By Region" />\n',
        encoding="utf-8",
    )
    (root / "pages" / "detail.md").write_text(
        "# Detail\n\n"
        ":::query name=rows connector=main\n"
        "SELECT * FROM sales\n"
        ":::\n\n"
        '<Table data={rows} />\n',
        encoding="utf-8",
    )
    # Dynamic page — must be skipped by the build.
    (root / "pages" / "[id].md").write_text(
        "# Dynamic ${id}\n\n"
        ":::query name=dyn connector=main\n"
        "SELECT * FROM sales WHERE region = '${id}'\n"
        ":::\n",
        encoding="utf-8",
    )


def test_build_produces_pages_and_data(tmp_path):
    proj = tmp_path / "proj"
    proj.mkdir()
    _make_project(proj)
    out = tmp_path / "dist"

    result = build_site(proj, out)

    # Pages written as directory index.html; dynamic page skipped.
    assert (out / "index.html").is_file()
    assert (out / "detail" / "index.html").is_file()
    assert "/index" not in result.pages  # urls are normalized
    assert "/" in result.pages and "/detail" in result.pages
    assert all("[" not in u for u in result.pages)

    # Query snapshots written with the expected shape.
    data = json.loads((out / "_dashdown" / "data" / "main" / "by_region.json").read_text())
    assert data["columns"] == ["region", "total"]
    assert data["rows"] == [["North", 100], ["South", 200]]
    assert data["query"] == "by_region"

    # The dynamic page's query is never exported.
    assert not (out / "_dashdown" / "data" / "main" / "dyn.json").exists()
    assert result.failed_pages == []
    assert result.failed_queries == []


def test_build_copies_static_assets(tmp_path):
    proj = tmp_path / "proj"
    proj.mkdir()
    _make_project(proj)
    out = tmp_path / "dist"
    build_site(proj, out)

    # Framework JS + user static both copied.
    assert (out / "_dashdown" / "static" / "core.js").is_file()
    assert (out / "assets" / "logo.txt").read_text() == "LOGO"


def test_build_publishes_llms_txt_at_root(tmp_path):
    # A project shipping llms.txt / llms-full.txt (the docs project does) has them
    # served from the static-build root, per the llms.txt convention.
    proj = tmp_path / "proj"
    proj.mkdir()
    _make_project(proj)
    (proj / "llms.txt").write_text("# Map\n", encoding="utf-8")
    (proj / "llms-full.txt").write_text("# Everything\n", encoding="utf-8")
    out = tmp_path / "dist"
    build_site(proj, out)

    assert (out / "llms.txt").read_text() == "# Map\n"
    assert (out / "llms-full.txt").read_text() == "# Everything\n"


def test_build_without_llms_txt_emits_none(tmp_path):
    # A project that doesn't ship them gets no stray root files.
    proj = tmp_path / "proj"
    proj.mkdir()
    _make_project(proj)
    out = tmp_path / "dist"
    build_site(proj, out)

    assert not (out / "llms.txt").exists()
    assert not (out / "llms-full.txt").exists()


def test_build_exports_colocated_component_assets(tmp_path):
    """A custom component's colocated .js/.css ships in the static export (and
    the page injects the tags), but its .py source never does."""
    proj = tmp_path / "proj"
    proj.mkdir()
    _make_project(proj)
    comp = proj / "components" / "Widget"
    comp.mkdir(parents=True)
    (comp / "Widget.py").write_text(
        "from dashdown import Component, register_component\n"
        "@register_component('Widget')\n"
        "class Widget(Component):\n"
        "    def render(self, attrs, ctx, inner=None):\n"
        "        return '<div data-async-component=\"widget\"></div>'\n",
        encoding="utf-8",
    )
    (comp / "Widget.js").write_text("export const x = 1;\n", encoding="utf-8")
    (comp / "Widget.css").write_text(".widget{}\n", encoding="utf-8")
    out = tmp_path / "dist"
    build_site(proj, out)

    base = out / "_dashdown" / "components" / "Widget"
    assert (base / "Widget.js").is_file()
    assert (base / "Widget.css").is_file()
    assert not (base / "Widget.py").exists()  # source never exported

    page = (out / "index.html").read_text()
    # Root-relative (base-resolved), and the import map is present.
    assert 'type="importmap"' in page
    # The import-map address MUST start with `./` in a static build: a bare
    # relative value (`_dashdown/static/`) is not a valid import-map specifier —
    # the browser nulls it ("blocked by a null value"), so a colocated module's
    # `import … from "dashdown/core.js"` would never resolve. `./` resolves
    # against the same runtime <base> as every other asset URL.
    assert '"dashdown/": "./_dashdown/static/"' in page
    assert 'src="_dashdown/components/Widget/Widget.js"' in page
    assert 'href="_dashdown/components/Widget/Widget.css"' in page


def test_build_only_pages_limits_render_and_snapshots(tmp_path):
    """`only_pages` renders just the named page(s) and snapshots only their
    queries — so unrelated pages' (possibly slow/flaky) queries never run. The
    nav/search/assets stay complete so the built page's chrome is intact."""
    proj = tmp_path / "proj"
    proj.mkdir()
    _make_project(proj)  # index.md (by_region) + detail.md (rows) + [id].md
    out = tmp_path / "dist"

    build_site(proj, out, only_pages=["/"])

    # Only the home page is emitted; the other page's HTML is not.
    assert (out / "index.html").is_file()
    assert not (out / "detail" / "index.html").exists()
    # Only the home page's query is snapshotted; detail's `rows` never ran.
    assert (out / "_dashdown" / "data" / "main" / "by_region.json").is_file()
    assert not (out / "_dashdown" / "data" / "main" / "rows.json").exists()
    # Chrome stays complete: the search index is baked and the nav still links
    # every page (the un-built one just 404s if clicked — a screenshot won't).
    assert (out / "_dashdown" / "search-index.json").is_file()
    assert "detail/index.html" in (out / "index.html").read_text()


def test_build_vendors_assets_with_no_cdn(tmp_path):
    """Static export ships the self-hosted CSS/JS and references no CDN
    (design backlog #13 — offline-capable)."""
    proj = tmp_path / "proj"
    proj.mkdir()
    _make_project(proj)
    out = tmp_path / "dist"
    build_site(proj, out)

    # Vendored assets copied into the export.
    assert (out / "_dashdown" / "static" / "vendor" / "tailwind.css").is_file()
    assert (out / "_dashdown" / "static" / "vendor" / "echarts.min.js").is_file()
    assert (out / "_dashdown" / "static" / "vendor" / "alpine.min.js").is_file()
    assert (out / "_dashdown" / "static" / "vendor" / "mermaid.min.js").is_file()
    assert (out / "_dashdown" / "static" / "vendor" / "fonts" / "inter.woff2").is_file()

    home = (out / "index.html").read_text()
    assert "_dashdown/static/vendor/tailwind.css" in home
    for needle in ("cdn.tailwindcss.com", "jsdelivr", "fonts.googleapis.com"):
        assert needle not in home


def test_build_links_custom_css_when_present(tmp_path):
    proj = tmp_path / "proj"
    proj.mkdir()
    _make_project(proj)
    (proj / "assets" / "custom.css").write_text(".x{}", encoding="utf-8")
    out = tmp_path / "dist"
    build_site(proj, out)

    # Copied into the export and linked root-relative (last, highest priority).
    assert (out / "assets" / "custom.css").read_text() == ".x{}"
    assert '<link rel="stylesheet" href="assets/custom.css"' in (out / "index.html").read_text()


def test_build_uses_root_relative_paths(tmp_path):
    proj = tmp_path / "proj"
    proj.mkdir()
    _make_project(proj)
    out = tmp_path / "dist"
    build_site(proj, out)

    # Every page emits root-relative URLs (resolved against a runtime <base>).
    home = (out / "index.html").read_text()
    assert '<script id="dashdown-build"' in home
    assert '"static": true' in home
    assert '"dataBase": "_dashdown/data"' in home
    assert 'href="_dashdown/static/dashdown.css"' in home
    assert 'src="_dashdown/static/dashdown.js"' in home
    assert 'href="detail/index.html"' in home  # link to another page
    # No root-absolute framework paths leak in (would break sub-path hosting).
    assert 'href="/_dashdown' not in home
    assert '"dataBase": "/_dashdown' not in home


def test_build_injects_generated_timestamp(tmp_path):
    """Every static page carries a "Generated <time>" provenance footer with a
    machine-readable ISO timestamp + a human-readable fallback. The same build
    instant feeds the `builtAt` client config, so the two stay in sync."""
    import re
    from datetime import datetime

    proj = tmp_path / "proj"
    proj.mkdir()
    _make_project(proj)
    out = tmp_path / "dist"
    build_site(proj, out)

    for page in ("index.html", "detail/index.html"):
        html = (out / page).read_text()
        assert "dashdown-build-stamp" in html
        assert "Generated <time" in html
        m = re.search(r'data-dashdown-build-time>([^<]+)</time>', html)
        assert m and "UTC" in m.group(1)  # readable no-JS fallback
        iso = re.search(r'<time datetime="([^"]+)" data-dashdown-build-time>', html)
        assert iso  # machine-readable instant
        # Parses as a real timestamp, and matches the client config's builtAt.
        datetime.fromisoformat(iso.group(1))
        built_at = re.search(r'"builtAt": "([^"]+)"', html)
        assert built_at and built_at.group(1) == iso.group(1)
        # The "built in <duration>" half is patched in once the total build time
        # is known — the placeholder must never survive into the output.
        assert re.search(r"built in \S+", html)
        assert "__DASHDOWN_BUILD_DURATION__" not in html


def test_format_build_duration():
    """The provenance footer's duration reads naturally across magnitudes."""
    from dashdown.build import _format_build_duration

    assert _format_build_duration(0.42) == "420ms"
    assert _format_build_duration(2.37) == "2.4s"
    assert _format_build_duration(65) == "1m 05s"


def test_dev_server_omits_generated_timestamp(tmp_path):
    """The footer is build-only: the live server never sets `built_at`, so the
    page-template gate (`{% if built_at %}`) leaves it out entirely."""
    from fastapi.testclient import TestClient
    from dashdown.server import create_app

    proj = tmp_path / "proj"
    proj.mkdir()
    _make_project(proj)

    html = TestClient(create_app(proj)).get("/").text
    assert "dashdown-build-stamp" not in html
    assert "data-dashdown-build-time" not in html


def test_build_injects_branding(tmp_path):
    proj = tmp_path / "proj"
    proj.mkdir()
    _make_project(proj)
    (proj / "assets" / "logo.svg").write_text("<svg/>", encoding="utf-8")
    (proj / "dashdown.yaml").write_text(
        "title: Test Dash\n"
        "branding:\n"
        "  logo: assets/logo.svg\n"
        '  palette: ["#6366f1", "#22c55e"]\n',
        encoding="utf-8",
    )
    out = tmp_path / "dist"
    build_site(proj, out)

    home = (out / "index.html").read_text()
    assert '<script id="dashdown-branding"' in home
    assert '"palette": ["#6366f1", "#22c55e"]' in home
    # Logo URL is root-relative like every other asset (resolved via <base>).
    assert '<img src="assets/logo.svg"' in home
    assert '<img src="/assets' not in home


def test_build_omits_branding_when_unset(tmp_path):
    proj = tmp_path / "proj"
    proj.mkdir()
    _make_project(proj)
    out = tmp_path / "dist"
    build_site(proj, out)

    home = (out / "index.html").read_text()
    assert 'id="dashdown-branding"' not in home
    assert "dashdown-brand-logo" not in home


def test_build_injects_format_config(tmp_path):
    proj = tmp_path / "proj"
    proj.mkdir()
    _make_project(proj)
    (proj / "dashdown.yaml").write_text(
        "title: Test Dash\nformat:\n  locale: de-DE\n  currency: EUR\n",
        encoding="utf-8",
    )
    out = tmp_path / "dist"
    build_site(proj, out)

    home = (out / "index.html").read_text()
    assert '<script id="dashdown-format"' in home
    assert '"locale": "de-DE"' in home
    assert '"currency": "EUR"' in home


def test_build_omits_format_config_when_unset(tmp_path):
    proj = tmp_path / "proj"
    proj.mkdir()
    _make_project(proj)
    out = tmp_path / "dist"
    build_site(proj, out)

    home = (out / "index.html").read_text()
    assert 'id="dashdown-format"' not in home


def test_build_injects_depth_aware_base_script(tmp_path):
    proj = tmp_path / "proj"
    proj.mkdir()
    _make_project(proj)
    # A nested page so we can confirm the base script carries its depth.
    (proj / "pages" / "sales").mkdir()
    (proj / "pages" / "sales" / "q1.md").write_text(
        "# Q1\n\n"
        ":::query name=q1 connector=main\n"
        "SELECT * FROM sales\n"
        ":::\n\n"
        "<Table data={q1} />\n",
        encoding="utf-8",
    )
    out = tmp_path / "dist"
    build_site(proj, out)

    # Home is depth 0; the nested page is depth 2 — same root-relative URLs,
    # only the base script's depth differs.
    assert "var n=0," in (out / "index.html").read_text()
    nested = (out / "sales" / "q1" / "index.html").read_text()
    assert "var n=2," in nested
    assert '"dataBase": "_dashdown/data"' in nested
    assert 'href="_dashdown/static/dashdown.css"' in nested


def test_build_strips_filter_components(tmp_path):
    proj = tmp_path / "proj"
    proj.mkdir()
    _make_project(proj)
    # A page with a filter (Dropdown) feeding a chart.
    (proj / "pages" / "filtered.md").write_text(
        "# Filtered\n\n"
        ":::query name=regions connector=main\n"
        "SELECT DISTINCT region FROM sales ORDER BY region\n"
        ":::\n\n"
        ":::query name=by_reg connector=main\n"
        "SELECT region, SUM(amount) AS total FROM sales "
        "WHERE '${region}' = '' OR region = '${region}' GROUP BY region\n"
        ":::\n\n"
        '<Dropdown name="region" data={regions} column="region" label="Region" />\n\n'
        '<Table data={by_reg} title="By Region" />\n',
        encoding="utf-8",
    )
    out = tmp_path / "dist"
    build_site(proj, out)

    html = (out / "filtered" / "index.html").read_text()
    # The Dropdown filter is omitted from the static output...
    assert 'data-async-component="dropdown"' not in html
    # ...along with the filter-row slot it would have been relocated into,
    # including the off-canvas drawer and its trigger button (#21).
    assert "dashdown-filter-bar" not in html
    assert "dashdown-filter-drawer" not in html
    # ...but the data component (Table) still renders.
    assert 'data-async-component="table"' in html
    # The data query snapshot is still exported (unfiltered, default params).
    assert (out / "_dashdown" / "data" / "main" / "by_reg.json").is_file()


def test_build_refuses_to_overwrite_project(tmp_path):
    proj = tmp_path / "proj"
    proj.mkdir()
    _make_project(proj)
    with pytest.raises(ValueError, match="Refusing to build"):
        build_site(proj, proj)


def test_build_records_query_failure_without_aborting(tmp_path):
    proj = tmp_path / "proj"
    proj.mkdir()
    _make_project(proj)
    # Add a page whose query references a missing table.
    (proj / "pages" / "broken.md").write_text(
        "# Broken\n\n"
        ":::query name=bad connector=main\n"
        "SELECT * FROM does_not_exist\n"
        ":::\n\n"
        "<Table data={bad} />\n",
        encoding="utf-8",
    )
    out = tmp_path / "dist"
    result = build_site(proj, out)

    # Other pages still built; the failure is recorded, not raised.
    assert (out / "index.html").is_file()
    assert any(name == "bad" for _c, name, _e in result.failed_queries)
    err = json.loads((out / "_dashdown" / "data" / "main" / "bad.json").read_text())
    assert err["rows"] == []
    assert "error" in err


# --------------------------------------------------------------------------- #
# Dynamic detail-page export helpers (pure functions)
# --------------------------------------------------------------------------- #

def test_route_param_names_extracts_brackets():
    assert _route_param_names("/detail-pages/[channel]") == ["channel"]
    assert _route_param_names("/orgs/[org]/repos/[repo]") == ["org", "repo"]
    assert _route_param_names("/static/page") == []


def test_concrete_url_substitutes_params():
    assert _concrete_url("/detail-pages/[channel]", {"channel": "pip"}) == "/detail-pages/pip"
    assert (
        _concrete_url("/orgs/[org]/repos/[repo]", {"org": "a", "repo": "b"})
        == "/orgs/a/repos/b"
    )


def test_snapshot_rel_path_plain_vs_per_record():
    # No params -> the original, unchanged path (back-compat).
    assert _snapshot_rel_path("main", "q", {}) == "_dashdown/data/main/q.json"
    # Route params -> a per-record file, distinct per value, readable + hashed.
    pip = _snapshot_rel_path("main", "q", {"channel": "pip"})
    docker = _snapshot_rel_path("main", "q", {"channel": "docker"})
    assert pip != docker
    assert pip.startswith("_dashdown/data/main/q__") and pip.endswith(".json")
    assert "pip" in pip
    # Deterministic.
    assert _snapshot_rel_path("main", "q", {"channel": "pip"}) == pip


def test_snapshot_rel_path_sanitizes_unsafe_values():
    rel = _snapshot_rel_path("main", "q", {"id": "a b/c?d"})
    seg = rel.split("/")[-1]
    # Only filename-safe chars survive in the readable part (path-safe, single seg).
    assert "/" not in seg.replace("_dashdown", "")
    assert "?" not in seg and " " not in seg


# --------------------------------------------------------------------------- #
# Dynamic detail-page export integration
# --------------------------------------------------------------------------- #

def _make_detail_project(root):
    (root / "pages" / "channels").mkdir(parents=True)
    (root / "data").mkdir()
    (root / "dashdown.yaml").write_text("title: Test Dash\n", encoding="utf-8")
    (root / "sources.yaml").write_text(
        "main:\n  type: csv\n  directory: data\n", encoding="utf-8"
    )
    (root / "data" / "downloads.csv").write_text(
        "channel,downloads\npip,100\npip,40\ndocker,20\nsource,5\n", encoding="utf-8"
    )
    (root / "pages" / "index.md").write_text("# Home\n", encoding="utf-8")
    # The dynamic template, opting into static export via `static_paths`.
    (root / "pages" / "channels" / "[channel].md").write_text(
        "---\n"
        "title: Channel\n"
        "static_paths:\n"
        "  connector: main\n"
        "  query: SELECT DISTINCT channel FROM downloads ORDER BY channel\n"
        "---\n\n"
        "# Channel\n\n"
        ":::query name=ch_summary connector=main\n"
        "SELECT channel, SUM(downloads) AS downloads FROM downloads "
        "WHERE channel = '${channel}' GROUP BY channel\n"
        ":::\n\n"
        "<Counter data={ch_summary} column=\"downloads\" label=\"Downloads\" />\n",
        encoding="utf-8",
    )


def test_build_exports_detail_pages_per_record(tmp_path):
    proj = tmp_path / "proj"
    proj.mkdir()
    _make_detail_project(proj)
    out = tmp_path / "dist"

    result = build_site(proj, out)

    # One concrete HTML page per enumerated slug (the [channel] template itself
    # is not emitted; its records are).
    for ch in ("pip", "docker", "source"):
        assert (out / "channels" / ch / "index.html").is_file()
        assert f"/channels/{ch}" in result.pages
    assert "/channels/[channel]" not in result.pages
    assert result.failed_pages == []

    # Per-record snapshots are distinct files with that record's data — not one
    # shared `ch_summary.json` overwritten per channel.
    snaps = sorted(
        p.name for p in (out / "_dashdown" / "data" / "main").glob("ch_summary__*.json")
    )
    assert len(snaps) == 3
    by_channel = {}
    for snap in snaps:
        data = json.loads((out / "_dashdown" / "data" / "main" / snap).read_text())
        # rows: [[channel, downloads]]
        ch, dl = data["rows"][0]
        by_channel[ch] = dl
    assert by_channel == {"pip": 140, "docker": 20, "source": 5}


def test_detail_page_query_def_points_at_its_own_snapshot(tmp_path):
    proj = tmp_path / "proj"
    proj.mkdir()
    _make_detail_project(proj)
    out = tmp_path / "dist"
    build_site(proj, out)

    # Each record's page carries a `data_url` resolving to its own snapshot, so
    # the static client fetches that record's data (not a shared param-less file).
    pip_html = (out / "channels" / "pip" / "index.html").read_text()
    docker_html = (out / "channels" / "docker" / "index.html").read_text()
    pip_url = _snapshot_rel_path("main", "ch_summary", {"channel": "pip"})
    docker_url = _snapshot_rel_path("main", "ch_summary", {"channel": "docker"})
    assert f'"data_url": "{pip_url}"' in pip_html
    assert f'"data_url": "{docker_url}"' in docker_html
    assert (out / pip_url).is_file() and (out / docker_url).is_file()


def test_dynamic_page_without_static_paths_is_skipped(tmp_path):
    proj = tmp_path / "proj"
    proj.mkdir()
    _make_detail_project(proj)
    # Replace the template with one that has NO static_paths block.
    (proj / "pages" / "channels" / "[channel].md").write_text(
        "# Channel\n\n"
        ":::query name=ch_summary connector=main\n"
        "SELECT * FROM downloads WHERE channel = '${channel}'\n"
        ":::\n\n"
        "<Table data={ch_summary} />\n",
        encoding="utf-8",
    )
    out = tmp_path / "dist"
    result = build_site(proj, out)

    assert not any(p.startswith("/channels/") for p in result.pages)
    assert not list((out / "_dashdown" / "data" / "main").glob("ch_summary*.json"))
    assert result.failed_pages == []


def test_detail_pages_with_url_in_body_render_per_record(tmp_path):
    """A detail template that bakes the page URL into its body (a <Table
    detail_slug> links to `{current path}/{value}`) can't share one render, so
    each record is rendered with its own current_path — links stay correct."""
    from dashdown.build import _ROUTE_SENTINEL

    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "pages" / "things").mkdir(parents=True)
    (proj / "data").mkdir()
    (proj / "dashdown.yaml").write_text("title: T\n", encoding="utf-8")
    (proj / "sources.yaml").write_text(
        "main:\n  type: csv\n  directory: data\n", encoding="utf-8"
    )
    (proj / "data" / "items.csv").write_text(
        "id,name\na,Alpha\nb,Bravo\n", encoding="utf-8"
    )
    (proj / "pages" / "index.md").write_text("# Home\n", encoding="utf-8")
    (proj / "pages" / "things" / "[id].md").write_text(
        "---\ntitle: Thing\nstatic_paths:\n"
        "  query: SELECT DISTINCT id FROM items ORDER BY id\n---\n\n"
        "# Thing\n\n"
        ":::query name=rows connector=main\nSELECT id, name FROM items\n:::\n\n"
        '<Table data={rows} detail_slug="id" />\n',
        encoding="utf-8",
    )
    out = tmp_path / "dist"
    build_site(proj, out)

    a_html = (out / "things" / "a" / "index.html").read_text()
    b_html = (out / "things" / "b" / "index.html").read_text()
    # Each record carries ITS OWN current_path in the detail_slug link pattern...
    assert "/things/a/{id}" in a_html
    assert "/things/b/{id}" in b_html
    assert "/things/b/{id}" not in a_html
    # ...and the shared-render sentinel never leaks into output.
    assert _ROUTE_SENTINEL not in a_html and _ROUTE_SENTINEL not in b_html


def test_static_paths_missing_column_records_failure(tmp_path):
    proj = tmp_path / "proj"
    proj.mkdir()
    _make_detail_project(proj)
    # Enumeration query returns a column that doesn't match the route param.
    (proj / "pages" / "channels" / "[channel].md").write_text(
        "---\n"
        "title: Channel\n"
        "static_paths:\n"
        "  query: SELECT DISTINCT downloads AS wrong_col FROM downloads\n"
        "---\n\n"
        "# Channel\n\n"
        ":::query name=ch_summary connector=main\n"
        "SELECT * FROM downloads WHERE channel = '${channel}'\n"
        ":::\n",
        encoding="utf-8",
    )
    out = tmp_path / "dist"
    result = build_site(proj, out)

    # Recorded as a failed page (missing `channel` column), build doesn't abort.
    assert any(url == "/channels/[channel]" for url, _e in result.failed_pages)
    assert (out / "index.html").is_file()
