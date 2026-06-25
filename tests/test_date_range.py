"""Tests for the DateRange filter component.

The preset *math* lives in static/components/daterange.js (no JS test harness in
this project), so these tests lock the Python→JS contract instead: the rendered
HTML must carry preset configs with the kind/unit/offset fields daterange.js's
presetRange() relies on, plus the structural affordances (All-time option,
custom inputs, range bounds)."""
import html
import json
import re

import pytest

import dashdown.components  # noqa: F401  (registers built-ins)
from dashdown.components.base import RenderContext
from dashdown.components.builtin.date_range import PRESET_RANGES
from dashdown.render.components import render_components


@pytest.fixture
def ctx():
    return RenderContext(queries={}, params={}, current_path="/")


def _config(rendered: str) -> dict:
    """Extract and decode the data-config JSON from a daterange's HTML."""
    m = re.search(r'data-config="([^"]*)"', rendered)
    assert m, f"no data-config in: {rendered}"
    return json.loads(html.unescape(m.group(1)))


def test_requires_name(ctx):
    # render_components swallows render errors into an inline error card (no 500).
    out = render_components("<DateRange label=\"x\" />", ctx)
    assert "DateRange requires a `name`" in out


def test_default_param_names(ctx):
    out = render_components('<DateRange name="date" label="Date" />', ctx)
    cfg = _config(out)
    assert cfg["start_param"] == "date_start"
    assert cfg["end_param"] == "date_end"
    assert cfg["url_sync"] is True


def test_custom_param_names(ctx):
    out = render_components(
        '<DateRange name="date" start_param="from" end_param="to" />', ctx
    )
    cfg = _config(out)
    assert cfg["start_param"] == "from"
    assert cfg["end_param"] == "to"


def test_default_presets_order_preserved(ctx):
    out = render_components('<DateRange name="date" />', ctx)
    cfg = _config(out)
    assert cfg["presets"] == [
        "today",
        "last_7_days",
        "last_30_days",
        "last_90_days",
        "custom",
    ]


def test_unknown_presets_dropped(ctx):
    out = render_components('<DateRange name="date" presets="today,bogus,custom" />', ctx)
    cfg = _config(out)
    assert cfg["presets"] == ["today", "bogus", "custom"]  # listed verbatim...
    # ...but only known keys get a resolvable config the JS can compute from.
    assert set(cfg["preset_configs"]) == {"today", "custom"}


def test_rolling_preset_config_shape(ctx):
    out = render_components('<DateRange name="date" presets="last_7_days" />', ctx)
    cfg = _config(out)
    last7 = cfg["preset_configs"]["last_7_days"]
    assert last7["kind"] == "rolling"
    assert last7["days_start"] == -6
    assert last7["days_end"] == 0


def test_calendar_preset_config_shape(ctx):
    """The regression that motivated the fix: calendar presets must carry a
    unit/offset the JS resolves to real boundaries, not a day-offset window that
    silently aliased this_month→last_30_days."""
    out = render_components(
        '<DateRange name="date" presets="this_month,last_month,this_year,this_week" />',
        ctx,
    )
    cfg = _config(out)
    pc = cfg["preset_configs"]

    assert pc["this_month"] == {
        "label": "This Month",
        "kind": "calendar",
        "unit": "month",
        "offset": 0,
    }
    assert pc["last_month"]["unit"] == "month" and pc["last_month"]["offset"] == -1
    assert pc["this_year"]["unit"] == "year" and pc["this_year"]["offset"] == 0
    assert pc["this_week"]["unit"] == "week" and pc["this_week"]["offset"] == 0


def test_calendar_presets_are_not_day_offset_aliases():
    """Guard the table directly: a calendar preset must never be expressed as a
    rolling day-offset window (the old this_month == last_30_days bug)."""
    for key in ("this_week", "last_week", "this_month", "last_month", "this_year", "last_year"):
        cfg = PRESET_RANGES[key]
        assert cfg["kind"] == "calendar"
        assert "days_start" not in cfg and "days_end" not in cfg


def test_all_time_option_present(ctx):
    out = render_components('<DateRange name="date" />', ctx)
    # The empty-value option is what clears the range (setPreset("")).
    assert '<option value="">All time</option>' in out


def test_custom_inputs_have_range_bounds(ctx):
    """Start input is capped at the end date and vice versa, so the picker can't
    produce an inverted range."""
    out = render_components('<DateRange name="date" presets="custom" />', ctx)
    assert 'x-model="startDate"' in out
    assert 'x-bind:max="endDate"' in out
    assert 'x-model="endDate"' in out
    assert 'x-bind:min="startDate"' in out


def test_placement_inline_by_default(ctx):
    # Inline by default: no routing marker (filter_bar.js leaves it put).
    default = render_components('<DateRange name="date" />', ctx)
    assert "data-filter-bar" not in default
    # Legacy filter_bar=false is the default now — also no marker.
    legacy = render_components('<DateRange name="date" filter_bar=false />', ctx)
    assert "data-filter-bar" not in legacy
    # `bar` opts into the top filter bar.
    barred = render_components('<DateRange name="date" bar />', ctx)
    assert 'data-filter-bar="true"' in barred
