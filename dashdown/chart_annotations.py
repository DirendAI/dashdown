"""Chart annotations for the ``explain`` affordance.

When a chart's explain ask carries a :class:`ChartContext`, the single LLM
completion is asked to return two parts: the usual Markdown commentary (with
inline ``[aN]`` refs) and a terminal fenced ```annotations`` block holding a
JSON array of *candidate* visual annotations (dashed threshold lines, range
bands, marked points…). This module owns that contract end to end:

- the per-chart-type **vocabulary** (anything the model invents is dropped);
- the prompt fragment that teaches the model the vocabulary and grounds it in
  the *actual* data domains (computed server-side from the full result);
- the parser that strips the fenced block out of the displayed answer;
- the **validator** — the restraint enforcement: every surviving annotation is
  checked against the full query result, capped at :data:`MAX_ANNOTATIONS`,
  and re-keyed to server-assigned ids;
- the ref injector that turns surviving ``[aN]`` tokens in the *rendered,
  sanitized* HTML into ``<abbr>`` chips (model text never becomes markup).

All annotation values are **data** (JSON shipped to the chart client), never
interpolated into SQL or HTML.

Named ``chart_annotations`` (not ``annotations``) deliberately: "annotation"
already means Cube's REST response-metadata block (see semantic_cube.py).
"""
from __future__ import annotations

import html as html_mod
import json
import logging
import re
from dataclasses import asdict, dataclass
from typing import Any

from dashdown.data.base import QueryResult

log = logging.getLogger(__name__)

#: Hard cap on annotations per answer, applied AFTER validation. Restraint is
#: enforced, not just prompted: an over-eager model still ships at most this
#: many marks.
MAX_ANNOTATIONS = 3

#: How far past the observed value domain a proposed number may sit and still
#: validate, as a fraction of the domain span. A threshold/target line a bit
#: above the max is legitimate ("Q3 target"); a hallucinated 5000 on a 0–100
#: chart is not.
DOMAIN_TOLERANCE = 0.15

#: Caps for the domain listings in the prompt fragment (token cost) and on
#: label length (chips + mark labels are meant to be short).
_MAX_PROMPT_CATEGORIES = 60
_MAX_PROMPT_SERIES = 20
_MAX_LABEL_LEN = 80

#: Which annotation types each chart type may carry. Chart types not listed
#: here have **no annotation vocabulary** — their explain prompt never asks
#: for annotations and their asks register without a chart_context:
#: commentary-only, exactly the pre-annotation behavior. That list is a
#: decision, not a gap (see docs/pages/ai/ask.md): radar, gauge, parallel and
#: themeriver have no ECharts mark support on their series types; sankey and
#: graph could outline a node, but the commentary naming it carries the same
#: information; sunburst, tree and treemap address nodes ambiguously (the same
#: name can recur across hierarchy levels). Faceted pies (``series=`` small
#: multiples) are excluded in :func:`build_chart_context` — their client
#: series indexes are patched per facet and a mark can't address one pie. The
#: other SVG geo maps (ChoroplethTime/ChoroplethFacets/BivariateMap) stay
#: commentary-only: their per-year facets / animation frames / two-metric
#: encoding give one static halo nothing stable to point at. Adding a type
#: here without a translator branch in static/components/annotations.js
#: (cartesian/ECharts) or the _geo.js annotation layer (SVG maps) would
#: validate marks the chart can't draw.
ANNOTATION_VOCAB: dict[str, frozenset[str]] = {
    "line": frozenset({"axis_line", "range", "point", "extremum"}),
    "bar": frozenset({"axis_line", "range", "extremum", "item"}),
    "scatter": frozenset({"axis_line", "range", "point"}),
    # OHLC: value-axis marks ground against the open/high/low/close columns
    # (carried on `extra`; `y` is None). `extremum` = highest high / lowest
    # low (client sets markPoint valueDim); `item` marks one session.
    "candlestick": frozenset({"axis_line", "range", "extremum", "item"}),
    # Matrix heatmap: `x`/`y` are BOTH category columns, the magnitude lives
    # in the `value` column (on `extra`). `item` addresses a cell (x AND y),
    # `extremum` the hottest/coldest cell — both drawn as a per-datum dashed
    # cell outline; extremum is resolved client-side from the live records.
    "heatmap": frozenset({"item", "extremum"}),
    # Distributions: a threshold line / band across the boxes is the natural
    # mark; `item` outlines one category's box. No `extremum` — the box
    # already draws its extremes. Violin's client x axis is a synthetic
    # numeric index axis, so it takes value-axis (y) marks only — axis="x"
    # candidates are dropped in validation.
    "boxplot": frozenset({"axis_line", "range", "item"}),
    "violin": frozenset({"axis_line", "range"}),
    # Calendar heatmap: `item` outlines one day cell, `extremum` the busiest/
    # quietest day (resolved client-side, same per-datum outline).
    "calendar": frozenset({"item", "extremum"}),
    # Combo carries two y-axes at different scales, so a free-coordinate `point`
    # (an explicit x+y the model picks) can't be grounded against one domain —
    # it's excluded. `axis_line`/`range` validate against the LEFT axis only
    # (see _value_axis_columns) since they draw on series 0's axis, and
    # `extremum` is per-series (ECharts recomputes it on that series' own axis).
    "combo": frozenset({"axis_line", "range", "extremum"}),
    # Part-of-whole charts: one addressable mark — call out a slice/stage.
    # Rendered client-side as a per-datum dashed outline (+ label callout on
    # pie), config-driven so it survives the setOption(notMerge) funnel.
    "pie": frozenset({"item"}),
    "funnel": frozenset({"item"}),
    # MapChart (ECharts choropleth): call out one region by feature name,
    # validated against the query's location column.
    "map": frozenset({"geo_item"}),
    # The hand-drawn SVG geo maps: ring one location (halo + leader-line label
    # in _geo.js::drawGeoAnnotations). Validated against the query's *join id*
    # column, normalized like the client's normalizeId — and against the
    # active year slice, since that's the frame the viewer sees.
    "bubble-map": frozenset({"geo_item"}),
    "dot-density-map": frozenset({"geo_item"}),
}

