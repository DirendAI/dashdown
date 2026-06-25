from __future__ import annotations

from dashdown.components.base import Component, RenderContext, register_component
from dashdown.components.builtin._util import (
    attr_str,
    esc,
    filter_bar_marker,
    new_id,
    safe_json,
)


@register_component("Search")
class Search(Component):
    """Search input component for filtering.

    Supports URL sync by default (url_sync=True).
    Search value is stored in the page-level Alpine store under `filters[name]`.
    
    Parameters:
    - name: Required. The filter name to store the search value under.
    - label: Optional. Display label (defaults to "Search").
    - placeholder: Optional. Input placeholder text (defaults to "Search...")
    - url_sync: Optional. Enable URL sync (default: True)
    - debounce: Optional. Debounce delay in milliseconds (default: 300)
    - bar: Optional. Relocate this control into the page's top filter bar.
      Default is inline (renders where authored).

    Example:
    <Search name="search" label="Search" placeholder="Type to search..." />
    
    URL format:
    ?search=query
    """

    is_filter = True

    def render(
        self, attrs, ctx: RenderContext, inner: str | None = None
    ) -> str:
        name = attr_str(attrs, "name")
        if not name:
            raise ValueError("Search requires a `name` attribute")
        
        label = attr_str(attrs, "label", "Search")
        placeholder = attr_str(attrs, "placeholder", "Search...")
        url_sync = attrs.get("url_sync", True)
        debounce = attrs.get("debounce", 300)
        # Inline by default (renders where authored); `bar` relocates it into the
        # top filter row (read by filter_bar.js). See filter_bar_marker.
        filter_bar_attr = filter_bar_marker(attrs, ctx)

        cid = new_id("dashdown-search")
        
        # Build config for frontend
        config = {
            "name": name,
            "label": label,
            "placeholder": placeholder,
            "url_sync": url_sync,
            "debounce": debounce,
        }
        
        # Single reactive path: the input binds straight to the central filters
        # store (debounced), exactly like <Dropdown>'s select. `search.js` mirrors
        # the store value to the URL. No private `searchValue` state.
        # Compact pill: magnifier icon prefix instead of a stacked label (the
        # label moves to aria-label), inline ✕ to clear.
        store_ref = f"$store.filters['{esc(name)}']"
        return (
            f'<div class="dashdown-search dashdown-filter-pill" id="{cid}" '
            f'data-async-component="search" '
            f'data-config="{esc(safe_json(config))}" '
            f'data-name="{esc(name)}" '
            f'data-url-sync="{str(url_sync).lower()}" '
            f"{filter_bar_attr}"
            f">"
            f'<svg class="dashdown-filter-pill-icon" fill="none" stroke="currentColor" '
            f'stroke-width="2" viewBox="0 0 24 24" aria-hidden="true">'
            f'<path stroke-linecap="round" d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z"/>'
            f'</svg>'
            f'<input type="text" class="input input-sm" '
            f'x-model.debounce.{debounce}ms="{store_ref}" '
            f'placeholder="{esc(placeholder)}" '
            f'aria-label="{esc(label)}" '
            f'>'
            f'<button type="button" class="dashdown-filter-pill-clear" '
            f'@click="{store_ref} = \'\'" '
            f'x-show="{store_ref}" x-cloak '
            f'aria-label="Clear {esc(label)}" '
            f'>✕</button>'
            f'</div>'
        )
