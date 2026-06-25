from __future__ import annotations

from dashdown.components.base import Component, register_component
from dashdown.components.builtin._util import attr_str, ref_str, resolve_semantic_query
from dashdown.components.builtin.line_chart import _chart_html, _chart_placeholder


def _split(raw: str | None) -> list[str]:
    return [d.strip() for d in str(raw or "").split(",") if d.strip()]


@register_component("ParallelChart")
class ParallelChart(Component):
    """Parallel-coordinates plot over several numeric columns.

    Two input modes:

    * ``data={specs} dimensions="price, speed, battery, rating" series="tier"`` —
      `dimensions` is a comma list of numeric columns (one vertical axis each) and
      every row becomes a polyline; an optional `series` colours by group.
    * **semantic** — ``by={products.category} dimensions="products.price,
      products.weight,products.rating"``: `dimensions` is a comma list of **measure**
      refs (one axis each) and `by` is the dimension that becomes one polyline per
      group (omit `by` for a single aggregate line). The metrics are combined into
      one synthetic query.
    """

    def render(self, attrs, ctx, inner: str | None = None):
        if attrs.get("data") is None:
            # --- semantic mode: dimensions are measure refs, by groups the lines ---
            dim_refs = _split(ref_str(attrs, "dimensions"))
            if len(dim_refs) < 2:
                raise ValueError(
                    "ParallelChart requires a `dimensions` list of at least two "
                    "measures (comma-separated `model.metric` refs), or `data={query}` "
                    "with numeric column names"
                )
            sem = resolve_semantic_query(
                attrs, ctx, measures=dim_refs, by_ref=ref_str(attrs, "by")
            )
            dims = [sem["columns"][r] for r in dim_refs]
            return _chart_placeholder(
                "parallel", attrs, ctx,
                name=sem["query_name"], x=sem["by"], y=None,
                extra={"dimensions": dims},
            )
        # --- data={query} mode: dimensions are numeric column names ---
        dims = _split(attr_str(attrs, "dimensions"))
        if len(dims) < 2:
            raise ValueError(
                "ParallelChart requires a `dimensions` attribute listing at least "
                "two numeric columns (comma-separated)"
            )
        return _chart_html(
            "parallel", attrs, ctx, require_x=False, require_y=False,
            extra={"dimensions": dims},
        )