#: Chart types whose "x" is semantically a location. ``map`` addresses
#: features by GeoJSON name (the MapChart join); the SVG maps address them by
#: the normalized join id (``_dashdownId`` client-side).
_GEO_TYPES = frozenset({"map", "bubble-map", "dot-density-map"})
_SVG_GEO_TYPES = frozenset({"bubble-map", "dot-density-map"})

#: Chart types that draw a single addressable series: a stray ``series`` field
#: the model tacked onto an item/extremum there is ignored rather than fatal
#: (the client ignores it too), matching the original pie/funnel posture.
_SINGLE_SERIES_TYPES = frozenset(
    {"pie", "funnel", "candlestick", "heatmap", "boxplot", "calendar"}
)

#: Config keys beyond x/y/series that shape the model's grounding and the
#: validator's domains. ``_chart_placeholder`` lifts these from the chart
#: config into ``ChartContext.extra`` — joining the ask id, so a changed
#: value column busts the answer cache (correctly). Types not listed thread
#: no extra keys, keeping their existing ask ids byte-identical.
CONTEXT_EXTRA_KEYS: dict[str, tuple[str, ...]] = {
    "candlestick": ("open", "high", "low", "close"),
    "heatmap": ("value",),
}

#: The [aN] ref token the model places in its commentary text.
_REF_TOKEN_RE = re.compile(r"\[(a\d+)\]")

#: The terminal fenced block carrying the candidate JSON. Backtick run length
#: is matched (``` or longer), the info string must be exactly `annotations`.
_ANNOTATION_FENCE_RE = re.compile(
    r"(?:^|\n)[ \t]*(`{3,})[ \t]*annotations[ \t]*\n(.*?)\n[ \t]*\1[ \t]*(?=\n|$)",
    re.DOTALL,
)


# --------------------------------------------------------------------------- #
# Chart context — the frozen shape snapshot an explain AskDef carries
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class ChartContext:
    """The resolved shape of the chart an explain ask belongs to.

    Frozen + hashable so it can sit on the (frozen) AskDef and join the
    ``ask_id`` hash: a changed chart shape means a different prompt and
    different validation, so it must mint a new id (busting the answer cache
    is correct there). ``None`` on an AskDef means plain commentary — a
    ``<Ask />`` block, a chart type with no vocabulary, or a chart on a
    ``live`` query (annotations are off there by design: the data changes
    under the marks every poll interval).
    """

    chart_type: str
    x: str | None = None
    #: The value column(s) — comma-separated for multi-metric charts (and the
    #: full bars+lines set for combo).
    y: str | None = None
    #: A second-dimension split column (``series=``), when set.
    series_by: str | None = None
    horizontal: bool = False
    stacked: bool = False
    #: Extra shape keys that change what the model is told (sorted key/value
    #: pairs, comma-joined values) — combo carries bars/lines/right_axis here.
    extra: tuple[tuple[str, str], ...] = ()

    def canonical(self) -> str:
        """Deterministic serialization for the ``ask_id`` hash."""
        return json.dumps(asdict(self), sort_keys=True, separators=(",", ":"))

    @property
    def y_columns(self) -> list[str]:
        return [c.strip() for c in (self.y or "").split(",") if c.strip()]


def build_chart_context(
    chart_type: str,
    *,
    x: str | None = None,
    y: str | None = None,
    series_by: str | None = None,
    horizontal: bool = False,
    stacked: bool = False,
    extra: tuple[tuple[str, str], ...] = (),
) -> ChartContext | None:
    """A :class:`ChartContext` for chart types with an annotation vocabulary,
    else ``None`` (the ask registers as plain commentary and keeps the exact
    pre-annotation id)."""
    if chart_type not in ANNOTATION_VOCAB:
        return None
    # A pie with `series=` renders faceted small multiples: the client patches
    # series indexes/centers per facet, so an `item` mark can't address one
    # pie. Those explains stay commentary-only.
    if chart_type == "pie" and series_by:
        return None
    return ChartContext(
        chart_type=chart_type,
        x=x or None,
        y=y or None,
        series_by=series_by or None,
        horizontal=bool(horizontal),
        stacked=bool(stacked),
        extra=tuple(sorted((str(k), str(v)) for k, v in extra)),
    )


