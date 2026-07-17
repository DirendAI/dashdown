"""Project discovery and config."""
from __future__ import annotations

import importlib.util
import json
import logging
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from dashdown.auth import AuthConfig, parse_auth_config
from dashdown.embed import EmbedConfig, parse_embed_config
from dashdown.enterprise import require_enterprise
from dashdown.data.base import Connector
from dashdown.data.registry import (
    default_connector_name,
    load_connectors,
    no_default_error,
)
from dashdown.llm import LLMAdapter, LLMConfig, create_adapter, parse_llm_config
from dashdown.python_query import PythonQuerySpec, load_python_queries
from dashdown.semantic import load_semantic_models
from dashdown.query_composition import compose_library_queries
from dashdown.query_library import load_queries
from dashdown.render.markdown import QuerySpec, parse_frontmatter
from dashdown.render.pipeline import (
    register_library_queries,
    register_python_library_queries,
)

_SLUG_RE = re.compile(r"^\[(\w+)\]$")
_HEX_COLOR_RE = re.compile(r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})$")


@dataclass
class BrandingConfig:
    """Optional ``branding:`` block in ``dashdown.yaml``.

        branding:
          logo: assets/logo.svg          # project-relative path or http(s) URL
          favicon: assets/favicon.png    # project-relative path or http(s) URL
          palette: ["#6366f1", "#22c55e", "#f59e0b"]   # chart series colors

    ``logo`` renders in the header next to the title; ``favicon`` overrides the
    bundled default browser tab icon; ``palette`` overrides the ECharts series
    colors on every chart.
    """

    logo: str | None = None
    favicon: str | None = None
    palette: list[str] = field(default_factory=list)


def parse_branding_config(raw: Any) -> BrandingConfig:
    """Parse and validate the ``branding:`` block. Raises ValueError when
    malformed so the server refuses to start with a half-broken config
    (same policy as ``auth:``)."""
    if raw is None:
        return BrandingConfig()
    if not isinstance(raw, dict):
        raise ValueError("branding: must be a mapping (logo / palette keys)")

    logo = raw.get("logo")
    if logo is not None:
        if not isinstance(logo, str) or not logo.strip():
            raise ValueError("branding.logo must be a non-empty string")
        logo = logo.strip()

    favicon = raw.get("favicon")
    if favicon is not None:
        if not isinstance(favicon, str) or not favicon.strip():
            raise ValueError("branding.favicon must be a non-empty string")
        favicon = favicon.strip()

    palette_raw = raw.get("palette", [])
    if not isinstance(palette_raw, list):
        raise ValueError("branding.palette must be a list of hex colors")
    palette: list[str] = []
    for color in palette_raw:
        if not isinstance(color, str) or not _HEX_COLOR_RE.match(color.strip()):
            raise ValueError(
                f"branding.palette entry {color!r} is not a hex color like '#6366f1'"
            )
        palette.append(color.strip())

    return BrandingConfig(logo=logo, favicon=favicon, palette=palette)


@dataclass
class FormatConfig:
    """Optional ``format:`` block in ``dashdown.yaml`` — project-wide display
    defaults for numbers, currency and dates.

        format:
          locale: de-DE            # BCP-47 tag: grouping/decimal separators + date labels
          currency: EUR            # default symbol ("€") or ISO 4217 code ("EUR")
          date_format: DD.MM.YYYY  # default moment.js-style date pattern

    These are *defaults only*: a component's own ``locale=`` / ``currency=`` /
    ``date_format=`` attribute overrides them. ``locale`` also drives
    ``format="date"`` rendering, so dates follow the project locale without a
    per-column setting. The currency default applies only where a component
    already opts into ``format="currency"`` — it never turns a plain number
    (e.g. a row count) into money.
    """

    locale: str | None = None
    currency: str | None = None
    date_format: str | None = None


def parse_format_config(raw: Any) -> FormatConfig:
    """Parse and validate the ``format:`` block. Raises ValueError when malformed
    so the server refuses to start with a half-broken config (same policy as
    ``auth:`` / ``branding:``)."""
    if raw is None:
        return FormatConfig()
    if not isinstance(raw, dict):
        raise ValueError("format: must be a mapping (locale / currency / date_format keys)")

    locale = raw.get("locale")
    if locale is not None:
        if not isinstance(locale, str) or not locale.strip():
            raise ValueError("format.locale must be a non-empty string like 'de-DE'")
        locale = locale.strip()

    currency = raw.get("currency")
    if currency is not None:
        if not isinstance(currency, str) or not currency.strip():
            raise ValueError(
                "format.currency must be a non-empty string like 'EUR' or '€'"
            )
        currency = currency.strip()

    date_format = raw.get("date_format")
    if date_format is not None:
        if not isinstance(date_format, str) or not date_format.strip():
            raise ValueError(
                "format.date_format must be a non-empty string like 'DD.MM.YYYY'"
            )
        date_format = date_format.strip()

    return FormatConfig(locale=locale, currency=currency, date_format=date_format)


def format_config_json(fmt: FormatConfig) -> str | None:
    """JSON payload for the ``#dashdown-format`` script tag (consumed by
    ``core.js::readFormatConfig``), or ``None`` when nothing is set so the
    template omits the tag entirely."""
    data = {
        k: v
        for k, v in (
            ("locale", fmt.locale),
            ("currency", fmt.currency),
            ("date_format", fmt.date_format),
        )
        if v
    }
    return json.dumps(data) if data else None


