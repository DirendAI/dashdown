"""Shared plumbing for the SVG geo map components.

The five geo maps (ChoroplethTime, ChoroplethFacets, BivariateMap, BubbleMap,
DotDensityMap) all emit the same async placeholder card and share a set of
basemap/color attributes; :func:`_map_html` keeps that in one place — the geo
analogue of ``line_chart._chart_html``. ``catalog.py`` lists ``_map_html`` in
``_HELPER_ATTRS`` so the common attrs it reads surface on each component.

Unlike the ECharts charts, all interactivity these components need (year
scrubber, metric toggle) is their own DOM, and every animation frame ships in
the one query result — so ``dashdown build`` exports render them fully with no
server. Keep that contract: never turn one of their controls into an
``is_filter`` Dashdown control.
"""
from __future__ import annotations

from typing import Any

from dashdown.chart_annotations import build_chart_context
from dashdown.components.base import RenderContext
from dashdown.components.builtin._util import (
    attr_bool,
    attr_int,
    attr_str,
    esc,
    grid_span_style,
    new_id,
    safe_json,
)
from dashdown.components.builtin.line_chart import _EXPAND_BTN_HTML, _explain_affordance
from dashdown.render.attrs import DataRef


def parse_metrics(
    spec: str | None, *, quantity_field: str | None = None
) -> list[dict[str, Any]]:
    """Parse ``metrics="col|Label|unit,…"`` into ``[{column,label,unit}, …]``.

    Label defaults to the column name; unit to ``""``. DotDensityMap passes
    ``quantity_field="per_dot"`` to read a fourth ``|`` segment (the quantity
    one dot stands for) — absent/invalid stays ``None`` and the client derives
    a value from the data.
    """
    if not spec:
        return []
    metrics: list[dict[str, Any]] = []
    for part in str(spec).split(","):
        fields = [f.strip() for f in part.split("|")]
        if not fields[0]:
            continue
        metric: dict[str, Any] = {
            "column": fields[0],
            "label": fields[1] if len(fields) > 1 and fields[1] else fields[0],
            "unit": fields[2] if len(fields) > 2 else "",
        }
        if quantity_field:
            try:
                metric[quantity_field] = float(fields[3])
            except (IndexError, ValueError):
                metric[quantity_field] = None
        metrics.append(metric)
    return metrics


def _map_html(
    map_type: str,
    attrs: dict[str, Any],
    ctx: RenderContext,
    *,
    config: dict[str, Any],
    default_height: int = 420,
    fixed_height: bool = True,
) -> str:
    """The shared async map card: resolves ``data=`` and the common basemap /
    color attrs, merges the component-specific ``config``, and emits the
    ``data-async-component`` placeholder the matching JS module hydrates.

    Common attrs (all optional): ``title``, ``scheme`` (named sequential ramp
    or bivariate palette), ``color`` (base color a ramp is derived from),
    ``scale`` (linear|log|quantile value→color mapping), ``map`` (bundled map
    name, default world), ``geojson`` (custom GeoJSON URL), ``id_field``
    (feature property to join on, default ``iso``), ``height``,
    ``empty_message``, and the ``<Grid>`` ``col-span``.

    ``fixed_height=False`` (ChoroplethFacets) lets the card grow with its
    facet grid instead of pinning the region height.

    Every card also carries the charts' ⛶ fullscreen button
    (``line_chart._EXPAND_BTN_HTML`` — the same cross-module reuse as
    ComboChart's ``_chart_card``): fullscreen.js re-draws the map into its
    modal via the renderer registry in ``static/components/_geo.js``. It sits
    outside ``.card-body``, so the JS map shell (which rebuilds the body on
    every data render) never clobbers it.

    ``explain`` works here too (``line_chart._explain_affordance`` — same
    cross-module reuse): the ✨ button + commentary footer are direct card
    children for the same rebuild-safety reason. Chart annotations ride the
    resolved geo shape: the annotation vocabulary
    (``chart_annotations.ANNOTATION_VOCAB``) grants ``geo_item`` halos to the
    bubble/dot maps only — the choropleths register a commentary-only ask
    (their context resolves to ``None``). ``annotations=false`` opts a map
    back down to commentary-only.
    """
    data_val = attrs.get("data")
    if isinstance(data_val, DataRef):
        name = data_val.name
    else:
        name = attr_str(attrs, "data")
    if not name:
        raise ValueError(f"{map_type} requires `data={{query_name}}` attribute")

    full: dict[str, Any] = {
        "type": map_type,
        "query_name": name,
        "title": attr_str(attrs, "title", ""),
        "empty_message": attr_str(attrs, "empty_message", "No data available"),
    }
    for key in ("scheme", "color", "scale", "map", "geojson", "id_field"):
        val = attr_str(attrs, key)
        if val:
            full[key] = val
    full.update(config)
    config_json = esc(safe_json(full))

    height = attr_int(attrs, "height", default_height) or default_height
    span = grid_span_style(attrs)
    cid = new_id(f"dashdown-{map_type}")

    if fixed_height:
        style = f"width:100%;height:{height}px;{span}"
        skeleton = '<div class="dashdown-chart-skeleton skeleton w-full h-full"></div>'
    else:
        # The facet grid defines its own height; the skeleton just reserves a
        # plausible footprint until the first data lands.
        style = f"width:100%;{span}"
        skeleton = (
            f'<div class="dashdown-chart-skeleton skeleton w-full" '
            f'style="height:{height}px"></div>'
        )

    # The resolved geo shape the `explain` affordance pins to its AskDef:
    # x is the join-id column, y the toggleable metric columns, and the year
    # slice rides `extra` (the validator grounds candidates in the frame the
    # viewer sees). None for map types without an annotation vocabulary —
    # their explain stays commentary-only — and under `annotations=false`.
    chart_context = None
    if attr_bool(attrs, "annotations", True):
        metric_cols = [
            m["column"] for m in (full.get("metrics") or []) if m.get("column")
        ]
        extra: list[tuple[str, str]] = []
        if full.get("year"):
            extra.append(("year", str(full["year"])))
            if full.get("year_value"):
                extra.append(("year_value", str(full["year_value"])))
        chart_context = build_chart_context(
            map_type,
            x=str(full.get("id") or "") or None,
            y=",".join(metric_cols) or None,
            extra=tuple(extra),
        )

    explain_html = _explain_affordance(
        map_type, attrs, ctx, cid=cid, name=name, chart_context=chart_context
    )
    body = f'<div class="card-body p-4 h-full">{skeleton}</div>'
    if explain_html and fixed_height:
        # Chart parity (_chart_card): the fixed height moves onto an inner
        # region so the card can grow when the commentary footer opens.
        inner = (
            f'<div class="dashdown-chart-region" style="height:{height}px">'
            f"{body}"
            f"</div>"
            f"{_EXPAND_BTN_HTML}"
            f"{explain_html}"
        )
        style = f"width:100%;{span}"
    else:
        inner = f"{body}{_EXPAND_BTN_HTML}{explain_html}"

    return (
        f'<div class="dashdown-map card bg-base-100 border border-base-300" '
        f'id="{cid}" '
        f'style="{style}" '
        f'data-async-component="{map_type}" '
        f'data-config="{config_json}" '
        f'data-component-id="{cid}" '
        f'data-query-name="{esc(name)}">'
        f"{inner}"
        f"</div>"
    )