# --------------------------------------------------------------------------- #
# Result introspection (shared by the prompt fragment and the validator)
# --------------------------------------------------------------------------- #
def cell_text(v: Any) -> str:
    """A cell rendered the way the model sees it in the prompt payload
    (see ``llm.format_result_for_llm``) — the one normalization category
    matching must agree with, or grounded candidates would fail validation."""
    if v is None:
        return "NULL"
    if hasattr(v, "isoformat"):
        return v.isoformat()
    return str(v)


def _as_number(v: Any) -> float | None:
    """Coerce a candidate value to float; None for non-numeric. Bools are not
    numbers here (JSON ``true`` as a threshold is model noise, not a value)."""
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        try:
            return float(v.strip())
        except ValueError:
            return None
    return None


def _column_index(result: QueryResult, name: str | None) -> int | None:
    if not name:
        return None
    try:
        return result.columns.index(name)
    except ValueError:
        return None


def _numeric_domain(result: QueryResult, indexes: list[int]) -> tuple[float, float] | None:
    """(min, max) over the numeric cells of ``indexes``, None when nothing
    numeric exists (then no numeric annotation can validate — fail closed)."""
    lo: float | None = None
    hi: float | None = None
    for row in result.rows:
        for i in indexes:
            if i >= len(row):
                continue
            n = _as_number(row[i])
            if n is None:
                continue
            lo = n if lo is None else min(lo, n)
            hi = n if hi is None else max(hi, n)
    if lo is None or hi is None:
        return None
    return lo, hi


def _in_domain(value: float, domain: tuple[float, float]) -> bool:
    lo, hi = domain
    span = hi - lo
    pad = DOMAIN_TOLERANCE * (span if span > 0 else max(abs(hi), 1.0))
    return (lo - pad) <= value <= (hi + pad)


def _x_categories(result: QueryResult, x_idx: int) -> list[str]:
    """Distinct x values in first-seen order, normalized like the prompt."""
    seen: dict[str, None] = {}
    for row in result.rows:
        if x_idx < len(row):
            seen.setdefault(cell_text(row[x_idx]), None)
    return list(seen)


def _series_names(result: QueryResult, ctx: ChartContext) -> set[str]:
    """Every name a candidate's ``series`` field may carry: the distinct
    values of a ``series=`` split column, plus the metric column names (how
    multi-metric and combo series are addressed client-side)."""
    names: set[str] = set(ctx.y_columns)
    s_idx = _column_index(result, ctx.series_by)
    if s_idx is not None:
        for row in result.rows:
            if s_idx < len(row) and row[s_idx] is not None:
                names.add(cell_text(row[s_idx]))
    return names


def _x_is_numeric(chart_type: str) -> bool:
    # Scatter is the one vocabulary chart whose x axis is a numeric domain;
    # line/bar/combo x is a category (often time rendered as strings).
    return chart_type == "scatter"


#: Leading-integer prefix — the slice of JS ``parseInt`` semantics geo join
#: ids actually exercise ("004", 4 and "4" all mean Afghanistan).
_INT_PREFIX_RE = re.compile(r"\s*[+-]?\d+")


def normalize_geo_id(value: Any) -> str | None:
    """Server twin of ``_geo.js::normalizeId`` — the canonical join key the
    client stamps on features as ``_dashdownId``. Both sides of a geo_item
    match (candidate and data) normalize through here, so "004" in the query
    matches the model echoing 4."""
    if value is None:
        return None
    text = cell_text(value).strip()
    if not text:
        return None
    m = _INT_PREFIX_RE.match(text)
    if m:
        return str(int(m.group()))
    return text


def _year_sliced(result: QueryResult, ctx: ChartContext) -> QueryResult:
    """The rows an SVG geo map actually draws: with a ``year`` column, only
    the configured ``year_value`` (else the latest year) — mirroring the
    client's ``sliceYear``. Grounding and validation both run on this slice,
    so a location present only in older years can't earn a halo the current
    frame has nothing to draw on. Non-geo charts pass through untouched."""
    if ctx.chart_type not in _SVG_GEO_TYPES:
        return result
    extra = dict(ctx.extra)
    idx = _column_index(result, extra.get("year"))
    if idx is None:
        return result
    target = (extra.get("year_value") or "").strip()
    if not target:
        latest: tuple[float, str] | None = None
        for row in result.rows:
            v = row[idx] if idx < len(row) else None
            n = _as_number(v)
            if n is None:
                continue
            if latest is None or n > latest[0]:
                latest = (n, cell_text(v))
        if latest is None:
            return result
        target = latest[1]
    rows = [r for r in result.rows if idx < len(r) and cell_text(r[idx]) == target]
    return QueryResult(columns=result.columns, rows=rows)