_EXTERNAL_URL_RE = re.compile(r"^(https?:|data:)", re.IGNORECASE)


def resolve_logo_url(logo: str | None, prefix: str = "/") -> str | None:
    """Turn a configured logo into an href. External URLs pass through;
    project-relative paths get ``prefix`` (``/`` on the dev server, ``""`` in
    the static build, where the runtime ``<base>`` resolves root-relative
    links)."""
    if logo is None:
        return None
    if _EXTERNAL_URL_RE.match(logo):
        return logo
    return prefix + logo.lstrip("/")


@dataclass
class GlobalDateFilterConfig:
    """Optional ``global_filters.date`` block in ``dashdown.yaml`` — a single
    date-range control shown in the page-header row on **every** page, applying
    project-wide.

        global_filters:
          date:
            enabled: true
            label: Period
            default: last_30_days        # preset applied on first visit
            presets: last_7_days,last_30_days,last_90_days,this_month,this_year,custom
            start_param: date_start      # the ${param} queries reference
            end_param: date_end

    Queries opt in by using the ``${start_param}`` / ``${end_param}`` placeholders
    in their SQL (default ``date_start`` / ``date_end``); queries without them are
    untouched. The selection **persists across navigation** (localStorage), so it
    behaves as one global filter rather than a per-page one. When a page is
    embedded (``?_embed``) the control renders as an ordinary page filter in the
    filter bar instead of the (chrome-less) header.
    """

    enabled: bool = False
    label: str = "Period"
    presets: str = "last_7_days,last_30_days,last_90_days,this_month,this_year,custom"
    default: str | None = None
    start_param: str = "date_start"
    end_param: str = "date_end"


def parse_global_filters_config(raw: Any) -> GlobalDateFilterConfig:
    """Parse and validate the ``global_filters:`` block. Raises ValueError when
    malformed so the server refuses to start half-broken (same policy as
    ``auth:`` / ``branding:`` / ``embed:``)."""
    if raw is None:
        return GlobalDateFilterConfig()
    if not isinstance(raw, dict):
        raise ValueError("global_filters: must be a mapping (a 'date' key)")

    date_raw = raw.get("date")
    if date_raw is None:
        return GlobalDateFilterConfig()
    if not isinstance(date_raw, dict):
        raise ValueError("global_filters.date must be a mapping")

    cfg = GlobalDateFilterConfig(enabled=bool(date_raw.get("enabled", False)))

    label = date_raw.get("label")
    if label is not None:
        if not isinstance(label, str) or not label.strip():
            raise ValueError("global_filters.date.label must be a non-empty string")
        cfg.label = label.strip()

    presets = date_raw.get("presets")
    if presets is not None:
        if not isinstance(presets, str) or not presets.strip():
            raise ValueError(
                "global_filters.date.presets must be a non-empty comma-separated string"
            )
        cfg.presets = presets.strip()

    default = date_raw.get("default")
    if default is not None:
        if not isinstance(default, str) or not default.strip():
            raise ValueError("global_filters.date.default must be a preset name string")
        cfg.default = default.strip()

    for key in ("start_param", "end_param"):
        val = date_raw.get(key)
        if val is not None:
            if not isinstance(val, str) or not val.strip():
                raise ValueError(f"global_filters.date.{key} must be a non-empty string")
            setattr(cfg, key, val.strip())

    return cfg


@dataclass
class FiltersConfig:
    """Optional ``filters`` block — cross-cutting behavior for the interactive
    filter controls (Search / Combobox / Slider / RangeSlider / DateRange).

        filters:
          debounce: 500     # ms of quiet after the last keystroke or slider drag
                            # before a filter change re-fetches data (default 300)

    ``debounce`` is the **project-wide default**: every filter control commits its
    value to the store — the single reactive path data components re-fetch off —
    only after this quiet period, coalescing a burst of keystrokes / drag ticks
    into a single fetch. Raise it for a slow, per-query-expensive warehouse (e.g.
    BigQuery) where firing on partial input piles up requests; lower it for a
    snappy local backend (DuckDB/CSV) where instant feedback is free. A single
    control overrides it with a per-instance ``debounce=`` attribute.
    """

    debounce: int = 300


def parse_filters_config(raw: Any) -> FiltersConfig:
    """Parse and validate the ``filters:`` block. Raises ValueError when malformed
    so the server refuses to start half-broken (same policy as ``auth:`` etc.)."""
    if raw is None:
        return FiltersConfig()
    if not isinstance(raw, dict):
        raise ValueError("filters: must be a mapping (a 'debounce' key)")

    cfg = FiltersConfig()
    debounce = raw.get("debounce")
    if debounce is not None:
        if (
            not isinstance(debounce, int)
            or isinstance(debounce, bool)
            or debounce < 0
        ):
            raise ValueError(
                "filters.debounce must be a non-negative integer (milliseconds)"
            )
        cfg.debounce = debounce
    return cfg


