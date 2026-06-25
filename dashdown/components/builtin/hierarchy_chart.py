from __future__ import annotations

from typing import Any

from dashdown.components.base import Component, RenderContext, register_component
from dashdown.components.builtin._util import attr_str, ref_str
from dashdown.components.builtin.line_chart import _chart_html


def _hierarchy_html(chart_type: str, attrs: dict[str, Any], ctx: RenderContext) -> str:
    """Shared placeholder for the adjacency-list hierarchy charts.

    Both Sunburst and Tree build a nested hierarchy client-side from a flat
    ``id`` / ``parent`` edge list — `id` names each node, `parent` points at its
    parent's id (empty / unknown ⇒ a root). `value` (optional) sizes a node and
    `label` (optional) is its display name (defaults to the id).
    """
    # A parent/child adjacency list isn't a measure-by-dimension shape, so a
    # semantic `metric=`/`by=` reference can't express it — refuse it with an
    # actionable message (checked before the id/parent requirement so the hint,
    # not "requires id/parent", is what a semantic author sees).
    if ref_str(attrs, "metric"):
        raise ValueError(
            f"{chart_type.capitalize()}Chart can't be built from a semantic "
            f"`metric=`/`by=` reference: it needs a parent/child hierarchy "
            f"(`id`/`parent` columns), which the metric/dimension grammar can't "
            f"express. Use `data={{query}}` with `id`/`parent` columns."
        )
    node_id = attr_str(attrs, "id")
    parent = attr_str(attrs, "parent")
    if not node_id or not parent:
        raise ValueError(f"{chart_type} requires `id` and `parent` attributes")
    extra: dict[str, Any] = {"node_id": node_id, "parent": parent}
    value = attr_str(attrs, "value")
    if value:
        extra["value"] = value
    label = attr_str(attrs, "label")
    if label:
        extra["label"] = label
    return _chart_html(
        chart_type, attrs, ctx, require_x=False, require_y=False, extra=extra
    )


@register_component("SunburstChart")
class SunburstChart(Component):
    """Sunburst — a hierarchy as nested rings, area encoding the value.

    Usage:
        <SunburstChart data={org} id="id" parent="parent" value="headcount" label="name" />
    """

    def render(self, attrs, ctx, inner: str | None = None):
        return _hierarchy_html("sunburst", attrs, ctx)


@register_component("TreeChart")
class TreeChart(Component):
    """Tree — a hierarchy as a node-link diagram (left-to-right, collapsible).

    Same `id`/`parent`/`value`/`label` attributes as
    [SunburstChart](/components/charts/sunburst-chart); multiple roots are
    wrapped under one synthetic root.
    """

    def render(self, attrs, ctx, inner: str | None = None):
        return _hierarchy_html("tree", attrs, ctx)
