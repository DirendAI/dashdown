"""Tabs layout component — section a page into switchable panels."""
from __future__ import annotations

from typing import Any

from dashdown.components.base import Component, RenderContext, register_component
from dashdown.components.builtin._util import (
    attr_str,
    esc,
    grid_span_style,
    new_id,
    safe_json,
)


@register_component("Tab")
class Tab(Component):
    """One panel inside a `<Tabs>` container.

    Usage:
        <Tabs>
          <Tab title="Overview">…markdown/components…</Tab>
          <Tab title="By region">…</Tab>
        </Tabs>

    Renders a panel `<div>` carrying its title as `data-tab-title`; the
    enclosing `<Tabs>` wrapper (and `tabs.js`) builds the tab bar from those
    markers. The title is also emitted as a screen-hidden heading shown only
    in print/PDF mode, where every panel is force-shown stacked (see the
    `.dashdown-print` rules in dashdown.css).

    Attributes:
    - title: Required. The tab's label in the tab bar.
    """

    def render(
        self,
        attrs: dict[str, Any],
        ctx: RenderContext,
        inner: str | None = None,
    ) -> str:
        title = attr_str(attrs, "title")
        if not title:
            raise ValueError('Tab requires a `title` attribute (e.g. title="Overview")')

        inner_html = (inner or "").strip()
        return (
            f'<div class="dashdown-tab-panel" data-tab-title="{esc(title)}">'
            f'<div class="dashdown-tab-panel-heading">{esc(title)}</div>'
            f"{inner_html}"
            f"</div>"
        )


@register_component("Tabs")
class Tabs(Component):
    """Switchable tabbed sections — pure **layout**, not a filter.

    Shows one `<Tab>` panel at a time behind a tab bar ("Overview · By region ·
    Raw data"). Unlike `<ButtonGroup>` (which writes a filter value into
    `$store.filters` and changes the *data*), Tabs only toggles which authored
    content is visible — no SQL involvement, so it survives static builds
    (`is_filter = False`) and adds no injection surface.

    Usage:
        <Tabs name="view">
          <Tab title="Overview">…</Tab>
          <Tab title="By region">…</Tab>
        </Tabs>

    The tab bar is built client-side by `tabs.js` from the panels' `data-tab-title`
    markers (direct children only, so nested `<Tabs>` inside a panel keep their own
    bar). Before JS runs — or with JS off — CSS shows the first panel; print/PDF
    mode force-shows *all* panels stacked with their headings, so hidden charts
    still make it into an export. Switching tabs triggers a chart re-measure
    (ECharts initialized inside a hidden panel is 0-sized until revealed).

    Attributes:
    - name: Optional. Sync the active tab to the URL as `?name=<title-slug>`
      (deep-linkable, back/forward-aware). Without a name there is no URL sync.
    - default: Optional. Title of the tab active on first load (URL wins).
      Defaults to the first tab.
    - url_sync: Optional. Set `false` to keep a named Tabs out of the URL.
      Default `true`.
    - label: Optional. Accessible label for the tab bar (default "Tabs").
    - col-span / span: Optional. Columns to span inside a `<Grid>`.
    """

    def render(
        self,
        attrs: dict[str, Any],
        ctx: RenderContext,
        inner: str | None = None,
    ) -> str:
        inner_html = (inner or "").strip()
        if "data-tab-title=" not in inner_html:
            raise ValueError(
                "Tabs requires at least one <Tab title=…>…</Tab> child"
            )

        name = attr_str(attrs, "name", "") or ""
        default = attr_str(attrs, "default", "") or ""
        url_sync = attrs.get("url_sync", True)
        label = attr_str(attrs, "label", "Tabs")

        cid = new_id("dashdown-tabs")
        span = grid_span_style(attrs)
        style = f' style="{span}"' if span else ""

        config = {
            "name": name,
            "default": default,
            "url_sync": url_sync,
        }

        return (
            f'<div class="dashdown-tabs" id="{cid}" '
            f'data-async-component="tabs" '
            f'data-config="{esc(safe_json(config))}"{style}>'
            f'<div class="dashdown-tabs-nav" role="tablist" '
            f'aria-label="{esc(label)}"></div>'
            f'<div class="dashdown-tabs-panels">{inner_html}</div>'
            f"</div>"
        )