@dataclass
class SearchConfig:
    """Optional ``search`` block — the built-in full-text search box shown in the
    app header (centered) and at the top of the mobile menu on every page.

        search:
          enabled: true                 # show the built-in search control (default true)
          placeholder: "Search docs…"   # input placeholder text
          max_results: 8                # results listed in the dropdown

    Disabling it removes only the *built-in chrome* control; the ``<SiteSearch>``
    component and the ``/_dashdown/api/search-index`` endpoint (and its baked
    static-build JSON) are unaffected, so a project can still place its own search
    box on a page.
    """

    enabled: bool = True
    placeholder: str = "Search…"
    max_results: int = 8


def parse_search_config(raw: Any) -> SearchConfig:
    """Parse and validate the ``search:`` block. Raises ValueError when malformed
    so the server refuses to start half-broken (same policy as ``auth:`` etc.)."""
    if raw is None:
        return SearchConfig()
    if not isinstance(raw, dict):
        raise ValueError("search: must be a mapping")

    cfg = SearchConfig(enabled=bool(raw.get("enabled", True)))

    placeholder = raw.get("placeholder")
    if placeholder is not None:
        if not isinstance(placeholder, str) or not placeholder.strip():
            raise ValueError("search.placeholder must be a non-empty string")
        cfg.placeholder = placeholder.strip()

    max_results = raw.get("max_results")
    if max_results is not None:
        if not isinstance(max_results, int) or isinstance(max_results, bool) or max_results <= 0:
            raise ValueError("search.max_results must be a positive integer")
        cfg.max_results = max_results

    return cfg


@dataclass
class SidebarConfig:
    """The desktop collapse behavior of the app's side navigation. Nested under
    the ``layout:`` block (it's part of a project's chrome), as ``layout.sidebar``.

        layout:
          sidebar:
            collapsed: false        # default-open seed; true → nav hidden on first visit
            toggle: true            # show the desktop collapse control (true) / hide it
            show_single_page: false # hide the nav + its buttons when the project
                                    # has only one page; true → always show it
            hidden: false           # true → never render the nav at all

    ``collapsed`` sets only the *first-visit* default — a reader's later collapse
    choice is kept in ``localStorage`` and wins over this seed (same precedence as
    a saved theme over the OS default). With ``toggle: false`` the collapse control
    is omitted, so the nav is pinned to whatever ``collapsed`` says. Desktop-only:
    the mobile slide-in menu is unaffected. ``show_single_page`` controls the
    single-page case: by default a project with one navigable page hides the
    sidebar and both menu buttons entirely (there's nothing to navigate to);
    ``true`` forces the nav to show even then. ``hidden`` removes the nav and both
    menu buttons regardless of page count (it overrides ``show_single_page``) —
    for blog/article-style sites that navigate through in-page links instead.
    """

    collapsed: bool = False
    toggle: bool = True
    show_single_page: bool = False
    hidden: bool = False


def parse_sidebar_config(raw: Any) -> SidebarConfig:
    """Parse and validate the ``sidebar:`` block. Raises ValueError when malformed
    so the server refuses to start half-broken (same policy as ``auth:`` etc.)."""
    if raw is None:
        return SidebarConfig()
    if not isinstance(raw, dict):
        raise ValueError("sidebar: must be a mapping")

    cfg = SidebarConfig()
    for key in ("collapsed", "toggle", "show_single_page", "hidden"):
        val = raw.get(key)
        if val is not None:
            if not isinstance(val, bool):
                raise ValueError(f"sidebar.{key} must be a boolean")
            setattr(cfg, key, val)
    return cfg


# Content-column widths. `l` is the historical full-dashboard width
# (Tailwind's max-w-7xl, 80rem); `s`/`m` narrow it for article-style reading.
# The rem values themselves live in dashdown.css (keyed by the data-page-width
# attribute); here we only validate the token.
LAYOUT_WIDTHS = ("s", "m", "l")


@dataclass
class LayoutConfig:
    """Optional ``layout:`` block — project-wide defaults for a page's chrome:
    content width, top header, floating theme toggle, and the side-nav behavior.

        layout:
          width: l          # s | m | l — content-column width (default l, the full
                            # dashboard width; m ≈ medium, s ≈ narrow article/blog)
          header: true      # show the top app header (brand / search / theme). false
                            # drops it site-wide — e.g. a single-page blog.
          theme_toggle: true   # when the header is hidden, show a subtle floating
                               # light/dark sun/moon toggle (default on; false opts out).
          sidebar:          # side-nav behavior (see SidebarConfig)
            hidden: false
            collapsed: false

    ``width``/``header``/``theme_toggle`` are **per-page overridable** via
    frontmatter (a page's ``width:`` / ``header:`` / ``theme_toggle:``), so a
    project can default to full-width dashboards yet mark one page as a narrow
    article, or hide the header on just the landing page. ``sidebar`` is
    project-wide only (the nav is a single app-level element). A malformed value
    fails at startup (same policy as ``auth:`` etc.)."""

    width: str = "l"
    header: bool = True
    # Only meaningful when the header is hidden (the header carries its own theme
    # toggle otherwise): render a small floating sun/moon control so a chrome-less
    # page still lets the reader flip light/dark. On by default so hiding the
    # header doesn't silently strip the theme control; set false to drop it too.
    theme_toggle: bool = True
    # Side-nav collapse / hide behavior. Nested here (not a top-level block) so all
    # of a project's chrome lives under one `layout:` key. Project-wide, not
    # per-page overridable.
    sidebar: SidebarConfig = field(default_factory=SidebarConfig)


