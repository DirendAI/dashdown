"""Tests for the Table component — header/format/sort/pagination config (Task 7)."""
import html
import json
import re

import pytest

import dashdown.components  # noqa: F401  (registers built-ins)
from dashdown.components.base import RenderContext
from dashdown.components.builtin.table import _parse_map
from dashdown.render.components import render_components


@pytest.fixture
def ctx():
    return RenderContext(queries={}, params={}, current_path="/")


def _config(rendered: str) -> dict:
    """Extract and decode the data-config JSON from a table's HTML."""
    m = re.search(r'data-config="([^"]*)"', rendered)
    assert m, f"no data-config in: {rendered}"
    return json.loads(html.unescape(m.group(1)))


def test_parse_map_basic():
    assert _parse_map("patient_id=Patient, est_fee=Est. Fee") == {
        "patient_id": "Patient",
        "est_fee": "Est. Fee",
    }


def test_parse_map_empty_and_malformed():
    assert _parse_map(None) == {}
    assert _parse_map("") == {}
    # A bare token with no `=` is skipped, not crashed on.
    assert _parse_map("nope, ok=Yes") == {"ok": "Yes"}


def test_table_basic_config(ctx):
    out = render_components('<Table data={rows} title="People" />', ctx)
    cfg = _config(out)
    assert cfg["query_name"] == "rows"
    assert cfg["title"] == "People"
    assert cfg["page_size"] == 10  # default pagination
    # currency is omitted unless set, so the project default / JS "$" applies.
    assert "currency" not in cfg
    # No optional maps requested → keys absent.
    assert "headers" not in cfg
    assert "formats" not in cfg
    assert "sort" not in cfg


def test_table_header_overrides(ctx):
    out = render_components(
        '<Table data={rows} headers="patient_id=Patient, total_charge=Fee" />', ctx
    )
    cfg = _config(out)
    assert cfg["headers"] == {"patient_id": "Patient", "total_charge": "Fee"}


def test_table_format_and_currency(ctx):
    out = render_components(
        '<Table data={rows} format="total_charge=currency, appt_date=date" currency="€" />',
        ctx,
    )
    cfg = _config(out)
    assert cfg["formats"] == {"total_charge": "currency", "appt_date": "date"}
    assert cfg["currency"] == "€"
    # No locale attr → key omitted (JS uses the browser locale).
    assert "locale" not in cfg


def test_table_iso_currency_and_locale(ctx):
    out = render_components(
        '<Table data={rows} format="total_charge=currency" '
        'currency="EUR" locale="de-DE" />',
        ctx,
    )
    cfg = _config(out)
    assert cfg["currency"] == "EUR"
    assert cfg["locale"] == "de-DE"


def test_table_date_format(ctx):
    out = render_components(
        '<Table data={rows} format="appt_date=date" date_format="DD.MM.YYYY" />',
        ctx,
    )
    cfg = _config(out)
    assert cfg["date_format"] == "DD.MM.YYYY"


def test_table_sort_default_dir(ctx):
    out = render_components('<Table data={rows} sort="appt_date" />', ctx)
    cfg = _config(out)
    assert cfg["sort"] == "appt_date"
    assert cfg["sort_dir"] == "asc"


def test_table_sort_explicit_dir(ctx):
    out = render_components('<Table data={rows} sort="total_charge desc" />', ctx)
    cfg = _config(out)
    assert cfg["sort"] == "total_charge"
    assert cfg["sort_dir"] == "desc"


def test_table_search_default_on_auto(ctx):
    # Search is on by default, flagged `search_auto` so the JS can gate it to
    # tables with enough rows (single-row detail tables stay clean).
    cfg = _config(render_components('<Table data={rows} />', ctx))
    assert cfg["search"] is True
    assert cfg["search_auto"] is True
    assert cfg["search_placeholder"] == "Search…"


def test_table_search_bare_is_explicit(ctx):
    # Explicit `search` forces it on (no auto gate).
    cfg = _config(render_components('<Table data={rows} search />', ctx))
    assert cfg["search"] is True
    assert "search_auto" not in cfg
    assert cfg["search_placeholder"] == "Search…"


