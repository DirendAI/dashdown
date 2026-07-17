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

import json
import logging
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

from dashdown.chart_annotations import ChartContext, build_chart_context
from dashdown.data.base import QueryResult
from dashdown.llm import (
    AskDef,
    LLMAdapter,
    generate_answer,
    resolve_model_name,
    unavailable_notice,
)
from dashdown.render.markdown import render_markdown_text
from dashdown.render.pipeline import (
    DEFAULT_CACHE_TTL,
    cache_result,
    get_cached_result,
    get_python_query_def,
    get_query_def,
    register_python_query_def,
    serialize_result,
    serialize_value,
    _substitute_params,
)
from dashdown.python_query import run_python_query
from dashdown.semantic import (
    DATE_END_PARAM,
    DATE_START_PARAM,
    GRAIN_TOKENS,
    build_semantic_spec,
    resolve_ref,
)

if TYPE_CHECKING:  # pragma: no cover - typing only (avoids an import cycle)
    from dashdown.project import Project

log = logging.getLogger(__name__)

_PARAM_RE = re.compile(r"\$\{(\w+)\}")

# Defensive cap on rows returned by the raw-SQL rung: a hand-written SELECT has no
# author-set LIMIT and its result feeds both the model payload and the wire.
MAX_SQL_ROWS = 1000

# The valid resolution kinds the parser recognizes; anything else degrades to none.
_KINDS = frozenset({"semantic", "query", "sql", "none"})


# --------------------------------------------------------------------------- #
# Errors — mapped by the endpoint to distinct HTTP codes (502 vs 500).
# --------------------------------------------------------------------------- #
class AskLLMError(RuntimeError):
    """An LLM call (resolve or answer) failed — the endpoint maps this to 502."""


class AskQueryError(RuntimeError):
    """Executing the resolved query failed — the endpoint maps this to 500."""


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
    '"by": "<dimension or empty>", "grain": "<grain or empty>", '
    '"filters": {"<dimension>": ["value", ...]}, "date_start": "", "date_end": ""}\n'
    '  {"kind": "query", "name": "<query name>", "params": {"<param>": "<value>"}}\n'
    '  {"kind": "none", "reason": "<why the catalog cannot answer this>"}\n\n'
    "Rules: `metric` and `by` must be names listed under the chosen model; `grain` "
    "must be one of the model's grains and only makes sense with a time-dimension "
    "`by`; `filters` keys must be the model's dimensions. `name` must be a query "
    "listed in the catalog. If nothing in the catalog can answer the question, "
    'return kind "none" with a short reason. Never invent names.'
)

# Appended to the system prompt only when ask.allow_sql is on (rung 3 opt-in).
_ALLOW_SQL_CLAUSE = (
    "\n\nRaw SQL is also permitted for this project when no catalog source fits: "
    '{"kind": "sql", "sql": "SELECT ..."}. Prefer a catalog source whenever one '
    "answers the question; only reach for raw SQL as a last resort."
)


