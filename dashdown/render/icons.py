"""Bundled named SVG icons for sidebar navigation.

A small, monochrome subset of Heroicons/Lucide outline icons. A page's
``icon:`` frontmatter may name one of these (e.g. ``icon: home``) to render a
crisp `currentColor` SVG that follows the theme and active state, instead of an
OS-dependent emoji. Any value that isn't a known name falls back to being
rendered verbatim (emoji or arbitrary text), so existing dashboards are
unchanged.

Icons are stored as their inner SVG markup (paths/circles) and wrapped in a
consistent stroke-based `<svg>` by :func:`nav_icon_svg`. Keep them outline-style
(``fill="none"``, ``stroke="currentColor"``) so they read as a coherent set.
"""

from __future__ import annotations

from markupsafe import Markup

# name -> inner SVG markup. Outline style, viewBox 0 0 24 24, stroke-width 2.
_ICON_PATHS: dict[str, str] = {
    "home": (
        '<path stroke-linecap="round" stroke-linejoin="round" d="M3 12l9-9 9 9M5 10v10a1 1 0 '
        "001 1h3m10-11v10a1 1 0 01-1 1h-3m-6 0a1 1 0 001-1v-4a1 1 0 011-1h2a1 1 0 011 1v4a1 1 0 "
        '001 1m-6 0h6"/>'
    ),
    "users": (
        '<path stroke-linecap="round" stroke-linejoin="round" d="M17 20h5v-2a3 3 0 00-5.356-1.857M17 '
        "20H7m10 0v-2c0-.656-.126-1.283-.356-1.857M7 20H2v-2a3 3 0 015.356-1.857M7 20v-2c0-.656.126-1.283"
        '.356-1.857m0 0a5.002 5.002 0 019.288 0M15 7a3 3 0 11-6 0 3 3 0 016 0z"/>'
    ),
    "user": (
        '<path stroke-linecap="round" stroke-linejoin="round" d="M16 7a4 4 0 11-8 0 4 4 0 018 '
        '0zM12 14a7 7 0 00-7 7h14a7 7 0 00-7-7z"/>'
    ),
    "calendar": (
        '<path stroke-linecap="round" stroke-linejoin="round" d="M8 7V3m8 4V3m-9 8h10M5 21h14a2 2 0 '
        '002-2V7a2 2 0 00-2-2H5a2 2 0 00-2 2v12a2 2 0 002 2z"/>'
    ),
    "clock": (
        '<path stroke-linecap="round" stroke-linejoin="round" d="M12 8v4l3 3m6-3a9 9 0 11-18 0 9 9 0 '
        '0118 0z"/>'
    ),
    "chart-bar": (
        '<path stroke-linecap="round" stroke-linejoin="round" d="M9 19v-6a2 2 0 00-2-2H5a2 2 0 00-2 '
        "2v6a2 2 0 002 2h2a2 2 0 002-2zm0 0V9a2 2 0 012-2h2a2 2 0 012 2v10m-6 0a2 2 0 002 2h2a2 2 0 "
        '002-2m0 0V5a2 2 0 012-2h2a2 2 0 012 2v14a2 2 0 01-2 2h-2a2 2 0 01-2-2z"/>'
    ),
    "chart-pie": (
        '<path stroke-linecap="round" stroke-linejoin="round" d="M11 3.055A9.001 9.001 0 1020.945 '
        '13H11V3.055z"/><path stroke-linecap="round" stroke-linejoin="round" d="M20.488 9H15V3.512A9.025 '
        '9.025 0 0120.488 9z"/>'
    ),
    "trending-up": (
        '<path stroke-linecap="round" stroke-linejoin="round" d="M3 17l6-6 4 4 8-8m0 0h-5m5 0v5"/>'
    ),
    "dollar": (
        '<path stroke-linecap="round" stroke-linejoin="round" d="M12 8c-1.657 0-3 .895-3 2s1.343 2 3 '
        "2 3 .895 3 2-1.343 2-3 2m0-8c1.11 0 2.08.402 2.599 1M12 8V7m0 1v8m0 0v1m0-1c-1.11 0-2.08-.402-2.599"
        '-1M21 12a9 9 0 11-18 0 9 9 0 0118 0z"/>'
    ),
    "clipboard": (
        '<path stroke-linecap="round" stroke-linejoin="round" d="M9 5H7a2 2 0 00-2 2v12a2 2 0 002 '
        "2h10a2 2 0 002-2V7a2 2 0 00-2-2h-2M9 5a2 2 0 002 2h2a2 2 0 002-2M9 5a2 2 0 012-2h2a2 2 0 012 "
        '2m-3 7h3m-3 4h3m-6-4h.01M9 16h.01"/>'
    ),
    "check": (
        '<path stroke-linecap="round" stroke-linejoin="round" d="M5 13l4 4L19 7"/>'
    ),
    "check-circle": (
        '<path stroke-linecap="round" stroke-linejoin="round" d="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 '
        '0118 0z"/>'
    ),
    "wrench": (
        '<path stroke-linecap="round" stroke-linejoin="round" d="M14.7 6.3a1 1 0 000 1.4l1.6 1.6a1 1 0 '
        "001.4 0l3.77-3.77a6 6 0 01-7.94 7.94l-6.91 6.91a2.12 2.12 0 01-3-3l6.91-6.91a6 6 0 017.94-7.94"
        'l-3.76 3.76z"/>'
    ),
    "settings": (
        '<path stroke-linecap="round" stroke-linejoin="round" d="M10.325 4.317c.426-1.756 2.924-1.756 '
        "3.35 0a1.724 1.724 0 002.573 1.066c1.543-.94 3.31.826 2.37 2.37a1.724 1.724 0 001.065 2.572c1.756"
        ".426 1.756 2.924 0 3.35a1.724 1.724 0 00-1.066 2.573c.94 1.543-.826 3.31-2.37 2.37a1.724 1.724 0 "
        "00-2.572 1.065c-.426 1.756-2.924 1.756-3.35 0a1.724 1.724 0 00-2.573-1.066c-1.543.94-3.31-.826-2.37"
        "-2.37a1.724 1.724 0 00-1.065-2.572c-1.756-.426-1.756-2.924 0-3.35a1.724 1.724 0 001.066-2.573c-.94"
        '-1.543.826-3.31 2.37-2.37.996.608 2.296.07 2.572-1.065z"/><path stroke-linecap="round" '
        'stroke-linejoin="round" d="M15 12a3 3 0 11-6 0 3 3 0 016 0z"/>'
    ),
    "package": (
        '<path stroke-linecap="round" stroke-linejoin="round" d="M20 7l-8-4-8 4m16 0l-8 4m8-4v10l-8 '
        '4m0-10L4 7m8 4v10M4 7v10l8 4"/>'
    ),
    "map": (
        '<path stroke-linecap="round" stroke-linejoin="round" d="M9 20l-5.447-2.724A1 1 0 013 16.382V5.618a1 '
        "1 0 011.447-.894L9 7m0 13l6-3m-6 3V7m6 10l4.553 2.276A1 1 0 0021 18.382V7.618a1 1 0 00-.553-.894L15 "
        '4m0 13V4m0 0L9 7"/>'
    ),
    "document": (
        '<path stroke-linecap="round" stroke-linejoin="round" d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 '
        '0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z"/>'
    ),
    "folder": (
        '<path stroke-linecap="round" stroke-linejoin="round" d="M3 7v10a2 2 0 002 2h14a2 2 0 002-2V9a2 2 '
        '0 00-2-2h-6l-2-2H5a2 2 0 00-2 2z"/>'
    ),
    "lock": (
        '<path stroke-linecap="round" stroke-linejoin="round" d="M12 15v2m-6 4h12a2 2 0 002-2v-6a2 2 0 '
        '00-2-2H6a2 2 0 00-2 2v6a2 2 0 002 2zm10-10V7a4 4 0 00-8 0v4h8z"/>'
    ),
    "target": (
        '<circle cx="12" cy="12" r="9"/><circle cx="12" cy="12" r="5"/><circle cx="12" cy="12" r="1.5"/>'
    ),
    "table": (
        '<path stroke-linecap="round" stroke-linejoin="round" d="M12 3v18M3 9h18M3 15h18M5 3h14a2 2 0 '
        '012 2v14a2 2 0 01-2 2H5a2 2 0 01-2-2V5a2 2 0 012-2z"/>'
    ),
    "sparkles": (
        '<path stroke-linecap="round" stroke-linejoin="round" d="M5 3v4M3 5h4M6 17v4m-2-2h4m5-16l2.286 '
        '6.857L21 12l-5.714 2.143L13 21l-2.286-6.857L5 12l5.714-2.143L13 3z"/>'
    ),
    "tooth": (
        '<path stroke-linecap="round" stroke-linejoin="round" d="M12 5.5c-1.5-1.5-3-2-4.5-2C5 3.5 3 5.5 3 '
        "9c0 4 1.5 7 2.5 9.5.5 1.25 1 2 1.75 2 1 0 1.25-1 1.75-2.5.5-1.5 1-2.5 3-2.5s2.5 1 3 2.5c.5 1.5.75 "
        '2.5 1.75 2.5.75 0 1.25-.75 1.75-2C19.5 16 21 13 21 9c0-3.5-2-5.5-4.5-5.5-1.5 0-3 .5-4.5 2z"/>'
    ),
}

