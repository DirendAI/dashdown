"""Grid layout component — a responsive CSS grid for dashboard pages."""
from __future__ import annotations

from typing import Any

from dashdown.components.base import Component, RenderContext, register_component
from dashdown.components.builtin._util import attr_int, attr_str, new_id


@register_component("Grid")
class Grid(Component):
    """Lay children out on a fixed-column CSS grid.

    Usage:
        <Grid cols=3>
          <LineChart data={revenue} x="month" y="revenue" col-span=2 />
          <PieChart data={status} x="status" y="count" />
        </Grid>

    Children may set `col-span=N` to span multiple columns (see
    ``grid_span_style`` in ``_util``). The grid collapses to a single column
    on narrow viewports (see ``.dashdown-grid-responsive`` in dashdown.css).

    Attributes:
        cols / columns: number of equal-width columns (default 2)
        gap: CSS gap between cells (defaults to the --dashdown-grid-gap
            design token, 1rem)
    """

    def render(
        self,
        attrs: dict[str, Any],
        ctx: RenderContext,
        inner: str | None = None,
    ) -> str:
        cols = attr_int(attrs, "cols")
        if cols is None:
            cols = attr_int(attrs, "columns", 2)
        if not cols or cols <= 0:
            cols = 1

        gap = attr_str(attrs, "gap", "var(--dashdown-grid-gap, 1rem)")
        cid = new_id("dashdown-grid")
        # minmax(0, 1fr) lets children shrink instead of overflowing the track.
        style = f"grid-template-columns:repeat({cols},minmax(0,1fr));gap:{gap};"

        inner_html = (inner or "").strip()
        return (
            f'<div id="{cid}" class="dashdown-grid dashdown-grid-responsive" '
            f'style="{style}" data-cols="{cols}">{inner_html}</div>'
        )
