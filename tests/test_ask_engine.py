"""Tests for the runtime ask engine (dashdown/ask_engine.py + POST /api/ask + CLI).

Layers, mirroring tests/test_ask.py: a scriptable ``FakeAdapter`` injected via
``app.state.project.llm_adapter`` (first ``complete`` returns the resolution JSON,
the second returns the answer text), the resolution ladder (semantic / library
query / sql-gated / none), server-side chart inference, the answer cache, the
JSONL ask log, and the ``dashdown ask`` CLI command.
"""
from __future__ import annotations

import json
import shutil
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from dashdown import ask_engine
from dashdown.ask_engine import (
    ask_suggestions,
    build_ask_catalog,
    cached_ask_catalog,
    infer_chart_shape,
    normalize_question,
    parse_resolution,
)
from dashdown.cli import app as cli_app
from dashdown.data.base import QueryResult
from dashdown.llm import LLMAdapter, LLMConfig
from dashdown.project import load_project
from dashdown.render import pipeline
from dashdown.render.pipeline import serialize_result
from dashdown.server import create_app

runner = CliRunner()

_SEMANTIC_EXAMPLE = Path(__file__).parent / "fixtures" / "semantic_first_class"

_bsl_installed = True
try:  # the semantic extra
    import boring_semantic_layer  # noqa: F401
    import ibis  # noqa: F401
except ImportError:  # pragma: no cover
    _bsl_installed = False

needs_bsl = pytest.mark.skipif(not _bsl_installed, reason="requires dashdown-md[semantic]")


@pytest.fixture(autouse=True)
def _clear_caches():
    """Answer cache + query/def caches are module-global; isolate every test."""

    def _clear():
        ask_engine._answer_cache.clear()
        ask_engine._rate_marks.clear()
        pipeline._query_def_cache.clear()
        pipeline._result_cache.clear()
        pipeline._python_def_cache.clear()
        pipeline._stream_def_cache.clear()
        pipeline._library_keys.clear()
        pipeline._python_library_keys.clear()

    _clear()
    yield
    _clear()


class FakeAdapter(LLMAdapter):
    """Scriptable per-call adapter: returns the queued replies in order (first the
    resolution JSON, then the answer text), then falls back to the last reply."""

    def __init__(self, *replies: str):
        super().__init__(LLMConfig(provider="mistral", api_key="test"))
        self.replies = list(replies)
        self.calls: list[tuple[str, str]] = []

    def complete(self, system: str, prompt: str) -> str:
        self.calls.append((system, prompt))
        if self.replies:
            return self.replies.pop(0)
        return ""


# --------------------------------------------------------------------------- #
# Project fixtures
# --------------------------------------------------------------------------- #
def _make_lib_project(root: Path, *, llm: bool = True, extra_yaml: str = "") -> None:
    """A project with a CSV source and a `by_region` library query."""
    (root / "pages").mkdir()
    (root / "data").mkdir()
    (root / "queries").mkdir()
    yaml = "title: Ask Engine Test\n"
    if llm:
        yaml += "llm:\n  provider: mistral\n  api_key: dummy\n"
    yaml += extra_yaml
    (root / "dashdown.yaml").write_text(yaml, encoding="utf-8")
    (root / "sources.yaml").write_text(
        "main:\n  type: csv\n  directory: data\n", encoding="utf-8"
    )
    (root / "data" / "sales.csv").write_text(
        "region,amount\nNorth,100\nSouth,200\nWest,50\n", encoding="utf-8"
    )
    (root / "queries" / "by_region.sql").write_text(
        "---\ndescription: Revenue by region\n---\n"
        "SELECT region, SUM(amount) AS total FROM sales\n"
        "WHERE (region = '${region}' OR '${region}' = '')\n"
        "GROUP BY region ORDER BY total DESC\n",
        encoding="utf-8",
    )
    (root / "pages" / "index.md").write_text("# Home\n", encoding="utf-8")


def _client(tmp_path: Path, fake: FakeAdapter | None = None, **kw) -> TestClient:
    proj = tmp_path / "proj"
    proj.mkdir()
    _make_lib_project(proj, **kw)
    app = create_app(proj)
    app.state.project.llm_adapter = fake
    return TestClient(app)


# --------------------------------------------------------------------------- #
# Catalog + resolution parsing (pure)
# --------------------------------------------------------------------------- #
def test_catalog_lists_library_queries(tmp_path):
    proj = tmp_path / "proj"
    proj.mkdir()
    _make_lib_project(proj)
    project = load_project(proj)
    try:
        catalog = build_ask_catalog(project)
    finally:
        project.close()
    names = [q["name"] for q in catalog["queries"]]
    assert "by_region" in names
    (entry,) = [q for q in catalog["queries"] if q["name"] == "by_region"]
    assert entry["params"] == ["region"]
    assert entry["description"] == "Revenue by region"
    assert entry["connector"] == "main"


def test_parse_resolution_tolerates_markdown_fences(tmp_path):
    proj = tmp_path / "proj"
    proj.mkdir()
    _make_lib_project(proj)
    project = load_project(proj)
    try:
        fenced = '```json\n{"kind": "query", "name": "by_region", "params": {}}\n```'
        res = parse_resolution(fenced, project, allow_sql=False)
    finally:
        project.close()
    assert res.kind == "query"
    assert res.name == "by_region"


def test_parse_resolution_invalid_json_is_none(tmp_path):
    proj = tmp_path / "proj"
    proj.mkdir()
    _make_lib_project(proj)
    project = load_project(proj)
    try:
        res = parse_resolution("I think you want the sales table.", project, allow_sql=False)
    finally:
        project.close()
    assert res.kind == "none"
    assert res.reason


def test_parse_resolution_unknown_query_is_none(tmp_path):
    proj = tmp_path / "proj"
    proj.mkdir()
    _make_lib_project(proj)
    project = load_project(proj)
    try:
        res = parse_resolution('{"kind":"query","name":"ghost"}', project, allow_sql=False)
    finally:
        project.close()
    assert res.kind == "none"
    assert "ghost" in res.reason


