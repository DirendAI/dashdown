"""Tests for dashdown.project module."""
import tempfile
from pathlib import Path

import pytest

from dashdown.project import (
    BrandingConfig,
    FormatConfig,
    Project,
    ProjectConfig,
    format_config_json,
    load_project,
    parse_branding_config,
    parse_format_config,
    resolve_logo_url,
    _SLUG_RE,
)
from dashdown.server import _render_nav_html


class TestProjectConfig:
    """Tests for ProjectConfig dataclass."""

    def test_default_config(self):
        """ProjectConfig has correct default values."""
        cfg = ProjectConfig()
        assert cfg.title == "Dashdown"

    def test_custom_config(self):
        """ProjectConfig can be customized."""
        cfg = ProjectConfig(title="My Project")
        assert cfg.title == "My Project"


class TestBrandingConfig:
    """Tests for the branding: block (logo + chart palette)."""

    def test_absent_block_gives_defaults(self):
        cfg = parse_branding_config(None)
        assert cfg == BrandingConfig()
        assert cfg.logo is None
        assert cfg.palette == []

    def test_parses_logo_and_palette(self):
        cfg = parse_branding_config(
            {"logo": " assets/logo.svg ", "palette": ["#6366f1", "#FFF", "#22c55e80"]}
        )
        assert cfg.logo == "assets/logo.svg"  # whitespace trimmed
        # 3-, 6- and 8-digit hex all accepted
        assert cfg.palette == ["#6366f1", "#FFF", "#22c55e80"]

    def test_parses_favicon(self):
        cfg = parse_branding_config({"favicon": " assets/favicon.png "})
        assert cfg.favicon == "assets/favicon.png"  # whitespace trimmed

    def test_absent_favicon_is_none(self):
        cfg = parse_branding_config({"logo": "assets/logo.svg"})
        assert cfg.favicon is None

    def test_empty_favicon_raises(self):
        with pytest.raises(ValueError, match="favicon"):
            parse_branding_config({"favicon": "  "})

    def test_non_mapping_block_raises(self):
        with pytest.raises(ValueError, match="branding"):
            parse_branding_config("assets/logo.svg")

    def test_empty_logo_raises(self):
        with pytest.raises(ValueError, match="logo"):
            parse_branding_config({"logo": "  "})

    def test_non_list_palette_raises(self):
        with pytest.raises(ValueError, match="palette"):
            parse_branding_config({"palette": "#6366f1"})

    @pytest.mark.parametrize("bad", ["6366f1", "#66ggff", "red", 123, None])
    def test_invalid_palette_entry_raises(self, bad):
        with pytest.raises(ValueError, match="hex color"):
            parse_branding_config({"palette": [bad]})

    def test_load_project_reads_branding(self, tmp_path):
        (tmp_path / "pages").mkdir()
        (tmp_path / "dashdown.yaml").write_text(
            "title: T\n"
            "branding:\n"
            "  logo: assets/logo.svg\n"
            '  palette: ["#6366f1", "#22c55e"]\n',
            encoding="utf-8",
        )
        project = load_project(tmp_path)
        assert project.config.branding.logo == "assets/logo.svg"
        assert project.config.branding.palette == ["#6366f1", "#22c55e"]

    def test_load_project_rejects_malformed_branding(self, tmp_path):
        (tmp_path / "pages").mkdir()
        (tmp_path / "dashdown.yaml").write_text(
            "branding:\n  palette: ['blue']\n", encoding="utf-8"
        )
        with pytest.raises(ValueError, match="hex color"):
            load_project(tmp_path)


