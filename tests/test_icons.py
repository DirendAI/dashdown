"""Tests for bundled named nav icons (render/icons.py)."""

from __future__ import annotations

from dashdown.render.icons import _ICON_PATHS, is_named_icon, nav_icon_svg


class TestNavIconSvg:
    def test_known_name_returns_svg(self):
        svg = nav_icon_svg("home")
        assert svg is not None
        assert str(svg).startswith("<svg")
        assert 'stroke="currentColor"' in str(svg)
        assert 'fill="none"' in str(svg)

    def test_unknown_name_returns_none(self):
        assert nav_icon_svg("🦷") is None
        assert nav_icon_svg("not-a-real-icon") is None

    def test_empty_returns_none(self):
        assert nav_icon_svg("") is None

    def test_case_and_underscore_insensitive(self):
        assert nav_icon_svg("CHART_BAR") is not None
        assert nav_icon_svg("Chart-Bar") is not None

    def test_aliases_resolve(self):
        # alias -> canonical produce the same inner markup
        assert str(nav_icon_svg("dental")) == str(nav_icon_svg("tooth"))
        assert str(nav_icon_svg("team")) == str(nav_icon_svg("users"))
        assert str(nav_icon_svg("cog")) == str(nav_icon_svg("settings"))

    def test_aria_hidden_present(self):
        assert 'aria-hidden="true"' in str(nav_icon_svg("user"))

    def test_custom_class(self):
        assert 'class="my-cls"' in str(nav_icon_svg("home", cls="my-cls"))

    def test_is_named_icon(self):
        assert is_named_icon("home") is True
        assert is_named_icon("dental") is True  # alias
        assert is_named_icon("🦷") is False
        assert is_named_icon("") is False

    def test_all_paths_are_outline_markup(self):
        # every bundled icon is path/circle inner markup, no raw <script>/<svg>
        for name, inner in _ICON_PATHS.items():
            assert "<script" not in inner, name
            assert inner.strip().startswith("<"), name
