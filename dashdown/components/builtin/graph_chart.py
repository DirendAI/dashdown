from __future__ import annotations

from dashdown.components.base import Component, register_component
from dashdown.components.builtin._util import attr_str, ref_str, resolve_semantic_query
from dashdown.components.builtin.line_chart import _chart_html, _chart_placeholder


@register_component("GraphChart")
class GraphChart(Component):
    """Force-directed network graph from an edge-list query.

    Two input modes:

    * ``data={links} source="from" target="to" value="weight"`` — each row is one
      edge; `value` (optional) weights the edges. `source`/`target` alias `x`/`y`.
    * **semantic** — ``source={l.from} target={l.to} value={l.weight}``:
      `source`/`target` are two **dimensions** (primary `by` + secondary `series`)
      and `value` is the **measure** weighting each edge (required in semantic mode —
      a measure aggregates the edge list; e.g. a `count` measure for unweighted
      edges).

    Nodes are the union of the `source` and `target` columns, sized by their total
    incident weight.
    """

    def render(self, attrs, ctx, inner: str | None = None):
        if attrs.get("data") is None:
            # --- semantic mode: source/target are dimensions, value is a measure ---
            source_ref = ref_str(attrs, "source")
            target_ref = ref_str(attrs, "target")
            value_ref = ref_str(attrs, "value")
            if not source_ref or not target_ref:
                raise ValueError("GraphChart requires `source` and `target` attributes")
            if not value_ref:
                raise ValueError(
                    "GraphChart in semantic mode requires a `value` measure (an edge "
                    "weight, e.g. a count) — a measure aggregates the edge list. Use "
                    "`data={query}` for unweighted edges."
                )
            sem = resolve_semantic_query(
                attrs, ctx, measures=[value_ref], by_ref=source_ref, series_ref=target_ref
            )
            return _chart_placeholder(
                "graph", attrs, ctx,
                name=sem["query_name"], x=sem["by"], y=sem["series"],
                extra={"value": sem["columns"][value_ref]},
            )
        # --- data={query} mode: source/target/value are column names ---
        attrs = dict(attrs)
        source = attr_str(attrs, "source")
        target = attr_str(attrs, "target")
        value = attr_str(attrs, "value")
        if not source or not target:
            raise ValueError("GraphChart requires `source` and `target` attributes")
        attrs["x"] = source
        attrs["y"] = target
        extra = {"value": value} if value else {}
        return _chart_html("graph", attrs, ctx, extra=extra)