def parse_layout_config(raw: Any) -> LayoutConfig:
    """Parse and validate the ``layout:`` block. Raises ValueError when malformed
    so the server refuses to start half-broken (same policy as ``auth:`` etc.)."""
    if raw is None:
        return LayoutConfig()
    if not isinstance(raw, dict):
        raise ValueError("layout: must be a mapping")

    cfg = LayoutConfig()
    width = raw.get("width")
    if width is not None:
        if not isinstance(width, str) or width not in LAYOUT_WIDTHS:
            raise ValueError("layout.width must be one of: s, m, l")
        cfg.width = width
    header = raw.get("header")
    if header is not None:
        if not isinstance(header, bool):
            raise ValueError("layout.header must be a boolean")
        cfg.header = header
    theme_toggle = raw.get("theme_toggle")
    if theme_toggle is not None:
        if not isinstance(theme_toggle, bool):
            raise ValueError("layout.theme_toggle must be a boolean")
        cfg.theme_toggle = theme_toggle
    # Side-nav behavior nests under layout:. parse_sidebar_config keeps its own
    # per-key validation/messages (the block key is still literally `sidebar`).
    cfg.sidebar = parse_sidebar_config(raw.get("sidebar"))
    return cfg


def resolve_page_layout(
    frontmatter: dict[str, Any], layout: LayoutConfig
) -> tuple[str, bool, bool]:
    """Resolve a page's effective ``(page_width, show_header, show_theme_toggle)``.

    Per-page frontmatter (``width:`` / ``header:`` / ``theme_toggle:``) overrides
    the project-wide ``layout:`` defaults. Frontmatter is handled **leniently** —
    an invalid ``width`` or non-boolean ``header``/``theme_toggle`` is ignored
    (falls back to the config default) rather than 500-ing the page, matching how
    the rest of frontmatter is treated."""
    width = layout.width
    fm_width = frontmatter.get("width")
    if isinstance(fm_width, str) and fm_width in LAYOUT_WIDTHS:
        width = fm_width

    show_header = layout.header
    fm_header = frontmatter.get("header")
    if isinstance(fm_header, bool):
        show_header = fm_header

    show_theme_toggle = layout.theme_toggle
    fm_theme_toggle = frontmatter.get("theme_toggle")
    if isinstance(fm_theme_toggle, bool):
        show_theme_toggle = fm_theme_toggle

    return width, show_header, show_theme_toggle


@dataclass
class PythonQueriesConfig:
    """Optional ``python_queries`` block — the policy knob for ``queries/**/*.py``
    Python queries.

        python_queries:
          enabled: false      # default true

    Python queries run **author-supplied code in-process**, the exact same trust
    boundary as a custom ``components/*.py`` (already ``exec``'d at load) — so the
    default is **on**, parity with custom components. A **managed / multi-tenant**
    host that serves *semi-trusted* project directories sets ``enabled: false`` to
    refuse arbitrary in-process code execution; ``queries/*.py`` are then skipped
    (not imported, not registered) and any reference 404s as an unknown query.
    """

    enabled: bool = True


def parse_python_queries_config(raw: Any) -> PythonQueriesConfig:
    """Parse and validate the ``python_queries:`` block. Raises ValueError when
    malformed so the server refuses to start half-broken (same policy as ``auth:``
    / ``embed:``)."""
    if raw is None:
        return PythonQueriesConfig()
    if not isinstance(raw, dict):
        raise ValueError("python_queries: must be a mapping (an 'enabled' key)")
    enabled = raw.get("enabled", True)
    if not isinstance(enabled, bool):
        raise ValueError("python_queries.enabled must be a boolean")
    return PythonQueriesConfig(enabled=enabled)


@dataclass
class AskConfig:
    """Optional ``ask:`` block — the runtime natural-language ask box / endpoint
    (``POST /_dashdown/api/ask``, ``dashdown/ask_engine.py``).

        ask:
          enabled: true        # default true (effective only when llm: is configured)
          allow_sql: false     # rung 3 opt-in — let the model emit raw SQL; default false
          max_rows: 50         # rows of result data shown to the answering model
          cache_ttl: 3600      # answer cache seconds
          log: true            # append runtime asks to .dashdown/ask_log.jsonl

    The engine routes a free-form question onto an existing data source (semantic
    model, library/python query, or — only with ``allow_sql`` — raw SQL). It is
    inert unless an ``llm:`` provider is also configured (like ``<Ask />``): the box
    then reports "no LLM provider configured" instead of erroring. ``allow_sql`` is
    the single lever that lets the model bypass the constrained ladder — off by
    default, and clearly marked in provenance when on."""

    enabled: bool = True
    allow_sql: bool = False
    max_rows: int = 50
    cache_ttl: int = 3600
    log: bool = True


def parse_ask_config(raw: Any) -> AskConfig:
    """Parse and validate the ``ask:`` block. Raises ValueError when malformed so
    the server refuses to start half-broken (same policy as ``search:`` etc.)."""
    if raw is None:
        return AskConfig()
    if not isinstance(raw, dict):
        raise ValueError("ask: must be a mapping")

    cfg = AskConfig()
    for key in ("enabled", "allow_sql", "log"):
        val = raw.get(key)
        if val is not None:
            if not isinstance(val, bool):
                raise ValueError(f"ask.{key} must be a boolean")
            setattr(cfg, key, val)
    for key in ("max_rows", "cache_ttl"):
        val = raw.get(key)
        if val is not None:
            if not isinstance(val, int) or isinstance(val, bool) or val <= 0:
                raise ValueError(f"ask.{key} must be a positive integer")
            setattr(cfg, key, val)
    return cfg


