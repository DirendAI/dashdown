from __future__ import annotations

from dashdown.components.base import Component, register_component
from dashdown.components.builtin._util import attr_str, ref_str, resolve_semantic_query
from dashdown.components.builtin.line_chart import _chart_html, _chart_placeholder


@register_component("SankeyChart")
class SankeyChart(Component):
    """Sankey flow diagram from an edge-list query.

    Two input modes:

    * ``data={flow} source="stage_from" target="stage_to" value="users"`` — each
      row is one link; `value` sets the link width. `source`/`target` alias the
      generic `x`/`y` axes.
    * **semantic** — ``source={f.stage_from} target={f.stage_to} value={f.users}``:
      `source`/`target` are two **dimensions** (primary `by` + secondary `series`)
      and `value` is the **measure** weighting each link, combined into one
      synthetic query grouped by the source×target pair.

    Nodes are the union of the `source` and `target` columns. (The flow must be
    acyclic — ECharts can't lay out a Sankey with a cycle.)
    """

    def render(self, attrs, ctx, inner: str | None = None):
        if attrs.get("data") is None:
            # --- semantic mode: source/target are dimensions, value is a measure ---
            source_ref = ref_str(attrs, "source")
            target_ref = ref_str(attrs, "target")
            value_ref = ref_str(attrs, "value")
            if not source_ref or not target_ref or not value_ref:
                raise ValueError(
                    "SankeyChart requires `source`, `target`, and `value` attributes"
                )
            sem = resolve_semantic_query(
                attrs, ctx, measures=[value_ref], by_ref=source_ref, series_ref=target_ref
            )
            return _chart_placeholder(
                "sankey", attrs, ctx,
                name=sem["query_name"], x=sem["by"], y=sem["series"],
                extra={"value": sem["columns"][value_ref]},
            )
        # --- data={query} mode: source/target/value are column names ---
        attrs = dict(attrs)
        source = attr_str(attrs, "source")
        target = attr_str(attrs, "target")
        value = attr_str(attrs, "value")
        if not source or not target or not value:
            raise ValueError(
                "SankeyChart requires `source`, `target`, and `value` attributes"
            )
        attrs["x"] = source
        attrs["y"] = target
        return _chart_html("sankey", attrs, ctx, extra={"value": value})