def _value_axis_columns(ctx: ChartContext) -> list[str]:
    """The value column(s) whose numeric domain grounds (in the prompt) and
    validates the value-axis marks — ``axis_line``/``range`` on ``y``.

    For a **combo** chart this is only the LEFT/primary axis columns: those
    marks draw on series 0, which rides the left axis, so validating a
    right-axis magnitude (a different scale) against them would place the mark
    against the wrong ruler. ``extremum`` is unaffected — it carries no
    coordinate and ECharts recomputes it per series, on that series' own axis.

    A **candlestick**'s prices live in the open/high/low/close columns (``y``
    is None) and a matrix **heatmap**'s magnitude in its ``value`` column
    (``y`` is the row *category* column) — both carried on ``extra``. Every
    other chart type uses all its y columns.
    """
    extra = dict(ctx.extra)
    if ctx.chart_type == "candlestick":
        cols = [extra[k] for k in ("open", "high", "low", "close") if extra.get(k)]
        return cols or ctx.y_columns
    if ctx.chart_type == "heatmap":
        value = extra.get("value")
        return [value] if value else []
    cols = ctx.y_columns
    if ctx.chart_type != "combo":
        return cols
    right = {
        c.strip()
        for c in dict(ctx.extra).get("right_axis", "").split(",")
        if c.strip()
    }
    left = [c for c in cols if c not in right]
    # A combo with everything on the right axis is degenerate; fall back to all.
    return left or cols


# --------------------------------------------------------------------------- #
# Prompt fragment
# --------------------------------------------------------------------------- #
_TYPE_SHAPES = {
    "axis_line": (
        '{"type": "axis_line", "axis": "x" or "y", "value": <value>, '
        '"label": "<short label>"} — a dashed reference line at that value '
        "(axis \"x\" = the category/x column, \"y\" = the measure)"
    ),
    "range": (
        '{"type": "range", "axis": "x" or "y", "from": <value>, "to": <value>, '
        '"label": "<short label>"} — a shaded band between two values'
    ),
    "point": (
        '{"type": "point", "x": <x value>, "y": <number>, '
        '"series": "<series>" (optional), "label": "<short label>"} — a marker '
        "at one data coordinate"
    ),
    "extremum": (
        '{"type": "extremum", "kind": "max" or "min", '
        '"series": "<series>" (optional), "label": "<short label>"} — marks the '
        "highest/lowest point of a series"
    ),
    "item": (
        '{"type": "item", "x": "<category>", "series": "<series>" (optional), '
        '"label": "<short label>"} — highlights that category\'s bar'
    ),
    "geo_item": (
        '{"type": "geo_item", "name": "<location>", "label": "<short label>"} '
        "— highlights that region on the map"
    ),
}

#: Pie/funnel reuse the ``item`` type but draw a single series, so their prompt
#: shape omits the ``series`` field (a stray one is ignored server-side, but
#: advertising it just invites the model to waste a slot).
_ITEM_SHAPE_PART_OF_WHOLE = (
    '{"type": "item", "x": "<category>", "label": "<short label>"} '
    "— highlights that slice or stage"
)

#: Single-series chart types where ``item``/``extremum`` mean something more
#: specific than "highlights that category's bar" — teach the model each
#: type's own reading (and omit the ``series`` field, as for pie/funnel).
_ITEM_SHAPES_BY_CHART = {
    "pie": _ITEM_SHAPE_PART_OF_WHOLE,
    "funnel": _ITEM_SHAPE_PART_OF_WHOLE,
    "candlestick": (
        '{"type": "item", "x": "<date/category>", "label": "<short label>"} '
        "— marks that session"
    ),
    "heatmap": (
        '{"type": "item", "x": "<x category>", "y": "<y category>", '
        '"label": "<short label>"} — outlines that cell'
    ),
    "boxplot": (
        '{"type": "item", "x": "<category>", "label": "<short label>"} '
        "— outlines that category's box"
    ),
    "calendar": (
        '{"type": "item", "x": "<date from the data>", '
        '"label": "<short label>"} — outlines that day'
    ),
}
_EXTREMUM_SHAPES_BY_CHART = {
    "candlestick": (
        '{"type": "extremum", "kind": "max" or "min", "label": "<short label>"} '
        "— marks the session with the highest high (max) or lowest low (min)"
    ),
    "heatmap": (
        '{"type": "extremum", "kind": "max" or "min", "label": "<short label>"} '
        "— outlines the highest/lowest cell"
    ),
    "calendar": (
        '{"type": "extremum", "kind": "max" or "min", "label": "<short label>"} '
        "— outlines the highest/lowest day"
    ),
}

#: Violin's client x axis is a synthetic numeric index axis — only value-axis
#: (y) marks can draw there, so its prompt never offers axis "x" (validation
#: drops such candidates anyway).
_AXIS_LINE_SHAPE_Y_ONLY = (
    '{"type": "axis_line", "axis": "y", "value": <number>, '
    '"label": "<short label>"} — a dashed horizontal reference line at that value'
)
_RANGE_SHAPE_Y_ONLY = (
    '{"type": "range", "axis": "y", "from": <number>, "to": <number>, '
    '"label": "<short label>"} — a shaded horizontal band between two values'
)