@dataclass
class ProjectConfig:
    title: str = "Dashdown"
    auth: AuthConfig = field(default_factory=AuthConfig)
    branding: BrandingConfig = field(default_factory=BrandingConfig)
    format: FormatConfig = field(default_factory=FormatConfig)
    llm: LLMConfig = field(default_factory=LLMConfig)
    ask: AskConfig = field(default_factory=AskConfig)
    embed: EmbedConfig = field(default_factory=EmbedConfig)
    global_date: GlobalDateFilterConfig = field(default_factory=GlobalDateFilterConfig)
    filters: FiltersConfig = field(default_factory=FiltersConfig)
    search: SearchConfig = field(default_factory=SearchConfig)
    layout: LayoutConfig = field(default_factory=LayoutConfig)
    python_queries: PythonQueriesConfig = field(default_factory=PythonQueriesConfig)


@dataclass
class Project:
    root: Path
    config: ProjectConfig
    connectors: dict[str, Connector] = field(default_factory=dict)
    # The source name a query with no explicit `connector=` runs on — computed
    # once at load by `default_connector_name` (see data/registry.py). None
    # when there is no unambiguous default (several unflagged sources).
    default_connector: str | None = None
    # Shared query library: name -> QuerySpec parsed from queries/**/*.{sql,dax}
    # at load time (see dashdown/query_library.py). Pages reference these by name
    # (`data={finance.mrr}`); also the catalogue for introspection / a generated
    # query reference.
    queries: dict[str, QuerySpec] = field(default_factory=dict)
    # Python queries: name -> PythonQuerySpec parsed from queries/**/*.py at load
    # time (see dashdown/python_query.py). Referenced by name from a page exactly
    # like a SQL/DAX library query; the data API / poller / build run the
    # decorated function instead of `connector.query(sql)`. Empty when
    # `python_queries.enabled` is false.
    python_queries: dict[str, PythonQuerySpec] = field(default_factory=dict)
    # Semantic models (name -> SemanticModel) parsed from semantic/**/*.py at load
    # time (see dashdown/semantic.py). A page component references a metric
    # directly (`metric={model.metric} by={model.dim}`); the render pipeline
    # compiles it into a synthetic Python query. Empty when
    # `python_queries.enabled` is false (same trust boundary).
    semantic_models: dict[str, Any] = field(default_factory=dict)
    # Lazily created from config.llm on first <Ask /> request (the provider
    # SDK import is deferred). Tests inject a fake adapter here directly.
    llm_adapter: LLMAdapter | None = None
    # Colocated frontend assets for custom components: relative POSIX paths
    # under components/ for every non-underscore .js / .css (e.g.
    # "Timeline/Timeline.js"). Discovered at load time, served at
    # /_dashdown/components/<rel>, and injected into every page so a custom
    # component can ship its hydration JS/CSS next to its .py. See
    # `_discover_component_assets`.
    component_js: list[str] = field(default_factory=list)
    component_css: list[str] = field(default_factory=list)

    def get_llm_adapter(self) -> LLMAdapter:
        if self.llm_adapter is None:
            self.llm_adapter = create_adapter(self.config.llm)
        return self.llm_adapter

    @property
    def pages_dir(self) -> Path:
        return self.root / "pages"

    @property
    def components_dir(self) -> Path:
        return self.root / "components"

    @property
    def assets_dir(self) -> Path:
        return self.root / "assets"

    @property
    def queries_dir(self) -> Path:
        return self.root / "queries"

    def page_path(self, url_path: str) -> tuple[Path | None, dict[str, str]]:
        """Map a URL path to a markdown file under pages/.

        Supports dynamic segments: a file named ``[slug].md`` or a directory
        named ``[slug]`` matches any single path segment and captures it as a
        parameter.

        Returns ``(md_file_path, params)`` or ``(None, {})`` if no match.
        """
        rel = url_path.strip("/")
        parts = rel.split("/") if rel else []
        result = self._match_parts(self.pages_dir, parts, {})
        if result is not None:
            return result
        return None, {}

    def _match_parts(
        self, base: Path, parts: list[str], params: dict[str, str]
    ) -> tuple[Path, dict[str, str]] | None:
        """Recursively match URL parts against the filesystem, allowing
        ``[param]`` directories and ``[param].md`` files."""
        pages_root = self.pages_dir.resolve()

        if not parts:
            # Terminal: look for index.md.
            idx = base / "index.md"
            try:
                idx.resolve().relative_to(pages_root)
            except (ValueError, OSError):
                return None
            if idx.is_file():
                return idx, dict(params)
            return None

        segment, rest = parts[0], parts[1:]

        # 1) Exact directory match.
        exact_dir = base / segment
        if exact_dir.is_dir():
            r = self._match_parts(exact_dir, rest, params)
            if r is not None:
                return r

        # 2) Exact file match (only when no more parts).
        if not rest:
            exact_file = base / f"{segment}.md"
            try:
                exact_file.resolve().relative_to(pages_root)
            except (ValueError, OSError):
                pass
            else:
                if exact_file.is_file():
                    return exact_file, dict(params)

        # 3) Dynamic directory [param].
        if base.is_dir():
            for child in sorted(base.iterdir()):
                if child.is_dir():
                    m = _SLUG_RE.match(child.name)
                    if m:
                        new_params = {**params, m.group(1): segment}
                        r = self._match_parts(child, rest, new_params)
                        if r is not None:
                            return r

        # 4) Dynamic file [param].md (only when no more parts).
        if not rest and base.is_dir():
            for child in sorted(base.iterdir()):
                if child.is_file() and child.suffix == ".md":
                    m = _SLUG_RE.match(child.stem)
                    if m:
                        try:
                            child.resolve().relative_to(pages_root)
                        except (ValueError, OSError):
                            continue
                        return child, {**params, m.group(1): segment}

        return None

    def list_pages(self) -> list[str]:
        if not self.pages_dir.is_dir():
            return []
        out = []
        for p in sorted(self.pages_dir.rglob("*.md")):
            rel = p.relative_to(self.pages_dir).with_suffix("")
            url = "/" + str(rel).replace("\\", "/")
            if url.endswith("/index"):
                url = url[: -len("index")] or "/"
            out.append(url)
        return out

    def navigable_page_count(self) -> int:
        """Number of pages that appear in the sidebar nav — i.e. excluding dynamic
        ``[slug]`` pages, which are omitted from the tree. Used to decide whether
        the nav is worth showing at all (a single-page project hides it unless
        ``sidebar.show_single_page`` is set)."""
        if not self.pages_dir.is_dir():
            return 0
        count = 0
        for p in self.pages_dir.rglob("*.md"):
            rel = p.relative_to(self.pages_dir).with_suffix("")
            parts = str(rel).replace("\\", "/").split("/")
            if any(_SLUG_RE.match(part) for part in parts):
                continue
            count += 1
        return count

    def show_sidebar(self) -> bool:
        """Whether the sidebar nav (and its menu buttons) should render:
        ``layout.sidebar.hidden`` drops it outright; otherwise a single-page
        project omits it unless ``layout.sidebar.show_single_page`` forces it on.
        The single decision both the server and the static build feed to the
        template."""
        sb = self.config.layout.sidebar
        if sb.hidden:
            return False
        return sb.show_single_page or self.navigable_page_count() > 1

    def nav_tree(self) -> list[dict[str, Any]]:
        """Build a hierarchical navigation tree from pages + frontmatter.

        Each node: {url, label, order, children: [...]}.
        Frontmatter keys: sidebar_label, sidebar_position, icon.
        """
        if not self.pages_dir.is_dir():
            return []
        return _build_nav_tree(self.pages_dir)

    def close(self) -> None:
        for c in self.connectors.values():
            try:
                c.close()
            except Exception:
                pass