def build_resolver_prompt(
    catalog: dict[str, Any], question: str, allow_sql: bool
) -> tuple[str, str]:
    """Return ``(system, user)`` for the resolution call.

    The ``sql`` rung is described in the system prompt **only** when ``allow_sql``
    — a model can't pick a rung it was never told about (belt to the validator's
    braces, which reject ``sql`` regardless when the config is off)."""
    system = RESOLVER_SYSTEM_PROMPT + (_ALLOW_SQL_CLAUSE if allow_sql else "")
    user = (
        "Catalog:\n"
        + json.dumps(catalog, indent=2, default=str)
        + f"\n\nQuestion: {question}\n\nJSON:"
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
    # semantic
    model: str | None = None
    metric: str | None = None
    by: str | None = None
    grain: str | None = None
    filters: dict[str, list[str]] = field(default_factory=dict)
    date_start: str = ""
    date_end: str = ""
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


def _none(reason: str) -> Resolution:
    return Resolution(kind="none", reason=reason, provenance=f"unresolved: {reason}")


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
        return Resolution(
            kind="none",
            reason=str(obj.get("reason", "") or "no matching data source"),
            provenance="unresolved: "
            + str(obj.get("reason", "") or "no matching data source"),
        )

    if kind == "sql":
        if not allow_sql:
            return _none("raw SQL is disabled for this project (ask.allow_sql)")
        sql = obj.get("sql")
        if not isinstance(sql, str) or not sql.strip():
            return _none("sql resolution missing a sql string")
        return Resolution(
            kind="sql",
            sql=sql.strip(),
            provenance="raw SQL (ask.allow_sql)",
        )

    if kind == "query":
        return _validate_query(obj, project)

    return _validate_semantic(obj, project)


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


def _validate_semantic(obj: dict[str, Any], project: "Project") -> Resolution:
    model = obj.get("model")
    handle = project.semantic_models.get(model) if isinstance(model, str) else None
    if handle is None:
        return _none(f"unknown semantic model {model!r}")

    metric = obj.get("metric")
    if not isinstance(metric, str) or metric.strip() not in handle.measure_lookup:
        return _none(f"unknown metric {metric!r} on model {model!r}")
    metric = metric.strip()

    by = obj.get("by")
    by = by.strip() if isinstance(by, str) and by.strip() else None
    if by is not None and by not in handle.dim_lookup:
        return _none(f"unknown dimension {by!r} on model {model!r}")

    grain = obj.get("grain")
    grain = grain.strip().lower() if isinstance(grain, str) and grain.strip() else None
    if grain is not None and grain not in GRAIN_TOKENS:
        return _none(f"unknown grain {grain!r}")

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
        grain=grain,
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
        if resolution.kind == "query":
            return _execute_query(resolution, project, params)
        if resolution.kind == "sql":
            return _execute_sql(resolution, project)
    except AskQueryError:
        raise
    except Exception as e:  # noqa: BLE001 - any backend failure becomes a 500
        raise AskQueryError(f"{type(e).__name__}: {e}") from e
    raise AskQueryError(f"cannot execute resolution kind {resolution.kind!r}")


def _execute_semantic(
    resolution: Resolution, project: "Project", params: dict[str, str]
) -> QueryResult:
    metric_ref = f"{resolution.model}.{resolution.metric}"
    by_ref = f"{resolution.model}.{resolution.by}" if resolution.by else None
    ref = resolve_ref(
        project.semantic_models, metric_ref, by_ref, grain=resolution.grain
    )
    spec = build_semantic_spec(project.semantic_models, ref, project.connectors)
    # Register so a follow-up data fetch by the synthetic name resolves too (same
    # seam the render pipeline uses for a `metric={…}` chart).
    register_python_query_def(spec.name, spec.connector, spec)
    resolution.query_name = spec.name
    resolution.connector = spec.connector

    # build_filters reads dimension keys + date_start/date_end off params. Start
    # from the live dashboard filters, then overlay the resolution's own filters
    # (data, never `${param}`-interpolated).
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


def infer_chart_shape(result: QueryResult) -> dict[str, str] | None:
    """Infer ``{type, x, y}`` for a result, or ``None`` for a headline value.

    Mirrors ``resolveAutoConfig`` so the client renders *exactly* this chart:
    temporal x → line, categorical x → bar, numeric x → scatter; y is the first
    numeric column that isn't x. A single-row result (or one with no chartable
    numeric column) is a headline, not a chart → ``None``. Classification runs on
    the **serialized** cells (dates as ISO strings, Decimals as floats) so it sees
    what the browser would."""
    payload = serialize_result(result)
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
    y = next((c for c in columns if c != x and kind[c] == "numeric"), None)
    if not y:
        return None

    chart_type = "bar"
    if kind[x] == "temporal":
        chart_type = "line"
    elif kind[x] == "numeric":
        chart_type = "scatter"
    return {"type": chart_type, "x": x, "y": y}


# --------------------------------------------------------------------------- #
# Answer cache: (normalized question, frozen params) -> full response payload
# --------------------------------------------------------------------------- #
# Mirrors llm.py::_answer_cache. The value is the whole response payload so a hit
# replays chart + table + answer identically; `refresh` (POST body) bypasses it —
# config can't disable runtime refresh, cache_ttl bounds the cost instead.
_answer_cache: dict[tuple[str, tuple], tuple[dict[str, Any], float]] = {}

_WS_RE = re.compile(r"\s+")


def normalize_question(question: str) -> str:
    """Lowercase + collapse whitespace, so trivially-different spellings of one
    question share a cache entry."""
    return _WS_RE.sub(" ", (question or "").strip().lower())


def _freeze(params: dict[str, str]) -> tuple:
    return tuple(sorted((str(k), str(v)) for k, v in params.items()))


def get_cached_answer(
    question: str, params: dict[str, str]
) -> dict[str, Any] | None:
    key = (normalize_question(question), _freeze(params))
    entry = _answer_cache.get(key)
    if entry is None:
        return None
    payload, expiry = entry
    if time.monotonic() > expiry:
        _answer_cache.pop(key, None)
        return None
    return payload


def cache_answer(
    question: str, params: dict[str, str], payload: dict[str, Any], ttl: int
) -> None:
    key = (normalize_question(question), _freeze(params))
    _answer_cache[key] = (payload, time.monotonic() + ttl)


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
) -> dict[str, Any]:
    """Resolve → execute → answer one runtime question, returning the full payload.

    Assumes the caller already checked ``llm`` + ``ask`` are enabled (see
    :func:`ask_unavailable_notice`). Two LLM calls on a cache miss: resolve, then
    answer. Raises :class:`AskLLMError` (→ 502) on an LLM failure and
    :class:`AskQueryError` (→ 500) on a query failure; a *bad resolution* is never
    an error — it degrades to kind ``none`` in the payload."""
    params = dict(params or {})
    cfg = project.config.ask
    llm_cfg = project.config.llm
    model = resolve_model_name(llm_cfg)

    if not refresh:
        cached = get_cached_answer(question, params)
        if cached is not None:
            hit = dict(cached)
            hit["cached"] = True
            return hit

    adapter = project.get_llm_adapter()
    started = time.monotonic()

    # 1) Resolve.
    catalog = build_ask_catalog(project)
    system, user = build_resolver_prompt(catalog, question, cfg.allow_sql)
    try:
        raw = adapter.complete(system, user)
    except Exception as e:  # noqa: BLE001
        raise AskLLMError(f"{type(e).__name__}: {e}") from e
    resolution = parse_resolution(raw, project, cfg.allow_sql)

    # kind "none": the model couldn't route it. Carry the reason as the answer;
    # no data, no chart, no second (billable) LLM call.
    if resolution.kind == "none":
        payload = {
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
        cache_answer(question, params, payload, cfg.cache_ttl)
        _log(project, question, resolution, 0, started, model, cfg)
        return payload

    # 2) Execute.
    result = execute_resolution(resolution, project, params)
    chart = infer_chart_shape(result)

    # 3) Answer — reuse the exact <Ask /> generation path. A chart shape means a
    # ChartContext, so generate_answer runs the annotation protocol for it.
    chart_ctx: ChartContext | None = None
    if chart is not None:
        chart_ctx = build_chart_context(chart["type"], x=chart["x"], y=chart["y"])
    synthetic = AskDef(
        id="_ask_runtime",
        queries=((resolution.query_name or "result", resolution.connector or ""),),
        prompt=question,
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

    serialized = serialize_result(result)
    payload = {
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
        "answer_html": answer_html,
        "answer_text": answer_text,
        "annotations": annotations,
        "model": model,
        "cached": False,
    }
    cache_answer(question, params, payload, cfg.cache_ttl)
    _log(project, question, resolution, len(result.rows), started, model, cfg)
    return payload


def _resolution_detail(resolution: Resolution) -> dict[str, Any]:
    """The rung-specific fields, for the response's ``resolved.detail``."""
    if resolution.kind == "semantic":
        return {
            "model": resolution.model,
            "metric": resolution.metric,
            "by": resolution.by,
            "grain": resolution.grain,
            "filters": resolution.filters,
            "date_start": resolution.date_start,
            "date_end": resolution.date_end,
        }
    if resolution.kind == "query":
        return {"name": resolution.name, "params": resolution.params}
    if resolution.kind == "sql":
        return {"sql": resolution.sql}
    return {}


def _log(
    project: "Project",
    question: str,
    resolution: Resolution,
    rows: int,
    started: float,
    model: str,
    cfg: Any,
) -> None:
    if not cfg.log:
        return
    log_ask(
        project,
        {
            "ts": datetime.now(timezone.utc).isoformat(),
            "question": question,
            "kind": resolution.kind,
            "provenance": resolution.provenance,
            "rows": rows,
            "duration_ms": round((time.monotonic() - started) * 1000, 1),
            "model": model,
            "cached": False,
        },
    )


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
