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

    The whole attribute is HTML-escaped by the caller (so ``"``/``&``/``<`` stay
    attribute-safe); here we only make it a valid JS string, escaping backslash
    and the single-quote delimiter. Mirrors ``toggle.py::_js_str``.
    """
    return "'" + s.replace("\\", "\\\\").replace("'", "\\'") + "'"


def _parse_options(raw) -> list[str]:
    """Options from ``options="a,b,c"`` (comma string) or ``options={[a, b]}``
    (array literal → a Python list via the attrs parser). Either way: trimmed,
    non-empty, in order."""
    if isinstance(raw, (list, tuple)):
        items = [str(x) for x in raw]
    elif raw is None:
        items = []
    else:
        items = str(raw).split(",")
    return [s.strip() for s in items if str(s).strip()]


@register_component("ButtonGroup")
class ButtonGroup(Component):
    """Single-select filter shown as an inline **segmented control** — a row of
    pill buttons where exactly one is active ("All · Active · Churned",
    "Day · Week · Month"). A lower-friction alternative to a `<Dropdown>` for a
    small, fixed set of options (one click instead of open-then-pick).

    The picked value is stored as a **string** in the page-level Alpine store
    under ``filters[name]`` — uniform with every other filter, so it reaches SQL
    only through the context-aware ``_substitute_params`` (**no new injection
    surface**). With ``include_all`` (default) an extra "All" segment stores ``""``
    so the author's empty-means-all guard matches every row, exactly like a
    single-select Dropdown:

        :::query name=users connector=main
        SELECT * FROM users
        WHERE '${status}' = '' OR status = '${status}'
        :::

    For a **dynamic** or high-cardinality column, reach for ``<Dropdown>`` (it
    populates options from the data); a segmented control is for the handful of
    fixed choices you'd lay out as buttons.

    Attributes:
    - name: Required. The filter key your SQL reads as ``${name}``.
    - options: Required. The choices — ``options="Active,Churned"`` (comma string)
      or ``options={[Active,Churned]}`` (array literal). Value == label.
    - label: Optional. Inline label shown before the segments (defaults to name).
    - include_all: Optional. Prepend an "All" segment that clears the filter
      (stores ``""``). Default ``true``.
    - all_label: Optional. Text for that segment (default ``"All"``).
    - default: Optional. Value selected on first load (URL params still win). With
      ``include_all`` and no default, "All" starts active.
    - url_sync: Optional. Sync the value to the URL (default ``true``).
    - bar: Optional. Relocate this control into the page's top filter bar.
      Default is inline (renders where authored).

    Example:
        <ButtonGroup name="status" label="Status" options="Active,Churned" />
    """

    is_filter = True

    def render(
        self, attrs, ctx: RenderContext, inner: str | None = None
    ) -> str:
        name = attr_str(attrs, "name")
        if not name:
            raise ValueError("ButtonGroup requires a `name` attribute")

        options = _parse_options(attrs.get("options"))
        if not options:
            raise ValueError(
                "ButtonGroup requires an `options` attribute "
                '(e.g. options="Active,Churned")'
            )

        label = attr_str(attrs, "label", name)
        include_all = attr_bool(attrs, "include_all", True)
        all_label = attr_str(attrs, "all_label", "All")
        default = attr_str(attrs, "default", "") or ""
        url_sync = attrs.get("url_sync", True)
        # Inline by default (renders where authored); `bar` relocates it into the
        # top filter row (read by filter_bar.js). See filter_bar_marker.
        filter_bar_attr = filter_bar_marker(attrs, ctx)

        cid = new_id("dashdown-buttongroup")
        store_ref = f"$store.filters['{name}']"

        # Each segment writes its value to the store on click and reflects the
        # active state from it — the same inline-Alpine + thin-JS split as
        # <Toggle> (buttongroup.js does URL sync, chip suppression, default seed).
        # The store value is a string, so a segment is active iff it === its
        # value; the "All" segment (value "") is active when nothing is set.
        segments: list[str] = []
        if include_all:
            segments.append(
                f'<button type="button" role="radio" '
                f'class="dashdown-segment" '
                f':class="!{store_ref} ? \'dashdown-segment-active\' : \'\'" '
                f':aria-checked="!{store_ref}" '
                f"@click=\"{store_ref} = ''\" "
                f">{esc(all_label)}</button>"
            )
        for opt in options:
            opt_js = _js_str(opt)
            segments.append(
                f'<button type="button" role="radio" '
                f'class="dashdown-segment" '
                f':class="{store_ref} === {esc(opt_js)} ? \'dashdown-segment-active\' : \'\'" '
                f':aria-checked="{store_ref} === {esc(opt_js)}" '
                f'@click="{store_ref} = {esc(opt_js)}" '
                f">{esc(opt)}</button>"
            )

        config = {
            "name": name,
            "label": label,
            "options": options,
            "include_all": include_all,
            "default": default,
            "url_sync": url_sync,
        }

        return (
            f'<div class="dashdown-button-group dashdown-filter-pill" id="{cid}" '
            f'data-async-component="buttongroup" '
            f'data-config="{esc(safe_json(config))}" '
            f'data-name="{esc(name)}" '
            f'data-url-sync="{str(url_sync).lower()}" '
            f"{filter_bar_attr}"
            f">"
            f'<span class="dashdown-filter-pill-label">{esc(label)}'
            f'<span class="dashdown-filter-pill-colon">:</span></span>'
            f'<div class="dashdown-segments" role="radiogroup" '
            f'aria-label="{esc(label)}">'
            f"{''.join(segments)}"
            f"</div>"
            f"</div>"
        )