def load_project(root: Path) -> Project:
    root = root.resolve()
    if not root.is_dir():
        raise FileNotFoundError(f"Project directory not found: {root}")

    cfg_path = root / "dashdown.yaml"
    cfg = ProjectConfig()
    if cfg_path.exists():
        raw = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
        # A broken `llm:` block (unset ${API_KEY} env var, unknown provider, …)
        # must not stop `serve`/`build` — AI commentary is a convenience, not a
        # guard. Degrade to disabled and carry the reason so every <Ask /> card
        # can explain why commentary is off. (`auth:` below stays fail-hard.)
        try:
            llm_config = parse_llm_config(raw.get("llm"))
        except ValueError as e:
            logging.getLogger(__name__).warning(
                "llm: %s — AI commentary disabled", e
            )
            llm_config = LLMConfig(error=str(e))
        cfg = ProjectConfig(
            title=raw.get("title", cfg.title),
            auth=parse_auth_config(raw.get("auth")),
            branding=parse_branding_config(raw.get("branding")),
            format=parse_format_config(raw.get("format")),
            llm=llm_config,
            ask=parse_ask_config(raw.get("ask")),
            embed=parse_embed_config(raw.get("embed")),
            global_date=parse_global_filters_config(raw.get("global_filters")),
            filters=parse_filters_config(raw.get("filters")),
            search=parse_search_config(raw.get("search")),
            layout=parse_layout_config(raw.get("layout")),
            python_queries=parse_python_queries_config(raw.get("python_queries")),
        )
        # Auth + embedding are enterprise features: parsed above so a broken
        # block still fails with its specific error, but *activating* either
        # needs the unlock (dashdown/enterprise.py). Gated on the parsed
        # result, so inert blocks (`type: none`, `enabled: false`) stay legal.
        if cfg.auth.enabled:
            require_enterprise("auth")
        if cfg.embed.enabled:
            require_enterprise("embed")

    # Auto-import any user component / connector modules so their @register_*
    # runs *before* sources.yaml is resolved — a custom connector defined in a
    # project's components/ must be registered by the time load_connectors looks
    # its type up (otherwise its type is "unknown" at load time). Recurses, so a
    # component colocated in its own folder (components/Timeline/Timeline.py) is
    # picked up alongside the flat components/*.py layout.
    _import_user_modules(root / "components")

    # Colocated frontend assets: a custom component's hydration JS/CSS living
    # next to its .py (components/Timeline/Timeline.js). Discovered once here and
    # injected into every page (see server.py / build.py / page.html).
    component_js, component_css = _discover_component_assets(root / "components")

    connectors = load_connectors(root / "sources.yaml", root)
    # The source a query with no explicit `connector=` runs on (sources.yaml's
    # top-level `default:` key → sole source; None when several sources and no
    # `default:` make it ambiguous). Library/python specs that parsed without a connector are
    # resolved against it right below, so everything registered into the global
    # caches carries a concrete name; an ambiguous unqualified query fails at
    # startup (mirroring render_page's per-page check).
    default_connector = default_connector_name(connectors)

    def _resolve_connector(spec) -> None:
        if not spec.connector:
            if default_connector is None and len(connectors) > 1:
                raise no_default_error(f"library query '{spec.name}'")
            spec.connector = default_connector or ""

    # Shared query library: parse queries/**/*.{sql,dax} and register each into
    # the global query-def cache, so the data API and WS stream endpoint resolve
    # them with zero endpoint changes (both already look up by name+connector).
    # A duplicate/derived-name collision raises here — fail-at-startup, like a
    # malformed auth: block. Registration evicts any stale library keys from a
    # prior load so a renamed/deleted file leaves no ghost (dev-server reload).
    # Compile dbt-style ref() composition into inline CTEs *before* registering, so
    # substitution still runs once over the composed SQL. DAX connectors can't
    # compose (CTEs are SQL), so they're excluded from ref().
    dax_connectors = frozenset(
        name for name, c in connectors.items() if type(c).__name__ == "DAXConnector"
    )
    queries = load_queries(root / "queries")
    for spec in queries.values():
        _resolve_connector(spec)
    queries = compose_library_queries(queries, dax_connectors)
    register_library_queries(queries)

    # Python queries (queries/**/*.py). Same loader machinery (name =
    # dotted path, traversal guard), with a uniqueness check against the SQL/DAX
    # names already loaded (a `foo.sql` and `foo.py` collide → fail-at-startup).
    # Gated by `python_queries.enabled` (default on, == custom-component trust
    # boundary): a managed/multi-tenant host disables in-process code execution by
    # setting it false, and the .py files are then skipped entirely. Registration
    # evicts stale Python keys from a prior load (dev-server reload), like the SQL
    # library.
    if cfg.python_queries.enabled:
        python_queries = load_python_queries(
            root / "queries", reserved_names=set(queries)
        )
        for py_spec in python_queries.values():
            _resolve_connector(py_spec)
    else:
        py_dir = root / "queries"
        if py_dir.is_dir() and any(py_dir.rglob("*.py")):
            logging.getLogger(__name__).info(
                "python_queries.enabled is false — skipping queries/*.py in %s", root
            )
        python_queries = {}
    register_python_library_queries(python_queries)

    # Semantic models (semantic/**/*.yml). BSL/Ibis models loaded at project init,
    # bridged to the project's connectors for pushdown. Gated by
    # the same `python_queries.enabled` switch (in-process code execution trust
    # boundary). Compilation of a metric reference into a synthetic query happens
    # at render time (per page).
    if cfg.python_queries.enabled:
        semantic_models = load_semantic_models(root / "semantic", connectors)
    else:
        sem_dir = root / "semantic"
        if sem_dir.is_dir() and (any(sem_dir.rglob("*.yml")) or any(sem_dir.rglob("*.yaml"))):
            logging.getLogger(__name__).info(
                "python_queries.enabled is false — skipping semantic/*.yml in %s", root
            )
        semantic_models = {}

    return Project(
        root=root,
        config=cfg,
        connectors=connectors,
        default_connector=default_connector,
        queries=queries,
        python_queries=python_queries,
        semantic_models=semantic_models,
        component_js=component_js,
        component_css=component_css,
    )


