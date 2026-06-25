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


def _js_str(s: str) -> str:
    """A single-quoted JS string literal for an inline Alpine expression.

    The whole expression is HTML-escaped by the caller (so ``"``/``&``/``<`` in
    the value are attribute-safe); here we only need it to be a valid JS string,
    so backslash and the single-quote delimiter are escaped.
    """
    return "'" + s.replace("\\", "\\\\").replace("'", "\\'") + "'"


@register_component("Toggle")
class Toggle(Component):
    """Boolean / toggle filter — a one-click switch (or checkbox) for a two-valued
    facet ("show only active", "include archived", "paid only").

    The picked value is stored as a **string** in the page-level Alpine store under
    ``filters[name]`` (uniform with every other filter), so it reaches SQL only
    through the context-aware ``_substitute_params`` — ``"true"`` → ``'true'``,
    ``""`` → ``''`` — with no new injection surface.

    Two value modes via ``on_value`` / ``off_value`` (both **arbitrary strings**):

    - **All-guard (default):** ``on_value="true"`` / ``off_value=""``. The value is
      only the on/off *sentinel*: off writes ``""`` so the ``'${paid}' = ''`` guard
      is true and the ``OR`` matches every row (show all); on writes ``"true"`` so the
      fixed condition applies — ``WHERE '${paid}' = '' OR is_paid = TRUE``. Same
      empty-means-all convention as a multi-select Dropdown.
    - **Two-state:** any non-empty ``off_value`` compares the column *directly* so both
      directions filter — e.g. ``on_value="Yes" off_value="No"`` against a text column,
      ``WHERE status = ${open}``, or ``off_value="false"`` for a strict boolean,
      ``WHERE is_paid = ${paid}``.

    Three-state (All / Yes / No) stays the Dropdown's job; ``<Toggle>`` is the
    one-click binary affordance.

    Attributes:
    - name: Required. The filter name to store the value under.
    - label: Optional. Display label (defaults to name).
    - on_value: Optional. Value stored when checked (default ``"true"``; any string).
    - off_value: Optional. Value stored when unchecked (default ``""``; any string).
    - default: Optional. Initial state on first load (default ``false`` = off). URL
      params still win over this (precedence URL > default > off).
    - variant: Optional. ``switch`` (default) or ``checkbox`` styling.
    - url_sync: Optional. Sync the value to the URL (default ``true``).
    - bar: Optional. Relocate this control into the page's top filter bar.
      Default is inline (renders where authored).

    Example:
        <Toggle name="paid" label="Paid only" />
        <Toggle name="open" label="Open" on_value="Yes" off_value="No" />
    """

    is_filter = True

    def render(
        self, attrs, ctx: RenderContext, inner: str | None = None
    ) -> str:
        name = attr_str(attrs, "name")
        if not name:
            raise ValueError("Toggle requires a `name` attribute")

        label = attr_str(attrs, "label", name)
        on_value = attr_str(attrs, "on_value", "true")
        off_value = attr_str(attrs, "off_value", "")
        default_on = attr_bool(attrs, "default", False)
        variant = (attr_str(attrs, "variant", "switch") or "switch").lower()
        url_sync = attrs.get("url_sync", True)
        # Inline by default (renders where authored); `bar` relocates it into the
        # top filter row (read by filter_bar.js). See filter_bar_marker.
        filter_bar_attr = filter_bar_marker(attrs, ctx)

        cid = new_id("dashdown-toggle")

        # DaisyUI base classes (both already in the vendored bundle — no rebuild);
        # the size comes from our own `.dashdown-toggle-input` CSS.
        box_class = "checkbox" if variant == "checkbox" else "toggle"

        # The value rides in the store as a string. A checkbox `x-model` would bind
        # a *boolean*, so bind explicitly: reflect store→checkbox with :checked, and
        # write the on/off string back on change. `toggle.js` does URL-sync, control
        # registration, and the first-load default seed. The whole expression is
        # HTML-escaped as a unit, so arbitrary on/off strings stay attribute-safe.
        store_ref = f"$store.filters['{name}']"
        on_js = _js_str(on_value)
        off_js = _js_str(off_value)
        checked_expr = f"{store_ref} === {on_js}"
        change_expr = (
            f"{store_ref} = $event.target.checked ? {on_js} : {off_js}"
        )

        config = {
            "name": name,
            "label": label,
            "on_value": on_value,
            "off_value": off_value,
            "default": default_on,
            "variant": "checkbox" if variant == "checkbox" else "switch",
            "url_sync": url_sync,
        }

        return (
            f'<div class="dashdown-toggle dashdown-filter-pill" id="{cid}" '
            f'data-async-component="toggle" '
            f'data-config="{esc(safe_json(config))}" '
            f'data-name="{esc(name)}" '
            f'data-url-sync="{str(url_sync).lower()}" '
            f"{filter_bar_attr}"
            f">"
            f'<span class="dashdown-filter-pill-label">{esc(label)}'
            f'<span class="dashdown-filter-pill-colon">:</span></span>'
            f'<input type="checkbox" class="{box_class} dashdown-toggle-input" '
            f'role="switch" '
            f':checked="{esc(checked_expr)}" '
            f'@change="{esc(change_expr)}" '
            f'aria-label="{esc(label)}" '
            f"/>"
            f"</div>"
        )