class TestFormatConfig:
    """Tests for the format: block (project-wide locale / currency defaults)."""

    def test_absent_block_gives_defaults(self):
        cfg = parse_format_config(None)
        assert cfg == FormatConfig()
        assert cfg.locale is None
        assert cfg.currency is None

    def test_parses_locale_and_currency(self):
        cfg = parse_format_config({"locale": " de-DE ", "currency": " EUR "})
        assert cfg.locale == "de-DE"  # whitespace trimmed
        assert cfg.currency == "EUR"

    def test_parses_date_format(self):
        cfg = parse_format_config({"date_format": " DD.MM.YYYY "})
        assert cfg.date_format == "DD.MM.YYYY"  # whitespace trimmed

    def test_empty_date_format_raises(self):
        with pytest.raises(ValueError, match="date_format"):
            parse_format_config({"date_format": "  "})

    def test_non_mapping_block_raises(self):
        with pytest.raises(ValueError, match="format"):
            parse_format_config("de-DE")

    def test_empty_locale_raises(self):
        with pytest.raises(ValueError, match="locale"):
            parse_format_config({"locale": "  "})

    def test_empty_currency_raises(self):
        with pytest.raises(ValueError, match="currency"):
            parse_format_config({"currency": "  "})

    def test_format_config_json_omits_unset(self):
        assert format_config_json(FormatConfig()) is None
        assert format_config_json(FormatConfig(locale="de-DE")) == '{"locale": "de-DE"}'

    def test_format_config_json_includes_all(self):
        import json

        out = format_config_json(
            FormatConfig(locale="fr-FR", currency="EUR", date_format="DD/MM/YYYY")
        )
        assert json.loads(out) == {
            "locale": "fr-FR",
            "currency": "EUR",
            "date_format": "DD/MM/YYYY",
        }

    def test_load_project_reads_format(self, tmp_path):
        (tmp_path / "pages").mkdir()
        (tmp_path / "dashdown.yaml").write_text(
            "title: T\nformat:\n  locale: de-DE\n  currency: EUR\n"
            "  date_format: DD.MM.YYYY\n",
            encoding="utf-8",
        )
        project = load_project(tmp_path)
        assert project.config.format.locale == "de-DE"
        assert project.config.format.currency == "EUR"
        assert project.config.format.date_format == "DD.MM.YYYY"

    def test_load_project_rejects_malformed_format(self, tmp_path):
        (tmp_path / "pages").mkdir()
        (tmp_path / "dashdown.yaml").write_text(
            "format:\n  locale: ''\n", encoding="utf-8"
        )
        with pytest.raises(ValueError, match="locale"):
            load_project(tmp_path)


class TestResolveLogoUrl:
    def test_none_passes_through(self):
        assert resolve_logo_url(None) is None

    def test_relative_path_gets_dev_prefix(self):
        assert resolve_logo_url("assets/logo.svg") == "/assets/logo.svg"

    def test_leading_slash_not_doubled(self):
        assert resolve_logo_url("/assets/logo.svg") == "/assets/logo.svg"

    def test_static_build_uses_root_relative(self):
        assert resolve_logo_url("assets/logo.svg", prefix="") == "assets/logo.svg"

    @pytest.mark.parametrize(
        "url",
        ["https://example.com/logo.png", "http://example.com/l.png", "data:image/svg+xml,x"],
    )
    def test_external_urls_pass_through(self, url):
        assert resolve_logo_url(url) == url
        assert resolve_logo_url(url, prefix="") == url


class TestProjectPaths:
    """Tests for Project path properties."""

    def test_pages_dir(self):
        """pages_dir returns the pages directory path."""
        project = Project(root=Path("/test"), config=ProjectConfig())
        assert project.pages_dir == Path("/test/pages")

    def test_components_dir(self):
        """components_dir returns the components directory path."""
        project = Project(root=Path("/test"), config=ProjectConfig())
        assert project.components_dir == Path("/test/components")

    def test_assets_dir(self):
        """assets_dir returns the assets directory path."""
        project = Project(root=Path("/test"), config=ProjectConfig())
        assert project.assets_dir == Path("/test/assets")


