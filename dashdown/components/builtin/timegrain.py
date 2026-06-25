"""TimeGrain — a filter control that writes a canonical time-grain token.

Sugar over ``<Dropdown options="day,week,month,…">``: it renders the same filter
pill but is grain-aware — it labels each option nicely (``Month``, not ``month``),
validates the list against the canonical :data:`~dashdown.semantic.GRAIN_TOKENS`,
and seeds a real ``default`` grain (a plain Dropdown can't). The value it writes
into ``$store.filters[name]`` is exactly what a chart's ``grain={name}`` reads at
fetch time, so it joins the existing filter re-fetch path with no new plumbing:

    <TimeGrain name="trendGrain" default="month" />
    <LineChart metric={sales.revenue} by={sales.order_date} grain={trendGrain} />
"""
from __future__ import annotations

from dashdown.components.base import Component, RenderContext, register_component
from dashdown.components.builtin._util import (
    attr_bool,
    attr_str,
    esc,
    filter_bar_marker,
    new_id,
    safe_json,
)

# Pretty labels for the canonical tokens; any token not listed falls back to
# ``.title()`` (keeps this in step with GRAIN_TOKENS without a second source).
_GRAIN_LABELS = {
    "second": "Second",
    "minute": "Minute",
    "hour": "Hour",
    "day": "Day",
    "week": "Week",
    "month": "Month",
    "quarter": "Quarter",
    "year": "Year",
}

_DEFAULT_GRAINS = "day,week,month,quarter,year"


@register_component("TimeGrain")
class TimeGrain(Component):
    """Time-grain picker — writes a `day`/`week`/`month`/… token for `grain={name}`.

    Usage: ``<TimeGrain name="trendGrain" default="month" />`` paired with a chart's
    ``grain={trendGrain}``. ``grains=`` overrides the offered list (default
    ``day,week,month,quarter,year``); ``native`` adds a "Native" (ungrouped) choice;
    ``default=`` seeds the first-load selection (else `month`, or the first grain).
    Like every filter it takes ``bar`` to relocate into the top filter bar.
    """

    is_filter = True

    def render(self, attrs, ctx: RenderContext, inner: str | None = None) -> str:
        from dashdown.semantic import GRAIN_TOKENS

        name = attr_str(attrs, "name")
        if not name:
            raise ValueError("TimeGrain requires a `name` attribute")
        label = attr_str(attrs, "label", "Grain")
        raw = attr_str(attrs, "grains") or _DEFAULT_GRAINS
        grains = [g.strip().lower() for g in str(raw).split(",") if g.strip()]
        unknown = [g for g in grains if g not in GRAIN_TOKENS]
        if unknown:
            raise ValueError(
                f"TimeGrain: unknown grain(s) {unknown}; valid tokens are "
                f"{list(GRAIN_TOKENS)}"
            )
        if not grains:
            raise ValueError("TimeGrain `grains=` resolved to an empty list")

        native = attr_bool(attrs, "native", False)
        default = attr_str(attrs, "default")
        if default:
            default = default.lower()
            if default not in grains:
                raise ValueError(
                    f"TimeGrain default={default!r} is not one of grains {grains}"
                )
        elif native:
            default = ""  # start ungrouped
        else:
            # No explicit default and no "native" escape → pick a sensible grain so
            # the displayed selection matches the chart's actual grouping on load.
            default = "month" if "month" in grains else grains[0]

        url_sync = attrs.get("url_sync", True)
        filter_bar_attr = filter_bar_marker(attrs, ctx)
        cid = new_id("dashdown-timegrain")
        config = {
            "name": name,
            "default": default,
            "grains": grains,
            "native": native,
            "url_sync": url_sync,
        }

        opts = '<option value="">Native</option>' if native else ""
        opts += "".join(
            f'<option value="{esc(g)}">{esc(_GRAIN_LABELS.get(g, g.title()))}</option>'
            for g in grains
        )

        # Same pill chrome + `x-model` store binding as the explicit-options
        # Dropdown, so chips/placement/URL-sync all behave identically; timegrain.js
        # only adds the first-load default seed + URL mirror (the toggle.js role).
        return (
            f'<div class="dashdown-dropdown dashdown-filter-pill" id="{cid}" '
            f'data-async-component="timegrain" '
            f'data-config="{esc(safe_json(config))}" '
            f'data-filter-name="{esc(name)}" '
            f'data-url-sync="{str(url_sync).lower()}" '
            f"{filter_bar_attr}"
            f">"
            f'<span class="dashdown-filter-pill-label">{esc(label)}'
            f'<span class="dashdown-filter-pill-colon">:</span></span>'
            f'<select class="select select-sm" aria-label="{esc(label)}" '
            f"x-model=\"$store.filters['{esc(name)}']\""
            f">{opts}</select>"
            f"</div>"
        )
