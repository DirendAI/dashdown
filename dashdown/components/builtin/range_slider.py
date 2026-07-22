from __future__ import annotations

from dashdown.components.base import Component, RenderContext, register_component
from dashdown.components.builtin._util import (
    attr_float,
    attr_str,
    esc,
    filter_bar_marker,
    format_config,
    new_id,
    resolve_debounce,
    safe_json,
)
from dashdown.render.attrs import DataRef


def _num(v: float) -> str:
    """Render a config number without a trailing `.0` for whole values, so the
    JSON the client reads (and any author who inspects it) stays tidy."""
    f = float(v)
    return str(int(f)) if f.is_integer() else str(f)


@register_component("RangeSlider")
class RangeSlider(Component):
    """Numeric range filter — a dual-handle slider for a *between* bound on a
    numeric column (price, age, score, …).

    Like ``DateRange`` it owns **two** filter keys, a low/high pair stored in the
    page-level Alpine store under ``min_param`` / ``max_param`` (default
    ``{name}_min`` / ``{name}_max``). Each rides into SQL only through the
    context-aware ``_substitute_params`` — there is **no new injection surface**;
    the values are quoted string literals like every other filter.

    A handle resting on its track bound writes ``""`` (empty-means-all, the same
    convention a Dropdown/Toggle uses), so **guard each bound** exactly like the
    other filters — the empty case (wide-open slider, or the very first fetch
    before the control seeds) then shows everything instead of erroring on
    ``CAST('' AS DOUBLE)``:

        :::query name=products connector=main
        SELECT name, price FROM products
        WHERE ('${price_range_filter_min}' = ''
               OR price >= CAST(${price_range_filter_min} AS DOUBLE))
          AND ('${price_range_filter_max}' = ''
               OR price <= CAST(${price_range_filter_max} AS DOUBLE))
        :::

    Charts/tables that reference such a query re-fetch on change via the same
    ``queryUsesFilters`` path every other filter uses.

    Attributes:
    - name: Required. Base name for the two filter keys.
    - label: Optional. Display label (defaults to name; shown as the inline prefix).
    - min: Optional. Track minimum (default ``0``).
    - max: Optional. Track maximum (default ``100``).
    - step: Optional. Handle increment (default ``1``).
    - default: Optional. Initial ``[low, high]`` pair, e.g. ``default={[0, 10000]}``
      (array literal) or ``default="0,10000"`` (comma string). Defaults to the full
      ``[min, max]`` range. URL params still win over this (precedence URL > default).
    - min_param / max_param: Optional. URL/filter key names (default ``{name}_min`` /
      ``{name}_max``).
    - format / currency / decimals / locale: Optional. Format the readout values
      (same ``formatValue`` config the other components use), e.g. ``format="currency"``.
    - debounce: Optional. Quiet period (ms) after the last drag tick before the
      pair re-fetches data (the handles/readout still move instantly). Defaults to
      the project-wide ``filters.debounce`` (300 unless raised in dashdown.yaml).
    - url_sync: Optional. Sync the pair to the URL (default ``true``).
    - bar: Optional. Relocate this control into the page's top filter bar.
      Default is inline (renders where authored).

    Example:
        <RangeSlider name="price_range_filter" min={0} max={10000} step={50}
                     default={[0, 10000]} label="Price Range ($)" />
    """

    is_filter = True

    def filter_param_names(self, attrs) -> set[str]:
        # Writes derived min/max keys, never `name` itself (range_slider.js).
        name = attr_str(attrs, "name")
        if not name:
            return set()
        return {
            attr_str(attrs, "min_param", f"{name}_min"),
            attr_str(attrs, "max_param", f"{name}_max"),
        }

    def render(
        self, attrs, ctx: RenderContext, inner: str | None = None
    ) -> str:
        name = attr_str(attrs, "name")
        if not name:
            raise ValueError("RangeSlider requires a `name` attribute")

        label = attr_str(attrs, "label", name)
        lo_bound = attr_float(attrs, "min", 0.0)
        hi_bound = attr_float(attrs, "max", 100.0)
        if hi_bound <= lo_bound:
            raise ValueError(
                f"RangeSlider `max` ({hi_bound}) must be greater than `min` ({lo_bound})"
            )
        step = attr_float(attrs, "step", 1.0)
        if step <= 0:
            step = 1.0

        default_lo, default_hi = self._parse_default(
            attrs.get("default"), lo_bound, hi_bound
        )

        min_param = attr_str(attrs, "min_param", f"{name}_min")
        max_param = attr_str(attrs, "max_param", f"{name}_max")
        url_sync = attrs.get("url_sync", True)
        # Debounce the store write (data re-fetch) so a drag settles before firing;
        # the handles/readout stay live. Per-control `debounce=` wins, else the
        # project-wide `filters.debounce` default (see resolve_debounce).
        debounce = resolve_debounce(attrs, ctx)
        fmt = format_config(attrs)
        # Inline by default (renders where authored); `bar` relocates it into the
        # top filter row (read by filter_bar.js). See filter_bar_marker.
        filter_bar_attr = filter_bar_marker(attrs, ctx)

        cid = new_id("dashdown-rangeslider")

        config = {
            "name": name,
            "label": label,
            "min": lo_bound,
            "max": hi_bound,
            "step": step,
            "default_lo": default_lo,
            "default_hi": default_hi,
            "min_param": min_param,
            "max_param": max_param,
            "debounce": debounce,
            "url_sync": url_sync,
        }
        if fmt:
            config["format"] = fmt

        # Two overlaid native range inputs (no JS slider lib): the lo/hi handles
        # share one track, with a coloured fill between them. rangeSliderComponent
        # (range_slider.js) owns clamping, the readout, URL sync + the filter
        # store writes — the same minimal role dateRangeComponent plays. The
        # values live in the store as strings (uniform with every filter), so the
        # control binds explicitly rather than x-model-ing a number into the store.
        return (
            f'<div class="dashdown-range-slider dashdown-filter-pill" id="{cid}" '
            f'data-async-component="rangeslider" '
            f'data-config="{esc(safe_json(config))}" '
            f'data-name="{esc(name)}" '
            f'data-min-param="{esc(min_param)}" '
            f'data-max-param="{esc(max_param)}" '
            f'data-url-sync="{str(url_sync).lower()}" '
            f"{filter_bar_attr}"
            f"x-data=\"rangeSliderComponent('{esc(name)}')\" "
            f">"
            f'<span class="dashdown-filter-pill-label">{esc(label)}'
            f'<span class="dashdown-filter-pill-colon">:</span></span>'
            f'<div class="dashdown-range-control">'
            f'<span class="dashdown-range-readout" aria-hidden="true">'
            f'<span x-text="fmt(lo)"></span>'
            f'<span class="dashdown-range-readout-sep">–</span>'
            f'<span x-text="fmt(hi)"></span>'
            f"</span>"
            f'<div class="dashdown-range-track">'
            f'<div class="dashdown-range-fill" :style="fillStyle()"></div>'
            f'<input type="range" class="dashdown-range-input dashdown-range-lo" '
            f'min="{_num(lo_bound)}" max="{_num(hi_bound)}" step="{_num(step)}" '
            f'x-model.number="lo" @input="onLoInput" '
            f'aria-label="{esc(label)} minimum" '
            f':aria-valuetext="fmt(lo)" />'
            f'<input type="range" class="dashdown-range-input dashdown-range-hi" '
            f'min="{_num(lo_bound)}" max="{_num(hi_bound)}" step="{_num(step)}" '
            f'x-model.number="hi" @input="onHiInput" '
            f'aria-label="{esc(label)} maximum" '
            f':aria-valuetext="fmt(hi)" />'
            f"</div>"
            f"</div>"
            f"</div>"
        )

    @staticmethod
    def _parse_default(
        raw, lo_bound: float, hi_bound: float
    ) -> tuple[float, float]:
        """Resolve the `default` attr to a clamped ``(lo, hi)`` pair.

        Accepts an array literal (``default={[0, 10000]}`` → a Python ``list`` via
        the attrs parser), a comma string (``default="0,10000"``), or nothing
        (full ``[min, max]`` range). A malformed value silently falls back to the
        full range rather than erroring the whole page.
        """
        lo, hi = lo_bound, hi_bound
        parts: list = []
        if isinstance(raw, (list, tuple)):
            parts = list(raw)
        elif isinstance(raw, str) and raw.strip():
            parts = [p.strip() for p in raw.split(",")]
        elif isinstance(raw, DataRef):
            # `default={something}` that parsed as a ref (a bare identifier, not a
            # `[...]` list) isn't a meaningful default — ignore it.
            parts = []

        if len(parts) >= 2:
            try:
                lo = float(parts[0])
                hi = float(parts[1])
            except (TypeError, ValueError):
                lo, hi = lo_bound, hi_bound

        # Keep lo <= hi and both inside the track.
        lo = max(lo_bound, min(lo, hi_bound))
        hi = max(lo_bound, min(hi, hi_bound))
        if lo > hi:
            lo, hi = hi, lo
        return lo, hi