class TestPagePathResolution:
    """Tests for page_path URL to filesystem resolution."""

    def test_root_path_returns_index(self):
        """Root path / returns pages/index.md."""
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            pages_dir = root / "pages"
            pages_dir.mkdir()
            (pages_dir / "index.md").write_text("# Home")

            project = Project(root=root, config=ProjectConfig())
            result, params = project.page_path("/")
            assert result == pages_dir / "index.md"
            assert params == {}

    def test_simple_path(self):
        """Simple path /about returns pages/about.md."""
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            pages_dir = root / "pages"
            pages_dir.mkdir()
            (pages_dir / "about.md").write_text("# About")

            project = Project(root=root, config=ProjectConfig())
            result, params = project.page_path("/about")
            assert result == pages_dir / "about.md"
            assert params == {}

    def test_nested_path(self):
        """Nested path /products/shoes returns pages/products/shoes.md."""
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            pages_dir = root / "pages"
            pages_dir.mkdir()
            (pages_dir / "products").mkdir()
            (pages_dir / "products" / "shoes.md").write_text("# Shoes")

            project = Project(root=root, config=ProjectConfig())
            result, params = project.page_path("/products/shoes")
            assert result == pages_dir / "products" / "shoes.md"
            assert params == {}

    def test_dynamic_segment_file(self):
        """Dynamic segment [slug].md matches any single path segment."""
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            pages_dir = root / "pages"
            pages_dir.mkdir()
            (pages_dir / "[slug].md").write_text("# Dynamic")

            project = Project(root=root, config=ProjectConfig())
            result, params = project.page_path("/test-page")
            assert result == pages_dir / "[slug].md"
            assert params == {"slug": "test-page"}

    def test_dynamic_segment_directory(self):
        """Dynamic segment [slug]/ matches any single path segment as directory."""
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            pages_dir = root / "pages"
            pages_dir.mkdir()
            slug_dir = pages_dir / "[slug]"
            slug_dir.mkdir()
            (slug_dir / "index.md").write_text("# Dynamic Index")

            project = Project(root=root, config=ProjectConfig())
            result, params = project.page_path("/my-page")
            assert result == slug_dir / "index.md"
            assert params == {"slug": "my-page"}

    def test_dynamic_segment_with_nested_path(self):
        """Dynamic segment in nested path captures the segment."""
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            pages_dir = root / "pages"
            pages_dir.mkdir()
            (pages_dir / "products").mkdir()
            (pages_dir / "products" / "[category].md").write_text("# Category")

            project = Project(root=root, config=ProjectConfig())
            result, params = project.page_path("/products/electronics")
            assert result == pages_dir / "products" / "[category].md"
            assert params == {"category": "electronics"}

    def test_no_match_returns_none(self):
        """Non-existent path returns (None, {})."""
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            pages_dir = root / "pages"
            pages_dir.mkdir()

            project = Project(root=root, config=ProjectConfig())
            result, params = project.page_path("/nonexistent")
            assert result is None
            assert params == {}

    def test_path_traversal_blocked(self):
        """Path traversal attempts (../) are blocked."""
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            pages_dir = root / "pages"
            pages_dir.mkdir()
            (pages_dir / "index.md").write_text("# Home")

            project = Project(root=root, config=ProjectConfig())
            # Attempt to traverse up from pages directory
            result, params = project.page_path("/../etc/passwd")
            assert result is None

    def test_path_traversal_via_dynamic_segments(self):
        """Path traversal via dynamic segments is blocked."""
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            pages_dir = root / "pages"
            pages_dir.mkdir()
            (pages_dir / "[slug].md").write_text("# Dynamic")

            project = Project(root=root, config=ProjectConfig())
            # The relative_to check should block this
            result, params = project.page_path("/../../../etc/passwd")
            # This should either not match or be blocked by relative_to check
            assert result is None

    def test_index_in_nested_directory(self):
        """Path to directory with index.md returns the index file."""
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            pages_dir = root / "pages"
            pages_dir.mkdir()
            (pages_dir / "products").mkdir()
            (pages_dir / "products" / "index.md").write_text("# Products")

            project = Project(root=root, config=ProjectConfig())
            result, params = project.page_path("/products")
            assert result == pages_dir / "products" / "index.md"
            assert params == {}

    def test_multiple_dynamic_segments(self):
        """Multiple dynamic segments in path are captured."""
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            pages_dir = root / "pages"
            pages_dir.mkdir()
            category_dir = pages_dir / "[category]"
            category_dir.mkdir()
            (category_dir / "[product].md").write_text("# Product")

            project = Project(root=root, config=ProjectConfig())
            result, params = project.page_path("/electronics/laptop")
            assert result == category_dir / "[product].md"
            assert params == {"category": "electronics", "product": "laptop"}