def _import_user_modules(directory: Path) -> None:
    """Import every ``*.py`` under ``directory`` (recursively) so its
    ``@register_component`` / ``@register_connector`` runs at load time.

    Recurses so a component colocated in its own folder
    (``components/Timeline/Timeline.py``) is loaded just like the flat
    ``components/callout.py`` layout. ``_``-prefixed files are skipped — the
    convention for shared helpers / ``__init__.py`` that shouldn't auto-load.
    Each file is imported standalone (no package context), so use absolute
    imports from ``dashdown`` or installed packages, not relative ones. The
    module name is keyed on the path under ``directory`` so two same-named files
    in different folders don't collide.
    """
    if not directory.is_dir():
        return
    for py in sorted(directory.rglob("*.py")):
        if py.name.startswith("_") or "__pycache__" in py.parts:
            continue
        rel = py.relative_to(directory).with_suffix("")
        mod_name = "_dashdown_user_" + "_".join(rel.parts)
        spec = importlib.util.spec_from_file_location(mod_name, py)
        if spec is None or spec.loader is None:
            continue
        module = importlib.util.module_from_spec(spec)
        sys.modules[mod_name] = module
        try:
            spec.loader.exec_module(module)
        except Exception as e:  # noqa: BLE001
            logging.getLogger(__name__).error(
                "Failed to load user module %s: %s", py, e
            )