# --------------------------------------------------------------------------- #
# Memoized catalog + ask suggestions (pure, no LLM)
# --------------------------------------------------------------------------- #
def _fake_handle(measures, dimensions, time_dimension=None):
    """A minimal semantic-model handle for catalog composition (no ibis/bsl).

    ``build_ask_catalog`` reads only ``.measures`` / ``.dimensions`` /
    ``.time_dimension`` off a handle, so a namespace with those suffices."""
    return SimpleNamespace(
        measures=measures, dimensions=dimensions, time_dimension=time_dimension
    )


def _fake_query(description, connector="main", sql="SELECT 1"):
    return SimpleNamespace(description=description, connector=connector, sql=sql)


def _fake_project(*, semantic_models=None, queries=None, python_queries=None):
    """A duck-typed project carrying only the fields the catalog builder reads."""
    return SimpleNamespace(
        semantic_models=semantic_models or {},
        queries=queries or {},
        python_queries=python_queries or {},
    )


def test_cached_ask_catalog_memoizes_per_project(monkeypatch):
    calls: list[object] = []
    real = ask_engine.build_ask_catalog

    def counting(project):
        calls.append(project)
        return real(project)

    monkeypatch.setattr(ask_engine, "build_ask_catalog", counting)
    proj = _fake_project(queries={"by_region": _fake_query("Revenue by region")})
    first = cached_ask_catalog(proj)
    second = cached_ask_catalog(proj)
    # Two calls, one build — and the identical dict comes back both times.
    assert first is second
    assert len(calls) == 1
    # A distinct project instance builds its own catalog (reload → new Project).
    cached_ask_catalog(_fake_project())
    assert len(calls) == 2


def test_ask_suggestions_composition_and_no_sql_leak():
    proj = _fake_project(
        semantic_models={
            "sales": _fake_handle(
                ["sales.revenue", "sales.orders", "sales.aov"],
                ["sales.region", "sales.channel"],
                time_dimension="sales.order_date",
            ),
        },
        queries={
            "by_region": _fake_query(
                "Revenue by region. Second sentence ignored.",
                sql="SELECT * FROM t WHERE x = '${secret_param}'",
            ),
        },
        python_queries={
            "ml.churn": SimpleNamespace(description="", connector="main"),
        },
    )
    out = ask_suggestions(proj)
    # First two measures paired with the model's time dimension.
    assert out[0] == "aov by order_date"  # measures are sorted: aov, orders, revenue
    assert out[1] == "orders by order_date"
    # Library query: first sentence only, lowercased lead, no trailing period.
    assert "revenue by region" in out
    assert "Second sentence" not in " ".join(out)
    # Descriptionless python query → humanized name.
    assert "ml churn" in out
    # Never leaks SQL, param names, or schema.
    joined = " ".join(out)
    assert "${" not in joined and "secret_param" not in joined
    assert "SELECT" not in joined


def test_ask_suggestions_caps_at_six():
    proj = _fake_project(
        semantic_models={
            f"m{i}": _fake_handle([f"m{i}.a", f"m{i}.b"], [f"m{i}.d"])
            for i in range(4)
        },
        queries={
            f"q{i}": _fake_query(f"Query number {i}") for i in range(10)
        },
    )
    out = ask_suggestions(proj)
    assert len(out) == 6
    # Only the first two models (by catalog/sorted order) contribute measures.
    assert out[:4] == ["a by d", "b by d", "a by d", "b by d"]


def test_ask_suggestions_measure_falls_back_to_dimension_then_bare():
    with_dim = _fake_project(
        semantic_models={"s": _fake_handle(["s.rev"], ["s.region"])}
    )
    assert ask_suggestions(with_dim) == ["rev by region"]
    bare = _fake_project(semantic_models={"s": _fake_handle(["s.rev"], [])})
    assert ask_suggestions(bare) == ["rev"]


def test_ask_suggestions_endpoint_shape(tmp_path):
    client = _client(tmp_path)
    resp = client.get("/_dashdown/api/ask/suggestions")
    assert resp.status_code == 200
    body = resp.json()
    assert isinstance(body["suggestions"], list)
    # The library query's description drives one starter.
    assert "revenue by region" in body["suggestions"]


def test_ask_suggestions_endpoint_empty_when_ask_disabled(tmp_path):
    client = _client(tmp_path, llm=False)
    resp = client.get("/_dashdown/api/ask/suggestions")
    assert resp.status_code == 200
    assert resp.json() == {"suggestions": []}


# --------------------------------------------------------------------------- #
# Chart inference (mirrors chart.js::resolveAutoConfig)
# --------------------------------------------------------------------------- #
class TestChartInference:
    # infer_chart_shape takes the *serialized* payload (what the browser sees),
    # so tests serialize exactly like answer_question does.
    @staticmethod
    def _shape(columns, rows):
        return infer_chart_shape(serialize_result(QueryResult(columns=columns, rows=rows)))

    def test_temporal_x_is_line_sorted_by_x(self):
        # Temporal charts carry sort_by=x — the server ships a concrete type,
        # which skips the client's resolveAutoConfig (where auto time series
        # get their sort), so the hint must ride the config.
        shape = self._shape(
            ["week", "revenue"],
            [["2026-01-01", 10], ["2026-01-08", 20], ["2026-01-15", 15]],
        )
        assert shape == {"type": "line", "x": "week", "y": "revenue", "sort_by": "week"}

    def test_categorical_x_is_bar(self):
        shape = self._shape(
            ["region", "total"], [["North", 100], ["South", 200], ["West", 50]]
        )
        assert shape == {"type": "bar", "x": "region", "y": "total"}

    def test_numeric_x_is_scatter(self):
        shape = self._shape(["spend", "revenue"], [[10, 100], [20, 150], [30, 90]])
        assert shape == {"type": "scatter", "x": "spend", "y": "revenue"}

    def test_single_value_is_no_chart(self):
        assert self._shape(["total"], [[42]]) is None

    def test_single_row_breakdown_is_no_chart(self):
        # One row is a headline, even with a category column.
        assert self._shape(["region", "total"], [["North", 42]]) is None

    def test_no_numeric_column_is_no_chart(self):
        assert (
            self._shape(["region", "manager"], [["North", "Ann"], ["South", "Bob"]])
            is None
        )