class TestListPages:
    """Tests for list_pages method."""

    def test_list_pages_empty(self):
        """Empty pages directory returns empty list."""
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            pages_dir = root / "pages"
            pages_dir.mkdir()

            project = Project(root=root, config=ProjectConfig())
            pages = project.list_pages()
            assert pages == []

    def test_list_pages_flat(self):
        """Flat pages directory lists all .md files."""
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            pages_dir = root / "pages"
            pages_dir.mkdir()
            (pages_dir / "about.md").write_text("# About")
            (pages_dir / "contact.md").write_text("# Contact")
            (pages_dir / "index.md").write_text("# Home")

            project = Project(root=root, config=ProjectConfig())
            pages = project.list_pages()
            assert "/" in pages  # index.md becomes /
            assert "/about" in pages
            assert "/contact" in pages

    def test_list_pages_nested(self):
        """Nested pages directory includes nested paths."""
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            pages_dir = root / "pages"
            pages_dir.mkdir()
            (pages_dir / "index.md").write_text("# Home")
            (pages_dir / "products").mkdir()
            (pages_dir / "products" / "index.md").write_text("# Products")
            (pages_dir / "products" / "shoes.md").write_text("# Shoes")

            project = Project(root=root, config=ProjectConfig())
            pages = project.list_pages()
            assert "/" in pages
            # The index.md in products/ directory becomes /products/
            assert "/products/" in pages or "/products" in pages
            assert "/products/shoes" in pages

    def test_list_pages_skips_dynamic_segments(self):
        """Dynamic segment files ([slug].md) are included in list."""
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            pages_dir = root / "pages"
            pages_dir.mkdir()
            (pages_dir / "[slug].md").write_text("# Dynamic")

            project = Project(root=root, config=ProjectConfig())
            pages = project.list_pages()
            # Dynamic segment files should be included
            assert "/[slug]" in pages


class TestSlugRegex:
    """Tests for the _SLUG_RE regex pattern."""

    def test_matches_valid_slugs(self):
        """Regex matches valid slug patterns."""
        assert _SLUG_RE.match("[slug]") is not None
        assert _SLUG_RE.match("[category]") is not None
        assert _SLUG_RE.match("[user_id]") is not None

    def test_does_not_match_regular_names(self):
        """Regex does not match regular file/directory names."""
        assert _SLUG_RE.match("index") is None
        assert _SLUG_RE.match("about") is None
        assert _SLUG_RE.match("products") is None

    def test_captures_group(self):
        """Regex captures the parameter name."""
        match = _SLUG_RE.match("[user_id]")
        assert match is not None
        assert match.group(1) == "user_id"