def _discover_component_assets(directory: Path) -> tuple[list[str], list[str]]:
    """Find colocated frontend assets for custom components under ``directory``.

    Returns ``(js_paths, css_paths)`` — relative POSIX paths (e.g.
    ``"Timeline/Timeline.js"``) for every ``.js`` / ``.css`` file, recursively,
    excluding ``_``-prefixed files (a shared helper module imported by another
    component's JS is private — it still loads via that ``import``, it just isn't
    auto-injected as its own ``<script>``). Sorted for deterministic injection
    order. These are served at ``/_dashdown/components/<rel>`` and emitted into
    every page by the template.
    """
    if not directory.is_dir():
        return [], []
    js: list[str] = []
    css: list[str] = []
    for f in directory.rglob("*"):
        if not f.is_file() or f.name.startswith("_") or "__pycache__" in f.parts:
            continue
        suffix = f.suffix.lower()
        if suffix == ".js":
            js.append(f.relative_to(directory).as_posix())
        elif suffix == ".css":
            css.append(f.relative_to(directory).as_posix())
    return sorted(js), sorted(css)


def _build_nav_tree(pages_dir: Path) -> list[dict[str, Any]]:
    """Walk pages_dir recursively, read frontmatter from each .md, and build
    a nested nav structure sorted by ``sidebar_position`` then label."""

    @dataclass
    class _Node:
        url: str = ""
        label: str = ""
        order: int = 100
        icon: str = ""
        children: list["_Node"] = field(default_factory=list)
        is_page: bool = False

    # Collect all pages keyed by their URL parts tuple.
    pages: dict[tuple[str, ...], _Node] = {}

    for md_file in sorted(pages_dir.rglob("*.md")):
        rel = md_file.relative_to(pages_dir).with_suffix("")
        parts = tuple(p for p in str(rel).replace("\\", "/").split("/"))

        # Skip dynamic slug files/dirs — they don't belong in the sidebar.
        if any(_SLUG_RE.match(p) for p in parts):
            continue

        fm = parse_frontmatter(md_file.read_text(encoding="utf-8"))
        is_index = parts[-1] == "index"

        if is_index and len(parts) == 1:
            url = "/"
            default_label = "Home"
        elif is_index:
            url = "/" + "/".join(parts[:-1])
            default_label = parts[-2].replace("_", " ").replace("-", " ").title()
        else:
            url = "/" + "/".join(parts)
            default_label = parts[-1].replace("_", " ").replace("-", " ").title()

        node = _Node(
            url=url,
            label=str(fm.get("sidebar_label", fm.get("title", default_label))),
            order=int(fm.get("sidebar_position", 100)),
            icon=str(fm.get("icon", "")),
            is_page=True,
        )

        if is_index:
            key = parts[:-1] if len(parts) > 1 else ()
        else:
            key = parts

        pages[key] = node

    # Build tree. For each page with N parts, its parent is the page with N-1 parts.
    root_children: list[_Node] = []

    # Ensure intermediate group nodes exist.
    all_keys = sorted(pages.keys(), key=lambda k: len(k))
    for key in all_keys:
        if len(key) <= 1:
            continue
        parent_key = key[:-1]
        if parent_key not in pages:
            # Create a virtual group node (directory without index.md).
            pages[parent_key] = _Node(
                url="/" + "/".join(parent_key),
                label=parent_key[-1].replace("_", " ").replace("-", " ").title(),
                order=100,
                is_page=False,
            )

    for key in sorted(pages.keys(), key=lambda k: len(k)):
        node = pages[key]
        if len(key) <= 1 and key != ():
            root_children.append(node)
        elif key == ():
            # Home – insert at front.
            root_children.insert(0, node)
        else:
            parent_key = key[:-1]
            parent = pages.get(parent_key)
            if parent is not None:
                parent.children.append(node)
            else:
                root_children.append(node)

    def sort_nodes(nodes: list[_Node]) -> list[_Node]:
        for n in nodes:
            if n.children:
                n.children = sort_nodes(n.children)
        return sorted(nodes, key=lambda n: (n.order, n.label))

    root_children = sort_nodes(root_children)

    def to_dict(n: _Node) -> dict[str, Any]:
        d: dict[str, Any] = {"url": n.url, "label": n.label}
        if n.icon:
            d["icon"] = n.icon
        if n.children:
            d["children"] = [to_dict(c) for c in n.children]
        if not n.is_page:
            d["group"] = True
        return d

    return [to_dict(n) for n in root_children]


def build_breadcrumbs(
    current: str,
    nav_tree: list[dict[str, Any]],
    page_title: str = "",
) -> list[dict[str, str]]:
    """Build breadcrumb trail for the current URL.

    Returns a list of {url, label} dicts from root to current page.
    """
    crumbs: list[dict[str, str]] = [{"url": "/", "label": "Home"}]
    if current == "/":
        return crumbs

    # Build a flat lookup: url -> label from the nav tree.
    label_map: dict[str, str] = {}
    _flatten_nav(nav_tree, label_map)

    parts = [p for p in current.strip("/").split("/") if p]
    for i, part in enumerate(parts):
        url = "/" + "/".join(parts[: i + 1])
        label = label_map.get(url, part.replace("_", " ").replace("-", " ").title())
        crumbs.append({"url": url, "label": label})

    # Override the last breadcrumb label with the page's own title if available.
    if page_title and crumbs:
        crumbs[-1]["label"] = page_title

    return crumbs


def _flatten_nav(nodes: list[dict[str, Any]], out: dict[str, str]) -> None:
    for n in nodes:
        out[n["url"]] = n["label"]
        if "children" in n:
            _flatten_nav(n["children"], out)
