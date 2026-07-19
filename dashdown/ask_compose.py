"""AI page composition — "add elements to this page" from an operator instruction.

The compose surface is the keep flow's generative sibling: where "keep" pins an
answer the operator already saw, compose turns a free-form *instruction* ("add a
KPI row with revenue and orders at the top") into new page content. One
constrained LLM call (:func:`compose_plan`) sees the **ask catalog** (semantic
measures/dimensions + named queries — never a schema) plus a fixed element
vocabulary and returns a **typed plan** — JSON entries, never markdown/HTML/
component code. :func:`build_composed_markdown` then compiles the plan into an
authored markdown section through the *same validators the keep flow uses*
(``_validate_semantic`` / ``_validate_list`` / the query-name check), so every
name that lands in a file came out of a server-side validator; free text
(headings, prose, titles) is length-capped and angle-escaped so the model can
never author raw markup.

**Preview-then-apply.** Unlike keep (the operator already saw the answer they're
keeping), a composed section is model-chosen — so the compose endpoint returns
the compiled section *without writing*, the client shows it, and the apply
endpoint re-validates the client-echoed plan (deterministic compilation, so
preview == written) before splicing it into the page. The written section is
wrapped in the standard ``dashdown:keep`` marker pair with ``kind=composed``, so
the existing section toolbars (edit / remove / post-write flash in
``page_edit.js``) govern composed sections with zero client changes.

An individual plan entry that fails validation is **dropped with a reason**
(returned to the client), never a 500 — the same degrade posture as the
resolution ladder; only a plan with *no* surviving entry is an error.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Any

from dashdown.ask_engine import (
    AskLLMError,
    DEFAULT_LIST_LIMIT,
    _KEEP_CHART_COMPONENTS,
    _attr_escape,
    _comment_safe,
    _extract_json,
    _semantic_options,
    _validate_list,
    _validate_semantic,
    cached_ask_catalog,
)

if TYPE_CHECKING:  # pragma: no cover
    from dashdown.project import Project

# Bounds on what one compose can write — a plan is model output, so every free
# dimension is capped before it can touch a file.
MAX_PLAN_SECTIONS = 8
MAX_PROSE_CHARS = 600
MAX_TITLE_CHARS = 120
MAX_KPI_METRICS = 6

COMPOSE_SYSTEM_PROMPT = (
    "You are a dashboard-composition planner for an analytics engine. You are "
    "given a catalog of the data sources a dashboard exposes and an operator "
    "instruction asking to ADD content to a dashboard page. Return a single "
    "JSON object — a PLAN of page elements drawn ONLY from the catalog — never "
    "markdown, never HTML, never component code.\n\n"
    "Output ONLY the JSON object, shaped:\n"
    '{"title": "<optional short section heading or empty>", '
    '"sections": [<1-8 entries>]}\n\n'
    "Each entry is one of:\n"
    '  {"element": "heading", "text": "<short subheading>"}\n'
    '  {"element": "prose", "text": "<one short framing sentence>"}\n'
    '  {"element": "kpi_row", "metrics": ["<model>.<measure>", ...]}'
    "   (1-6 headline numbers side by side)\n"
    '  {"element": "value", "model": "<model>", "metric": "<measure>"}'
    "   (one headline number)\n"
    '  {"element": "chart", "model": "<model>", "metric": "<measure>", '
    '"by": "<dimension>", "series": "<second dimension or empty>", '
    '"grain": "<grain or empty>", '
    '"chart": "<line|bar|scatter|pie|funnel|treemap or empty>", '
    '"title": "<chart title>"}\n'
    '  {"element": "chart", "query": "<query name>", "title": "<chart title>"}'
    "   (a catalog query, shape auto-inferred)\n"
    '  {"element": "table", "model": "<model>", "metric": "<measure>", '
    '"by": "<dimension>"}\n'
    '  {"element": "table", "query": "<query name>"}\n'
    '  {"element": "list", "model": "<model>", "columns": ["<dimension>", ...], '
    '"order_by": "<one of columns>", "desc": true, "limit": 10}'
    "   (detail rows)\n\n"
    "Rules: every model / measure / dimension / query name must come from the "
    "catalog — never invent names. Keep the plan minimal: exactly what the "
    "instruction asks for, nothing decorative. Use kpi_row for several headline "
    "metrics, value for one. prose is at most one short factual framing "
    "sentence — no analysis, no invented numbers. When the instruction cannot "
    'be satisfied from the catalog, return {"error": "<what the catalog can '
    'offer instead>"}.'
)


def build_compose_prompt(
    catalog: dict[str, Any], instruction: str
) -> tuple[str, str]:
    """``(system, user)`` for the compose call — same catalog JSON the resolver
    ships, with the instruction in place of a question."""
    user = (
        "Catalog:\n"
        + json.dumps(catalog, indent=2, default=str)
        + f"\n\nInstruction: {instruction}\n\nJSON:"
    )
    return COMPOSE_SYSTEM_PROMPT, user


def _plan_shape_ok(plan: Any) -> bool:
    return (
        isinstance(plan, dict)
        and isinstance(plan.get("sections"), list)
        and bool(plan["sections"])
    )


def compose_plan(project: "Project", instruction: str) -> dict[str, Any]:
    """One constrained LLM call → a typed plan (with one self-repair retry).

    Mirrors the resolver's error split: an unusable *response* gets exactly one
    corrective retry, then raises :class:`ValueError` (a clean 400); a transport
    failure raises :class:`AskLLMError` (a 502). A plan the model itself refuses
    (``{"error": …}``) raises ValueError carrying the model's reason — the
    closest-offer text the operator can act on."""
    adapter = project.get_llm_adapter()
    catalog = cached_ask_catalog(project)
    system, user = build_compose_prompt(catalog, instruction)
    try:
        raw = adapter.complete(system, user)
    except Exception as e:  # noqa: BLE001
        raise AskLLMError(f"{type(e).__name__}: {e}") from e
    plan = _extract_json(raw)
    if isinstance(plan, dict) and plan.get("error"):
        raise ValueError(str(plan["error"]))
    if not _plan_shape_ok(plan):
        repair = (
            user
            + "\n\nYour previous response was not a valid plan (a JSON object "
            'with a non-empty "sections" list). Return ONLY the corrected JSON '
            "plan, using only names from the catalog."
        )
        try:
            raw = adapter.complete(system, repair)
        except Exception as e:  # noqa: BLE001
            raise AskLLMError(f"{type(e).__name__}: {e}") from e
        plan = _extract_json(raw)
        if isinstance(plan, dict) and plan.get("error"):
            raise ValueError(str(plan["error"]))
        if not _plan_shape_ok(plan):
            raise ValueError("the model did not return a usable compose plan")
    return plan  # type: ignore[return-value]


# --------------------------------------------------------------------------- #
# Plan → markdown compilation (pure + deterministic; every name re-validated)
# --------------------------------------------------------------------------- #
def _plain_text(value: Any, limit: int) -> str:
    """Whitespace-collapsed, length-capped, angle-escaped free text — the only
    path model prose takes into a file, so it can never carry markup."""
    text = " ".join(str(value or "").split())
    if not text:
        raise ValueError("empty text")
    if len(text) > limit:
        text = text[:limit].rstrip() + "…"
    return text.replace("<", "&lt;").replace(">", "&gt;")


def _validated_query_name(value: Any, project: "Project") -> str:
    name = str(value or "").strip()
    if not name:
        raise ValueError("missing query name")
    if name not in project.queries and name not in project.python_queries:
        raise ValueError(f"unknown query {name!r}")
    return name


def _validated_semantic(
    entry: dict[str, Any], project: "Project", *, scalar: bool = False
):
    """Run an entry's semantic fields through the one blessed validator.

    ``scalar`` drops any grouping (a value/kpi cell is a headline aggregate)."""
    obj = {
        "kind": "semantic",
        "model": entry.get("model"),
        "metric": entry.get("metric"),
        "by": None if scalar else entry.get("by"),
        "series": None if scalar else entry.get("series"),
        "grain": None if scalar else entry.get("grain"),
        "filters": {},
    }
    res = _validate_semantic(obj, project)
    if res.kind == "none":
        raise ValueError(res.reason or "invalid semantic reference")
    return res


def _metric_label(measure: str) -> str:
    return measure.split(".")[-1].replace("_", " ").title()


def _sem_attr_run(res) -> list[str]:
    out = [f"metric={{{res.model}.{res.metric}}}"]
    if res.by:
        out.append(f"by={{{res.model}.{res.by}}}")
    if res.series:
        out.append(f"series={{{res.model}.{res.series}}}")
    if res.grain:
        out.append(f'grain="{res.grain}"')
    return out


def _compile_kpi_row(entry: dict[str, Any], project: "Project") -> str:
    metrics = entry.get("metrics")
    if not isinstance(metrics, list) or not metrics:
        raise ValueError("kpi_row needs a non-empty 'metrics' list")
    if len(metrics) > MAX_KPI_METRICS:
        raise ValueError(f"kpi_row is capped at {MAX_KPI_METRICS} metrics")
    cells = []
    for ref in metrics:
        model, _, measure = str(ref).partition(".")
        res = _validated_semantic(
            {"model": model, "metric": measure}, project, scalar=True
        )
        label = _attr_escape(_metric_label(res.metric))
        cells.append(f'  <Counter metric={{{res.model}.{res.metric}}} label="{label}" />')
    return f"<Grid cols={len(cells)}>\n" + "\n".join(cells) + "\n</Grid>"


def _compile_value(entry: dict[str, Any], project: "Project") -> str:
    if entry.get("query"):
        name = _validated_query_name(entry.get("query"), project)
        return f"<Counter data={{{name}}} />"
    res = _validated_semantic(entry, project, scalar=True)
    label = _attr_escape(_metric_label(res.metric))
    return f'<Counter metric={{{res.model}.{res.metric}}} label="{label}" />'


def _compile_chart(entry: dict[str, Any], project: "Project") -> str:
    title = entry.get("title")
    title_attr = (
        f' title="{_attr_escape(_plain_text(title, MAX_TITLE_CHARS))}"'
        if title
        else ""
    )
    if entry.get("query"):
        # A named query's columns aren't in the catalog, so the shape is
        # inferred client-side by the auto chart.
        name = _validated_query_name(entry.get("query"), project)
        return f"<Chart auto data={{{name}}}{title_attr} />"
    res = _validated_semantic(entry, project)
    if not res.by:
        raise ValueError(
            "a chart needs a 'by' dimension — use element 'value' for a single number"
        )
    ctype = str(entry.get("chart") or "").strip().lower()
    if ctype and ctype not in _KEEP_CHART_COMPONENTS:
        raise ValueError(f"unknown chart type {ctype!r}")
    if not ctype:
        opts = _semantic_options(project, res.model) or {}
        temporal = bool(res.grain) or res.by == opts.get("time_dimension")
        ctype = "line" if temporal else "bar"
    component = _KEEP_CHART_COMPONENTS[ctype]
    return f"<{component} {' '.join(_sem_attr_run(res))}{title_attr} />"


def _compile_table(entry: dict[str, Any], project: "Project") -> str:
    if entry.get("query"):
        name = _validated_query_name(entry.get("query"), project)
        return f"<Table data={{{name}}} />"
    res = _validated_semantic(entry, project)
    if not res.by:
        raise ValueError("a semantic table needs a 'by' dimension")
    return f"<Table {' '.join(_sem_attr_run(res))} />"


def _compile_list(entry: dict[str, Any], project: "Project") -> str:
    obj = {
        "kind": "list",
        "model": entry.get("model"),
        "columns": entry.get("columns"),
        "order_by": entry.get("order_by"),
        "desc": entry.get("desc", True),
        "limit": entry.get("limit", DEFAULT_LIST_LIMIT),
        "filters": {},
    }
    res = _validate_list(obj, project)
    if res.kind == "none":
        raise ValueError(res.reason or "invalid list reference")
    attrs = [
        f'model="{_attr_escape(res.model)}"',
        f'columns="{_attr_escape(", ".join(res.columns))}"',
    ]
    if res.order_by:
        attrs.append(f'order_by="{_attr_escape(res.order_by)}"')
    attrs.append("desc" if res.desc else "desc=false")
    attrs.append(f"limit={res.limit}")
    if entry.get("title"):
        attrs.append(
            f'title="{_attr_escape(_plain_text(entry.get("title"), MAX_TITLE_CHARS))}"'
        )
    return f"<List {' '.join(attrs)} />"


def _compile_entry(entry: Any, project: "Project") -> str:
    if not isinstance(entry, dict):
        raise ValueError("plan entry must be an object")
    element = str(entry.get("element", "")).strip().lower()
    if element == "heading":
        return f"### {_plain_text(entry.get('text'), MAX_TITLE_CHARS)}"
    if element == "prose":
        return _plain_text(entry.get("text"), MAX_PROSE_CHARS)
    if element == "kpi_row":
        return _compile_kpi_row(entry, project)
    if element == "value":
        return _compile_value(entry, project)
    if element == "chart":
        return _compile_chart(entry, project)
    if element == "table":
        return _compile_table(entry, project)
    if element == "list":
        return _compile_list(entry, project)
    raise ValueError(f"unknown element {element!r}")


def build_composed_markdown(
    project: "Project", instruction: str, plan: Any
) -> tuple[str, str, list[dict[str, Any]]]:
    """Compile a compose plan into a marker-wrapped markdown section.

    Pure and deterministic (no LLM, no I/O), so the apply endpoint re-running it
    on the client-echoed plan provably writes what the preview showed. Returns
    ``(section_text, keep_id, dropped)`` — ``dropped`` is one
    ``{"entry", "reason"}`` per plan entry that failed validation (individually
    dropped, never a whole-plan failure); a plan with *no* surviving entry
    raises :class:`ValueError`. Components are blank-line separated (unlike the
    single-line keep recipe) because a plan freely mixes prose and components —
    markdown needs the block boundary."""
    instr = " ".join(str(instruction or "").split())
    if not instr:
        raise ValueError("empty instruction")
    if not isinstance(plan, dict):
        raise ValueError("plan must be a JSON object")
    if plan.get("error"):
        raise ValueError(str(plan["error"]))
    sections = plan.get("sections")
    if not isinstance(sections, list) or not sections:
        raise ValueError("plan has no sections")

    components: list[str] = []
    dropped: list[dict[str, Any]] = []
    for i, entry in enumerate(sections):
        if len(components) >= MAX_PLAN_SECTIONS:
            dropped.append(
                {"entry": entry, "reason": f"plan is capped at {MAX_PLAN_SECTIONS} sections"}
            )
            continue
        try:
            components.append(_compile_entry(entry, project))
        except ValueError as e:
            dropped.append({"entry": entry, "reason": str(e)})
    if not components:
        first = dropped[0]["reason"] if dropped else "empty plan"
        raise ValueError(f"no plan entry survived validation ({first})")

    date = datetime.now().strftime("%Y-%m-%d")
    keep_id = uuid.uuid4().hex[:8]
    open_marker = (
        f"<!-- dashdown:keep id={keep_id} kind=composed · "
        f"compose: {_comment_safe(instr)} · {date} -->"
    )
    close_marker = f"<!-- /dashdown:keep id={keep_id} -->"
    parts = [open_marker]
    if plan.get("title"):
        parts.append(f"## {_plain_text(plan.get('title'), MAX_TITLE_CHARS)}")
    parts.extend(components)
    parts.append(close_marker)
    return "\n" + "\n\n".join(parts) + "\n", keep_id, dropped