#: The SVG geo maps join on an id column (ISO codes, not names) — teach the
#: model to echo the ids the data carries. With several metrics the optional
#: ``metric`` field scopes the halo to one of them (it only shows while that
#: metric is toggled active).
_GEO_ITEM_SHAPE_SVG = (
    '{"type": "geo_item", "id": "<location id from the data>", '
    '"label": "<short label>"} — rings that location on the map'
)
_GEO_ITEM_SHAPE_SVG_MULTI = (
    '{"type": "geo_item", "id": "<location id from the data>", '
    '"metric": "<metric column>" (optional), "label": "<short label>"} '
    "— rings that location on the map (shown only while that metric is active)"
)


def _shape_summary(ctx: ChartContext) -> str:
    # A map's x/y are semantically location/value — name them that way so the
    # model reasons about regions, not axes.
    is_map = ctx.chart_type in _GEO_TYPES
    bits = [f"a {ctx.chart_type} chart"]
    if ctx.x:
        bits.append(f"{'location' if is_map else 'x'} column '{ctx.x}'")
    ys = ctx.y_columns
    if ys:
        if is_map:
            label = "value columns" if len(ys) > 1 else "value column"
        else:
            label = "y columns" if len(ys) > 1 else "y column"
        bits.append(f"{label} {', '.join(repr(c) for c in ys)}")
    if ctx.series_by:
        bits.append(f"split into one series per '{ctx.series_by}' value")
    if ctx.horizontal:
        bits.append("drawn horizontally")
    if ctx.stacked:
        bits.append("stacked")
    for key, value in ctx.extra:
        if value:
            bits.append(f"{key}: {value}")
    return ", ".join(bits)


def annotation_instructions(ctx: ChartContext, result: QueryResult) -> str:
    """The prompt fragment appended to a chart-context ask: the vocabulary for
    this chart type, the *actual* data domains (computed from the full result,
    not the row-capped payload the model sees — so candidates are grounded and
    validation passes more often), the restraint instruction, and the output
    protocol (terminal fenced block + ``[aN]`` refs)."""
    allowed = sorted(ANNOTATION_VOCAB.get(ctx.chart_type, frozenset()))
    is_geo = ctx.chart_type in _GEO_TYPES
    # An SVG geo map draws one year frame at a time — ground the model (and
    # the validator, which slices identically) in the frame the viewer sees.
    result = _year_sliced(result, ctx)
    lines = [
        "You may also propose visual annotations to draw on the chart "
        f"({_shape_summary(ctx)}).",
    ]

    x_idx = _column_index(result, ctx.x)
    if x_idx is not None:
        if _x_is_numeric(ctx.chart_type):
            x_domain = _numeric_domain(result, [x_idx])
            if x_domain is not None:
                lines.append(f"X values range from {x_domain[0]:g} to {x_domain[1]:g}.")
        else:
            cats = _x_categories(result, x_idx)
            shown = cats[:_MAX_PROMPT_CATEGORIES]
            suffix = (
                f" (first {len(shown)} of {len(cats)})" if len(cats) > len(shown) else ""
            )
            if ctx.chart_type in _SVG_GEO_TYPES:
                cats_label = "Location ids"
            elif is_geo:
                cats_label = "Location names"
            else:
                cats_label = "X categories"
            lines.append(f"{cats_label}{suffix}: {', '.join(shown)}")
    # A matrix heatmap addresses cells by BOTH category axes — list the y
    # categories too, or the model can't ground an `item`'s `y` field.
    if ctx.chart_type == "heatmap":
        y_cat_idx = _column_index(result, ctx.y)
        if y_cat_idx is not None:
            y_cats = _x_categories(result, y_cat_idx)
            shown_y = y_cats[:_MAX_PROMPT_CATEGORIES]
            suffix = (
                f" (first {len(shown_y)} of {len(y_cats)})"
                if len(y_cats) > len(shown_y)
                else ""
            )
            lines.append(f"Y categories{suffix}: {', '.join(shown_y)}")
    y_idx = [
        i
        for i in (_column_index(result, c) for c in _value_axis_columns(ctx))
        if i is not None
    ]
    y_domain = _numeric_domain(result, y_idx) if y_idx else None
    if y_domain is not None:
        if is_geo:
            val_label = "Values"
        elif ctx.chart_type in ("heatmap", "calendar"):
            val_label = "Cell values"
        elif ctx.chart_type == "candlestick":
            val_label = "Prices"
        else:
            val_label = "Y values"
        lines.append(f"{val_label} range from {y_domain[0]:g} to {y_domain[1]:g}.")
    series = sorted(_series_names(result, ctx))
    if len(series) > 1:
        shown_series = series[:_MAX_PROMPT_SERIES]
        # A geo map's y columns are toggleable metrics, not coloured series —
        # name them the way the geo_item `metric` field addresses them.
        series_label = "Metric columns" if is_geo else "Series names"
        lines.append(f"{series_label}: {', '.join(shown_series)}")

    lines.append("Allowed annotation types (JSON objects):")
    for t in allowed:
        shape = _TYPE_SHAPES[t]
        # Single-series chart types get their own item/extremum reading (and
        # don't advertise the `series` field).
        if t == "item":
            shape = _ITEM_SHAPES_BY_CHART.get(ctx.chart_type, shape)
        if t == "extremum":
            shape = _EXTREMUM_SHAPES_BY_CHART.get(ctx.chart_type, shape)
        # Violin draws on a synthetic numeric x axis — value-axis marks only.
        if ctx.chart_type == "violin":
            if t == "axis_line":
                shape = _AXIS_LINE_SHAPE_Y_ONLY
            elif t == "range":
                shape = _RANGE_SHAPE_Y_ONLY
        # The SVG geo maps join on ids, not names.
        if t == "geo_item" and ctx.chart_type in _SVG_GEO_TYPES:
            shape = (
                _GEO_ITEM_SHAPE_SVG_MULTI
                if len(ctx.y_columns) > 1
                else _GEO_ITEM_SHAPE_SVG
            )
        lines.append(f"- {shape}")
    lines.append(
        "Propose at most 3 annotations, only where a mark genuinely helps a "
        "viewer; an empty list is the right answer for an unremarkable chart. "
        "Values must come from the data above — never invent numbers or "
        "categories."
    )
    # The worked example must use a type from THIS chart's vocabulary — an
    # off-vocabulary example teaches the model a shape validation will drop.
    if "extremum" in allowed:
        example = '[{"type": "extremum", "kind": "max", "label": "June peak"}]'
    elif "item" in allowed:
        example = '[{"type": "item", "x": "June", "label": "Standout"}]'
    elif "geo_item" in allowed:
        if ctx.chart_type in _SVG_GEO_TYPES:
            example = '[{"type": "geo_item", "id": "276", "label": "Top market"}]'
        else:
            example = (
                '[{"type": "geo_item", "name": "Germany", "label": "Top market"}]'
            )
    else:
        example = (
            '[{"type": "axis_line", "axis": "y", "value": 100, "label": "Target"}]'
        )
    lines.append(
        "Reference each annotation from your commentary with [a1], [a2], … in "
        "array order, placed immediately after the phrase it supports "
        '(e.g. "Revenue peaked in June [a1]."). Then end your answer with '
        "exactly one fenced code block labeled `annotations` containing the "
        "JSON array (or [] for none):\n"
        "```annotations\n"
        f"{example}\n"
        "```"
    )
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# Answer parsing
# --------------------------------------------------------------------------- #
def split_annotated_answer(raw: str) -> tuple[str, list[dict]]:
    """Split the model's raw answer into ``(commentary_md, candidates)``.

    The fenced ```annotations`` block is stripped from the commentary even
    when its JSON is garbled — a broken fence must degrade to commentary-only,
    never show raw JSON to the viewer. Missing fence → ``(raw, [])``.
    """
    candidates: list[dict] = []
    matches = list(_ANNOTATION_FENCE_RE.finditer(raw or ""))
    if not matches:
        return (raw or "").strip(), []
    # The protocol says one terminal block; if the model emitted several, the
    # last one wins (closest to "terminal") and all are stripped from display.
    last = matches[-1]
    try:
        parsed = json.loads(last.group(2))
    except json.JSONDecodeError:
        log.debug("chart annotations fence did not parse; commentary-only")
        parsed = None
    if isinstance(parsed, list):
        candidates = [c for c in parsed if isinstance(c, dict)]
    commentary = _ANNOTATION_FENCE_RE.sub("", raw).strip()
    return commentary, candidates


