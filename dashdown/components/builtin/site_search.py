from __future__ import annotations

from dashdown.components.base import Component, RenderContext, register_component
from dashdown.components.builtin._util import attr_str, esc, new_id, safe_json


@register_component("SiteSearch")
class SiteSearch(Component):
    """Full-text search across every page of the project.

    Renders an input + a results dropdown; ``site_search.js`` fetches the search
    index once (``/_dashdown/api/search-index`` live, ``_dashdown/search-index.json``
    in a static build) and ranks pages/sections in the browser. Unlike the
    ``<Search>`` *filter*, this is **not** a filter control — it searches a static
    snapshot of the docs, so it must survive ``dashdown build`` (``is_filter`` stays
    False) and works in static exports/embeds for free.

    Parameters:
    - placeholder: input placeholder (default "Search documentation…").
    - label: accessible label (default "Search").
    - max_results: how many results to show (default 8).

    Example:
        <SiteSearch placeholder="Search the docs…" />
    """

    is_filter = False

    def render(
        self, attrs, ctx: RenderContext, inner: str | None = None
    ) -> str:
        placeholder = attr_str(attrs, "placeholder", "Search documentation…")
        label = attr_str(attrs, "label", "Search")
        try:
            max_results = int(attrs.get("max_results", 8))
        except (TypeError, ValueError):
            max_results = 8

        cid = new_id("dashdown-site-search")
        config = {
            "placeholder": placeholder,
            "label": label,
            "max_results": max_results,
        }
        return (
            f'<div class="dashdown-site-search" id="{cid}" '
            f'data-async-component="site-search" '
            f'data-config="{esc(safe_json(config))}">'
            f'<div class="dashdown-site-search-box">'
            f'<svg class="dashdown-site-search-icon" fill="none" stroke="currentColor" '
            f'stroke-width="2" viewBox="0 0 24 24" aria-hidden="true">'
            f'<path stroke-linecap="round" d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z"/>'
            f"</svg>"
            f'<input type="text" class="input input-sm dashdown-site-search-input" '
            f'placeholder="{esc(placeholder)}" aria-label="{esc(label)}" '
            f'autocomplete="off" spellcheck="false" role="combobox" '
            f'aria-expanded="false" aria-autocomplete="list">'
            f'<kbd class="dashdown-site-search-hint" aria-hidden="true">/</kbd>'
            f"</div>"
            f'<div class="dashdown-site-search-results" role="listbox" hidden></div>'
            f"</div>"
        )
