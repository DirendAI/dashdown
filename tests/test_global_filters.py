"""Tests for the project-wide global date filter.

Covers `global_filters.date` config parsing and how `render_page` places the
control: returned on `RenderedPage.global_date_html` for the sticky app header
normally, injected into the body as an ordinary filter-bar filter when embedded,
and omitted in static builds.
"""
from __future__ import annotations

import json
import re

import pytest

from dashdown.project import GlobalDateFilterConfig, parse_global_filters_config
from dashdown.render.pipeline import render_page

# A page with a query that references the global date params, so the control is
# relevant and shown.
PAGE = (
    "# Sales\n\n"
    ":::query name=q connector=main\n"
    "SELECT * FROM t WHERE d >= '${date_start}' AND d <= '${date_end}'\n"
    ":::\n\n"
    "<Table data={q} />\n"
)

# A page whose queries never reference the date params — the control is not
# relevant here, so it should be omitted even when the filter is enabled.
PAGE_NO_DATE = (
    "# Docs\n\n"
    ":::query name=q connector=main\n"
    "SELECT * FROM t\n"
    ":::\n\n"
    "<Table data={q} />\n"
)


def _render(page=PAGE, **kwargs):
    return render_page(page, {}, **kwargs)


def _daterange_div(html: str) -> str | None:
    m = re.search(r'<div class="dashdown-daterange[^>]*data-async-component="daterange"[^>]*>', html)
    return m.group(0) if m else None


def _daterange_config(html: str) -> dict:
    m = re.search(r'data-config="([^"]*)"', _daterange_div(html))
    # The config JSON is HTML-escaped (quotes → &quot;).
    import html as _h

    return json.loads(_h.unescape(m.group(1)))


# --------------------------------------------------------------------------- #
# Config parsing
# --------------------------------------------------------------------------- #


def test_parse_absent_block_is_disabled():
    cfg = parse_global_filters_config(None)
    assert cfg.enabled is False
    assert cfg.start_param == "date_start" and cfg.end_param == "date_end"


def test_parse_full_block():
    cfg = parse_global_filters_config(
        {
            "date": {
                "enabled": True,
                "label": "Period",
                "presets": "last_7_days,this_year,custom",
                "default": "last_30_days",
                "start_param": "from",
                "end_param": "to",
            }
        }
    )
    assert cfg == GlobalDateFilterConfig(
        enabled=True,
        label="Period",
        presets="last_7_days,this_year,custom",
        default="last_30_days",
        start_param="from",
        end_param="to",
    )


def test_parse_date_present_but_disabled():
    cfg = parse_global_filters_config({"date": {"enabled": False, "label": "X"}})
    assert cfg.enabled is False and cfg.label == "X"


@pytest.mark.parametrize(
    "raw",
    [
        "notamapping",
        {"date": "notamapping"},
        {"date": {"label": ""}},
        {"date": {"presets": ""}},
        {"date": {"start_param": "  "}},
    ],
)
def test_parse_malformed_raises(raw):
    with pytest.raises(ValueError):
        parse_global_filters_config(raw)


# --------------------------------------------------------------------------- #
# render_page placement
# --------------------------------------------------------------------------- #


def test_disabled_renders_no_control():
    rp = _render(global_date=GlobalDateFilterConfig(enabled=False))
    assert rp.global_date_html == ""
    assert _daterange_div(rp.body_html) is None


def test_page_without_date_params_omits_control():
    # Enabled, but no query on the page references ${date_start}/${date_end}.
    cfg = GlobalDateFilterConfig(enabled=True)
    rp = _render(page=PAGE_NO_DATE, global_date=cfg)
    assert rp.global_date_html == ""
    assert _daterange_div(rp.body_html) is None


def test_page_without_date_params_omits_control_in_embed():
    cfg = GlobalDateFilterConfig(enabled=True)
    rp = _render(page=PAGE_NO_DATE, global_date=cfg, embed=True)
    assert _daterange_div(rp.body_html) is None


def test_custom_param_names_drive_detection():
    # The control keys off the *configured* param names, not the defaults.
    page = (
        "# X\n\n:::query name=q connector=main\n"
        "SELECT * FROM t WHERE d >= '${from}'\n:::\n\n<Table data={q} />\n"
    )
    cfg = GlobalDateFilterConfig(enabled=True, start_param="from", end_param="to")
    assert _daterange_div(_render(page=page, global_date=cfg).global_date_html) is not None
    # Default names no longer match → omitted.
    assert _render(page=page, global_date=GlobalDateFilterConfig(enabled=True)).global_date_html == ""


def test_enabled_returns_header_control():
    cfg = GlobalDateFilterConfig(enabled=True, default="last_30_days")
    rp = _render(global_date=cfg)
    # Returned for the sticky app header — not grafted into the page body.
    assert _daterange_div(rp.body_html) is None
    div = _daterange_div(rp.global_date_html)
    assert div is not None
    # Header placement carries no routing marker so filter_bar.js leaves it put.
    assert "data-filter-bar" not in div
    assert 'data-name="date"' in div
    assert 'data-start-param="date_start"' in div
    conf = _daterange_config(rp.global_date_html)
    assert conf["persist"] is True
    assert conf["default"] == "last_30_days"


def test_custom_params_thread_through():
    page = (
        "# X\n\n:::query name=q connector=main\n"
        "SELECT * FROM t WHERE d BETWEEN '${from}' AND '${to}'\n:::\n\n<Table data={q} />\n"
    )
    cfg = GlobalDateFilterConfig(enabled=True, start_param="from", end_param="to")
    div = _daterange_div(_render(page=page, global_date=cfg).global_date_html)
    assert 'data-start-param="from"' in div and 'data-end-param="to"' in div


def test_embed_renders_as_filter_bar_filter():
    cfg = GlobalDateFilterConfig(enabled=True)
    rp = _render(global_date=cfg, embed=True)
    # In embed mode the header is omitted, so it rides body_html as a filter.
    assert rp.global_date_html == ""
    div = _daterange_div(rp.body_html)
    assert div is not None
    # As a normal page filter it routes into the bar (embeds omit the header)...
    assert 'data-filter-bar="true"' in div
    # ...and the filter bar slot is forced to appear so it has a home.
    assert "dashdown-filter-bar" in rp.body_html


def test_static_build_omits_control():
    cfg = GlobalDateFilterConfig(enabled=True, default="last_30_days")
    rp = _render(global_date=cfg, static_build=True)
    assert rp.global_date_html == ""
    assert _daterange_div(rp.body_html) is None