#: The space-before-punctuation gap a removed inline ``[aN]`` token strands
#: ("June [a1]." → "June ."). Shared by :func:`strip_ref_tokens` and
#: :func:`inject_refs` so the two never drift on the punctuation set. Kept to
#: just this — no run-of-spaces collapse — so it is safe on rendered HTML too,
#: where a doubled space can be significant (e.g. inside a ``<pre>`` block).
_PUNCT_GAP_RE = re.compile(r"[ \t]+([.,;:!?)])")


def strip_ref_tokens(commentary_md: str) -> str:
    """Remove every ``[aN]`` token from the replayable plain text. The
    typewriter replay shows raw text — chips only exist in the rendered HTML,
    so tokens would read as literal ``[a1]`` noise while typing."""
    text = _PUNCT_GAP_RE.sub(r"\1", _REF_TOKEN_RE.sub("", commentary_md))
    # Plain replay text (never HTML): also collapse the doubled space the token
    # itself leaves behind ("June  July" → "June July").
    return re.sub(r"[ \t]{2,}", " ", text)


# --------------------------------------------------------------------------- #
# Validation — the restraint enforcement
# --------------------------------------------------------------------------- #
def _clean_label(candidate: dict) -> str:
    label = candidate.get("label")
    if not isinstance(label, str):
        return ""
    return label.strip()[:_MAX_LABEL_LEN]


def _clean_series(
    candidate: dict, valid_series: set[str]
) -> tuple[str | None, bool]:
    """Return ``(series, ok)``. A missing/blank series is fine (whole chart);
    a named one must exist — a mark pointed at a series the chart doesn't
    draw would render against nothing."""
    raw = candidate.get("series")
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        return None, True
    name = cell_text(raw).strip()
    if name in valid_series:
        return name, True
    return None, False


