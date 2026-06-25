"""Shared helpers for built-in components."""
from __future__ import annotations

import html
import json
import uuid
from typing import Any

from dashdown.components.base import RenderContext
from dashdown.render.attrs import DataRef


def resolve_dataset(attrs: dict[str, Any], ctx: RenderContext, key: str = "data"):
    """Resolve a `data={...}` ref to a QueryResult. Returns (name, result)."""
    val = attrs.get(key)
    if not isinstance(val, DataRef):
        raise ValueError(
            f"Component requires a `{key}={{query_name}}` attribute "
            f"referencing a :::query block"
        )
    return val.name, ctx.get_query(val.name)


def attr_str(attrs: dict[str, Any], key: str, default: str | None = None) -> str | None:
    v = attrs.get(key, default)
    return None if v is None else str(v)


def attr_bool(attrs: dict[str, Any], key: str, default: bool = False) -> bool:
    v = attrs.get(key, default)
    if isinstance(v, bool):
        return v
    if isinstance(v, str):
        return v.lower() in ("true", "1", "yes")
    return bool(v)


def to_filter_bar(attrs: dict[str, Any]) -> bool:
    """Whether a filter control should relocate to the top filter bar.

    Placement is **inline by default**: a control renders where it's authored
    (e.g. right above the chart it filters). It opts INTO the top filter bar with
    ``bar`` (``<Dropdown … bar />``) — or the legacy ``filter_bar=true``.
    ``filter_bar=false`` is the default now and a harmless no-op. (The project
    global date control passes ``filter_bar=True`` in embed mode to route itself
    into the bar, since embeds omit the app header.)
    """
    return attr_bool(attrs, "bar", False) or attr_bool(attrs, "filter_bar", False)


def filter_bar_marker(attrs: dict[str, Any], ctx: RenderContext) -> str:
    """Decide a filter control's placement and emit its routing marker.

    Returns ``'data-filter-bar="true" '`` (trailing space, ready to splice into
    the control's root tag) when the control opts into the top bar — read by
    ``filter_bar.js``, which relocates only marked controls — else ``""`` (inline,
    the default). Sets ``ctx.has_bar_filters`` so the pipeline emits the
    filter-bar slot **only** when something wants it; a page of purely inline
    controls gets no top chrome at all.
    """
    if to_filter_bar(attrs):
        ctx.has_bar_filters = True
        return 'data-filter-bar="true" '
    return ""


def attr_int(attrs: dict[str, Any], key: str, default: int | None = None) -> int | None:
    v = attrs.get(key, default)
    if v is None:
        return default
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def attr_float(attrs: dict[str, Any], key: str, default: float | None = None) -> float | None:
    v = attrs.get(key, default)
    if v is None:
        return default
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def grid_span_style(attrs: dict[str, Any]) -> str:
    """CSS fragment letting a `<Grid>` child span multiple columns.

    Reads `col-span` (or `span`) and returns e.g. ``grid-column:span 2;`` —
    inert outside a CSS grid, so it is safe to emit unconditionally.
    """
    n = attr_int(attrs, "col-span")
    if n is None:
        n = attr_int(attrs, "span")
    if not n or n <= 1:
        return ""
    return f"grid-column:span {n};"


def format_config(attrs: dict[str, Any]) -> dict[str, Any]:
    """Collect display-formatting attrs into a config fragment for the JS
    formatter (``formatValue`` in ``core.js``).

    Reads ``format`` (currency|number|percent|date|datetime), ``currency`` (a
    symbol like ``$``/``€`` or an ISO 4217 code like ``EUR`` for full locale
    currency formatting), ``decimals`` (fraction-digit count), ``locale`` (a
    BCP-47 tag like ``de-DE`` controlling grouping/decimal separators), and
    ``date_format`` (a moment.js-style pattern like ``DD.MM.YYYY`` or an Intl
    style keyword for ``format="date"`` columns). Only keys the author actually
    set are included, so JS defaults stay authoritative.
    """
    cfg: dict[str, Any] = {}
    fmt = attr_str(attrs, "format")
    if fmt:
        cfg["format"] = fmt
    currency = attr_str(attrs, "currency")
    if currency:
        cfg["currency"] = currency
    if "decimals" in attrs:
        decimals = attr_int(attrs, "decimals")
        if decimals is not None:
            cfg["decimals"] = decimals
    locale = attr_str(attrs, "locale")
    if locale:
        cfg["locale"] = locale
    date_format = attr_str(attrs, "date_format")
    if date_format:
        cfg["date_format"] = date_format
    return cfg


def ref_str(attrs: dict[str, Any], key: str) -> str | None:
    """Read an attr that may be a `{model.metric}` DataRef or a bare string."""
    v = attrs.get(key)
    if isinstance(v, DataRef):
        return v.name
    return attr_str(attrs, key)


def ref_or_literal(attrs: dict[str, Any], key: str) -> tuple[str | None, bool]:
    """Distinguish ``key={ref}`` from ``key="literal"`` (the render/attrs convention).

    Returns ``(value, is_ref)``: a ``{model.field}`` / ``{control}`` DataRef yields
    its name with ``is_ref=True``; a quoted/bare literal yields its string with
    ``is_ref=False``; a missing attr yields ``(None, False)``. Used for ``grain=``,
    where a literal token (author-fixed) and a control reference (interactive) mean
    different things downstream.
    """
    v = attrs.get(key)
    if isinstance(v, DataRef):
        return v.name, True
    return attr_str(attrs, key), False


