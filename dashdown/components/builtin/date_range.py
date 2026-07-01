from __future__ import annotations

from dashdown.components.base import Component, RenderContext, register_component
from dashdown.components.builtin._util import (
    attr_str,
    esc,
    filter_bar_marker,
    new_id,
    resolve_debounce,
    safe_json,
)


# Preset date ranges. Two kinds the frontend knows how to resolve:
#   - "rolling": a window measured in days back from today. days_start/days_end
#     are offsets (0 = today, -6 = six days ago).
#   - "calendar": anchored to a calendar unit (week/month/year). offset 0 = the
#     current period from its start through today (month/week/year-to-date);
#     offset -1 = the whole previous period (its start through its end).
# Calendar ranges can't be expressed as fixed day-offsets (months/years vary in
# length), so daterange.js computes the boundaries from the viewer's local
# "today" — see presetRange()/calendarRange() there. Weeks start Monday (ISO).
PRESET_RANGES = {
    "today": {"label": "Today", "kind": "rolling", "days_start": 0, "days_end": 0},
    "yesterday": {"label": "Yesterday", "kind": "rolling", "days_start": -1, "days_end": -1},
    "last_7_days": {"label": "Last 7 Days", "kind": "rolling", "days_start": -6, "days_end": 0},
    "last_30_days": {"label": "Last 30 Days", "kind": "rolling", "days_start": -29, "days_end": 0},
    "last_90_days": {"label": "Last 90 Days", "kind": "rolling", "days_start": -89, "days_end": 0},
    "this_week": {"label": "This Week", "kind": "calendar", "unit": "week", "offset": 0},
    "last_week": {"label": "Last Week", "kind": "calendar", "unit": "week", "offset": -1},
    "this_month": {"label": "This Month", "kind": "calendar", "unit": "month", "offset": 0},
    "last_month": {"label": "Last Month", "kind": "calendar", "unit": "month", "offset": -1},
    "this_year": {"label": "This Year", "kind": "calendar", "unit": "year", "offset": 0},
    "last_year": {"label": "Last Year", "kind": "calendar", "unit": "year", "offset": -1},
    "custom": {"label": "Custom", "kind": "custom"},
}