# --------------------------------------------------------------------------- #
# Chart preferences — the extended type vocabulary + per-type shape rules
# --------------------------------------------------------------------------- #
class TestChartPrefs:
    TEMPORAL_3 = [
        ["2026-01-01", "email", 10],
        ["2026-01-08", "search", 20],
        ["2026-01-15", "email", 15],
    ]
    CATEGORICAL_3 = [["North", "email", 10], ["South", "search", 20]]

    @staticmethod
    def _shape(res, columns, rows):
        payload = serialize_result(QueryResult(columns=columns, rows=rows))
        return ask_engine.resolution_chart_shape(res, payload)

    def _sem(self, pref, by, series="channel"):
        return ask_engine.Resolution(
            kind="semantic", model="orders", metric="revenue",
            by=by, series=series, chart_pref=pref,
        )

    def test_aliases_normalize(self):
        assert ask_engine._parse_chart_pref({"chart": "river"}) == "themeriver"
        assert ask_engine._parse_chart_pref({"chart": "Stream"}) == "themeriver"
        assert ask_engine._parse_chart_pref({"chart": "donut"}) == "pie"
        assert ask_engine._parse_chart_pref({"chart": "area"}) == "line"
        assert ask_engine._parse_chart_pref({"chart": "hologram"}) is None

    def test_themeriver_on_temporal_series(self):
        shape = self._shape(
            self._sem("themeriver", "order_date"),
            ["order_date", "channel", "revenue"], self.TEMPORAL_3,
        )
        assert shape == {
            "type": "themeriver", "x": "order_date", "y": "revenue",
            "series_by": "channel",
        }

    def test_themeriver_categorical_falls_back_to_split_bar(self):
        shape = self._shape(
            self._sem("themeriver", "region"),
            ["region", "channel", "revenue"], self.CATEGORICAL_3,
        )
        assert shape["type"] == "bar" and shape["series_by"] == "channel"

    def test_heatmap_remaps_to_value_keys(self):
        shape = self._shape(
            self._sem("heatmap", "region"),
            ["region", "channel", "revenue"], self.CATEGORICAL_3,
        )
        assert shape == {
            "type": "heatmap", "x": "region", "y": "channel", "value": "revenue",
        }

    def test_sankey_remaps_to_value_keys(self):
        shape = self._shape(
            self._sem("sankey", "region"),
            ["region", "channel", "revenue"], self.CATEGORICAL_3,
        )
        assert shape == {
            "type": "sankey", "x": "region", "y": "channel", "value": "revenue",
        }

    def test_radar_keeps_series_split(self):
        shape = self._shape(
            self._sem("radar", "region"),
            ["region", "channel", "revenue"], self.CATEGORICAL_3,
        )
        assert shape["type"] == "radar" and shape["series_by"] == "channel"
        assert "sort_by" not in shape

    def test_gauge_on_scalar_result(self):
        res = ask_engine.Resolution(kind="query", name="q", chart_pref="gauge")
        assert self._shape(res, ["total"], [[42]]) == {
            "type": "gauge", "x": "total", "y": "total",
        }

    def test_gauge_on_multirow_is_dropped(self):
        res = ask_engine.Resolution(kind="query", name="q", chart_pref="gauge")
        shape = self._shape(res, ["region", "total"], [["N", 1], ["S", 2]])
        assert shape["type"] == "bar"

    def test_gauge_scalar_without_numeric_stays_none(self):
        res = ask_engine.Resolution(kind="query", name="q", chart_pref="gauge")
        assert self._shape(res, ["name"], [["Ann"]]) is None

    def test_calendar_on_temporal_two_col(self):
        res = ask_engine.Resolution(kind="query", name="q", chart_pref="calendar")
        shape = self._shape(
            res, ["day", "total"],
            [["2026-01-01", 1], ["2026-01-02", 2], ["2026-01-03", 3]],
        )
        assert shape["type"] == "calendar"
        assert "sort_by" not in shape

    def test_calendar_on_categorical_is_dropped(self):
        res = ask_engine.Resolution(kind="query", name="q", chart_pref="calendar")
        shape = self._shape(res, ["region", "total"], [["N", 1], ["S", 2]])
        assert shape["type"] == "bar"

    def test_heatmap_without_series_is_dropped(self):
        res = ask_engine.Resolution(kind="query", name="q", chart_pref="heatmap")
        shape = self._shape(res, ["region", "total"], [["N", 1], ["S", 2]])
        assert shape["type"] == "bar"

    def test_counter_aliases_normalize(self):
        for spoken in ("counters", "kpi", "KPIs", "stats", "big_number"):
            assert ask_engine._parse_chart_pref({"chart": spoken}) == "counter"

    def test_counter_pref_suppresses_chart_on_small_breakdown(self):
        res = ask_engine.Resolution(kind="query", name="q", chart_pref="counter")
        shape = self._shape(res, ["region", "total"], [["N", 1], ["S", 2]])
        assert shape is None

    def test_counter_pref_nonviable_keeps_inferred_chart(self):
        res = ask_engine.Resolution(kind="query", name="q", chart_pref="counter")
        rows = [[f"r{i}", i] for i in range(20)]  # too many cards
        shape = self._shape(res, ["region", "total"], rows)
        assert shape["type"] == "bar"