def validate_annotations(
    candidates: list[dict], result: QueryResult, ctx: ChartContext
) -> list[dict]:
    """Normalize + validate the model's candidates against the **full** query
    result; only survivors ship to the client.

    - type must be in this chart type's vocabulary; unknown fields are dropped
      (the output carries only the whitelisted shape per type);
    - category values must exist in the result's x column (normalized the same
      way the prompt payload renders cells);
    - numeric values/ranges must fall within (or near — :data:`DOMAIN_TOLERANCE`)
      the observed domain;
    - ``extremum`` keeps only the model's *intent* (kind + series): any
      coordinates it supplied are discarded, and the client's ECharts
      ``markPoint {type:'max'|'min'}`` recomputes the position on the live
      records — so the mark stays correct under filter drift;
    - ``series`` must name a real series (split value or metric column);
    - capped at :data:`MAX_ANNOTATIONS`; survivors are renumbered ``a1..aN``
      and carry a private ``_ref`` key (their position in the model's array)
      that :func:`inject_refs` consumes to re-associate ``[aN]`` text tokens.

    Invalid candidates are dropped silently (logged at debug) — validation
    failures degrade to fewer/zero marks, never an error card.
    """
    vocab = ANNOTATION_VOCAB.get(ctx.chart_type, frozenset())
    if not vocab or not isinstance(candidates, list):
        return []

    # SVG geo maps validate against the year frame the viewer sees (the same
    # slice annotation_instructions grounded the model in).
    result = _year_sliced(result, ctx)

    x_idx = _column_index(result, ctx.x)
    x_numeric = _x_is_numeric(ctx.chart_type)
    categories = (
        set(_x_categories(result, x_idx)) if (x_idx is not None and not x_numeric) else set()
    )
    # A matrix heatmap addresses cells by BOTH category axes: its `item`
    # carries a `y` validated against the row-category column.
    y_categories: set[str] = set()
    if ctx.chart_type == "heatmap":
        y_cat_idx = _column_index(result, ctx.y)
        if y_cat_idx is not None:
            y_categories = set(_x_categories(result, y_cat_idx))
    # Normalized-id → row map for the SVG geo maps: geo_item candidates match
    # through the same normalization the client applies (_dashdownId), and the
    # matched row backs the has-a-positive-value check (a location with no
    # drawable symbol shouldn't earn a halo).
    geo_rows: dict[str, Any] = {}
    if ctx.chart_type in _SVG_GEO_TYPES and x_idx is not None:
        for row in result.rows:
            if x_idx < len(row):
                norm = normalize_geo_id(row[x_idx])
                if norm is not None:
                    geo_rows.setdefault(norm, row)
    x_domain = (
        _numeric_domain(result, [x_idx]) if (x_idx is not None and x_numeric) else None
    )
    y_idx = [
        i
        for i in (_column_index(result, c) for c in _value_axis_columns(ctx))
        if i is not None
    ]
    y_domain = _numeric_domain(result, y_idx) if y_idx else None
    valid_series = _series_names(result, ctx)

    def _domain_for(axis: str) -> tuple[float, float] | None:
        return x_domain if axis == "x" else y_domain

    survivors: list[dict] = []
    for position, candidate in enumerate(candidates, start=1):
        if len(survivors) >= MAX_ANNOTATIONS:
            break
        if not isinstance(candidate, dict):
            continue
        a_type = candidate.get("type")
        if a_type not in vocab:
            log.debug("dropping annotation with type %r on %s", a_type, ctx.chart_type)
            continue
        out: dict[str, Any] = {
            "type": a_type,
            "label": _clean_label(candidate),
            "_ref": f"a{position}",
        }

        if a_type == "axis_line":
            axis = candidate.get("axis")
            if axis not in ("x", "y"):
                continue
            # Violin's client x axis is a synthetic numeric index axis — a
            # category mark has nothing to land on there.
            if axis == "x" and ctx.chart_type == "violin":
                continue
            value = candidate.get("value")
            if axis == "x" and not x_numeric:
                text = cell_text(value).strip()
                if text not in categories:
                    continue
                out.update(axis="x", value=text)
            else:
                n = _as_number(value)
                domain = _domain_for(axis)
                if n is None or domain is None or not _in_domain(n, domain):
                    continue
                out.update(axis=axis, value=n)

        elif a_type == "range":
            axis = candidate.get("axis")
            if axis not in ("x", "y"):
                continue
            if axis == "x" and ctx.chart_type == "violin":
                continue
            if axis == "x" and not x_numeric:
                lo_text = cell_text(candidate.get("from")).strip()
                hi_text = cell_text(candidate.get("to")).strip()
                if lo_text not in categories or hi_text not in categories:
                    continue
                out.update(axis="x", **{"from": lo_text, "to": hi_text})
            else:
                lo = _as_number(candidate.get("from"))
                hi = _as_number(candidate.get("to"))
                domain = _domain_for(axis)
                if lo is None or hi is None or domain is None:
                    continue
                if lo > hi:
                    lo, hi = hi, lo
                if not (_in_domain(lo, domain) and _in_domain(hi, domain)):
                    continue
                out.update(axis=axis, **{"from": lo, "to": hi})

        elif a_type == "point":
            y_val = _as_number(candidate.get("y"))
            if y_val is None or y_domain is None or not _in_domain(y_val, y_domain):
                continue
            if x_numeric:
                x_val = _as_number(candidate.get("x"))
                if x_val is None or x_domain is None or not _in_domain(x_val, x_domain):
                    continue
                out.update(x=x_val, y=y_val)
            else:
                x_text = cell_text(candidate.get("x")).strip()
                if x_text not in categories:
                    continue
                out.update(x=x_text, y=y_val)
            series, ok = _clean_series(candidate, valid_series)
            if not ok:
                continue
            if series:
                out["series"] = series

        elif a_type == "extremum":
            kind = candidate.get("kind")
            if kind not in ("max", "min") or y_domain is None:
                continue
            out["kind"] = kind
            # Single-series charts (candlestick, heatmap, calendar): a stray
            # `series` is ignored, not fatal — same posture as pie/funnel item.
            if ctx.chart_type not in _SINGLE_SERIES_TYPES:
                series, ok = _clean_series(candidate, valid_series)
                if not ok:
                    continue
                if series:
                    out["series"] = series

        elif a_type == "item":
            x_text = cell_text(candidate.get("x")).strip()
            if x_text not in categories:
                continue
            out["x"] = x_text
            # A heatmap cell is addressed by BOTH category axes.
            if ctx.chart_type == "heatmap":
                y_text = cell_text(candidate.get("y")).strip()
                if y_text not in y_categories:
                    continue
                out["y"] = y_text
            # `series` addresses one of several series; single-series charts
            # (pie, funnel, candlestick, heatmap, boxplot, calendar) have just
            # one, so ignore a series the model tacked on there rather than
            # dropping the whole mark over it (the client ignores it too).
            if ctx.chart_type not in _SINGLE_SERIES_TYPES:
                series, ok = _clean_series(candidate, valid_series)
                if not ok:
                    continue
                if series:
                    out["series"] = series

        elif a_type == "geo_item":
            # MapChart's prompt teaches `name`, the SVG geo maps teach `id`;
            # accept either spelling. Either way it must address a location
            # the (year-sliced) query returned.
            raw = candidate.get("name")
            if raw is None or (isinstance(raw, str) and not raw.strip()):
                raw = candidate.get("id")
            if ctx.chart_type in _SVG_GEO_TYPES:
                norm = normalize_geo_id(raw)
                row = geo_rows.get(norm) if norm is not None else None
                if row is None:
                    continue
                # An optional `metric` scopes the halo to one toggleable
                # metric; an unknown one is model noise — drop the field, not
                # the mark (the halo then shows on every metric).
                metric = candidate.get("metric")
                metric = metric.strip() if isinstance(metric, str) else ""
                metric_cols = (
                    [metric] if metric in ctx.y_columns else ctx.y_columns
                )
                values = (
                    _as_number(row[i])
                    for i in (_column_index(result, c) for c in metric_cols)
                    if i is not None and i < len(row)
                )
                if not any(v is not None and v > 0 for v in values):
                    continue  # nothing drawn there — no symbol to ring
                out["name"] = norm
                if metric in ctx.y_columns:
                    out["metric"] = metric
            else:
                name_text = cell_text(raw).strip() if raw is not None else ""
                if not name_text or name_text not in categories:
                    continue
                out["name"] = name_text

        survivors.append(out)

    for i, annotation in enumerate(survivors, start=1):
        annotation["id"] = f"a{i}"
    return survivors


