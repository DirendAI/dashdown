from __future__ import annotations

from dashdown.components.base import Component, RenderContext, register_component
from dashdown.components.builtin._util import (
    attr_float,
    attr_str,
    esc,
    filter_bar_marker,
    format_config,
    new_id,
    safe_json,
)


def _num(v: float) -> str:
    """Render a config number without a trailing ``.0`` for whole values."""
    f = float(v)
    return str(int(f)) if f.is_integer() else str(f)


@register_component("Slider")
class Slider(Component):
    """Single-value numeric **threshold** filter — one handle on a track for a
    ``min rating ≥``, ``price ≤``, ``top N`` style bound. The one-handled sibling
    of ``<RangeSlider>`` (which carries a low/high pair).

    The value is stored as a **string** under ``filters[name]`` like every other
    filter, so SQL reads it with ``${name}`` through the context-aware
    ``_substitute_params`` — no new injection surface. Guard the comparison so a
    missing value (the brief moment before the control seeds) shows everything
    rather than erroring on ``CAST('' AS DOUBLE)``; the operator you pick decides
    which handle position means "all" (``>=`` → the minimum, ``<=`` → the maximum):

        :::query name=top_rated connector=main
        SELECT * FROM products
        WHERE '${min_rating}' = ''
           OR rating >= CAST(${min_rating} AS DOUBLE)
        :::

    Attributes:
    - name: Required. Filter key your SQL reads as ``${name}``.
    - label: Optional. Inline label (defaults to name).
    - min: Optional. Track minimum (default ``0``).
    - max: Optional. Track maximum (default ``100``).
    - step: Optional. Handle increment (default ``1``).
    - default: Optional. Initial value (default: ``min``). URL params still win.
    - format / currency / decimals / locale: Optional. Format the readout value
      (same ``formatValue`` config the other components use).
    - url_sync: Optional. Mirror the value to the URL (default ``true``).
    - bar: Optional. Relocate into the page's top filter bar (default: inline).

    Example:
        <Slider name="min_rating" min={0} max={5} step={0.5} default={4} label="Min rating" />
    """

    is_filter = True

    def render(
        self, attrs, ctx: RenderContext, inner: str | None = None
    ) -> str:
        name = attr_str(attrs, "name")
        if not name:
            raise ValueError("Slider requires a `name` attribute")

        label = attr_str(attrs, "label", name)
        lo_bound = attr_float(attrs, "min", 0.0)
        hi_bound = attr_float(attrs, "max", 100.0)
        if hi_bound <= lo_bound:
            raise ValueError(
                f"Slider `max` ({hi_bound}) must be greater than `min` ({lo_bound})"
            )
        step = attr_float(attrs, "step", 1.0)
        if step <= 0:
            step = 1.0

        default = attr_float(attrs, "default", lo_bound)
        # Keep the default inside the track.
        default = max(lo_bound, min(default, hi_bound))

        url_sync = attrs.get("url_sync", True)
        fmt = format_config(attrs)
        # Inline by default; `bar` relocates into the top filter row. See
        # filter_bar_marker.
        filter_bar_attr = filter_bar_marker(attrs, ctx)

        cid = new_id("dashdown-slider")
        config = {
            "name": name,
            "label": label,
            "min": lo_bound,
            "max": hi_bound,
            "step": step,
            "default": default,
            "url_sync": url_sync,
        }
        if fmt:
            config["format"] = fmt

        # One native range input over a track with a fill from the minimum to the
        # handle. sliderComponent (slider.js) owns clamping, the readout, the
        # filter-store write + URL sync — the same minimal role rangeSliderComponent
        # plays. The value lives in the store as a string (uniform with every
        # filter), so the control binds explicitly rather than x-model-ing a number
        # straight into the store.
        return (
            f'<div class="dashdown-slider dashdown-filter-pill" id="{cid}" '
            f'data-async-component="slider" '
            f'data-config="{esc(safe_json(config))}" '
            f'data-name="{esc(name)}" '
            f'data-url-sync="{str(url_sync).lower()}" '
            f"{filter_bar_attr}"
            f"x-data=\"sliderComponent('{esc(name)}')\" "
            f">"
            f'<span class="dashdown-filter-pill-label">{esc(label)}'
            f'<span class="dashdown-filter-pill-colon">:</span></span>'
            f'<div class="dashdown-slider-control">'
            f'<span class="dashdown-slider-readout" x-text="fmt(value)" '
            f'aria-hidden="true"></span>'
            f'<div class="dashdown-slider-track">'
            f'<div class="dashdown-slider-fill" :style="fillStyle()"></div>'
            f'<input type="range" class="dashdown-slider-input" '
            f'min="{_num(lo_bound)}" max="{_num(hi_bound)}" step="{_num(step)}" '
            f'x-model.number="value" @input="onInput" '
            f'aria-label="{esc(label)}" :aria-valuetext="fmt(value)" />'
            f"</div>"
            f"</div>"
            f"</div>"
        )