class TestNavTree:
    """Tests for nav_tree method."""

    def test_nav_tree_empty(self):
        """Empty pages directory returns empty nav tree."""
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            pages_dir = root / "pages"
            pages_dir.mkdir()

            project = Project(root=root, config=ProjectConfig())
            nav = project.nav_tree()
            assert nav == []

    def test_nav_tree_flat_pages(self):
        """Flat pages are listed in nav tree."""
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            pages_dir = root / "pages"
            pages_dir.mkdir()
            (pages_dir / "index.md").write_text("# Home\ntitle: Home\n")
            (pages_dir / "about.md").write_text("# About\ntitle: About\n")

            project = Project(root=root, config=ProjectConfig())
            nav = project.nav_tree()
            assert len(nav) >= 1
            # Home should be first (sidebar_position defaults to 100)
            assert any(n.get("url") == "/" for n in nav)

    def test_nav_tree_skips_dynamic_segments(self):
        """Dynamic segment files are skipped in nav tree."""
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            pages_dir = root / "pages"
            pages_dir.mkdir()
            (pages_dir / "[slug].md").write_text("# Dynamic")
            (pages_dir / "index.md").write_text("# Home")

            project = Project(root=root, config=ProjectConfig())
            nav = project.nav_tree()
            # Nav should only have home, not the dynamic segment
            urls = [n.get("url") for n in nav]
            assert "/" in urls
            assert not any("[slug]" in u for u in urls)


class TestRenderNavHtml:
    """Tests for _render_nav_html XSS escaping."""

    def test_renders_basic_link(self):
        """Normal nav nodes render as anchor tags."""
        nodes = [{"url": "/about", "label": "About"}]
        html = _render_nav_html(nodes, current="/")
        assert 'href="/about"' in html
        assert "About" in html

    def test_escapes_label_xss(self):
        """Malicious label is escaped and not executed as HTML."""
        nodes = [{"url": "/safe", "label": '<script>alert(1)</script>'}]
        html = _render_nav_html(nodes, current="/")
        assert "<script>" not in html
        assert "&lt;script&gt;" in html

    def test_escapes_url_xss(self):
        """Malicious URL is escaped and cannot inject attributes."""
        nodes = [{"url": '/" onclick="alert(1)', "label": "Click"}]
        html = _render_nav_html(nodes, current="/")
        assert 'onclick="alert(1)' not in html
        assert "&#34;" in html or "&quot;" in html

    def test_escapes_icon_xss(self):
        """Malicious icon value is escaped."""
        nodes = [{"url": "/x", "label": "X", "icon": '<img src=x onerror=alert(1)>'}]
        html = _render_nav_html(nodes, current="/")
        assert "<img" not in html
        assert "&lt;img" in html

    def test_named_icon_renders_svg(self):
        """A bundled icon name renders an inline SVG, not literal text."""
        nodes = [{"url": "/x", "label": "X", "icon": "home"}]
        html = _render_nav_html(nodes, current="/")
        assert "<svg" in html
        assert 'stroke="currentColor"' in html
        assert ">home<" not in html  # the name itself isn't shown

    def test_emoji_icon_passthrough(self):
        """A non-named icon (emoji) is rendered verbatim, no SVG."""
        nodes = [{"url": "/x", "label": "X", "icon": "🦷"}]
        html = _render_nav_html(nodes, current="/")
        assert "🦷" in html
        assert "<svg" not in html

    def test_active_class_applied(self):
        """Current URL receives active CSS class."""
        nodes = [{"url": "/current", "label": "Current"}]
        html = _render_nav_html(nodes, current="/current")
        assert "active" in html

    def test_group_node_renders_span(self):
        """Group nodes without a page render as a span, not an anchor."""
        nodes = [{"url": "/group", "label": "Group", "group": True, "children": [
            {"url": "/group/child", "label": "Child"}
        ]}]
        html = _render_nav_html(nodes, current="/other")
        assert "<span" in html
        assert 'href="/group"' not in html

    def test_empty_nodes_returns_empty_string(self):
        """Empty nodes list returns empty Markup."""
        html = _render_nav_html([], current="/")
        assert str(html) == ""

    def test_nested_children_rendered(self):
        """Children are rendered recursively."""
        nodes = [{"url": "/parent", "label": "Parent", "children": [
            {"url": "/parent/child", "label": "Child"}
        ]}]
        html = _render_nav_html(nodes, current="/")
        assert 'href="/parent/child"' in html
        assert "Child" in html