# --------------------------------------------------------------------------- #
# Display form (payload["display"]["form"] — answer-shaped client rendering)
# --------------------------------------------------------------------------- #
class TestDisplayForm:
    @staticmethod
    def _form(resolution, columns, rows):
        payload = serialize_result(QueryResult(columns=columns, rows=rows))
        chart = ask_engine.resolution_chart_shape(resolution, payload)
        return ask_engine.display_form(resolution, chart, payload)

    def test_list_resolution_is_table(self):
        res = ask_engine.Resolution(kind="list", model="sales", columns=["customer"])
        assert self._form(res, ["customer"], [["Ann"], ["Bob"]]) == "table"

    def test_charted_result_is_chart(self):
        res = ask_engine.Resolution(kind="query", name="q")
        form = self._form(
            res, ["region", "total"], [["North", 100], ["South", 200]]
        )
        assert form == "chart"

    def test_one_by_one_result_is_value(self):
        res = ask_engine.Resolution(kind="query", name="q")
        assert self._form(res, ["total"], [[42]]) == "value"

    def test_semantic_aggregate_without_by_is_value(self):
        # A semantic metric with no grouping is a headline even when the backend
        # emits an extra column alongside the measure.
        res = ask_engine.Resolution(kind="semantic", model="sales", metric="revenue")
        assert self._form(res, ["revenue", "note"], [[42, "x"]]) == "value"

    def test_single_row_multi_column_query_is_table(self):
        # A one-row record (no metric to headline) reads as a table, not a value.
        res = ask_engine.Resolution(kind="query", name="q")
        assert self._form(res, ["region", "manager"], [["North", "Ann"]]) == "table"

    def test_one_row_kpi_set_is_counters(self):
        # Several numbers in one row = a KPI set → counter cards, no wish needed.
        res = ask_engine.Resolution(kind="query", name="q")
        form = self._form(
            res, ["revenue", "orders", "aov"], [[4419.5, 120, 36.8]]
        )
        assert form == "counters"

    def test_counter_pref_breakdown_is_counters(self):
        res = ask_engine.Resolution(kind="query", name="q", chart_pref="counter")
        form = self._form(
            res, ["channel", "revenue"], [["email", 1], ["search", 2]]
        )
        assert form == "counters"

    def test_counter_pref_single_number_stays_value(self):
        res = ask_engine.Resolution(kind="query", name="q", chart_pref="counter")
        assert self._form(res, ["total"], [[42]]) == "value"

    def test_counters_style_hint(self):
        assert "counter cards" in ask_engine._style_hint("counters")

    def test_unchartable_rows_are_table(self):
        res = ask_engine.Resolution(kind="query", name="q")
        form = self._form(
            res, ["region", "manager"], [["North", "Ann"], ["South", "Bob"]]
        )
        assert form == "table"

    def test_style_hint_per_form(self):
        assert "One short sentence" in ask_engine._style_hint("value")
        assert "chart" in ask_engine._style_hint("chart")
        assert "table" in ask_engine._style_hint("table")
        assert ask_engine._style_hint("none") == ""


# --------------------------------------------------------------------------- #
# Generated answer titles (resolver "title" field + deterministic fallback)
# --------------------------------------------------------------------------- #
class TestAnswerTitle:
    def test_parse_resolution_cleans_title(self, tmp_path):
        proj = tmp_path / "proj"
        proj.mkdir()
        _make_lib_project(proj)
        project = load_project(proj)
        try:
            res = parse_resolution(
                '{"kind": "query", "name": "by_region", '
                '"title": "  Channel   share? "}',
                project,
                False,
            )
        finally:
            project.close()
        assert res.title == "Channel share"

    def test_title_angle_brackets_escaped_and_capped(self, tmp_path):
        proj = tmp_path / "proj"
        proj.mkdir()
        _make_lib_project(proj)
        project = load_project(proj)
        try:
            res = parse_resolution(
                '{"kind": "query", "name": "by_region", '
                '"title": "<LineChart /> ' + "x" * 200 + '"}',
                project,
                False,
            )
        finally:
            project.close()
        assert "<" not in res.title and ">" not in res.title
        assert "&lt;LineChart /&gt;" in res.title
        assert len(res.title) <= ask_engine.MAX_TITLE_CHARS + 10  # + escapes

    def test_non_string_title_degrades_empty(self, tmp_path):
        proj = tmp_path / "proj"
        proj.mkdir()
        _make_lib_project(proj)
        project = load_project(proj)
        try:
            res = parse_resolution(
                '{"kind": "query", "name": "by_region", "title": ["x"]}',
                project,
                False,
            )
        finally:
            project.close()
        assert res.title == ""

    def test_derive_title_semantic(self):
        res = ask_engine.Resolution(
            kind="semantic", model="sales", metric="revenue",
            by="order_date", series="channel",
        )
        assert ask_engine._derive_title(res) == "Revenue by order date per channel"

    def test_derive_title_list_and_query(self):
        assert (
            ask_engine._derive_title(
                ask_engine.Resolution(kind="list", model="sales", columns=["c"])
            )
            == "Latest sales"
        )
        assert (
            ask_engine._derive_title(
                ask_engine.Resolution(kind="query", name="finance.mrr_by_month")
            )
            == "Finance mrr by month"
        )

    def test_answer_title_prefers_generated(self):
        res = ask_engine.Resolution(kind="query", name="x", title="Nice title")
        assert ask_engine.answer_title(res) == "Nice title"

    def test_payload_carries_title(self, tmp_path):
        fake = FakeAdapter(
            '{"kind": "query", "name": "by_region", "title": "Revenue by region"}',
            "North leads.",
        )
        client = _client(tmp_path, fake=fake)
        r = client.post("/_dashdown/api/ask", json={"question": "how do regions do?"})
        assert r.status_code == 200, r.text
        assert r.json()["title"] == "Revenue by region"

    def test_payload_title_falls_back_to_derived(self, tmp_path):
        fake = FakeAdapter(
            '{"kind": "query", "name": "by_region"}', "North leads."
        )
        client = _client(tmp_path, fake=fake)
        r = client.post("/_dashdown/api/ask", json={"question": "regions?"})
        assert r.status_code == 200, r.text
        assert r.json()["title"] == "By region"


def test_style_hint_rides_build_ask_prompt():
    from dashdown.llm import AskDef, build_ask_prompt

    ask = AskDef(
        id="x",
        queries=(("q", "main"),),
        prompt="How is revenue?",
        style_hint="At most two short sentences.",
    )
    result = QueryResult(columns=["total"], rows=[[1]])
    prompt = build_ask_prompt(ask, [result])
    assert "Answer style: At most two short sentences." in prompt
    # An authored ask (no hint) is byte-identical to the pre-hint prompt shape.
    bare = AskDef(id="x", queries=(("q", "main"),), prompt="How is revenue?")
    assert "Answer style" not in build_ask_prompt(bare, [result])


def test_normalize_question_collapses_whitespace():
    assert normalize_question("  Which  Region\nLeads? ") == "which region leads?"