# Friendly aliases -> canonical name above.
_ICON_ALIASES: dict[str, str] = {
    "house": "home",
    "people": "users",
    "team": "users",
    "group": "users",
    "person": "user",
    "profile": "user",
    "date": "calendar",
    "time": "clock",
    "bar-chart": "chart-bar",
    "barchart": "chart-bar",
    "chart": "chart-bar",
    "analytics": "chart-bar",
    "pie": "chart-pie",
    "pie-chart": "chart-pie",
    "donut": "chart-pie",
    "trend": "trending-up",
    "trending": "trending-up",
    "currency-dollar": "dollar",
    "money": "dollar",
    "revenue": "dollar",
    "list": "clipboard",
    "clipboard-list": "clipboard",
    "checkmark": "check",
    "tick": "check",
    "success": "check-circle",
    "tool": "wrench",
    "tools": "wrench",
    "cog": "settings",
    "gear": "settings",
    "config": "settings",
    "box": "package",
    "product": "package",
    "products": "package",
    "file": "document",
    "doc": "document",
    "report": "document",
    "directory": "folder",
    "secure": "lock",
    "auth": "lock",
    "goal": "target",
    "kpi": "target",
    "kpis": "target",
    "grid": "table",
    "star": "sparkles",
    "new": "sparkles",
    "dental": "tooth",
}


def _resolve(name: str) -> str | None:
    """Return the canonical icon key for a frontmatter value, or None."""
    key = name.strip().lower().replace("_", "-")
    if key in _ICON_PATHS:
        return key
    return _ICON_ALIASES.get(key)


def nav_icon_svg(value: str, *, cls: str = "dashdown-nav-svg") -> Markup | None:
    """Render a named icon as an inline `<svg>`, or None if not a known name.

    The returned markup is trusted (assembled from a fixed internal table, never
    user input beyond the looked-up name) so callers emit it without escaping.
    A non-matching value returns None and should be rendered verbatim by the
    caller (emoji / arbitrary text fallback).
    """
    if not value:
        return None
    key = _resolve(value)
    if key is None:
        return None
    inner = _ICON_PATHS[key]
    return Markup(
        f'<svg class="{cls}" viewBox="0 0 24 24" fill="none" stroke="currentColor" '
        f'stroke-width="2" aria-hidden="true">{inner}</svg>'
    )


def is_named_icon(value: str) -> bool:
    """True if ``value`` names a bundled icon (after alias resolution)."""
    return bool(value) and _resolve(value) is not None