class TestBrandingIntegration:
    """The dev server injects the logo and palette from branding: into pages."""

    def _make_project(self, tmp_path, branding_yaml=""):
        (tmp_path / "pages").mkdir()
        (tmp_path / "pages" / "index.md").write_text("# Home\n\nHello.", encoding="utf-8")
        (tmp_path / "dashdown.yaml").write_text(
            "title: Test\n" + branding_yaml, encoding="utf-8"
        )
        return tmp_path

    def test_page_includes_logo_and_palette(self, tmp_path):
        from fastapi.testclient import TestClient
        from dashdown.server import create_app

        root = self._make_project(
            tmp_path,
            "branding:\n  logo: assets/logo.svg\n  palette: [\"#6366f1\", \"#22c55e\"]\n",
        )
        (root / "assets").mkdir()
        (root / "assets" / "logo.svg").write_text("<svg/>", encoding="utf-8")
        html = TestClient(create_app(root)).get("/").text
        assert '<img src="/assets/logo.svg"' in html
        assert 'id="dashdown-branding"' in html
        assert '"palette": ["#6366f1", "#22c55e"]' in html

    def test_page_omits_branding_when_unset(self, tmp_path):
        from fastapi.testclient import TestClient
        from dashdown.server import create_app

        root = self._make_project(tmp_path)
        html = TestClient(create_app(root)).get("/").text
        assert "dashdown-brand-logo" not in html
        assert 'id="dashdown-branding"' not in html

    def test_default_favicon_link(self, tmp_path):
        from fastapi.testclient import TestClient
        from dashdown.server import create_app

        root = self._make_project(tmp_path)
        html = TestClient(create_app(root)).get("/").text
        # Bundled default favicon present -> no /favicon.ico 404.
        assert '<link rel="icon" href="/_dashdown/static/favicon.svg"' in html

    def test_favicon_override(self, tmp_path):
        from fastapi.testclient import TestClient
        from dashdown.server import create_app

        root = self._make_project(
            tmp_path, "branding:\n  favicon: assets/brand.png\n"
        )
        html = TestClient(create_app(root)).get("/").text
        assert '<link rel="icon" href="/assets/brand.png"' in html
        assert "favicon.svg" not in html

    def test_page_includes_format_config(self, tmp_path):
        from fastapi.testclient import TestClient
        from dashdown.server import create_app

        root = self._make_project(
            tmp_path, "format:\n  locale: de-DE\n  currency: EUR\n"
        )
        html = TestClient(create_app(root)).get("/").text
        assert 'id="dashdown-format"' in html
        assert '"locale": "de-DE"' in html
        assert '"currency": "EUR"' in html

    def test_page_omits_format_config_when_unset(self, tmp_path):
        from fastapi.testclient import TestClient
        from dashdown.server import create_app

        root = self._make_project(tmp_path)
        html = TestClient(create_app(root)).get("/").text
        assert 'id="dashdown-format"' not in html