def test_rate_limited_counts_and_refuses():
    assert ask_engine.rate_limited(2) is False
    assert ask_engine.rate_limited(2) is False
    assert ask_engine.rate_limited(2) is True  # third within the window
    # A refused attempt is not recorded — the window still holds two marks.
    assert len(ask_engine._rate_marks) == 2
    assert ask_engine.rate_limited(0) is False  # 0 disables


def test_parse_ask_config_rate_limit_validation():
    from dashdown.project import parse_ask_config

    assert parse_ask_config(None).rate_limit == 60
    assert parse_ask_config({"rate_limit": 0}).rate_limit == 0
    assert parse_ask_config({"rate_limit": 5}).rate_limit == 5
    for bad in (-1, "10", True, 1.5):
        with pytest.raises(ValueError):
            parse_ask_config({"rate_limit": bad})


def test_parse_ask_config_non_mapping_raises():
    from dashdown.project import parse_ask_config

    with pytest.raises(ValueError):
        parse_ask_config("x")


@pytest.mark.parametrize("key", ["enabled", "allow_sql", "log"])
@pytest.mark.parametrize("bad", [1, 0, "yes", "true", 1.0])
def test_parse_ask_config_non_bool_flags_raise(key, bad):
    # None means "unset" (uses the default), so it's *not* a bad value here.
    from dashdown.project import parse_ask_config

    with pytest.raises(ValueError):
        parse_ask_config({key: bad})


@pytest.mark.parametrize("key", ["max_rows", "cache_ttl"])
@pytest.mark.parametrize("bad", [0, -1, "10", True, 1.5])
def test_parse_ask_config_non_positive_int_raise(key, bad):
    from dashdown.project import parse_ask_config

    with pytest.raises(ValueError):
        parse_ask_config({key: bad})


@pytest.mark.parametrize("key", ["max_rows", "cache_ttl"])
def test_parse_ask_config_valid_int_sets_field(key):
    from dashdown.project import parse_ask_config

    cfg = parse_ask_config({key: 10})
    assert getattr(cfg, key) == 10


@pytest.mark.parametrize("key", ["enabled", "allow_sql", "log"])
def test_parse_ask_config_valid_bool_sets_field(key):
    from dashdown.project import parse_ask_config

    assert getattr(parse_ask_config({key: False}), key) is False


# --------------------------------------------------------------------------- #
# Wire-row cap — an answer payload ships at most ASK_WIRE_ROWS rows
# --------------------------------------------------------------------------- #
class TestWireRowCap:
    def _big_query_client(self, tmp_path: Path, n: int) -> TestClient:
        """A project with a `big` library query generating `n` rows, resolved to
        it by the fake adapter."""
        proj = tmp_path / "proj"
        proj.mkdir()
        _make_lib_project(proj)
        (proj / "queries" / "big.sql").write_text(
            f"SELECT i AS n FROM range({n}) AS t(i)\n", encoding="utf-8"
        )
        app = create_app(proj)
        app.state.project.llm_adapter = FakeAdapter(
            '{"kind": "query", "name": "big", "params": {}}', "Big answer."
        )
        return TestClient(app)

    def test_over_cap_ships_500_and_truncation_fields(self, tmp_path):
        client = self._big_query_client(tmp_path, 600)
        body = client.post("/_dashdown/api/ask", json={"question": "big list"}).json()
        assert body["resolved"]["kind"] == "query"
        assert len(body["rows"]) == ask_engine.ASK_WIRE_ROWS == 500
        assert body["truncated"] is True
        assert body["total_rows"] == 600

    def test_at_or_under_cap_omits_truncation_fields(self, tmp_path):
        client = self._big_query_client(tmp_path, 300)
        body = client.post("/_dashdown/api/ask", json={"question": "small list"}).json()
        assert len(body["rows"]) == 300
        assert "truncated" not in body
        assert "total_rows" not in body

    def test_cached_payload_is_capped(self, tmp_path):
        client = self._big_query_client(tmp_path, 600)
        first = client.post("/_dashdown/api/ask", json={"question": "big list"}).json()
        assert first["cached"] is False
        # The cache stores the capped payload — the replay is identical.
        second = client.post("/_dashdown/api/ask", json={"question": "big list"}).json()
        assert second["cached"] is True
        assert len(second["rows"]) == 500
        assert second["truncated"] is True
        assert second["total_rows"] == 600