# --------------------------------------------------------------------------- #
# Ref chips
# --------------------------------------------------------------------------- #
def inject_refs(html: str, annotations: list[dict]) -> str:
    """Replace surviving ``[aN]`` tokens in the *server-rendered, sanitized*
    HTML with ``<abbr>`` ref chips, and strip orphaned tokens (refs whose
    annotation didn't survive validation).

    The chip is built entirely by our code from validated data — every
    attribute value is escaped, so the model's label text never becomes
    markup. Consumes each annotation's private ``_ref`` key (the model-side
    numbering the tokens use, before survivors were renumbered).
    """
    by_ref: dict[str, dict] = {}
    for annotation in annotations:
        ref = annotation.pop("_ref", None)
        if ref:
            by_ref[ref] = annotation

    def _replace(m: re.Match[str]) -> str:
        annotation = by_ref.get(m.group(1))
        if annotation is None:
            return ""  # orphan: the mark it pointed at didn't survive
        number = annotation["id"][1:]  # "a2" -> "2"
        title = html_mod.escape(annotation.get("label") or "", quote=True)
        anno_id = html_mod.escape(annotation["id"], quote=True)
        return (
            f'<abbr class="dashdown-anno-ref" data-anno-id="{anno_id}" '
            f'title="{title}" tabindex="0">{number}</abbr>'
        )

    out = _REF_TOKEN_RE.sub(_replace, html)
    # Orphan removal can leave "June ." style gaps — tidy with the same
    # punctuation-gap rule strip_ref_tokens uses (no space-run collapse, so a
    # <pre> block's significant whitespace in the commentary survives).
    return _PUNCT_GAP_RE.sub(r"\1", out)