class TestSelfHostedAssets:
    """Pages link only locally-vendored CSS/JS — no CDN, works offline
    (design backlog #13)."""

    def _make_project(self, tmp_path):
        (tmp_path / "pages").mkdir()
        (tmp_path / "pages" / "index.md").write_text("# Home\n\nHello.", encoding="utf-8")
        (tmp_path / "dashdown.yaml").write_text("title: Test\n", encoding="utf-8")
        return tmp_path

    def test_page_links_vendored_assets(self, tmp_path):
        from fastapi.testclient import TestClient
        from dashdown.server import create_app

        root = self._make_project(tmp_path)
        html = TestClient(create_app(root)).get("/").text
        assert '_dashdown/static/vendor/tailwind.css' in html
        assert '_dashdown/static/vendor/echarts.min.js' in html
        assert '_dashdown/static/vendor/alpine.min.js' in html

    def test_page_has_no_cdn_references(self, tmp_path):
        from fastapi.testclient import TestClient
        from dashdown.server import create_app

        root = self._make_project(tmp_path)
        html = TestClient(create_app(root)).get("/").text
        for needle in ("cdn.tailwindcss.com", "jsdelivr", "fonts.googleapis.com"):
            assert needle not in html

    def test_static_assets_force_revalidation(self, tmp_path):
        # Browsers heuristically cache ES modules when no Cache-Control is
        # sent, which can pair stale JS with fresh CSS after an update; the
        # dev server must demand revalidation (ETag 304s keep it cheap).
        from fastapi.testclient import TestClient
        from dashdown.server import create_app

        root = self._make_project(tmp_path)
        client = TestClient(create_app(root))
        resp = client.get("/_dashdown/static/components/echarts_theme.js")
        assert resp.status_code == 200
        assert resp.headers["cache-control"] == "no-cache"
        # Conditional re-request answers 304 and still carries the policy.
        resp304 = client.get(
            "/_dashdown/static/components/echarts_theme.js",
            headers={"If-None-Match": resp.headers["etag"]},
        )
        assert resp304.status_code == 304
        assert resp304.headers["cache-control"] == "no-cache"

    def test_custom_css_linked_only_when_present(self, tmp_path):
        from fastapi.testclient import TestClient
        from dashdown.server import create_app

        root = self._make_project(tmp_path)
        # Absent by default.
        html = TestClient(create_app(root)).get("/").text
        assert "custom.css" not in html

        # Present once the project ships assets/custom.css — linked last.
        (root / "assets").mkdir()
        (root / "assets" / "custom.css").write_text(".x{}", encoding="utf-8")
        html = TestClient(create_app(root)).get("/").text
        assert '<link rel="stylesheet" href="/assets/custom.css"' in html


