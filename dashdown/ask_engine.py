"""Runtime ask engine — a natural-language question answered from a menu we control.

The author-pinned ``<Ask />`` block (``llm.py``) answers a *fixed* prompt over a
*fixed* query. This module is its runtime sibling: an operator types a free-form
question and the engine maps it — via one constrained LLM call — onto an existing,
already-trusted data source, runs it, and feeds the result to the *same*
``generate_answer`` path an ``<Ask />`` uses. The page is demoted from "the
product" to "one output of an answer".

**The resolution ladder (the whole safety story).** The LLM never emits free-form
SQL by default. It is shown a *catalog* (semantic models + library/python queries)
and returns a strict-JSON resolution choosing one rung, most-constrained first:

1. ``semantic`` — pick ``metric``/``by``/``grain``/``filters`` from the introspected
   semantic catalog. Values are pure JSON data (the semantic layer has *no*
   string-interpolation surface), so there is no injection path at all.
2. ``query`` — pick an *existing named* library/python query and supply ``${param}``
   values, which pass through the one blessed context-aware ``_substitute_params``
   (values become quoted literals; injection-inert).
3. ``sql`` — raw SQL, offered **only** behind ``ask.allow_sql: true`` (default
   false) and clearly marked in provenance.

Every answer therefore carries provenance naming exactly which rung and which
definition produced it. A malformed / hallucinated resolution degrades to kind
``none`` (the model's reason, no data) — **never a 500**.

**Two LLM calls per cache-miss:** one to *resolve* (this module), one to *answer*
(``llm.generate_answer``, reused verbatim — including the chart-annotation protocol
when a chart shape was inferred). The answer is cached by
``(normalized_question, frozen(params))`` with an ``ask.cache_ttl`` TTL so a repeat
question doesn't re-bill, and every runtime ask is appended to a project-local
``.dashdown/ask_log.jsonl`` — the seed of the telemetry moat, itself queryable by
Dashdown.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import threading
import time
from collections import OrderedDict, deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from dashdown.chart_annotations import ChartContext, build_chart_context
from dashdown.data.base import QueryResult
from dashdown.llm import (
    AskDef,
    generate_answer,
    resolve_model_name,
    unavailable_notice,
)
from dashdown.render.markdown import render_markdown_text
from dashdown.render.pipeline import (
    DEFAULT_CACHE_TTL,
    _PARAM_RE,
    _freeze_params,
    _substitute_params,
    cache_result,
    get_cached_result,
    get_python_query_def,
    get_query_def,
    register_python_query_def,
    serialize_result,
)
from dashdown.python_query import run_python_query
from dashdown.semantic import (
    DATE_END_PARAM,
    DATE_START_PARAM,
    GRAIN_TOKENS,
    SemanticListRef,
    build_semantic_list_spec,
    build_semantic_spec,
    resolve_ref,
    semantic_list_query_name,
)

if TYPE_CHECKING:  # pragma: no cover - typing only (avoids an import cycle)
    from dashdown.project import Project

log = logging.getLogger(__name__)

# Defensive cap on rows returned by the raw-SQL rung: a hand-written SELECT has no
# author-set LIMIT and its result feeds both the model payload and the wire.
MAX_SQL_ROWS = 1000

# The runtime cache keys on free-form operator text, so unlike the authored
# <Ask /> cache (bounded by the finite set of ask ids) its key space is
# unbounded — bound it like pipeline's _result_cache.
MAX_CACHED_ANSWERS = 256

# A kind-"none" payload is cached only briefly: a transient resolver misroute
# must not make a valid question "unanswerable" for the full cache_ttl.
NONE_ANSWER_TTL = 60

# ---- Rate limit (cost control) -------------------------------------------- #
# Every cache-miss ask is two billable LLM calls, and (unlike the authored
# <Ask />, whose prompts are a fixed authored set) the runtime endpoint accepts
# arbitrary questions — so an un-authed dashboard with ask enabled would be an
# open LLM-spend endpoint. A process-wide sliding window bounds the burn:
# generous for humans, a wall for crawlers/loops. `ask.rate_limit` (per minute)
# configures it; 0 disables. Cache hits never consume the budget.
_RATE_WINDOW_SECONDS = 60.0
_rate_lock = threading.Lock()
_rate_marks: deque[float] = deque()


def rate_limited(limit: int) -> bool:
    """Record one ask attempt; True when it exceeds ``limit`` per minute.

    Thread-safe (the endpoint runs in FastAPI's threadpool). A refused attempt
    is *not* recorded, so a client that backs off recovers as the window slides."""
    if limit <= 0:
        return False
    now = time.monotonic()
    with _rate_lock:
        while _rate_marks and now - _rate_marks[0] > _RATE_WINDOW_SECONDS:
            _rate_marks.popleft()
        if len(_rate_marks) >= limit:
            return True
        _rate_marks.append(now)
        return False

# The valid resolution kinds the parser recognizes; anything else degrades to none.
_KINDS = frozenset({"semantic", "list", "query", "sql", "none"})

# Chart types an operator may explicitly ask for ("as a funnel", "pie chart
# of…"). All of them consume the same {x: label, y: value} config the panel
# chart already speaks (chart.js buildChartOption), so honoring a preference
# is a type override — applied only when the result's shape supports it.
CHART_PREFS = frozenset({"line", "bar", "scatter", "pie", "funnel", "treemap"})

# Bounds for the list rung's row cap — a hallucinated/absent limit is clamped here
# (the request feeds both the model payload and the wire).
MIN_LIST_LIMIT = 1
MAX_LIST_LIMIT = 500
DEFAULT_LIST_LIMIT = 50
# Cap on how many dimension columns one list request may project (a runaway
# `columns` list is trimmed to this many valid dimensions).
MAX_LIST_COLUMNS = 8


# --------------------------------------------------------------------------- #
# Errors — mapped by the endpoint to distinct HTTP codes (502 vs 500).
# --------------------------------------------------------------------------- #
class AskLLMError(RuntimeError):
    """An LLM call (resolve or answer) failed — the endpoint maps this to 502."""


class AskQueryError(RuntimeError):
    """Executing the resolved query failed — the endpoint maps this to 500."""


class AskRateLimitError(RuntimeError):
    """The process-wide ask rate limit was hit — the endpoint maps this to 429."""


class AskListUnsupported(RuntimeError):
    """A ``list`` resolution targeted a semantic backend without ``build_list_spec``.

    Raised out of :func:`_execute_list` (the backend's ``NotImplementedError``) and
    caught by :func:`answer_question`, which degrades it to a kind ``none`` payload —
    so a routed-but-unsupported list request is never a 500, and never bills the
    second (answer) LLM call."""


# --------------------------------------------------------------------------- #
# Catalog — the menu the resolver LLM chooses from
# --------------------------------------------------------------------------- #
def build_ask_catalog(project: "Project") -> dict[str, Any]:
    """The resolvable data sources, as a plain dict the resolver prompt serializes.

    Three families, mirroring the resolution ladder: semantic models (measures /
    dimensions / time dimension / grain vocabulary), SQL/DAX library queries
    (name + description + connector + ``${param}`` names), and Python queries
    (name + description + connector). The LLM only ever sees these names — never a
    raw table schema — so it chooses *from a menu we control*.
    """
    semantic_models: list[dict[str, Any]] = []
    for name, handle in sorted(project.semantic_models.items()):
        # Expose the short (last-segment) names an author writes — a joined model
        # prefixes canonical names, but both spellings resolve via the *_lookup.
        measures = sorted({m.split(".")[-1] for m in handle.measures})
        dims = sorted({d.split(".")[-1] for d in handle.dimensions})
        time_dim = handle.time_dimension.split(".")[-1] if handle.time_dimension else None
        semantic_models.append(
            {
                "model": name,
                "measures": measures,
                "dimensions": dims,
                "time_dimension": time_dim,
                "grains": list(GRAIN_TOKENS),
            }
        )

    queries: list[dict[str, Any]] = []
    for name, spec in sorted(project.queries.items()):
        queries.append(
            {
                "name": name,
                "description": spec.description or "",
                "connector": spec.connector,
                "params": sorted(set(_PARAM_RE.findall(spec.sql))),
            }
        )

    python_queries: list[dict[str, Any]] = []
    for name, spec in sorted(project.python_queries.items()):
        python_queries.append(
            {
                "name": name,
                "description": spec.description or "",
                "connector": spec.connector,
            }
        )

    return {
        "semantic_models": semantic_models,
        "queries": queries,
        "python_queries": python_queries,
    }


# Bounds for the sql_tables schema hint: enough for a real project's core
# tables, small enough to not swamp the resolver prompt.
_SCHEMA_HINT_MAX_TABLES = 40
_SCHEMA_HINT_MAX_COLUMNS = 400


def sql_schema_hint(project: "Project") -> dict[str, list[str]] | None:
    """Best-effort ``{table: [columns...]}`` for the default connector.

    Only ever attached to the catalog when ``ask.allow_sql`` is on: the sql rung
    is useless blind (a model that can't see table/column names guesses them),
    and a project that enabled raw SQL has already granted the operator full
    read access, so showing the schema reveals nothing the flag didn't. Speaks
    ``information_schema`` (DuckDB-family, postgres, mysql, …); any failure —
    no default connector, a dialect without information_schema — returns
    ``None`` and the rung simply stays schema-blind rather than erroring."""
    connector_name = project.default_connector or ""
    conn = project.connectors.get(connector_name)
    if conn is None:
        return None
    try:
        result = conn.query(
            "SELECT table_name, column_name FROM information_schema.columns "
            "ORDER BY table_name, ordinal_position "
            f"LIMIT {_SCHEMA_HINT_MAX_COLUMNS}"
        )
    except Exception:  # noqa: BLE001 - a hint, never a failure
        return None
    tables: dict[str, list[str]] = {}
    for row in result.rows:
        if len(row) < 2:
            continue
        table, column = str(row[0]), str(row[1])
        if table not in tables and len(tables) >= _SCHEMA_HINT_MAX_TABLES:
            continue
        tables.setdefault(table, []).append(column)
    return tables or None


# --------------------------------------------------------------------------- #
# Resolver prompt
# --------------------------------------------------------------------------- #
RESOLVER_SYSTEM_PROMPT = (
    "You are a query router for an analytics engine. You are given a catalog of the "
    "data sources a dashboard exposes and a natural-language question. Map the "
    "question to exactly ONE data source from the catalog and return a single JSON "
    "object describing how to run it. Choose the most specific source that answers "
    "the question.\n\n"
    "Output ONLY the JSON object — no prose, no explanation, no markdown code "
    "fences. Use one of these shapes:\n\n"
    '  {"kind": "semantic", "model": "<model>", "metric": "<measure>", '
    '"by": "<dimension or empty>", "series": "<second dimension or empty>", '
    '"grain": "<grain or empty>", '
    '"filters": {"<dimension>": ["value", ...]}, "date_start": "", "date_end": ""}\n'
    '  {"kind": "list", "model": "<model>", "columns": ["<dimension>", ...], '
    '"order_by": "<dimension or empty>", "desc": true, "limit": 10, '
    '"filters": {"<dimension>": ["value", ...]}, "date_start": "", "date_end": ""}\n'
    '  {"kind": "query", "name": "<query name>", "params": {"<param>": "<value>"}}\n'
    '  {"kind": "none", "reason": "<why the catalog cannot answer this>"}\n\n'
    "Rules: `metric`, `by`, and `series` must be names listed under the chosen "
    "model. `by` is exactly ONE dimension — when the question needs a second "
    "grouping (e.g. revenue by week split per channel), put the second dimension "
    "in `series`, never a comma-joined pair. `grain` "
    "must be one of the model's grains and only makes sense with a time-dimension "
    "`by`; `filters` keys must be the model's dimensions. `name` must be a query "
    "listed in the catalog.\n\n"
    "Semantic models answer AGGREGATE questions only — a measure grouped by "
    "dimensions. A detail/list question ('show/list the last N orders', 'which "
    "customers ordered recently', 'give me the list of …') is NOT a semantic "
    "resolution. Answer it EITHER with a catalog query whose description matches "
    'the requested rows (prefer this when one fits — an author curated it), OR with '
    'kind "list" over a semantic model when no query matches. For a `list`: '
    "`columns` are the model's dimensions (1-8 of them, the fields to show); "
    "`order_by` must be one of the SELECTED `columns` — for 'latest/most recent' "
    "questions always include the model's time dimension in `columns` and order "
    "by it, with `desc: true` for newest/largest first; `limit` is an integer "
    "1-500 (default 50). A `list` never carries a `metric` — that's `semantic`. "
    "Any routed resolution may additionally carry "
    '"chart": one of line|bar|scatter|pie|funnel|treemap — set it ONLY when the '
    "operator asked for a specific chart type ('as a funnel', 'pie chart of…'); "
    "otherwise omit it and a sensible chart is inferred. A chart wish is never a "
    "reason to refuse: resolve the DATA the question needs and let `chart` carry "
    "the presentation. "
    "An OPEN or underspecified question ('show me something interesting', 'a "
    "random chart', 'give me an overview of the data') is also never a refusal: "
    "exercise judgment and pick a sensible resolution from the catalog yourself — "
    "e.g. a headline measure over the time dimension, or by its most natural "
    "grouping. Vague means CHOOSE, not refuse. "
    'Only when NO capability offered here can answer the question, return kind '
    '"none" — and in '
    "`reason`, say what the catalog CAN answer that comes closest, so the operator "
    "knows what to ask instead. Never invent names."
)

# Appended to the system prompt only when ask.allow_sql is on (rung 3 opt-in).
_ALLOW_SQL_CLAUSE = (
    "\n\nRaw SQL is also permitted for this project: "
    '{"kind": "sql", "sql": "SELECT ..."}. Prefer a catalog source whenever one '
    "answers the question — but when none of them can EXPRESS the question (a "
    "superlative single row, an ad-hoc join or calculation), PREFER a sql "
    'resolution over kind "none". The catalog\'s `sql_tables` lists the tables '
    "and columns raw SQL can reference on the default connector. SELECT/WITH "
    "statements only."
)


def build_resolver_prompt(
    catalog: dict[str, Any],
    question: str,
    allow_sql: bool,
    history: list[dict[str, Any]] | None = None,
) -> tuple[str, str]:
    """Return ``(system, user)`` for the resolution call.

    The ``sql`` rung is described in the system prompt **only** when ``allow_sql``
    — a model can't pick a rung it was never told about (belt to the validator's
    braces, which reject ``sql`` regardless when the config is off).

    When ``history`` (a sanitized, oldest-first list of ``{question, resolved}``
    entries from the operator's session) is non-empty, a session context block is
    inserted between the catalog and the question so the model can resolve a
    refinement ("only paid channels") carrying forward the session's model / metric
    / filters. The prior resolutions are **data for the prompt only** — never
    executed — so a refinement still routes through the same catalog validation."""
    system = RESOLVER_SYSTEM_PROMPT + (_ALLOW_SQL_CLAUSE if allow_sql else "")
    context = ""
    if history:
        lines = []
        for i, entry in enumerate(history, start=1):
            q = str(entry.get("question", ""))
            resolved = entry.get("resolved", {})
            lines.append(
                f"{i}. asked: {q} — resolved as: {json.dumps(resolved, default=str)}"
            )
        context = (
            "The operator's session so far, oldest first:\n"
            + "\n".join(lines)
            + "\nThe new question below continues this session — it may refine the "
            "latest resolution (a changed dimension/filter/time frame) or ask a "
            "follow-up in its context. Resolve the NEW question, carrying forward "
            "the session's model/metric/filters where the new question implies "
            "them.\n\n"
        )
    user = (
        "Catalog:\n"
        + json.dumps(catalog, indent=2, default=str)
        + "\n\n"
        + context
        + f"Question: {question}\n\nJSON:"
    )
    return system, user


# --------------------------------------------------------------------------- #
# Resolution parsing + validation (invalid → kind "none", never raises)
# --------------------------------------------------------------------------- #
@dataclass
class Resolution:
    """A validated routing decision. ``provenance`` is the human-readable trust
    line shown to the operator ("computed as …") and written to the ask log."""

    kind: str
    provenance: str = ""
    reason: str = ""
    # True when this "none" came from a *validation failure* of a non-none
    # resolution (vs the model explicitly declaring it can't answer) — the
    # signal the one-shot self-repair retry keys on.
    invalid: bool = False
    # semantic
    model: str | None = None
    metric: str | None = None
    by: str | None = None
    series: str | None = None
    # Presentation wish ("as a funnel") — any kind may carry one; honored by
    # resolution_chart_shape only when the result's shape supports it.
    chart_pref: str | None = None
    grain: str | None = None
    filters: dict[str, list[str]] = field(default_factory=dict)
    date_start: str = ""
    date_end: str = ""
    # list (reuses model / filters / date_start / date_end above)
    columns: list[str] = field(default_factory=list)
    order_by: str = ""
    desc: bool = True
    limit: int = DEFAULT_LIST_LIMIT
    # query
    name: str | None = None
    params: dict[str, str] = field(default_factory=dict)
    # sql
    sql: str | None = None
    # Filled during execution — the (name, connector) the answer is attributed to.
    query_name: str = ""
    connector: str = ""


# A resolution wrapped in ```json fences (or a stray prose preamble) still parses:
# the model is told to emit bare JSON, but real models slip fences in, so tolerate
# them by extracting the first {...} balanced object.
_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.DOTALL | re.IGNORECASE)


def _extract_json(text: str) -> Any | None:
    """Best-effort parse of a JSON object out of an LLM completion.

    Tries the raw text, then a fenced block, then the first balanced ``{...}`` — so
    a bare object, a ```json fenced one, or one with a prose preamble all parse.
    Returns ``None`` when nothing parses (→ the caller degrades to kind none)."""
    text = (text or "").strip()
    if not text:
        return None
    for candidate in _json_candidates(text):
        try:
            obj = json.loads(candidate)
        except (ValueError, TypeError):
            continue
        if isinstance(obj, dict):
            return obj
    return None


def _json_candidates(text: str):
    yield text
    m = _FENCE_RE.search(text)
    if m:
        yield m.group(1)
    # First balanced {...} — scan for the matching close brace.
    start = text.find("{")
    if start != -1:
        depth = 0
        for i in range(start, len(text)):
            c = text[i]
            if c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    yield text[start : i + 1]
                    break


def _none(reason: str, *, invalid: bool = True) -> Resolution:
    """An unresolved outcome. ``invalid=True`` (the default — every validation
    failure) marks it retryable by the self-repair pass; the model *explicitly*
    answering kind "none" passes ``invalid=False`` (retrying an honest "I can't
    answer this" would just re-bill for the same answer)."""
    return Resolution(
        kind="none", reason=reason, provenance=f"unresolved: {reason}", invalid=invalid
    )


def parse_resolution(
    text: str, project: "Project", allow_sql: bool
) -> Resolution:
    """Parse + validate the resolver output against the live catalog.

    Every validation failure degrades to kind ``none`` carrying a reason — a
    malformed body, an unknown kind, an off-catalog metric/dimension/query, a bad
    grain, or a ``sql`` kind when ``allow_sql`` is off. It **never raises**, so a
    hallucinated resolution can never 500 the endpoint."""
    obj = _extract_json(text)
    if obj is None:
        return _none("the model did not return a valid JSON resolution")

    kind = str(obj.get("kind", "")).strip().lower()
    if kind not in _KINDS:
        return _none(f"unknown resolution kind {kind!r}")

    if kind == "none":
        return _none(
            str(obj.get("reason", "") or "no matching data source"), invalid=False
        )

    if kind == "sql":
        if not allow_sql:
            return _none("raw SQL is disabled for this project (ask.allow_sql)")
        sql = obj.get("sql")
        if not isinstance(sql, str) or not sql.strip():
            return _none("sql resolution missing a sql string")
        sql = sql.strip()
        # Read-only guard (defense in depth on top of the connector's own
        # permissions): the rung answers questions, it never mutates. A
        # non-SELECT statement is a validation failure → self-repair applies.
        if not re.match(r"(?is)^\s*(select|with)\b", sql):
            return _none("sql resolutions must be a single SELECT/WITH statement")
        res = Resolution(
            kind="sql",
            sql=sql,
            provenance="raw SQL (ask.allow_sql)",
        )
    elif kind == "query":
        res = _validate_query(obj, project)
    elif kind == "list":
        res = _validate_list(obj, project)
    else:
        res = _validate_semantic(obj, project)

    # Presentation wish, valid on every routed kind ("pie chart of…", "as a
    # funnel"). Soft: an off-list value is dropped, never fatal.
    if res.kind != "none":
        res.chart_pref = _parse_chart_pref(obj)
    return res


def _parse_chart_pref(obj: dict[str, Any]) -> str | None:
    pref = obj.get("chart")
    pref = pref.strip().lower() if isinstance(pref, str) and pref.strip() else None
    return pref if pref in CHART_PREFS else None


def _validate_query(obj: dict[str, Any], project: "Project") -> Resolution:
    name = obj.get("name")
    if not isinstance(name, str) or not name.strip():
        return _none("query resolution missing a name")
    name = name.strip()
    if name not in project.queries and name not in project.python_queries:
        return _none(f"unknown query {name!r}")
    raw_params = obj.get("params") or {}
    params: dict[str, str] = {}
    if isinstance(raw_params, dict):
        params = {str(k): _scalar(v) for k, v in raw_params.items()}
    prov = f"named query '{name}'"
    if params:
        prov += " with " + ", ".join(f"{k}={v}" for k, v in sorted(params.items()))
    return Resolution(kind="query", name=name, params=params, provenance=prov)


def _candidate_names(value: Any) -> list[str]:
    """Normalize a possibly list-valued / comma-joined field into name candidates.

    Real models routinely return ``"name,channel"`` or ``["name", "channel"]``
    for a single-name field when the question implies two of something. The
    grammar says one name — but throwing the whole route away over packaging is
    exactly the kind of rigidity the forgiveness policy exists to avoid, so the
    validator splits and picks rather than failing."""
    if isinstance(value, list):
        parts = [str(v) for v in value]
    elif isinstance(value, str):
        parts = value.split(",")
    else:
        return []
    return [p.strip() for p in parts if p.strip()]


def _validate_semantic(obj: dict[str, Any], project: "Project") -> Resolution:
    model = obj.get("model")
    handle = project.semantic_models.get(model) if isinstance(model, str) else None
    if handle is None:
        return _none(f"unknown semantic model {model!r}")

    # Metric: first candidate that exists on the model (a comma/list-valued
    # metric keeps its first valid name — multi-metric asks are a follow-up).
    metric_candidates = _candidate_names(obj.get("metric"))
    metric = next(
        (m for m in metric_candidates if m in handle.measure_lookup), None
    )
    if metric is None:
        return _none(f"unknown metric {obj.get('metric')!r} on model {model!r}")

    # by / series: the grammar wants ONE dimension in `by` and an optional
    # second in `series` — but a model asking for two groupings often packs
    # them into `by` ("name,channel"). Split: first valid dimension → by,
    # next distinct valid one → series (unless an explicit valid `series`
    # was given); anything unknown is reported only if NOTHING valid remains.
    by_candidates = _candidate_names(obj.get("by"))
    dims = [d for d in by_candidates if d in handle.dim_lookup]
    if by_candidates and not dims:
        return _none(
            f"unknown dimension {obj.get('by')!r} on model {model!r}"
        )
    by = dims[0] if dims else None

    series_candidates = _candidate_names(obj.get("series"))
    series = next(
        (s for s in series_candidates if s in handle.dim_lookup and s != by), None
    )
    if series is None and len(dims) > 1:
        series = next((d for d in dims[1:] if d != by), None)

    grain = obj.get("grain")
    grain = grain.strip().lower() if isinstance(grain, str) and grain.strip() else None
    if grain is not None and grain not in GRAIN_TOKENS:
        return _none(f"unknown grain {grain!r}")
    # Grain only makes sense bucketing the model's time dimension; a
    # hallucinated grain on a categorical `by` would raise deep in the semantic
    # backend (→ a 500, breaking the never-500 contract), so soft-drop it —
    # same forgiveness as unknown filter keys.
    if grain is not None:
        time_dim = (handle.time_dimension or "").split(".")[-1]
        if not by or by.split(".")[-1] != time_dim:
            grain = None

    # Filters are soft: a value keyed by a real dimension is kept; an unknown
    # dimension key is dropped rather than failing the whole answer (the model
    # sometimes over-specifies), so a stray filter never turns a good route into a
    # "none".
    filters: dict[str, list[str]] = {}
    raw_filters = obj.get("filters") or {}
    if isinstance(raw_filters, dict):
        for key, value in raw_filters.items():
            if str(key) not in handle.dim_lookup:
                continue
            values = value if isinstance(value, list) else [value]
            values = [_scalar(v) for v in values if _scalar(v) != ""]
            if values:
                filters[str(key)] = values

    date_start = _scalar(obj.get("date_start"))
    date_end = _scalar(obj.get("date_end"))

    prov = f"semantic: {model}.{metric}"
    if by:
        prov += f" by {by}"
    if series:
        prov += f" per {series}"
    if grain:
        prov += f" ({grain})"
    if filters:
        prov += " where " + ", ".join(
            f"{k} in {v}" for k, v in sorted(filters.items())
        )
    return Resolution(
        kind="semantic",
        model=model,
        metric=metric,
        by=by,
        series=series,
        grain=grain,
        filters=filters,
        date_start=date_start,
        date_end=date_end,
        provenance=prov,
    )


def _validate_list(obj: dict[str, Any], project: "Project") -> Resolution:
    """Validate a ``list`` resolution against the live catalog (same forgiveness
    policy as :func:`_validate_semantic`).

    A detail/list request over a semantic model: ``columns`` (1..8 dimensions to
    show), ``order_by`` (one dimension), ``desc``, ``limit``. Coercion mirrors the
    semantic path — comma-joined / list-valued fields are split, unknown names are
    dropped rather than fatal, and only a request with **no valid column left**
    degrades to ``none``. ``order_by`` soft-falls-back to the model's time dimension
    (when it's among the selected columns) else the first column; ``limit`` is
    clamped to ``[1, 500]``; ``filters``/``date_start``/``date_end`` are handled
    exactly like the semantic rung.

    The backend's *capacity* to run a list (``build_list_spec``) is checked at
    execution — a backend that lacks it degrades the answer to ``none`` there — so
    validation only concerns names/shape."""
    model = obj.get("model")
    handle = project.semantic_models.get(model) if isinstance(model, str) else None
    if handle is None:
        return _none(f"unknown semantic model {model!r}")

    # columns: split every entry (list or comma-joined), keep valid dims in order,
    # dedupe on the canonical name, cap at MAX_LIST_COLUMNS.
    raw_cols = obj.get("columns")
    candidates: list[str] = []
    if isinstance(raw_cols, list):
        for entry in raw_cols:
            candidates.extend(_candidate_names(entry))
    else:
        candidates = _candidate_names(raw_cols)
    columns: list[str] = []
    seen_canon: set[str] = set()
    for c in candidates:
        canon = handle.dim_lookup.get(c)
        if canon is None or canon in seen_canon:
            continue
        seen_canon.add(canon)
        columns.append(c)
        if len(columns) >= MAX_LIST_COLUMNS:
            break
    if not columns:
        return _none(f"no valid columns for a list on model {model!r}")

    # order_by: a valid, in-scope (selected) dimension; else the model's time
    # dimension when it's selected; else the first column.
    time_short = (handle.time_dimension or "").split(".")[-1]
    order_by = next(
        (
            o
            for o in _candidate_names(obj.get("order_by"))
            if handle.dim_lookup.get(o) in seen_canon
        ),
        None,
    )
    if order_by is None:
        if handle.time_dimension and handle.time_dimension in seen_canon:
            order_by = time_short
        else:
            order_by = columns[0]

    # desc: coerce (default true — newest/largest first).
    raw_desc = obj.get("desc", True)
    if isinstance(raw_desc, str):
        desc = raw_desc.strip().lower() not in ("false", "0", "no", "")
    else:
        desc = bool(raw_desc)

    # limit: coerce + clamp to [MIN, MAX] (default 50).
    try:
        limit = int(obj.get("limit", DEFAULT_LIST_LIMIT))
    except (TypeError, ValueError):
        limit = DEFAULT_LIST_LIMIT
    limit = max(MIN_LIST_LIMIT, min(MAX_LIST_LIMIT, limit))

    # filters + date range: identical to the semantic rung.
    filters: dict[str, list[str]] = {}
    raw_filters = obj.get("filters") or {}
    if isinstance(raw_filters, dict):
        for key, value in raw_filters.items():
            if str(key) not in handle.dim_lookup:
                continue
            values = value if isinstance(value, list) else [value]
            values = [_scalar(v) for v in values if _scalar(v) != ""]
            if values:
                filters[str(key)] = values

    date_start = _scalar(obj.get("date_start"))
    date_end = _scalar(obj.get("date_end"))

    if order_by == time_short:
        ord_txt = "newest first" if desc else "oldest first"
    else:
        ord_txt = f"by {order_by} {'desc' if desc else 'asc'}"
    prov = f"list: {model} — {', '.join(columns)} · {ord_txt} · limit {limit}"
    if filters:
        prov += " · where " + ", ".join(
            f"{k} in {v}" for k, v in sorted(filters.items())
        )
    return Resolution(
        kind="list",
        model=model,
        columns=columns,
        order_by=order_by,
        desc=desc,
        limit=limit,
        filters=filters,
        date_start=date_start,
        date_end=date_end,
        provenance=prov,
    )


def _scalar(v: Any) -> str:
    """A single filter/param value as a string (list values are comma-joined)."""
    if v is None:
        return ""
    if isinstance(v, list):
        return ",".join(str(x) for x in v)
    return str(v)


# --------------------------------------------------------------------------- #
# Execution — run the resolved source, sharing the data API's result cache
# --------------------------------------------------------------------------- #
def execute_resolution(
    resolution: Resolution, project: "Project", params: dict[str, str]
) -> QueryResult:
    """Run the resolved query and return its ``QueryResult``.

    ``params`` are the operator's live filter values (dashboard state); the
    resolution's own filters/date/params layer on top. Query failures raise
    :class:`AskQueryError` (→ 500). Mutates ``resolution`` to record the
    ``(query_name, connector)`` the answer is attributed to."""
    try:
        if resolution.kind == "semantic":
            return _execute_semantic(resolution, project, params)
        if resolution.kind == "list":
            return _execute_list(resolution, project, params)
        if resolution.kind == "query":
            return _execute_query(resolution, project, params)
        if resolution.kind == "sql":
            return _execute_sql(resolution, project)
    except (AskQueryError, AskListUnsupported):
        # AskListUnsupported is a *routing* outcome (this backend can't run a list),
        # not a failure — answer_question degrades it to none. Let it through
        # unwrapped so it isn't masked as a 500.
        raise
    except Exception as e:  # noqa: BLE001 - any backend failure becomes a 500
        raise AskQueryError(f"{type(e).__name__}: {e}") from e
    raise AskQueryError(f"cannot execute resolution kind {resolution.kind!r}")


def _execute_semantic(
    resolution: Resolution, project: "Project", params: dict[str, str]
) -> QueryResult:
    metric_ref = f"{resolution.model}.{resolution.metric}"
    by_ref = f"{resolution.model}.{resolution.by}" if resolution.by else None
    series_ref = (
        f"{resolution.model}.{resolution.series}" if resolution.series else None
    )
    ref = resolve_ref(
        project.semantic_models,
        metric_ref,
        by_ref,
        series_ref,
        grain=resolution.grain,
    )
    spec = build_semantic_spec(project.semantic_models, ref, project.connectors)
    # Register so a follow-up data fetch by the synthetic name resolves too (same
    # seam the render pipeline uses for a `metric={…}` chart). The name is stable
    # per (metric, by, grain), so skip the redundant global write on repeats.
    if get_python_query_def(spec.name, spec.connector) is None:
        register_python_query_def(spec.name, spec.connector, spec)
    resolution.query_name = spec.name
    resolution.connector = spec.connector

    # build_filters reads dimension keys + date_start/date_end off params. Start
    # from the live dashboard filters, then overlay the resolution's own filters
    # (data, never `${param}`-interpolated). Known limitation: filter params are
    # comma-joined multi-value strings (the framework-wide Dropdown convention
    # build_filters splits on), so a single value that itself contains a comma
    # cannot ride this encoding.
    exec_params = dict(params)
    for dim, values in resolution.filters.items():
        exec_params[dim] = ",".join(values)
    if resolution.date_start:
        exec_params[DATE_START_PARAM] = resolution.date_start
    if resolution.date_end:
        exec_params[DATE_END_PARAM] = resolution.date_end
    return run_python_query(spec, exec_params, project.connectors)


def _execute_list(
    resolution: Resolution, project: "Project", params: dict[str, str]
) -> QueryResult:
    """Run a ``list`` resolution — a dims-only, ordered, limited projection compiled
    by the model's semantic backend.

    Mirrors :func:`_execute_semantic`: resolve the accepted column/order spellings to
    canonical dimension names, build a :class:`SemanticListRef`, compile it through
    the backend (``build_semantic_list_spec``), register the synthetic spec into the
    shared ``_python_def_cache``, and run it with the resolution's filters/date range
    overlaid on the live params. A backend without list support raises
    ``NotImplementedError`` here → re-raised as :class:`AskListUnsupported` for
    :func:`answer_question` to degrade to kind ``none``."""
    handle = project.semantic_models.get(resolution.model)
    if handle is None:
        raise AskQueryError(f"unknown semantic model {resolution.model!r}")
    columns = tuple(
        handle.dim_lookup[c] for c in resolution.columns if c in handle.dim_lookup
    )
    order_by = (
        handle.dim_lookup.get(resolution.order_by) if resolution.order_by else None
    )
    list_ref = SemanticListRef(
        model=resolution.model,
        columns=columns,
        order_by=order_by,
        desc=resolution.desc,
        limit=resolution.limit,
        query_name=semantic_list_query_name(
            resolution.model, columns, order_by, resolution.desc, resolution.limit
        ),
    )
    try:
        spec = build_semantic_list_spec(project.semantic_models, list_ref, project.connectors)
    except NotImplementedError as e:
        raise AskListUnsupported(str(e)) from e

    if get_python_query_def(spec.name, spec.connector) is None:
        register_python_query_def(spec.name, spec.connector, spec)
    resolution.query_name = spec.name
    resolution.connector = spec.connector

    exec_params = dict(params)
    for dim, values in resolution.filters.items():
        exec_params[dim] = ",".join(values)
    if resolution.date_start:
        exec_params[DATE_START_PARAM] = resolution.date_start
    if resolution.date_end:
        exec_params[DATE_END_PARAM] = resolution.date_end
    return run_python_query(spec, exec_params, project.connectors)


def _execute_query(
    resolution: Resolution, project: "Project", params: dict[str, str]
) -> QueryResult:
    name = resolution.name
    spec = project.python_queries.get(name)
    if spec is not None:
        connector = spec.connector
    else:
        connector = project.queries[name].connector
    resolution.query_name = name
    resolution.connector = connector

    exec_params = {**params, **resolution.params}
    cached = get_cached_result(name, connector, exec_params)
    if cached is not None:
        return cached

    py_spec = get_python_query_def(name, connector)
    if py_spec is not None:
        result = run_python_query(py_spec, exec_params, project.connectors)
        ttl = py_spec.cache_ttl if py_spec.cache_ttl is not None else DEFAULT_CACHE_TTL
        cache_result(name, connector, exec_params, result, ttl)
        return result

    query_def = get_query_def(name, connector)
    if query_def is None:
        raise AskQueryError(f"query {name!r} not registered for connector {connector!r}")
    sql, default_params, cache_ttl = query_def
    conn = project.connectors.get(connector)
    if conn is None:
        raise AskQueryError(f"connector {connector!r} not found")
    merged = {**default_params, **exec_params}
    result = conn.query(_substitute_params(sql, merged))
    ttl = cache_ttl if cache_ttl is not None else DEFAULT_CACHE_TTL
    cache_result(name, connector, exec_params, result, ttl)
    return result


def _execute_sql(resolution: Resolution, project: "Project") -> QueryResult:
    connector = project.default_connector or ""
    conn = project.connectors.get(connector)
    if conn is None:
        raise AskQueryError("no default connector to run raw SQL against")
    resolution.query_name = "(raw sql)"
    resolution.connector = connector
    result = conn.query(resolution.sql)
    # Defensive cap: a hand-written SELECT may lack a LIMIT.
    if len(result.rows) > MAX_SQL_ROWS:
        result = QueryResult(columns=result.columns, rows=result.rows[:MAX_SQL_ROWS])
    return result


# --------------------------------------------------------------------------- #
# Server-side chart-shape inference (mirrors chart.js::resolveAutoConfig)
# --------------------------------------------------------------------------- #
_TEMPORAL_RE = re.compile(r"^\d{4}-\d{2}(-\d{2})?([T ].*)?$")


def _classify(values: list[Any]) -> str:
    """Classify a column's sampled cells temporal / numeric / categorical —
    the exact heuristic ``chart.js::classifyValues`` uses (so the server infers
    the same shape the client would)."""
    numeric = temporal = non_null = 0
    for v in values:
        if v is None or v == "":
            continue
        non_null += 1
        if isinstance(v, bool):
            continue
        if isinstance(v, (int, float)):
            numeric += 1
            continue
        s = str(v)
        if _TEMPORAL_RE.match(s):
            temporal += 1
        elif s.strip() != "" and _is_number(s):
            numeric += 1
    if not non_null:
        return "categorical"
    if temporal / non_null > 0.8:
        return "temporal"
    if numeric / non_null > 0.8:
        return "numeric"
    return "categorical"


def _is_number(s: str) -> bool:
    try:
        float(s)
        return True
    except ValueError:
        return False


def infer_chart_shape(payload: dict[str, Any]) -> dict[str, str] | None:
    """Infer ``{type, x, y[, sort_by]}`` for a **serialized** result payload
    (``{"columns", "rows"}``), or ``None`` for a headline value.

    Mirrors ``resolveAutoConfig`` so the client renders *exactly* this chart:
    temporal x → line (sorted by x, like the client's auto path — the server
    ships a concrete type, which skips ``resolveAutoConfig``, so the sort hint
    must ride along or time series render in row order), categorical x → bar,
    numeric x → scatter; y is the first numeric column that isn't x. A
    single-row result (or one with no chartable numeric column) is a headline,
    not a chart → ``None``. Takes the serialized payload (dates as ISO strings,
    Decimals as floats) so it classifies what the browser would see — and so the
    caller serializes exactly once."""
    columns = payload["columns"]
    rows = payload["rows"]
    # A single row (or none) is a headline number, not a chart.
    if len(rows) <= 1 or not columns:
        return None

    sample = rows[:50]
    kind = {
        c: _classify([row[i] if i < len(row) else None for row in sample])
        for i, c in enumerate(columns)
    }

    def first_of(k: str, exclude: str | None = None) -> str | None:
        return next((c for c in columns if kind[c] == k and c != exclude), None)

    x = first_of("temporal") or first_of("categorical") or columns[0]
    y = first_of("numeric", exclude=x)
    if not y:
        return None

    if kind[x] == "temporal":
        return {"type": "line", "x": x, "y": y, "sort_by": x}
    if kind[x] == "numeric":
        return {"type": "scatter", "x": x, "y": y}
    return {"type": "bar", "x": x, "y": y}


def _find_col(columns: list[str], name: str) -> str | None:
    """Match a semantic name to a result column (exact, else by last segment —
    semantic results emit prefixed columns like ``orders.campaign_id``)."""
    short = name.split(".")[-1]
    for c in columns:
        if c == name or c.split(".")[-1] == short:
            return c
    return None


def resolution_chart_shape(
    resolution: Resolution, payload: dict[str, Any]
) -> dict[str, str] | None:
    """Chart shape for an executed resolution — series-aware for semantic ones.

    A semantic resolution with a ``series`` dimension yields three columns
    (by, series, metric); the generic ``infer_chart_shape`` would ignore the
    middle one and draw duplicate x categories. Here the resolution *names* the
    roles, so the shape carries ``series_by`` (the client config key that splits
    one metric into a colored series per value) and the annotation context gets
    the same split. Everything else falls through to ``infer_chart_shape``.

    An explicit operator chart wish (``resolution.chart_pref`` — "as a funnel")
    overrides the inferred *type* when the shape supports it: every pref speaks
    the same ``{x, y}`` config, but pie/funnel/treemap need a single unsplit
    category+value pairing, so a preference is dropped (never an error) when a
    ``series_by`` split is present or no chartable shape exists at all."""
    if (
        resolution.kind != "semantic"
        or not resolution.series
        or not resolution.by
        or not resolution.metric
    ):
        return _apply_chart_pref(infer_chart_shape(payload), resolution.chart_pref)
    columns = payload["columns"]
    rows = payload["rows"]
    if len(rows) <= 1 or not columns:
        return None
    x = _find_col(columns, resolution.by)
    series_col = _find_col(columns, resolution.series)
    y = _find_col(columns, resolution.metric)
    if not (x and series_col and y) or len({x, series_col, y}) != 3:
        return _apply_chart_pref(infer_chart_shape(payload), resolution.chart_pref)
    x_idx = columns.index(x)
    x_kind = _classify([row[x_idx] if x_idx < len(row) else None for row in rows[:50]])
    if x_kind == "temporal":
        shape = {"type": "line", "x": x, "y": y, "series_by": series_col, "sort_by": x}
    else:
        shape = {"type": "bar", "x": x, "y": y, "series_by": series_col}
    # Pie + series is the one split-compatible pref (faceted small multiples);
    # line/bar swap freely; funnel/treemap can't express a split — dropped.
    pref = resolution.chart_pref
    if pref in ("line", "bar", "pie"):
        shape["type"] = pref
    return shape


def _apply_chart_pref(
    shape: dict[str, str] | None, pref: str | None
) -> dict[str, str] | None:
    """Overlay an operator chart wish on an inferred shape (soft — a pref that
    the shape can't express leaves the inference untouched)."""
    if shape is None or pref is None or pref == shape.get("type"):
        return shape
    out = dict(shape)
    out["type"] = pref
    if pref in ("pie", "funnel", "treemap"):
        # Category+value charts: an ordering hint is meaningless (funnel sorts
        # itself; pie shows shares).
        out.pop("sort_by", None)
    return out


# --------------------------------------------------------------------------- #
# Answer cache: (normalized question, frozen params) -> full response payload
# --------------------------------------------------------------------------- #
# Mirrors llm.py::_answer_cache in shape, but bounded like pipeline's
# _result_cache: the key space here is free-form operator text, so without an
# LRU cap a stream of unique questions grows the dict forever. The value is the
# whole response payload so a hit replays chart + table + answer identically;
# `refresh` (POST body) bypasses it — config can't disable runtime refresh,
# cache_ttl bounds the cost instead.
_answer_cache: OrderedDict[
    tuple[str, tuple, str | None], tuple[dict[str, Any], float]
] = OrderedDict()

_WS_RE = re.compile(r"\s+")


def normalize_question(question: str) -> str:
    """Lowercase + collapse whitespace, so trivially-different spellings of one
    question share a cache entry."""
    return _WS_RE.sub(" ", (question or "").strip().lower())


def _cache_key(
    question: str, params: dict[str, str], context_key: str | None = None
) -> tuple[str, tuple, str | None]:
    # _freeze_params is the same freezer the result cache keys on, so the two
    # layers can never disagree about what "the same params" means. The optional
    # third element is a discriminator that keeps entries with the *same*
    # (question, params) from colliding when they mean different things: an edited
    # semantic spec (execute_spec passes a canonical spec fingerprint) or a
    # follow-up asked in a different session (answer_question passes a fingerprint
    # of the entire sanitized history — so the same follow-up text under two
    # histories that differ only in a chip-edited resolved.detail stays distinct).
    # When None, the key is the plain (question, params) pair — so a cold, no-history
    # ask keeps its original cache behavior.
    return (normalize_question(question), _freeze_params(params), context_key)


def get_cached_answer(
    question: str, params: dict[str, str], context_key: str | None = None
) -> dict[str, Any] | None:
    key = _cache_key(question, params, context_key)
    entry = _answer_cache.get(key)
    if entry is None:
        return None
    payload, expiry = entry
    if time.monotonic() > expiry:
        _answer_cache.pop(key, None)
        return None
    _answer_cache.move_to_end(key)
    return payload


def cache_answer(
    question: str,
    params: dict[str, str],
    payload: dict[str, Any],
    ttl: int,
    context_key: str | None = None,
) -> None:
    key = _cache_key(question, params, context_key)
    _answer_cache[key] = (payload, time.monotonic() + ttl)
    _answer_cache.move_to_end(key)
    while len(_answer_cache) > MAX_CACHED_ANSWERS:
        _answer_cache.popitem(last=False)


# --------------------------------------------------------------------------- #
# Ask log (JSONL) — one line per runtime ask; never raises
# --------------------------------------------------------------------------- #
def log_ask(project: "Project", entry: dict[str, Any]) -> None:
    """Append one ask record to ``<project>/.dashdown/ask_log.jsonl``.

    Best-effort: a log write must never break an answer, so any failure is a
    warning, not an exception."""
    try:
        log_dir = project.root / ".dashdown"
        log_dir.mkdir(parents=True, exist_ok=True)
        line = json.dumps(entry, default=str)
        with (log_dir / "ask_log.jsonl").open("a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception as e:  # noqa: BLE001 - logging is best-effort
        log.warning("failed to append to ask_log.jsonl: %s", e)


# --------------------------------------------------------------------------- #
# Orchestration — the single entry point the endpoint + CLI share
# --------------------------------------------------------------------------- #
def answer_question(
    project: "Project",
    question: str,
    params: dict[str, str] | None = None,
    *,
    refresh: bool = False,
    history: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Resolve → execute → answer one runtime question, returning the full payload.

    A thin driver over :func:`answer_question_staged`: it consumes both stages
    (``resolved`` then ``done``) and merges them into the single payload every
    non-streaming caller (the CLI, ``POST /api/ask`` without ``stream``) has always
    received — so the cache / log / rate-limit / self-repair semantics all live in
    the staged generator, in one place, and this stays byte-compatible.

    Assumes the caller already checked ``llm`` + ``ask`` are enabled (see
    :func:`ask_unavailable_notice`). Two LLM calls on a cache miss: resolve, then
    answer. Raises :class:`AskLLMError` (→ 502) on an LLM failure and
    :class:`AskQueryError` (→ 500) on a query failure; a *bad resolution* is never
    an error — it degrades to kind ``none`` in the payload.

    ``history`` is an optional oldest-first list of ``{question, resolved}`` entries
    (the operator's session, the follow-up surface): it is sanitized/bounded to
    plain data, threaded into the resolver prompt as a session block, folded into
    the answer-generation prompt as the prior questions, and fingerprinted into the
    cache key (so the same follow-up text asked under two different sessions — even
    ones differing only by a chip-edited resolution detail — doesn't collide)."""
    resolved_payload: dict[str, Any] = {}
    done_fields: dict[str, Any] = {}
    for stage, data in answer_question_staged(
        project, question, params, refresh=refresh, history=history
    ):
        if stage == "resolved":
            resolved_payload = data
        elif stage == "done":
            done_fields = data
    # The staged generator always yields exactly one `resolved` (the full payload
    # minus commentary) then one `done` (the commentary fields), so merging them
    # reconstructs the exact payload the pre-staging blocking path returned.
    payload = dict(resolved_payload)
    payload.update(done_fields)
    return payload


def _done_fields(payload: dict[str, Any]) -> dict[str, Any]:
    """The ``done``-stage fields (the commentary + cache flag) pulled off a full
    payload — shared by the cache-hit and kind-``none`` stagings, whose ``resolved``
    payload already carries its final answer text."""
    return {
        "answer_html": payload.get("answer_html", ""),
        "answer_text": payload.get("answer_text", ""),
        "annotations": payload.get("annotations", []),
        "cached": payload.get("cached", False),
    }


def answer_question_staged(
    project: "Project",
    question: str,
    params: dict[str, str] | None = None,
    *,
    refresh: bool = False,
    history: list[dict[str, Any]] | None = None,
):
    """Staged sibling of :func:`answer_question` — an iterator of two tuples,
    ``("resolved", payload)`` then ``("done", fields)``.

    The ``resolved`` payload is the complete response **minus the LLM commentary**
    (``resolved``, ``columns``, ``rows``, ``chart``, ``semantic_options`` when
    semantic, ``model``, ``cached``, and empty ``answer_html``/``answer_text``/
    ``annotations``); the ``done`` fields are ``{answer_html, answer_text,
    annotations, cached}``. The streaming ``/api/ask`` branch relays each stage as an
    SSE event so the operator sees provenance + chart + table the moment resolution
    and the query complete, while the commentary is still generating.

    **Eager vs. lazy split (deliberate).** The cache lookup and the rate-limit check
    run *eagerly*, when this function is called — before the returned iterator is
    first advanced — so the streaming endpoint can still answer a cache-miss over
    budget with a plain-JSON ``429`` *before* it commits to an SSE response. A
    :class:`AskRateLimitError` therefore raises out of this call, not out of
    iteration. Everything that can make a billable LLM call (resolve, self-repair,
    answer) lives inside the returned generator, so those failures surface *during*
    iteration — i.e. after SSE headers are sent — as an ``error`` event rather than
    a pre-header status code. A cache hit returns a two-item iterator replaying the
    stored payload (``cached: true``).

    Logging and the answer-cache write happen exactly once per ask, inside the
    generator (never per stage)."""
    params = dict(params or {})
    cfg = project.config.ask
    llm_cfg = project.config.llm
    model = resolve_model_name(llm_cfg)

    hist = _sanitize_history(history)
    # A follow-up asked under a different session must not share a cache entry with
    # the same text asked cold, so a fingerprint of the whole history discriminates
    # the key (histories differing only in a resolved.detail still fingerprint apart).
    hist_key = _history_fingerprint(hist) if hist else None

    if not refresh:
        cached = get_cached_answer(question, params, hist_key)
        if cached is not None:
            hit = dict(cached)
            hit["cached"] = True
            return iter(
                [("resolved", hit), ("done", _done_fields(hit))]
            )

    # Past the cache → LLM spend ahead; the rate limit guards exactly this line.
    # Eager (pre-iteration) so the streaming endpoint can 429 before headers.
    if rate_limited(cfg.rate_limit):
        raise AskRateLimitError(
            "ask rate limit reached "
            f"({cfg.rate_limit}/min — ask.rate_limit in dashdown.yaml); "
            "try again shortly"
        )

    return _answer_stages(project, question, params, cfg, model, hist, hist_key)


def _answer_stages(
    project: "Project",
    question: str,
    params: dict[str, str],
    cfg: Any,
    model: str,
    hist: list[dict[str, Any]],
    hist_key: str | None,
):
    """The live (cache-miss) staging generator: resolve → execute → answer, yielding
    ``resolved`` before the (second, billable) answer call so the panel paints early.

    Split out from :func:`answer_question_staged` so the cache/rate-limit gate there
    stays eager while every LLM call here stays lazy (see that docstring)."""
    adapter = project.get_llm_adapter()
    started = time.monotonic()

    # 0) Direct-SQL passthrough. An operator who TYPES SQL into the box (only
    # meaningful with allow_sql on) shouldn't have it round-tripped through the
    # resolver — skip that call and run it as-is (SELECT/WITH guard included,
    # via parse_resolution). Detection is attempt-and-fallback: "select the
    # best campaign from our list" is English that pattern-matches SQL, so a
    # failed *parse/execution* silently falls through to the normal resolver
    # path — a false positive costs one failed query, never a broken answer.
    if cfg.allow_sql and re.match(r"(?is)^\s*(select|with)\b", question):
        direct = parse_resolution(
            json.dumps({"kind": "sql", "sql": question}), project, cfg.allow_sql
        )
        if direct.kind == "sql":
            direct.provenance = "raw SQL (typed directly)"
            try:
                result = execute_resolution(direct, project, params)
            except AskQueryError:
                result = None  # not runnable SQL after all — treat as English
            if result is not None:
                yield from _stages_for_result(
                    project, question, params, cfg, model, hist, hist_key,
                    direct, result, adapter, started,
                )
                return

    # 1) Resolve.
    catalog = build_ask_catalog(project)
    if cfg.allow_sql:
        # The sql rung is useless blind — arm it with the default connector's
        # table/column names (only ever shown when the project opted into SQL).
        schema = sql_schema_hint(project)
        if schema:
            catalog["sql_tables"] = schema
    system, user = build_resolver_prompt(catalog, question, cfg.allow_sql, hist)
    try:
        raw = adapter.complete(system, user)
    except Exception as e:  # noqa: BLE001
        raise AskLLMError(f"{type(e).__name__}: {e}") from e
    resolution = parse_resolution(raw, project, cfg.allow_sql)

    # One-shot self-repair: a resolution that *failed validation* (an off-catalog
    # name, malformed JSON — `invalid`, as opposed to the model explicitly
    # declaring it can't answer) gets exactly one corrective retry with the
    # validation error quoted back. Small models misformat far more often than
    # they misunderstand; one extra call only on failures beats pinning the
    # question as "unanswerable".
    if resolution.kind == "none" and resolution.invalid:
        repair_user = (
            user
            + f"\n\nYour previous response was invalid: {resolution.reason}. "
            "Return a corrected JSON resolution using only names from the catalog."
        )
        try:
            raw = adapter.complete(system, repair_user)
        except Exception as e:  # noqa: BLE001
            raise AskLLMError(f"{type(e).__name__}: {e}") from e
        resolution = parse_resolution(raw, project, cfg.allow_sql)

    # kind "none": the model couldn't route it. Carry the reason as the answer;
    # no data, no chart, no second (billable) LLM call.
    if resolution.kind == "none":
        payload = _none_payload(question, resolution, model)
        # Short TTL: a "none" may be a transient resolver misroute, and the ask
        # box never sends refresh — caching it for the full cache_ttl would pin
        # a valid question as "unanswerable" for up to an hour.
        cache_answer(
            question, params, payload, min(cfg.cache_ttl, NONE_ANSWER_TTL), hist_key
        )
        _log(project, question, resolution, 0, started, model, cfg, history=hist)
        yield ("resolved", payload)
        yield ("done", _done_fields(payload))
        return

    # 2) Execute. Serialize once — the chart inference classifies the same
    # browser-facing cells the payload ships. A `list` routed onto a backend that
    # can't run one degrades to none here (never a 500, no billable answer call).
    try:
        result = execute_resolution(resolution, project, params)
    except AskListUnsupported as e:
        resolution = _none(str(e), invalid=False)
        payload = _none_payload(question, resolution, model)
        cache_answer(
            question, params, payload, min(cfg.cache_ttl, NONE_ANSWER_TTL), hist_key
        )
        _log(project, question, resolution, 0, started, model, cfg, history=hist)
        yield ("resolved", payload)
        yield ("done", _done_fields(payload))
        return
    yield from _stages_for_result(
        project, question, params, cfg, model, hist, hist_key,
        resolution, result, adapter, started,
    )


def _stages_for_result(
    project: "Project",
    question: str,
    params: dict[str, str],
    cfg: Any,
    model: str,
    hist: list[dict[str, Any]],
    hist_key: str | None,
    resolution: Resolution,
    result: QueryResult,
    adapter: Any,
    started: float,
):
    """Serialize → yield ``resolved`` → generate commentary → cache/log → yield
    ``done``, for an already-executed resolution. Shared by the normal resolver
    path and the direct-SQL passthrough (which skips the resolver call)."""
    serialized = serialize_result(result)
    chart = resolution_chart_shape(resolution, serialized)

    # The `resolved` stage: the complete payload with the commentary slots still
    # empty. It ships the instant data is in hand so the panel paints provenance +
    # chart + table while the (second) answer call runs.
    resolved_payload: dict[str, Any] = {
        "question": question,
        "resolved": {
            "kind": resolution.kind,
            "provenance": resolution.provenance,
            "query_name": resolution.query_name,
            "connector": resolution.connector,
            "detail": _resolution_detail(resolution),
        },
        "columns": serialized["columns"],
        "rows": serialized["rows"],
        "chart": chart,
        "answer_html": "",
        "answer_text": "",
        "annotations": [],
        "model": model,
        "cached": False,
    }
    if resolution.kind == "semantic":
        resolved_payload["semantic_options"] = _semantic_options(
            project, resolution.model
        )
    yield ("resolved", resolved_payload)

    # 3) Answer — reuse the exact <Ask /> generation path. A chart shape means a
    # ChartContext, so generate_answer runs the annotation protocol for it.
    chart_ctx: ChartContext | None = None
    if chart is not None:
        chart_ctx = build_chart_context(
            chart["type"],
            x=chart["x"],
            y=chart["y"],
            series_by=chart.get("series_by"),
        )
    # When this ask continues a session, fold the prior *questions* (only — no
    # resolutions, to keep tokens down) into the answer prompt so the commentary
    # reads in context. Rides through build_ask_prompt unchanged (llm.py untouched).
    answer_prompt = question
    if hist:
        prior_questions = [h["question"] for h in hist]
        answer_prompt = (
            f"{question}\n(This follows the operator's earlier questions in this "
            f"session: {' → '.join(prior_questions)})"
        )
    synthetic = AskDef(
        id="_ask_runtime",
        queries=((resolution.query_name or "result", resolution.connector or ""),),
        prompt=answer_prompt,
        max_rows=cfg.max_rows,
        page_title=project.config.title,
        chart_context=chart_ctx,
    )
    try:
        answer_html, answer_text, annotations = generate_answer(
            synthetic, [result], adapter, params
        )
    except Exception as e:  # noqa: BLE001
        raise AskLLMError(f"{type(e).__name__}: {e}") from e

    # Merge the commentary back into the full payload for the answer cache (so a
    # later hit replays chart + table + answer identically), then emit `done`.
    full_payload = dict(resolved_payload)
    full_payload["answer_html"] = answer_html
    full_payload["answer_text"] = answer_text
    full_payload["annotations"] = annotations
    cache_answer(question, params, full_payload, cfg.cache_ttl, hist_key)
    _log(project, question, resolution, len(result.rows), started, model, cfg, history=hist)
    yield (
        "done",
        {
            "answer_html": answer_html,
            "answer_text": answer_text,
            "annotations": annotations,
            "cached": False,
        },
    )


def _none_payload(
    question: str, resolution: Resolution, model: str
) -> dict[str, Any]:
    """The full response payload for a kind ``none`` outcome — the model's reason as
    the answer, no data / chart / second LLM call. Shared by the resolver-none path
    and the list-unsupported degrade path so both ship an identical shape."""
    return {
        "question": question,
        "resolved": {
            "kind": "none",
            "provenance": resolution.provenance,
            "query_name": None,
            "connector": None,
            "detail": {"reason": resolution.reason},
        },
        "columns": None,
        "rows": None,
        "chart": None,
        "answer_html": render_markdown_text(resolution.reason),
        "answer_text": resolution.reason,
        "annotations": [],
        "model": model,
        "cached": False,
    }


def _resolution_detail(resolution: Resolution) -> dict[str, Any]:
    """The rung-specific fields, for the response's ``resolved.detail``."""
    if resolution.kind == "semantic":
        detail: dict[str, Any] = {
            "model": resolution.model,
            "metric": resolution.metric,
            "by": resolution.by,
            "series": resolution.series,
            "grain": resolution.grain,
            "filters": resolution.filters,
            "date_start": resolution.date_start,
            "date_end": resolution.date_end,
        }
    elif resolution.kind == "list":
        detail = {
            "model": resolution.model,
            "columns": resolution.columns,
            "order_by": resolution.order_by,
            "desc": resolution.desc,
            "limit": resolution.limit,
            "filters": resolution.filters,
            "date_start": resolution.date_start,
            "date_end": resolution.date_end,
        }
    elif resolution.kind == "query":
        detail = {"name": resolution.name, "params": resolution.params}
    elif resolution.kind == "sql":
        detail = {"sql": resolution.sql}
    else:
        return {}
    if resolution.chart_pref:
        detail["chart"] = resolution.chart_pref
    return detail


def _log(
    project: "Project",
    question: str,
    resolution: Resolution,
    rows: int,
    started: float,
    model: str,
    cfg: Any,
    via: str = "ask",
    history: list[dict[str, Any]] | None = None,
) -> None:
    if not cfg.log:
        return
    entry: dict[str, Any] = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "question": question,
        "kind": resolution.kind,
        "via": via,
        "provenance": resolution.provenance,
        "rows": rows,
        "duration_ms": round((time.monotonic() - started) * 1000, 1),
        "model": model,
        "cached": False,
    }
    # Telemetry for refinement chains: how deep into a session this ask sits.
    if history:
        entry["session_depth"] = len(history)
    log_ask(project, entry)


# --------------------------------------------------------------------------- #
# Follow-up context + semantic-spec editing (Tier 1 answer refinement)
# --------------------------------------------------------------------------- #
_HISTORY_MAX_ENTRIES = 6
_HISTORY_QUESTION_MAX = 400


def _sanitize_history(history: Any) -> list[dict[str, Any]]:
    """Coerce a client-supplied ``history`` list to a bounded list of at most
    ``{question: str, resolved: {kind, detail}}`` entries, oldest first.

    It is **data for the resolver/answer prompts only** — never executed — so this
    only shapes/bounds it: non-dict or empty-question entries are dropped, each
    question is trimmed to 400 chars, each ``resolved`` is narrowed to a ``kind``
    string + an opaque ``detail`` value, and only the **last 6** entries are kept.
    Anything not a list, or a list of only malformed entries, → ``[]`` (no context
    block)."""
    if not isinstance(history, list):
        return []
    out: list[dict[str, Any]] = []
    for raw in history:
        if not isinstance(raw, dict):
            continue
        question = raw.get("question")
        if not isinstance(question, str) or not question.strip():
            continue
        entry: dict[str, Any] = {"question": question.strip()[:_HISTORY_QUESTION_MAX]}
        resolved = raw.get("resolved")
        clean: dict[str, Any] = {}
        if isinstance(resolved, dict):
            kind = resolved.get("kind")
            if isinstance(kind, str):
                clean["kind"] = kind
            if "detail" in resolved:
                clean["detail"] = resolved.get("detail")
        entry["resolved"] = clean
        out.append(entry)
    return out[-_HISTORY_MAX_ENTRIES:]


def _history_fingerprint(history: list[dict[str, Any]]) -> str:
    """A stable 16-hex fingerprint of the entire sanitized history, for the cache
    key's context discriminator. Two histories that differ only in a chip-edited
    ``resolved.detail`` fingerprint apart, so the same follow-up text under them
    can't collide."""
    return hashlib.sha256(
        json.dumps(history, sort_keys=True, default=str).encode()
    ).hexdigest()[:16]


def _semantic_options(
    project: "Project", model_name: str | None
) -> dict[str, Any] | None:
    """The editable vocabulary of a semantic model, for the answer panel's chips.

    Short (last-segment) names, sorted — built from the model handle exactly like
    :func:`build_ask_catalog` does, so a chip references a name the resolver /
    :func:`_validate_semantic` accepts. ``None`` when the model is unknown."""
    handle = project.semantic_models.get(model_name) if model_name else None
    if handle is None:
        return None
    return {
        "model": model_name,
        "measures": sorted({m.split(".")[-1] for m in handle.measures}),
        "dimensions": sorted({d.split(".")[-1] for d in handle.dimensions}),
        "time_dimension": (
            handle.time_dimension.split(".")[-1] if handle.time_dimension else None
        ),
        "grains": list(GRAIN_TOKENS),
    }


def _spec_fingerprint(resolution: Resolution) -> str:
    """A canonical, order-stable key for a validated semantic resolution.

    Two edited specs that mean the same thing hash the same; an edited spec never
    collides with the original answer to the same question text."""
    return json.dumps(
        {
            "model": resolution.model,
            "metric": resolution.metric,
            "by": resolution.by,
            "series": resolution.series,
            "grain": resolution.grain,
            "filters": resolution.filters,
            # A different chart = different annotations, so a different cached
            # commentary is correct, not a collision.
            "chart": resolution.chart_pref,
        },
        sort_keys=True,
        default=str,
    )


def execute_spec(
    project: "Project",
    question: str,
    spec: dict[str, Any],
    params: dict[str, str],
    *,
    commentary: bool,
    refresh: bool = False,
) -> dict[str, Any]:
    """Re-execute a client-edited semantic spec, optionally with LLM commentary.

    The answer panel's chips build a ``spec`` — ``{"kind": "semantic", "model",
    "metric", "by", "grain", "filters"}`` — and POST it here to re-run *without* an
    LLM resolution call. The spec is validated through the **same**
    :func:`_validate_semantic` the LLM output goes through (semantic values are pure
    JSON data — no injection surface); a non-semantic kind or a validation failure
    raises :class:`ValueError` (the endpoint 400s: a client-built spec that fails
    validation is a *client* error, unlike an LLM hallucination which degrades to
    ``none``).

    ``commentary=False`` (the chip path) executes + serializes + infers the chart
    and returns the standard payload with empty ``answer_html``/``answer_text`` and
    no annotations — **no LLM call, no rate-limit consumption, never cached**.
    ``commentary=True`` adds exactly one LLM call via the same
    :func:`generate_answer` path :func:`answer_question` uses (consuming the rate
    limit, raising :class:`AskRateLimitError` when exhausted) and caches the full
    payload under a key discriminated by the spec fingerprint (so an edited spec
    never collides with the original answer to the same question). ``refresh=True``
    bypasses the cache."""
    if not isinstance(spec, dict) or str(spec.get("kind", "")).strip().lower() != "semantic":
        raise ValueError(
            "spec must be a semantic spec ({'kind': 'semantic', 'model', 'metric', …})"
        )

    params = dict(params or {})
    cfg = project.config.ask
    model = resolve_model_name(project.config.llm)

    # Validate through the exact same catalog check the LLM output goes through.
    obj = {
        "kind": "semantic",
        "model": spec.get("model"),
        "metric": spec.get("metric"),
        "by": spec.get("by"),
        "series": spec.get("series"),
        "grain": spec.get("grain"),
        "filters": spec.get("filters") or {},
    }
    resolution = _validate_semantic(obj, project)
    if resolution.kind == "none":
        raise ValueError(f"invalid semantic spec: {resolution.reason}")
    resolution.chart_pref = _parse_chart_pref({"chart": spec.get("chart")})

    spec_key = _spec_fingerprint(resolution)

    # commentary=True can replay from cache; commentary=False is a bare re-execute
    # (no LLM cost to save), so it always runs fresh and reports cached: false.
    if commentary and not refresh:
        cached = get_cached_answer(question, params, spec_key)
        if cached is not None:
            hit = dict(cached)
            hit["cached"] = True
            return hit

    if commentary and rate_limited(cfg.rate_limit):
        raise AskRateLimitError(
            "ask rate limit reached "
            f"({cfg.rate_limit}/min — ask.rate_limit in dashdown.yaml); "
            "try again shortly"
        )

    started = time.monotonic()
    result = execute_resolution(resolution, project, params)
    serialized = serialize_result(result)
    chart = resolution_chart_shape(resolution, serialized)

    resolved = {
        "kind": resolution.kind,
        "provenance": resolution.provenance,
        "query_name": resolution.query_name,
        "connector": resolution.connector,
        "detail": _resolution_detail(resolution),
    }
    semantic_options = _semantic_options(project, resolution.model)

    if not commentary:
        payload = {
            "question": question,
            "resolved": resolved,
            "columns": serialized["columns"],
            "rows": serialized["rows"],
            "chart": chart,
            "answer_html": "",
            "answer_text": "",
            "annotations": [],
            "model": model,
            "semantic_options": semantic_options,
            "cached": False,
        }
        _log(project, question, resolution, len(result.rows), started, model, cfg, via="spec_edit")
        return payload

    # commentary=True: one LLM call, same generation path as answer_question step 3.
    chart_ctx: ChartContext | None = None
    if chart is not None:
        chart_ctx = build_chart_context(
            chart["type"],
            x=chart["x"],
            y=chart["y"],
            series_by=chart.get("series_by"),
        )
    synthetic = AskDef(
        id="_ask_runtime",
        queries=((resolution.query_name or "result", resolution.connector or ""),),
        prompt=question,
        max_rows=cfg.max_rows,
        page_title=project.config.title,
        chart_context=chart_ctx,
    )
    adapter = project.get_llm_adapter()
    try:
        answer_html, answer_text, annotations = generate_answer(
            synthetic, [result], adapter, params
        )
    except Exception as e:  # noqa: BLE001
        raise AskLLMError(f"{type(e).__name__}: {e}") from e

    payload = {
        "question": question,
        "resolved": resolved,
        "columns": serialized["columns"],
        "rows": serialized["rows"],
        "chart": chart,
        "answer_html": answer_html,
        "answer_text": answer_text,
        "annotations": annotations,
        "model": model,
        "semantic_options": semantic_options,
        "cached": False,
    }
    cache_answer(question, params, payload, cfg.cache_ttl, spec_key)
    _log(project, question, resolution, len(result.rows), started, model, cfg, via="spec_edit")
    return payload


# --------------------------------------------------------------------------- #
# "Keep on this page" — turn a liked runtime answer into authored markdown
# --------------------------------------------------------------------------- #
# The chart `type` the client inferred → the PascalCase component that renders it.
_KEEP_CHART_COMPONENTS = {
    "line": "LineChart",
    "bar": "BarChart",
    "scatter": "ScatterChart",
    "pie": "PieChart",
    "funnel": "FunnelChart",
    "treemap": "TreemapChart",
}


def _attr_escape(value: Any) -> str:
    """Make an arbitrary string safe as a double-quoted HTML/component attribute
    value. Strips ``<``/``>`` (so a value can't open a tag) and escapes ``"`` →
    ``&quot;`` (so it can't close the attribute) — the minimal defense the kept
    section needs, since these strings land verbatim in an authored ``.md``."""
    return str(value).replace("<", "").replace(">", "").replace('"', "&quot;")


def build_kept_markdown(
    project: "Project",
    question: str,
    resolved: dict[str, Any],
    chart: dict[str, Any] | None,
) -> str:
    """Render a liked runtime answer as an authored, **live** markdown section.

    The operator "keeps" an answer on a page: we append a ``## question`` section
    whose components re-query on every visit (and whose ``<Ask>`` re-answers), so a
    dashboard grows into "the answers you kept". The section is *authored markdown*,
    not a data snapshot — nothing here is baked, it all re-runs.

    **Trust model — the client is never trusted.** ``resolved`` is the response
    payload's ``resolved`` object, but it came back through the browser, so every
    name it carries is re-validated against the *live* catalog before it can land
    in a file:

    * ``semantic`` — the ``detail`` is rebuilt into the dict shape
      :func:`_validate_semantic` consumes and re-validated against the current
      semantic models; the *validated* :class:`Resolution` (not the client's
      values) is what gets emitted.
    * ``query`` — the name must still resolve to a real library or Python query.
    * ``list`` — the ``detail`` is re-validated by :func:`_validate_list` against
      the live models and emitted as a ``<List />`` (the authored twin of the list
      rung). Filters / date range are **not** carried into the file in v1 — when the
      resolution held any, the kept-from comment notes "filters not carried over".

    Only a raw-``sql`` answer stays unkeepable: it has no named artifact to
    reference from markdown. Any validation failure — off-catalog
    metric/dimension/query/column, unknown kind, sql — raises :class:`ValueError`
    with a reason (the endpoint maps it to a 400). Free-form strings that reach an
    attribute (the question title, inferred ``x``/``y`` columns) are run through
    :func:`_attr_escape` so a crafted value can't break out of its attribute.

    Returns just the section text (leading blank line + heading + components); the
    caller owns file I/O and blank-line separation."""
    kind = str((resolved or {}).get("kind", "")).strip().lower()
    detail = (resolved or {}).get("detail") or {}
    provenance = str((resolved or {}).get("provenance", "") or "").strip()

    if kind not in ("semantic", "query", "list"):
        why = (
            "raw SQL has no named source"
            if kind == "sql"
            else f"unknown kind {kind!r}"
        )
        raise ValueError(
            f"cannot keep a {kind or 'missing'!r} answer — only semantic, "
            f"named-query, and list answers reference a re-runnable artifact ({why})"
        )

    heading = " ".join(str(question or "").split())
    if not heading:
        raise ValueError("cannot keep an answer with an empty question")
    q_attr = _attr_escape(heading)

    components: list[str] = []
    # Set by the list rung when the resolution carried filters/date range that v1
    # doesn't emit into the file — noted in the kept-from comment.
    filters_dropped = False

    if kind == "semantic":
        # Re-validate the client-supplied detail against the live catalog. A bad
        # metric/dimension/grain degrades to kind "none" in _validate_semantic —
        # we refuse to write it.
        obj = {
            "kind": "semantic",
            "model": detail.get("model"),
            "metric": detail.get("metric"),
            "by": detail.get("by"),
            "series": detail.get("series"),
            "grain": detail.get("grain"),
            "filters": detail.get("filters") or {},
        }
        res = _validate_semantic(obj, project)
        if res.kind == "none":
            raise ValueError(f"semantic answer failed re-validation: {res.reason}")
        metric_ref = f"{res.model}.{res.metric}"
        by_ref = f"{res.model}.{res.by}" if res.by else None
        series_ref = f"{res.model}.{res.series}" if res.series else None

        if chart is not None:
            component = _KEEP_CHART_COMPONENTS.get(chart.get("type"), "LineChart")
            attrs = [f"metric={{{metric_ref}}}"]
            if by_ref:
                attrs.append(f"by={{{by_ref}}}")
            if series_ref:
                attrs.append(f"series={{{series_ref}}}")
            if res.grain:
                attrs.append(f'grain="{res.grain}"')
            attrs.append(f'title="{q_attr}"')
            components.append(f"<{component} {' '.join(attrs)} />")

        ask_attrs = [f"metric={{{metric_ref}}}"]
        if by_ref:
            ask_attrs.append(f"by={{{by_ref}}}")
        if series_ref:
            ask_attrs.append(f"series={{{series_ref}}}")
        ask_attrs.append(f'ask="{q_attr}"')
        components.append(f"<Ask {' '.join(ask_attrs)} />")

    elif kind == "list":
        # Re-validate the client-supplied detail against the live catalog. A list
        # with no valid column degrades to kind "none" in _validate_list — we refuse
        # to write it. Only the validated names land in the file (same trust model
        # as the semantic rung).
        obj = {
            "kind": "list",
            "model": detail.get("model"),
            "columns": detail.get("columns"),
            "order_by": detail.get("order_by"),
            "desc": detail.get("desc", True),
            "limit": detail.get("limit", DEFAULT_LIST_LIMIT),
            "filters": detail.get("filters") or {},
            "date_start": detail.get("date_start", ""),
            "date_end": detail.get("date_end", ""),
        }
        res = _validate_list(obj, project)
        if res.kind == "none":
            raise ValueError(f"list answer failed re-validation: {res.reason}")
        # Filters / date range are not carried into the file in v1 — flag it for the
        # kept-from comment when the validated resolution held any.
        filters_dropped = bool(res.filters) or bool(res.date_start) or bool(res.date_end)

        list_attrs = [
            f'model="{_attr_escape(res.model)}"',
            f'columns="{_attr_escape(", ".join(res.columns))}"',
        ]
        if res.order_by:
            list_attrs.append(f'order_by="{_attr_escape(res.order_by)}"')
        # The <List> default is desc=true, so an ascending list must say so
        # explicitly (a bare flag can't express "off").
        list_attrs.append("desc" if res.desc else "desc=false")
        list_attrs.append(f"limit={res.limit}")
        list_attrs.append(f'title="{q_attr}"')
        components.append(f"<List {' '.join(list_attrs)} />")

    else:  # kind == "query"
        name = detail.get("name") or (resolved or {}).get("query_name")
        if not isinstance(name, str) or not name.strip():
            raise ValueError("query answer is missing a query name")
        name = name.strip()
        if name not in project.queries and name not in project.python_queries:
            raise ValueError(f"query answer references unknown query {name!r}")

        if chart is not None:
            component = _KEEP_CHART_COMPONENTS.get(chart.get("type"), "LineChart")
            x = _attr_escape(chart.get("x", ""))
            y = _attr_escape(chart.get("y", ""))
            components.append(
                f'<{component} data={{{name}}} x="{x}" y="{y}" title="{q_attr}" />'
            )
        components.append(f"<Table data={{{name}}} />")
        components.append(f'<Ask data={{{name}}} ask="{q_attr}" />')

    date = datetime.now().strftime("%Y-%m-%d")
    note = " · filters not carried over" if filters_dropped else ""
    comment = f"<!-- kept from an ask answer · {provenance} · {date}{note} -->"
    parts = [f"## {heading}", comment, *components]
    return "\n" + "\n".join(parts) + "\n"


def ask_unavailable_notice(project: "Project") -> str | None:
    """Return a reader-facing notice when the runtime ask box is off, else ``None``.

    Off when no LLM provider is configured / it's misconfigured, or when
    ``ask.enabled`` is false. Shared by the endpoint and the CLI so both say the
    same thing (mirrors the ``<Ask />`` card convention)."""
    llm_cfg = project.config.llm
    if not llm_cfg.enabled:
        return unavailable_notice(llm_cfg)
    if not project.config.ask.enabled:
        return "The ask box is disabled for this project (ask.enabled: false)."
    return None