# --------------------------------------------------------------------------- #
# Endpoint — the resolution ladder end to end
# --------------------------------------------------------------------------- #
class TestAskEndpoint:
    def test_disabled_llm_returns_notice(self, tmp_path):
        client = _client(tmp_path, fake=None, llm=False)
        r = client.post("/_dashdown/api/ask", json={"question": "revenue by region"})
        assert r.status_code == 200
        body = r.json()
        assert body["answer_html"] == ""
        assert "no LLM provider is configured" in body["notice"]

    def test_disabled_ask_returns_notice(self, tmp_path):
        client = _client(
            tmp_path, fake=FakeAdapter(), extra_yaml="ask:\n  enabled: false\n"
        )
        r = client.post("/_dashdown/api/ask", json={"question": "revenue by region"})
        assert r.status_code == 200
        assert "disabled" in r.json()["notice"]

    def test_empty_question_is_400(self, tmp_path):
        client = _client(tmp_path, fake=FakeAdapter())
        assert client.post("/_dashdown/api/ask", json={"question": "  "}).status_code == 400
        assert client.post("/_dashdown/api/ask", json={}).status_code == 400

    def test_library_query_resolution_with_params(self, tmp_path):
        fake = FakeAdapter(
            '{"kind": "query", "name": "by_region", "params": {"region": "North"}}',
            "Revenue comes only from **North** here.",
        )
        client = _client(tmp_path, fake=fake)
        r = client.post("/_dashdown/api/ask", json={"question": "revenue in the north"})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["resolved"]["kind"] == "query"
        assert body["resolved"]["query_name"] == "by_region"
        assert body["resolved"]["connector"] == "main"
        assert "by_region" in body["resolved"]["provenance"]
        # The param substituted → only the North row reached the result.
        assert body["columns"] == ["region", "total"]
        assert len(body["rows"]) == 1
        assert body["rows"][0][0] == "North"
        assert "<strong>North</strong>" in body["answer_html"]
        assert body["cached"] is False
        # Two LLM calls: resolve, then answer.
        assert len(fake.calls) == 2

    def test_multi_row_result_infers_chart(self, tmp_path):
        fake = FakeAdapter(
            '{"kind": "query", "name": "by_region", "params": {}}',
            "North leads.",
        )
        client = _client(tmp_path, fake=fake)
        r = client.post("/_dashdown/api/ask", json={"question": "revenue by region"})
        body = r.json()
        assert body["chart"] == {"type": "bar", "x": "region", "y": "total"}
        assert len(body["rows"]) == 3

    def test_allow_sql_off_rejects_sql_kind(self, tmp_path):
        # Both replies pick the forbidden sql rung: the validation failure gets
        # ONE self-repair retry (quoting the error), then degrades to none.
        fake = FakeAdapter(
            '{"kind": "sql", "sql": "SELECT 1"}',
            '{"kind": "sql", "sql": "SELECT 1"}',
        )
        client = _client(tmp_path, fake=fake)
        r = client.post("/_dashdown/api/ask", json={"question": "raw please"})
        assert r.status_code == 200
        body = r.json()
        assert body["resolved"]["kind"] == "none"
        assert body["columns"] is None
        assert body["chart"] is None
        assert "allow_sql" in body["answer_html"] or "allow_sql" in body["answer_text"]
        # Resolve + one repair retry; never an answer call for a `none`.
        assert len(fake.calls) == 2
        assert "previous response was invalid" in fake.calls[1][1]

    def test_allow_sql_on_runs_raw_sql(self, tmp_path):
        fake = FakeAdapter(
            '{"kind": "sql", "sql": "SELECT region, SUM(amount) AS total '
            "FROM sales GROUP BY region ORDER BY total DESC\"}",
            "South is the largest.",
        )
        client = _client(tmp_path, fake=fake, extra_yaml="ask:\n  allow_sql: true\n")
        r = client.post("/_dashdown/api/ask", json={"question": "revenue by region raw"})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["resolved"]["kind"] == "sql"
        assert len(body["rows"]) == 3
        assert len(fake.calls) == 2

    def test_invalid_json_degrades_to_none(self, tmp_path):
        # Non-JSON output is a validation failure → one self-repair retry, then
        # (still non-JSON) degrades to none. Two resolver calls, no answer call.
        fake = FakeAdapter(
            "Sorry, I cannot help with that.",
            "Still not JSON, sorry.",
        )
        client = _client(tmp_path, fake=fake)
        r = client.post("/_dashdown/api/ask", json={"question": "??"})
        assert r.status_code == 200
        body = r.json()
        assert body["resolved"]["kind"] == "none"
        assert body["answer_text"]  # the model's reason rides in the answer
        assert len(fake.calls) == 2

    def test_none_is_cached_only_briefly(self, tmp_path):
        # A "none" may be a transient resolver misroute; caching it for the full
        # cache_ttl would pin a valid question as unanswerable for an hour.
        fake = FakeAdapter("garbage", "garbage")
        client = _client(tmp_path, fake=fake)
        client.post("/_dashdown/api/ask", json={"question": "flaky question"})
        entries = list(ask_engine._answer_cache.values())
        assert len(entries) == 1
        _, expiry = entries[0]
        assert expiry - time.monotonic() <= ask_engine.NONE_ANSWER_TTL + 1

    def test_non_object_body_is_400_not_422(self, tmp_path):
        # The endpoint owns its malformed-body contract (400 + message), so the
        # body param must accept arbitrary JSON instead of letting FastAPI 422.
        client = _client(tmp_path, fake=FakeAdapter("x"))
        for payload in ("just a string", [1, 2, 3], None):
            r = client.post("/_dashdown/api/ask", json=payload)
            assert r.status_code == 400, r.text

    def test_rate_limit_returns_429(self, tmp_path):
        # Two distinct questions with rate_limit: 1 — the second cache-miss is
        # refused with 429 and never reaches the LLM.
        fake = FakeAdapter(
            '{"kind": "query", "name": "by_region", "params": {}}', "One.",
            '{"kind": "query", "name": "by_region", "params": {}}', "Two.",
        )
        client = _client(tmp_path, fake=fake, extra_yaml="ask:\n  rate_limit: 1\n")
        r1 = client.post("/_dashdown/api/ask", json={"question": "first question"})
        assert r1.status_code == 200, r1.text
        calls_after_first = len(fake.calls)
        r2 = client.post("/_dashdown/api/ask", json={"question": "second question"})
        assert r2.status_code == 429
        assert "rate limit" in r2.json()["detail"]
        assert len(fake.calls) == calls_after_first  # no LLM spend on refusal

    def test_cache_hit_does_not_consume_rate_limit(self, tmp_path):
        fake = FakeAdapter(
            '{"kind": "query", "name": "by_region", "params": {}}', "One."
        )
        client = _client(tmp_path, fake=fake, extra_yaml="ask:\n  rate_limit: 1\n")
        assert (
            client.post("/_dashdown/api/ask", json={"question": "same q"}).status_code
            == 200
        )
        # Repeat of the same question is a cache hit — allowed despite the
        # exhausted per-minute budget.
        r = client.post("/_dashdown/api/ask", json={"question": "same q"})
        assert r.status_code == 200
        assert r.json()["cached"] is True

    def test_rate_limit_zero_disables(self, tmp_path):
        fake = FakeAdapter(
            '{"kind": "query", "name": "by_region", "params": {}}', "One.",
            '{"kind": "query", "name": "by_region", "params": {}}', "Two.",
        )
        client = _client(tmp_path, fake=fake, extra_yaml="ask:\n  rate_limit: 0\n")
        for q in ("q one", "q two"):
            assert (
                client.post("/_dashdown/api/ask", json={"question": q}).status_code
                == 200
            )


    def test_cache_hit_skips_llm(self, tmp_path):
        fake = FakeAdapter(
            '{"kind": "query", "name": "by_region", "params": {}}',
            "North leads.",
        )
        client = _client(tmp_path, fake=fake)
        r1 = client.post("/_dashdown/api/ask", json={"question": "revenue by region"})
        assert r1.json()["cached"] is False
        assert len(fake.calls) == 2
        # Same question → served from the answer cache, no new LLM calls.
        r2 = client.post("/_dashdown/api/ask", json={"question": "revenue by region"})
        assert r2.json()["cached"] is True
        assert len(fake.calls) == 2

    def test_refresh_bypasses_cache(self, tmp_path):
        fake = FakeAdapter(
            '{"kind": "query", "name": "by_region", "params": {}}',
            "North leads.",
            '{"kind": "query", "name": "by_region", "params": {}}',
            "North still leads.",
        )
        client = _client(tmp_path, fake=fake)
        client.post("/_dashdown/api/ask", json={"question": "revenue by region"})
        r = client.post(
            "/_dashdown/api/ask",
            json={"question": "revenue by region", "refresh": True},
        )
        assert r.json()["cached"] is False
        assert len(fake.calls) == 4

    def test_query_execution_failure_is_500(self, tmp_path):
        # A library query whose name resolves but whose SQL is broken → 500.
        proj = tmp_path / "proj"
        proj.mkdir()
        _make_lib_project(proj)
        (proj / "queries" / "broken.sql").write_text(
            "SELECT * FROM no_such_table\n", encoding="utf-8"
        )
        app = create_app(proj)
        fake = FakeAdapter('{"kind": "query", "name": "broken", "params": {}}')
        app.state.project.llm_adapter = fake
        client = TestClient(app)
        r = client.post("/_dashdown/api/ask", json={"question": "boom"})
        assert r.status_code == 500

    def test_llm_failure_is_502(self, tmp_path):
        class BoomAdapter(FakeAdapter):
            def complete(self, system, prompt):
                raise RuntimeError("rate limited")

        client = _client(tmp_path, fake=BoomAdapter())
        r = client.post("/_dashdown/api/ask", json={"question": "revenue by region"})
        assert r.status_code == 502
        assert "rate limited" in r.json()["detail"]

    def test_ask_log_written(self, tmp_path):
        fake = FakeAdapter(
            '{"kind": "query", "name": "by_region", "params": {}}',
            "North leads.",
        )
        proj = tmp_path / "proj"
        proj.mkdir()
        _make_lib_project(proj)
        app = create_app(proj)
        app.state.project.llm_adapter = fake
        client = TestClient(app)
        client.post("/_dashdown/api/ask", json={"question": "revenue by region"})
        log_path = proj / ".dashdown" / "ask_log.jsonl"
        assert log_path.is_file()
        (line,) = log_path.read_text(encoding="utf-8").splitlines()
        entry = json.loads(line)
        assert entry["kind"] == "query"
        assert entry["rows"] == 3
        assert entry["question"] == "revenue by region"
        assert "provenance" in entry and "duration_ms" in entry

    def test_ask_log_opt_out(self, tmp_path):
        fake = FakeAdapter(
            '{"kind": "query", "name": "by_region", "params": {}}',
            "North leads.",
        )
        proj = tmp_path / "proj"
        proj.mkdir()
        _make_lib_project(proj, extra_yaml="ask:\n  log: false\n")
        app = create_app(proj)
        app.state.project.llm_adapter = fake
        client = TestClient(app)
        client.post("/_dashdown/api/ask", json={"question": "revenue by region"})
        assert not (proj / ".dashdown" / "ask_log.jsonl").exists()