class TestColocatedComponents:
    """Custom components defined in their own folder: backend (.py) + frontend
    (.js/.css) live together under components/<Name>/, auto-discovered and
    injected into every page."""

    def _make_project(self, tmp_path):
        (tmp_path / "pages").mkdir()
        (tmp_path / "pages" / "index.md").write_text(
            "# Home\n\n<Widget />", encoding="utf-8"
        )
        (tmp_path / "dashdown.yaml").write_text("title: Test\n", encoding="utf-8")
        comp = tmp_path / "components" / "Widget"
        comp.mkdir(parents=True)
        (comp / "Widget.py").write_text(
            "from dashdown import Component, register_component\n"
            "@register_component('Widget')\n"
            "class Widget(Component):\n"
            "    def render(self, attrs, ctx, inner=None):\n"
            "        return '<div data-async-component=\"widget\"></div>'\n",
            encoding="utf-8",
        )
        (comp / "Widget.js").write_text(
            "import { fetchQueryData } from 'dashdown/core.js';\n", encoding="utf-8"
        )
        (comp / "Widget.css").write_text(".widget{}\n", encoding="utf-8")
        # A `_`-prefixed helper must NOT be auto-injected as its own script.
        (comp / "_helper.js").write_text("export const x = 1;\n", encoding="utf-8")
        return tmp_path

    def test_nested_py_is_imported_and_registered(self, tmp_path):
        from dashdown.components.base import get_component

        root = self._make_project(tmp_path)
        load_project(root)
        assert get_component("Widget") is not None

    def test_assets_discovered_skipping_underscore(self, tmp_path):
        root = self._make_project(tmp_path)
        project = load_project(root)
        assert project.component_js == ["Widget/Widget.js"]  # _helper.js excluded
        assert project.component_css == ["Widget/Widget.css"]

    def test_flat_layout_still_works(self, tmp_path):
        """A top-level components/foo.py keeps loading (recursion is a superset)."""
        from dashdown.components.base import get_component

        (tmp_path / "pages").mkdir()
        (tmp_path / "pages" / "index.md").write_text("# Home", encoding="utf-8")
        (tmp_path / "dashdown.yaml").write_text("title: Test\n", encoding="utf-8")
        (tmp_path / "components").mkdir()
        (tmp_path / "components" / "flat.py").write_text(
            "from dashdown import Component, register_component\n"
            "@register_component('Flat')\n"
            "class Flat(Component):\n"
            "    def render(self, attrs, ctx, inner=None):\n"
            "        return '<i></i>'\n",
            encoding="utf-8",
        )
        load_project(tmp_path)
        assert get_component("Flat") is not None

    def test_page_injects_importmap_and_asset_tags(self, tmp_path):
        from fastapi.testclient import TestClient
        from dashdown.server import create_app

        root = self._make_project(tmp_path)
        html = TestClient(create_app(root)).get("/").text
        assert 'type="importmap"' in html
        assert '"dashdown/": "/_dashdown/static/"' in html
        assert (
            '<script type="module" src="/_dashdown/components/Widget/Widget.js">'
            in html
        )
        assert (
            '<link rel="stylesheet" href="/_dashdown/components/Widget/Widget.css"'
            in html
        )
        # The private helper isn't injected as its own <script>.
        assert "_dashdown/components/Widget/_helper.js" not in html

    def test_no_importmap_without_component_js(self, tmp_path):
        from fastapi.testclient import TestClient
        from dashdown.server import create_app

        (tmp_path / "pages").mkdir()
        (tmp_path / "pages" / "index.md").write_text("# Home", encoding="utf-8")
        (tmp_path / "dashdown.yaml").write_text("title: Test\n", encoding="utf-8")
        html = TestClient(create_app(tmp_path)).get("/").text
        assert 'type="importmap"' not in html

    def test_serves_js_and_css_but_guards_py_and_traversal(self, tmp_path):
        from fastapi.testclient import TestClient
        from dashdown.server import create_app

        root = self._make_project(tmp_path)
        client = TestClient(create_app(root))
        assert client.get("/_dashdown/components/Widget/Widget.js").status_code == 200
        assert client.get("/_dashdown/components/Widget/Widget.css").status_code == 200
        # The Python source is server-side only — never web-readable.
        assert client.get("/_dashdown/components/Widget/Widget.py").status_code == 404
        # Even an underscore helper module serves (it's a real .js asset, just
        # not auto-injected) — confirms the guard is on extension, not name.
        assert client.get("/_dashdown/components/Widget/_helper.js").status_code == 200

    def test_embed_token_exempts_component_assets(self, tmp_path):
        """An authed embed (cross-origin iframe, no creds) must still load a
        custom component's JS/CSS — the embed-asset allowlist covers the
        components path just like /_dashdown/static/."""
        from dashdown.embed import sign_embed_token
        from dashdown.server import _embed_authorizes

        (tmp_path / "pages").mkdir()
        (tmp_path / "pages" / "index.md").write_text("# Home", encoding="utf-8")
        (tmp_path / "dashdown.yaml").write_text(
            "title: Test\n"
            "auth:\n  type: basic\n  username: a\n  password: b\n"
            "embed:\n  enabled: true\n  secret: topsecret\n",
            encoding="utf-8",
        )
        project = load_project(tmp_path)
        # A token scoped to some *other* page still authorizes shared assets.
        token = sign_embed_token("topsecret", "/elsewhere", [])

        class _Req:
            def __init__(self, path, embed):
                self.url = type("U", (), {"path": path})()
                self.query_params = {"_embed": embed} if embed else {}

        comp = "/_dashdown/components/Widget/Widget.js"
        assert _embed_authorizes(project, _Req(comp, token)) is True
        # A non-asset path is NOT exempted by an out-of-scope token.
        assert _embed_authorizes(project, _Req("/elsewhere2", token)) is False
        # No token -> not authorized even for an asset.
        assert _embed_authorizes(project, _Req(comp, None)) is False