@register_component("DateRange")
class DateRange(Component):
    """Date range picker component with preset ranges and custom selection.

    Renders as a compact pill: inline label + a preset <select> ("All time"
    clears the range); picking "Custom" reveals start/end date inputs.

    Supports URL sync by default (url_sync=True).
    Selected date range is stored in the page-level Alpine store under `filters[name]`.

    Parameters:
    - name: Required. The filter name to store the date range under.
    - label: Optional. Display label (defaults to name; shown as the inline prefix).
    - presets: Optional. Comma-separated list of preset ranges, in display order
      (default: today,last_7_days,last_30_days,last_90_days,custom). Available:
      today, yesterday, last_7_days, last_30_days, last_90_days, this_week,
      last_week, this_month, last_month, this_year, last_year, custom. The
      calendar presets (this_/last_ week/month/year) are anchored to real
      calendar boundaries, not day-offset approximations.
    - start_param: Optional. URL parameter name for start date (defaults to name + "_start")
    - end_param: Optional. URL parameter name for end date (defaults to name + "_end")
    - url_sync: Optional. Enable URL sync (default: True)
    - default: Optional. A preset name (e.g. "last_30_days") applied on first load
      when nothing is set (no URL params and no persisted value).
    - persist: Optional. Remember the selection in localStorage so it survives
      navigation — the mechanism behind the project-wide global date filter.
    - debounce: Optional. Quiet period (ms) before a change re-fetches data, so a
      preset (which sets start+end) or a custom edit coalesces into one fetch.
      Defaults to the project-wide ``filters.debounce`` (300 unless raised).
    - bar: Optional. Relocate this control into the page's top filter bar.
      Default is inline (renders where authored).

    Example:
    <DateRange name="date_range" label="Date Range" presets="today,last_7_days,last_30_days,custom" />
    
    URL format:
    ?date_range_start=2024-01-01&date_range_end=2024-01-31
    """

    is_filter = True

    def render(
        self, attrs, ctx: RenderContext, inner: str | None = None
    ) -> str:
        name = attr_str(attrs, "name")
        if not name:
            raise ValueError("DateRange requires a `name` attribute")
        
        label = attr_str(attrs, "label", name)
        presets_str = attr_str(attrs, "presets", "today,last_7_days,last_30_days,last_90_days,custom")
        start_param = attr_str(attrs, "start_param", f"{name}_start")
        end_param = attr_str(attrs, "end_param", f"{name}_end")
        url_sync = attrs.get("url_sync", True)
        # `default="last_30_days"` applies a preset on first load when nothing is
        # set (no URL params, no persisted value). `persist` remembers the
        # selection in localStorage so it survives navigation — the basis of the
        # project-wide global date filter (see GlobalDateFilterConfig).
        default_preset = attr_str(attrs, "default", "") or None
        persist = bool(attrs.get("persist", False))
        # Debounce the store write (data re-fetch): a preset sets start+end and a
        # custom edit touches each input, so coalesce them into one fetch. The
        # initial seed stays immediate (daterange.js). Per-control `debounce=`
        # wins, else the project-wide `filters.debounce` (see resolve_debounce).
        debounce = resolve_debounce(attrs, ctx)
        # Inline by default (renders where authored); `bar` relocates it into the
        # top filter row (read by filter_bar.js). The global date control passes
        # `filter_bar=True` in embed mode to route itself there. See filter_bar_marker.
        filter_bar_attr = filter_bar_marker(attrs, ctx)

        # Parse presets
        presets = [p.strip() for p in presets_str.split(",") if p.strip()]

        cid = new_id("dashdown-daterange")

        # Presets render as <option>s in a compact select; the empty "All time"
        # option clears the range (handled by setPreset).
        preset_options = ['<option value="">All time</option>']
        preset_options += [
            f'<option value="{esc(p)}">{esc(PRESET_RANGES[p]["label"])}</option>'
            for p in presets
            if p in PRESET_RANGES
        ]
        preset_html = "".join(preset_options)

        # Build full preset configs for selected presets only
        preset_configs = {p: PRESET_RANGES[p] for p in presets if p in PRESET_RANGES}

        # Build config for frontend
        config = {
            "name": name,
            "label": label,
            "presets": presets,
            "preset_configs": preset_configs,
            "start_param": start_param,
            "end_param": end_param,
            "url_sync": url_sync,
            "default": default_preset,
            "persist": persist,
            "debounce": debounce,
        }

        # Compact pill: inline label prefix + preset select; the two date inputs
        # only appear when the "Custom" preset is active so
        # the common case stays one small control. Labels move to aria-labels.
        return (
            f'<div class="dashdown-daterange dashdown-filter-pill" id="{cid}" '
            f'data-async-component="daterange" '
            f'data-config="{esc(safe_json(config))}" '
            f'data-name="{esc(name)}" '
            f'data-start-param="{esc(start_param)}" '
            f'data-end-param="{esc(end_param)}" '
            f'data-url-sync="{str(url_sync).lower()}" '
            f"{filter_bar_attr}"
            f'x-data="dateRangeComponent(\'{esc(name)}\')" '
            f">"
            f'<span class="dashdown-filter-pill-label">{esc(label)}'
f'<span class="dashdown-filter-pill-colon">:</span></span>'
            f'<select class="select select-sm" aria-label="{esc(label)} preset" '
            f'x-model="activePreset" '
            f'@change="setPreset($event.target.value)" '
            f'>{preset_html}</select>'
            f'<div class="dashdown-daterange-custom" '
            f'x-show="activePreset === \'custom\'" x-cloak '
            f'>'
            f'<input type="date" class="input input-sm" '
            f'x-model="startDate" '
            f'x-bind:max="endDate" '
            f'@change="updateFromInputs" '
            f'aria-label="{esc(label)} start date" '
            f'>'
            f'<span class="text-base-content/60">–</span>'
            f'<input type="date" class="input input-sm" '
            f'x-model="endDate" '
            f'x-bind:min="startDate" '
            f'@change="updateFromInputs" '
            f'aria-label="{esc(label)} end date" '
            f'>'
            f'</div>'
            f'</div>'
        )