# --------------------------------------------------------------------------- #
# Template context — ask_enabled threaded into page()
# --------------------------------------------------------------------------- #
class TestAskEnabledTemplate:
    def test_page_renders_with_ask_enabled(self, tmp_path):
        # The template treats ask_enabled default-false; here we only assert the
        # page renders (200) with the llm block on — the frontend markup is
        # another agent's scope, so we don't assert on specific ask-box HTML.
        client = _client(tmp_path, fake=FakeAdapter())
        assert client.get("/").status_code == 200


# --------------------------------------------------------------------------- #
# Semantic resolution (needs the BSL/Ibis extra) — end to end through execution
# --------------------------------------------------------------------------- #
def _semantic_project(tmp_path: Path) -> Path:
    dst = tmp_path / "sem_proj"
    shutil.copytree(
        _SEMANTIC_EXAMPLE,
        dst,
        ignore=shutil.ignore_patterns("__pycache__", "*.duckdb*", "sources.yaml"),
    )
    (dst / "sources.yaml").write_text("main:\n  type: csv\n  directory: data\n")
    cfg = (dst / "dashdown.yaml").read_text()
    cfg += "\nllm:\n  provider: mistral\n  api_key: dummy\n"
    (dst / "dashdown.yaml").write_text(cfg)
    return dst


