"""Smoke tests for the ``examples/growth-answers`` demo project (the
AI-readiness essay's "Monday 9:07" scene made runnable — see
``plans/ai-readiness/PLAN.md``).

These are deliberately shallow: the project's own README documents the demo
script. This file just guards against rot — the project loads, every shared
library query actually runs against the seeded CSVs and returns sensible
data, and the trigger spec parses — so a change elsewhere in the framework
that breaks the example fails CI instead of being discovered at demo time.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from dashdown.project import load_project
from dashdown.render.pipeline import (
    _library_keys,
    _python_def_cache,
    _python_library_keys,
    _query_def_cache,
    _stream_def_cache,
    _substitute_params,
)

EXAMPLE = Path(__file__).parent.parent / "examples" / "growth-answers"

_bsl_installed = True
try:  # the semantic extra — the example ships a semantic/growth.yml model
    import boring_semantic_layer  # noqa: F401
    import ibis  # noqa: F401
except ImportError:  # pragma: no cover
    _bsl_installed = False

needs_bsl = pytest.mark.skipif(
    not _bsl_installed, reason="requires dashdown-md[semantic]"
)


@pytest.fixture(autouse=True)
def _clear_caches():
    """The query-def caches are module-global; isolate this file from others."""

    def _clear():
        _query_def_cache.clear()
        _stream_def_cache.clear()
        _python_def_cache.clear()
        _library_keys.clear()
        _python_library_keys.clear()

    _clear()
    yield
    _clear()


@needs_bsl
def test_project_loads():
    proj = load_project(EXAMPLE)
    try:
        assert proj.default_connector == "growth"
        assert "growth" in proj.connectors

        expected_queries = {
            "kpi.repeat_rate",
            "kpi.weekly_repeat_trend",
            "campaigns.performance",
            "campaigns.repeat_purchasers",
        }
        assert expected_queries <= set(proj.queries)
        # Every library query documents itself (the runtime ask resolver
        # reads these descriptions when picking a query off the menu).
        for name in expected_queries:
            assert proj.queries[name].description, f"{name} has no description"

        assert "orders" in proj.semantic_models
    finally:
        proj.close()


@needs_bsl
def test_library_queries_return_sensible_data():
    """Every shared-library query runs against the seeded CSVs and returns
    non-empty, plausible results — the actual data story the README claims."""
    proj = load_project(EXAMPLE)
    try:
        conn = proj.connectors[proj.default_connector]

        def _run(name: str, params: dict[str, str] | None = None):
            spec = proj.queries[name]
            sql = _substitute_params(spec.sql, params or {})
            return conn.query(sql)

        # kpi.repeat_rate: a single row, single numeric column (0-100 scale).
        repeat_rate = _run("kpi.repeat_rate")
        assert repeat_rate.columns == ["repeat_rate"]
        assert len(repeat_rate.rows) == 1
        assert len(repeat_rate.rows[0]) == 1
        value = repeat_rate.rows[0][0]
        assert isinstance(value, (int, float))
        # A plausible repeat-purchase rate — comfortably inside 0-100, and in
        # the "healthy but dipping" range the essay's scene describes.
        assert 0.0 < value < 100.0

        # kpi.weekly_repeat_trend: several weeks of a (week, orders, rate) trend.
        trend = _run("kpi.weekly_repeat_trend")
        assert trend.rows
        assert set(trend.columns) == {"week", "orders", "repeat_rate"}
        assert len(trend.rows) >= 8  # ~10 weeks of seeded history

        # campaigns.performance (channel="" == no filter, "all channels"):
        # Summer Referral Push should lead on repeat orders, Viral Reels
        # Blast should have the most total orders but ~0 repeat.
        perf = _run("campaigns.performance", {"channel": ""})
        assert perf.rows
        by_campaign = {row[0]: row for row in perf.rows}
        assert "Summer Referral Push" in by_campaign
        assert "Viral Reels Blast" in by_campaign
        cols = perf.columns
        orders_i = cols.index("orders")
        repeat_orders_i = cols.index("repeat_orders")
        top_repeat_campaign = max(perf.rows, key=lambda r: r[repeat_orders_i])
        assert top_repeat_campaign[0] == "Summer Referral Push"
        viral_row = by_campaign["Viral Reels Blast"]
        assert viral_row[orders_i] > by_campaign["Summer Referral Push"][orders_i]
        assert viral_row[repeat_orders_i] == 0

        # The channel filter's "no selection" guard narrows correctly.
        email_only = _run("campaigns.performance", {"channel": "email"})
        assert email_only.rows
        assert all(row[cols.index("channel")] == "email" for row in email_only.rows)

        # campaigns.repeat_purchasers: the "who to call" list, non-empty and
        # dominated by the Summer Referral Push campaign.
        purchasers = _run("campaigns.repeat_purchasers")
        assert purchasers.rows
        campaign_i = purchasers.columns.index("campaign")
        referral_count = sum(
            1 for row in purchasers.rows if row[campaign_i] == "Summer Referral Push"
        )
        assert referral_count > len(purchasers.rows) / 2
    finally:
        proj.close()


@needs_bsl
def test_semantic_model_answers_a_metric_query():
    """The semantic/growth.yml model resolves a metric+dimension reference and
    pushes it down through the growth CSV connector."""
    from dashdown.python_query import run_python_query
    from dashdown.semantic import SemanticRef, build_semantic_spec

    proj = load_project(EXAMPLE)
    try:
        ref = SemanticRef(
            model="orders", metrics=("revenue",), by=None, connector="growth"
        )
        spec = build_semantic_spec(proj.semantic_models, ref, proj.connectors)
        result = run_python_query(spec, {}, proj.connectors)
        assert result.rows
        assert result.rows[0][0] > 0
    finally:
        proj.close()


@needs_bsl
def test_trigger_spec_parses():
    proj = load_project(EXAMPLE)
    try:
        assert "repeat-rate" in proj.triggers
        trigger = proj.triggers["repeat-rate"]
        assert trigger.query == "kpi.repeat_rate"
        assert trigger.condition.subject == "value"
        assert trigger.condition.op == "<"
        assert trigger.condition.threshold == 20.0
        # Ships off by default. A disabled trigger's actions are not built —
        # its webhook references ${DEMO_HOOK_URL}, and the whole point is that
        # the project loads with no environment configured; the fail-hard
        # env-expansion runs only once the trigger is enabled.
        assert trigger.enabled is False
        assert trigger.actions == []
    finally:
        proj.close()


def test_list_question_routes_to_recent_orders_table():
    """The 'show me the last N customers that ordered' shape: a detail/list
    question resolves to the customers.recent_orders library query and comes
    back as a table (rows, newest first) — not a forced aggregate."""
    from dashdown import ask_engine

    class _Fake:
        def __init__(self):
            self.calls = []

        def complete(self, system, prompt):
            self.calls.append((system, prompt))
            if len(self.calls) == 1:
                return (
                    '{"kind": "query", "name": "customers.recent_orders", '
                    '"params": {}}'
                )
            return "The most recent orders came mostly from Summer Referral Push."

    proj = load_project(EXAMPLE)
    proj.llm_adapter = _Fake()
    try:
        assert "customers.recent_orders" in proj.queries
        payload = ask_engine.answer_question(
            proj, "show me the last 10 customers that ordered (routing smoke)"
        )
        assert payload["resolved"]["kind"] == "query"
        assert payload["resolved"]["query_name"] == "customers.recent_orders"
        cols = payload["columns"]
        assert "customer" in cols and "order_date" in cols
        rows = payload["rows"]
        assert 0 < len(rows) <= 50
        # Newest first.
        dates = [r[cols.index("order_date")] for r in rows]
        assert dates[0] >= dates[-1]
        # The list answer renders even if a chart is inferred — the table is
        # the deliverable; answer text rides along.
        assert payload["answer_text"]
    finally:
        proj.close()


@needs_bsl
def test_list_rung_resolves_over_growth_semantic_model():
    """The generic "list" rung: a detail question routed onto the growth semantic
    model (no author-curated query) comes back as an ordered, limited table —
    newest first — compiled by the semantic backend, not LLM-written SQL."""
    from dashdown import ask_engine

    class _Fake:
        def __init__(self):
            self.calls = []

        def complete(self, system, prompt):
            self.calls.append((system, prompt))
            if len(self.calls) == 1:
                return (
                    '{"kind": "list", "model": "orders", '
                    '"columns": ["order_date", "campaign_id"], '
                    '"order_by": "order_date", "desc": true, "limit": 10}'
                )
            return "The most recent orders, newest first."

    proj = load_project(EXAMPLE)
    proj.llm_adapter = _Fake()
    try:
        payload = ask_engine.answer_question(
            proj, "show me the 10 most recent orders"
        )
        assert payload["resolved"]["kind"] == "list"
        assert "list:" in payload["resolved"]["provenance"]
        cols = payload["columns"]
        rows = payload["rows"]
        assert 0 < len(rows) <= 10
        date_col = next(c for c in cols if c.split(".")[-1] == "order_date")
        dates = [r[cols.index(date_col)] for r in rows]
        assert dates[0] >= dates[-1]  # newest first
        # A list answer never attaches the aggregate-editor chips.
        assert "semantic_options" not in payload
    finally:
        proj.close()