def test_table_search_placeholder(ctx):
    cfg = _config(render_components('<Table data={rows} search="Find a patient…" />', ctx))
    assert cfg["search"] is True
    assert "search_auto" not in cfg
    assert cfg["search_placeholder"] == "Find a patient…"


def test_table_search_opt_out(ctx):
    cfg = _config(render_components('<Table data={rows} search=false />', ctx))
    assert "search" not in cfg
    assert "search_placeholder" not in cfg


def test_table_fullscreen_default_on(ctx):
    """Fullscreen is on by default — the key is omitted, so the JS default
    (`fullscreen !== false`) keeps the ⛶ button showing."""
    cfg = _config(render_components('<Table data={rows} />', ctx))
    assert "fullscreen" not in cfg


def test_table_fullscreen_opt_out(ctx):
    """`fullscreen=false` emits the explicit flag the JS toolbar checks."""
    cfg = _config(render_components('<Table data={rows} fullscreen=false />', ctx))
    assert cfg["fullscreen"] is False


def test_table_page_size_override(ctx):
    out = render_components('<Table data={rows} page-size=25 />', ctx)
    cfg = _config(out)
    assert cfg["page_size"] == 25


def test_table_page_size_zero_disables_pagination(ctx):
    out = render_components('<Table data={rows} page-size=0 />', ctx)
    cfg = _config(out)
    assert cfg["page_size"] == 0


def test_table_row_link(ctx):
    # `row_link` makes the whole row clickable; the pattern is passed through to
    # the client, which fills `{column}` per-row and navigates on click.
    cfg = _config(render_components('<Table data={rows} row_link="/customers/{id}" />', ctx))
    assert cfg["row_link"] == "/customers/{id}"


def test_table_row_link_absent_by_default(ctx):
    cfg = _config(render_components('<Table data={rows} />', ctx))
    assert "row_link" not in cfg


def test_table_detail_slug_builds_link_pattern():
    # `detail_slug` is the cell-link shorthand: it makes the named column link to
    # `{current_path}/{value}` (the drill-down to a `[slug].md` detail page).
    ctx = RenderContext(queries={}, params={}, current_path="/channels")
    cfg = _config(render_components('<Table data={rows} detail_slug="channel" />', ctx))
    assert cfg["link_column"] == "channel"
    assert cfg["link_pattern"] == "/channels/{channel}"


def test_table_heatmap_bare_is_all_numeric(ctx):
    # Bare `heatmap` shades every numeric column → True sentinel + default scheme.
    cfg = _config(render_components("<Table data={rows} heatmap />", ctx))
    assert cfg["heatmap"] is True
    assert cfg["heatmap_scheme"] == "sequential"


def test_table_heatmap_all_keyword(ctx):
    cfg = _config(render_components('<Table data={rows} heatmap="all" />', ctx))
    assert cfg["heatmap"] is True


def test_table_heatmap_column_list(ctx):
    cfg = _config(
        render_components('<Table data={rows} heatmap="amount, profit" />', ctx)
    )
    assert cfg["heatmap"] == ["amount", "profit"]
    assert cfg["heatmap_scheme"] == "sequential"


def test_table_heatmap_diverging_scheme(ctx):
    cfg = _config(
        render_components(
            '<Table data={rows} heatmap="profit" heatmap_scheme="diverging" />', ctx
        )
    )
    assert cfg["heatmap"] == ["profit"]
    assert cfg["heatmap_scheme"] == "diverging"


def test_table_heatmap_unknown_scheme_falls_back(ctx):
    cfg = _config(
        render_components('<Table data={rows} heatmap heatmap_scheme="rainbow" />', ctx)
    )
    assert cfg["heatmap_scheme"] == "sequential"


def test_table_heatmap_absent_and_opt_out(ctx):
    assert "heatmap" not in _config(render_components("<Table data={rows} />", ctx))
    # `heatmap=false` is an explicit opt-out, not "shade everything".
    cfg = _config(render_components("<Table data={rows} heatmap=false />", ctx))
    assert "heatmap" not in cfg
    assert "heatmap_scheme" not in cfg


def test_table_requires_data(ctx):
    out = render_components("<Table title=\"x\" />", ctx)
    assert "Table requires" in out