@needs_bsl
class TestSemanticResolution:
    def test_relative_date_fails_validation_not_execution(self, tmp_path):
        # "this week" as a date literal must be a VALIDATION failure (invalid →
        # self-repair eligible), never reach the backend where a date column vs
        # string comparison raises a raw IbisTypeError into the panel.
        project = load_project(_semantic_project(tmp_path))
        try:
            res = parse_resolution(
                '{"kind": "semantic", "model": "sales", "metric": "revenue", '
                '"by": "region", "date_start": "this week"}',
                project,
                False,
            )
        finally:
            project.close()
        assert res.kind == "none"
        assert res.invalid is True
        assert "ISO date" in res.reason and "this week" in res.reason

    def test_relative_time_dimension_filter_fails_validation(self, tmp_path):
        project = load_project(_semantic_project(tmp_path))
        try:
            res = parse_resolution(
                '{"kind": "semantic", "model": "sales", "metric": "revenue", '
                '"by": "region", "filters": {"order_date": ["this week"]}}',
                project,
                False,
            )
        finally:
            project.close()
        assert res.kind == "none"
        assert "date column" in res.reason
        assert "date_start" in res.reason

    def test_iso_dates_and_categorical_filters_pass(self, tmp_path):
        project = load_project(_semantic_project(tmp_path))
        try:
            res = parse_resolution(
                '{"kind": "semantic", "model": "sales", "metric": "revenue", '
                '"by": "region", "filters": {"region": ["North"], '
                '"order_date": ["2026-07-13"]}, '
                '"date_start": "2026-07-13", "date_end": "2026-07-19"}',
                project,
                False,
            )
        finally:
            project.close()
        assert res.kind == "semantic"
        assert res.date_start == "2026-07-13"
        assert res.date_end == "2026-07-19"

    def test_relative_date_repairs_to_iso(self, tmp_path):
        # End-to-end: first resolution carries "this week", the self-repair
        # retry (fed the validation reason + today's date in the prompt)
        # returns concrete ISO boundaries → the answer succeeds.
        fake = FakeAdapter(
            '{"kind": "semantic", "model": "sales", "metric": "revenue", '
            '"by": "region", "date_start": "this week"}',
            '{"kind": "semantic", "model": "sales", "metric": "revenue", '
            '"by": "region", "date_start": "2020-01-01", "date_end": "2030-01-01"}',
            "Revenue held steady this week.",
        )
        app = create_app(_semantic_project(tmp_path))
        app.state.project.llm_adapter = fake
        client = TestClient(app)
        r = client.post(
            "/_dashdown/api/ask",
            json={"question": "what happened this week with channels"},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["resolved"]["kind"] == "semantic"
        assert body["resolved"]["detail"]["date_start"] == "2020-01-01"
        # resolve + repair + answer = three calls.
        assert len(fake.calls) == 3
        # The repair prompt quoted the reason and the prompt anchors today.
        assert "ISO date" in fake.calls[1][1]
        assert "Today's date:" in fake.calls[0][1]

    def test_semantic_happy_path(self, tmp_path):
        fake = FakeAdapter(
            '{"kind": "semantic", "model": "sales", "metric": "revenue", '
            '"by": "region", "filters": {}, "date_start": "", "date_end": ""}',
            "**North** drives the most revenue.",
        )
        app = create_app(_semantic_project(tmp_path))
        app.state.project.llm_adapter = fake
        client = TestClient(app)

        r = client.post(
            "/_dashdown/api/ask",
            json={"question": "which region drives revenue?"},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["resolved"]["kind"] == "semantic"
        assert body["resolved"]["query_name"].startswith("_sem.")
        assert body["resolved"]["connector"] == "main"
        assert body["resolved"]["detail"]["metric"] == "revenue"
        # revenue by region → categorical x → bar.
        assert body["chart"]["type"] == "bar"
        assert "<strong>North</strong>" in body["answer_html"]
        assert len(fake.calls) == 2

    def test_grain_on_categorical_by_is_dropped(self, tmp_path):
        # A hallucinated grain on a non-time dimension must not 500 — it's
        # soft-dropped (same forgiveness as unknown filter keys) and the
        # resolution still routes.
        fake = FakeAdapter(
            '{"kind": "semantic", "model": "sales", "metric": "revenue", '
            '"by": "region", "grain": "month", "filters": {}}',
            "North leads.",
        )
        app = create_app(_semantic_project(tmp_path))
        app.state.project.llm_adapter = fake
        client = TestClient(app)
        r = client.post(
            "/_dashdown/api/ask", json={"question": "revenue by region monthly"}
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["resolved"]["kind"] == "semantic"
        assert body["resolved"]["detail"]["grain"] is None

    def test_unknown_metric_is_none(self, tmp_path):
        # An off-catalog metric is a validation failure → one self-repair retry
        # (both replies hallucinate here), then none. Two resolver calls.
        fake = FakeAdapter(
            '{"kind": "semantic", "model": "sales", "metric": "ghost", "by": "region"}',
            '{"kind": "semantic", "model": "sales", "metric": "ghost", "by": "region"}',
        )
        app = create_app(_semantic_project(tmp_path))
        app.state.project.llm_adapter = fake
        client = TestClient(app)
        r = client.post("/_dashdown/api/ask", json={"question": "ghost metric"})
        assert r.status_code == 200
        assert r.json()["resolved"]["kind"] == "none"
        assert len(fake.calls) == 2


# --------------------------------------------------------------------------- #
# CLI: `dashdown ask`
# --------------------------------------------------------------------------- #
class TestAskCLI:
    def test_ask_prints_provenance_table_and_answer(self, tmp_path, monkeypatch):
        proj = tmp_path / "proj"
        proj.mkdir()
        _make_lib_project(proj)
        fake = FakeAdapter(
            '{"kind": "query", "name": "by_region", "params": {}}',
            "North leads the pack.",
        )
        import dashdown.project as project_mod

        monkeypatch.setattr(project_mod, "create_adapter", lambda cfg: fake)
        res = runner.invoke(cli_app, ["ask", "revenue by region", "-p", str(proj)])
        assert res.exit_code == 0, res.stdout + res.stderr
        assert "Provenance:" in res.stderr
        assert "by_region" in res.stderr
        # Result table + answer land on stdout.
        assert "region" in res.stdout and "total" in res.stdout
        assert "North leads the pack." in res.stdout

    def test_ask_json_output(self, tmp_path, monkeypatch):
        proj = tmp_path / "proj"
        proj.mkdir()
        _make_lib_project(proj)
        fake = FakeAdapter(
            '{"kind": "query", "name": "by_region", "params": {}}',
            "North leads.",
        )
        import dashdown.project as project_mod

        monkeypatch.setattr(project_mod, "create_adapter", lambda cfg: fake)
        res = runner.invoke(
            cli_app, ["ask", "revenue by region", "-p", str(proj), "--json"]
        )
        assert res.exit_code == 0, res.stdout + res.stderr
        payload = json.loads(res.stdout)
        assert payload["resolved"]["kind"] == "query"
        assert payload["chart"]["type"] == "bar"

    def test_ask_disabled_llm_exits_nonzero(self, tmp_path):
        proj = tmp_path / "proj"
        proj.mkdir()
        _make_lib_project(proj, llm=False)
        res = runner.invoke(cli_app, ["ask", "revenue by region", "-p", str(proj)])
        assert res.exit_code == 1
        assert "no LLM provider" in res.stderr