def resolve_semantic(
    attrs: dict[str, Any],
    ctx,
    *,
    metric_key: str = "metric",
    by_key: str = "by",
    series_key: str | None = "series",
    grain_key: str = "grain",
) -> dict[str, Any] | None:
    """Resolve a `metric={model.metric} by={model.dim}` reference, if present.

    Shared by every component that can render a metric (charts, Value, Table).
    Returns ``{query_name, metric, by, format}`` (``metric``/``by`` are the
    *canonical* BSL field names, so they're also the result column names) and
    records the ref on ``ctx.semantic_refs`` (the pipeline compiles it into a
    synthetic query). Returns ``None`` when the component uses the normal
    ``data={query}`` path. A bad model/metric/dimension raises — surfaced as the
    component's inline error card.

    The attr keys are overridable so one component can resolve **two** refs from
    distinct attr namespaces — e.g. ``<Counter>`` resolves its headline from
    ``metric=``/``by=`` and its sparkline from ``sparkline=``/``sparkline-by=``.
    Pass ``series_key=None`` to ignore the ``series=`` second dimension (a scalar
    or single-line consumer that can't split into coloured series).
    """
    metric_ref = ref_str(attrs, metric_key)
    if not metric_ref:
        return None
    from dashdown.semantic import resolve_ref

    by_ref = ref_str(attrs, by_key)
    series_ref = ref_str(attrs, series_key) if series_key else None
    # `grain=` distinguishes a literal token (`grain="month"`, author-fixed, baked
    # into the query identity) from a control reference (`grain={trendGrain}`, read
    # from the live params at fetch so a reader can switch grain). The existing
    # `key="lit"` vs `key={ref}` attr convention is exactly that distinction.
    grain_val, grain_is_ref = ref_or_literal(attrs, grain_key)
    ref = resolve_ref(
        ctx.semantic_models, metric_ref, by_ref, series_ref,
        grain=None if grain_is_ref else grain_val,
        grain_param=grain_val if grain_is_ref else None,
    )
    ctx.semantic_refs[ref.query_name] = ref
    handle = ctx.semantic_models[ref.model]
    # `metric` is the single column the scalar widgets (Value/Counter/Table) read;
    # `metrics` is the comma-joined column list a chart turns into one series per
    # metric. They coincide for the common single-metric case. `series` is the
    # optional second dimension a chart splits one metric by (its own coloured
    # series per value). A display format is only applied when there's a single
    # metric (several metrics could disagree).
    return {
        "query_name": ref.query_name,
        "metric": ref.metric,
        "metrics": ",".join(ref.metrics),
        "by": ref.by,
        "series": ref.series,
        "format": handle.measure_formats.get(ref.metric) if len(ref.metrics) == 1 else None,
    }


def resolve_semantic_query(
    attrs: dict[str, Any],
    ctx,
    *,
    measures: list[str],
    by_ref: str | None = None,
    series_ref: str | None = None,
    grain_key: str = "grain",
) -> dict[str, Any]:
    """Resolve a **multi-measure** semantic reference for a chart whose result
    columns map onto named *roles* — Candlestick ``open/high/low/close``,
    Heatmap/Sankey/Graph ``value``, Parallel axes, Combo ``bars/lines`` — rather
    than the single ``metric→y`` slot :func:`resolve_semantic` handles.

    The caller supplies the measure refs (already split out of its role attrs) and
    which dimension refs fill the primary (``by``) / secondary (``series``)
    grouping. This builds **one** synthetic semantic query (``resolve_ref`` — the
    same path every chart uses), records it on ``ctx.semantic_refs`` for
    the pipeline, and returns the canonical result-column name for each measure so
    the component can slot it onto its role. This is exactly how a BI tool binds an
    OHLC/heatmap visual to several measures of a semantic model: N measures grouped
    by a dimension, each measure mapped to a visual role.

    Returns ``{query_name, by, series, columns: {ref: result_column},
    canon: callable, formats: {result_column: {...}}, model}``. Raises (surfaced as
    the component's inline error card) on an unknown model/measure/dimension or an
    illegal combination — e.g. a ``series`` second dimension with several measures
    (``resolve_ref``'s rule).
    """
    from dashdown.semantic import resolve_ref

    # `grain="month"` (literal, baked into the query identity) vs `grain={control}`
    # (interactive, read per-fetch) — the same attr convention resolve_semantic uses.
    grain_val, grain_is_ref = ref_or_literal(attrs, grain_key)
    ref = resolve_ref(
        ctx.semantic_models,
        ",".join(measures),
        by_ref,
        series_ref,
        grain=None if grain_is_ref else grain_val,
        grain_param=grain_val if grain_is_ref else None,
    )
    ctx.semantic_refs[ref.query_name] = ref
    handle = ctx.semantic_models[ref.model]

    def canon(r: str) -> str:
        """A measure ref (`sales.revenue` / bare `revenue`) → its result column."""
        part = r.split(".", 1)[1] if "." in r else r
        return handle.measure_lookup.get(part, part)

    return {
        "query_name": ref.query_name,
        "by": ref.by,
        "series": ref.series,
        "columns": {r: canon(r) for r in measures},
        "canon": canon,
        "formats": handle.measure_formats,
        "model": ref.model,
    }


def new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


def safe_json(obj: Any) -> str:
    """JSON for embedding inside HTML <script> blocks."""
    return json.dumps(obj, default=str).replace("</", "<\\/")


def esc(s: Any) -> str:
    return html.escape("" if s is None else str(s))
