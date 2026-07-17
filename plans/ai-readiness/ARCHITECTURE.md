# AI-Readiness — technical architecture

Companion to `PLAN.md`. This fixes the contracts the implementation waves build against.
Line numbers are as of branch point `d8b07f9`.

## A. Runtime ask engine (`dashdown/ask_engine.py`, new)

Two LLM calls per cache-miss question:

1. **Resolve** — the LLM sees a *catalog* (semantic models + library/python queries) and maps
   the question to a JSON resolution. It never sees raw schemas beyond that menu.
2. **Answer** — the resolved query runs; the result feeds the *existing*
   `generate_answer()` path (`llm.py:763`) via a synthetic `AskDef`, including the
   chart-annotation protocol when a chart shape was inferred.

### Catalog (`build_ask_catalog(project) -> dict`)

- Semantic: for each `Project.semantic_models` handle: model name, measures
  (`handle.measure_lookup`), dimensions (`handle.dim_lookup`), `time_dimension`,
  grain tokens (`GRAIN_TOKENS`, semantic.py:85).
- Library queries (`Project.queries`): name, `QuerySpec.description`, connector, params
  (regex `\$\{(\w+)\}` over sql).
- Python queries (`Project.python_queries`): name, description, connector.

### Resolution JSON (LLM output, strictly validated — invalid → kind "none")

```json
{"kind": "semantic", "model": "growth", "metric": "repeat_rate", "by": "campaign",
 "grain": "week", "filters": {"channel": ["paid"]}, "date_start": "", "date_end": ""}
{"kind": "query", "name": "campaign.performance", "params": {"region": "EU"}}
{"kind": "sql", "sql": "SELECT ..."}            // only offered when ask.allow_sql
{"kind": "none", "reason": "..."}
```

Validation: metric/by must exist in the handle's lookups; grain ∈ `GRAIN_TOKENS`; query
name must exist in library/python registries; `sql` rejected unless `allow_sql`. Tolerate
markdown fences around the JSON. Any validation failure degrades to kind `none` with the
reason — never a 500.

### Execution

- **semantic**: `resolve_ref(models, "model.metric", "model.by", grain=…)` (semantic.py:609)
  → `build_semantic_spec(models, ref, connectors)` (semantic.py:867) →
  `run_python_query(spec, params, connectors)` in `asyncio.to_thread`. Filters/date range
  ride `params` exactly as `build_filters(handle, params)` (semantic.py:709) expects
  (read it for the multi-value encoding). Register the spec via
  `register_python_query_def` so a follow-up data fetch by name also works.
- **query**: python-first `get_python_query_def(name, connector)` (pipeline.py:181), else
  `get_query_def` (pipeline.py:222) → `_substitute_params(sql, params)` → `connector.query`
  in thread — mirror `get_query_data` (server.py:315). Result cache:
  `get_cached_result`/`cache_result` shared with the data API.
- **sql**: `connector.query(sql)` in thread; cap rows defensively (1000).

### Chart inference (server-side, `infer_chart_shape(result) -> ChartShape | None`)

Mirror the client heuristic (`resolveAutoConfig`, chart.js:1883): sample rows, classify
columns temporal/numeric/categorical; temporal x → `line`, categorical x → `bar`,
numeric x → `scatter`; first numeric column → y. Single-row single-numeric → no chart
(headline value). The inferred `{type, x, y}` ships in the response so the client renders
*exactly* this chart, and feeds `build_chart_context(...)` (chart_annotations.py:189) so
`generate_answer` returns validated annotations for it. `ChartContext=None` (no chart)
falls back to plain commentary — unchanged behavior.

### `ask:` config (`AskConfig` on `ProjectConfig`, parsed fail-hard like `parse_search_config`)

`enabled: bool = True` (effective only when `llm:` enabled) · `allow_sql: bool = False` ·
`max_rows: int = 50` · `cache_ttl: int = 3600` · `log: bool = True`.

### Answer cache + ask log

- Cache key `(normalized_question, frozen(params))` → full response payload, TTL
  `ask.cache_ttl`; module-level dict in ask_engine.py (mirror `_answer_cache`, llm.py:627).
  `refresh: true` in the POST body bypasses (config can't disable runtime refresh — the
  box is operator-facing; cache_ttl bounds cost).
- Log: append JSONL to `<project>/.dashdown/ask_log.jsonl` when `ask.log`:
  `{ts, question, kind, provenance, rows, duration_ms, model, cached}`. Never raises
  (log failure is a warning).

## B. `POST /_dashdown/api/ask` (server.py) + CLI

Sync `def` endpoint (threadpool, like `get_ask_commentary` server.py:610). Behind the
existing auth middleware automatically. Response `200 JSON`:

```json
{"question": "...",
 "resolved": {"kind": "semantic|query|sql|none", "provenance": "human-readable string",
              "query_name": "...", "connector": "...", "detail": {}},
 "columns": [...], "rows": [[...]],
 "chart": {"type": "line", "x": "week", "y": "repeat_rate"},
 "answer_html": "...", "answer_text": "...", "annotations": [],
 "model": "mistral-small-latest", "cached": false}
```

- llm/ask disabled → `200 {"notice": unavailable_notice(cfg)}` (matches the ask-card
  convention, server.py:633). Malformed body/empty question → 400. LLM failure → 502.
  Query failure → 500 with type+message.
- `kind: "none"` → `columns/rows/chart` null, `answer_html` carries the model's reason.
- No SSE in v1: the client typewriter-replays (`replayAnswer` machinery). Chart-context
  answers can't stream anyway (annotations fence).
- Template context: `page()` threads `ask_enabled` (llm enabled ∧ ask.enabled ∧ not
  embed) — template treats it default-false so `build.py` needs no change.
- **CLI**: `dashdown ask "question" [-p dir] [--param k=v] [--json]` — Typer command
  (pattern: `metric`, cli.py:319): prints provenance line, result table (reuse the
  `query` command's table printer), answer text.

## C. Frontend ask box (`static/components/ask_box.js`, new)

- **Markup**: new Jinja macro next to `site_search` (page.html:20), emitted in the header
  next to `.dashdown-header-search`, gated `{% if ask_enabled %}` (default-false ⇒ off in
  embeds/static builds). Reuse `.dashdown-site-search*` classes for the input; new
  `.dashdown-ask-box*` classes for the answer panel — **hand-written rules in
  dashdown.css only, no new Tailwind utility classes** (the prebuilt vendor bundle won't
  contain them).
- **Init**: from `app.js::init()` *outside* the `hasAsyncComponents()` gate (site-search
  precedent, app.js:245). Filters read lazily at submit: `window.Alpine?.store("filters")`
  → fallback `parseUrlParams()`; merge `readRouteParams()`.
- **Submit**: new `postJson(url, body)` helper in core.js (first POST in the codebase);
  include `?_embed` token via `readEmbedToken()` if present.
- **Answer panel** (dropdown under the box, closeable, Esc/click-away like site-search):
  1. provenance line (small, muted — the trust surface),
  2. chart: container with `.dashdown-chart`+`.dashdown-chart-container` structure;
     `el._echarts_instance = echarts.init(container, currentEChartsTheme())` then
     `updateChart(el, recordsOf(payload), chartConfig)` (chart.js:2007); annotations via
     `setChartAnnotations(el, payload.annotations)` (annotations.js:551) + ref-chip
     hover wiring (`.dashdown-anno-ref` → `emphasizeChartAnnotation`, ask.js:219 pattern),
  3. table: `renderTableInto(host, records, {export: false, page_size: 10})` (table.js:631),
  4. answer body: `.dashdown-ask-body` styling + word-batched typewriter (lift
     `replayAnswer`, ask.js:163), then swap in `answer_html`,
  5. loading / error / notice states.
- Panel is inserted only on user interaction ⇒ print/screenshot readiness untouched
  (headless never types a question).

## D. Triggers + actions (`dashdown/actions.py`, `dashdown/triggers.py`, new)

- **Actions**: `Action` ABC (`fire(event: dict) -> None`) + `@register_action("type")`
  registry (connector-registry pattern). Built-ins: `webhook` (POST JSON via stdlib
  urllib, timeout 10s) and `slack` (incoming-webhook `{"text": …}` formatting). Config
  values support `${ENV}` expansion (reuse the `_resolve_secret` pattern).
- **Trigger spec** (`triggers/*.yml`, name = stem):

```yaml
query: kpi.repeat_rate      # library or python query name
connector: demo             # optional → project default
interval: 300               # seconds (min 5)
when: "value < 0.12"        # value|rows <op> number  — parsed by regex, NEVER eval
message: "Repeat-purchase rate slipped"
cooldown: 3600              # optional re-fire seconds while still breached
params: {}                  # optional fixed query params
enabled: true
actions: [{type: webhook, url: "${HOOK_URL}"}]
```

- **Condition**: `value` = first cell of first row (numeric), `rows` = row count.
  Operators `< <= > >= == !=`. Small parser + `evaluate(condition, result)`; unit-tested.
- **Runner** (`TriggerRunner`): per trigger, build the fetch thunk exactly like the WS
  endpoint (server.py:570-583, python-first) and `stream_hub.subscribe(key, fetch, name,
  interval)` — **socket-less subscribe is supported** (streaming.py:132; skip
  `watch_disconnect`). Consume the queue in an asyncio task; parse each frame; evaluate;
  fire on clear→breach transition (+ every `cooldown` while breached); actions run in
  `asyncio.to_thread`, exceptions logged never fatal. Event payload:
  `{trigger, message, when, value, rows_count, columns, sample_rows(≤10), fired_at}`.
- **Wiring**: `Project.triggers` loaded in `load_project` (fail-hard parse); runner
  started on app startup, stopped+restarted in `reload_project` (server.py:1130); store
  on `app.state.trigger_runner`. `dashdown serve` watcher list gains `triggers/`.

## E. Demo (`examples/growth-answers/`) — see PLAN.md

Model on `tests/fixtures/semantic_first_class/` (the only live semantic example) and
`docs/` project shape. `llm:` block = mistral + `${MISTRAL_API_KEY}`, model
`mistral-small-latest`.

## F. Testing strategy

- Unit/endpoint tests follow `tests/test_ask.py`: `FakeAdapter(LLMAdapter)` (line 81)
  injected via `app.state.project.llm_adapter = fake`; autouse cache-clearing fixture
  extended with ask_engine + trigger caches. For runtime ask the fake returns first a
  resolution JSON then an answer (script per-call).
- e2e (review phase): local OpenAI-compatible mock server + `llm: {provider: ollama,
  base_url: http://127.0.0.1:<port>/v1, model: mock}` (`REQUIRES_API_KEY=False`) — full
  live path with no real key. Real-key smoke test: user's Mistral key.

## G. Out-of-band constraints

- Do **not** edit `docs/` (the agent-docs freshness test locks it to the generator);
  user-facing docs pages are a follow-up.
- No new Tailwind utility classes (prebuilt vendor bundle); hand-written CSS in
  dashdown.css referencing `--dashdown-*` tokens.
- Keep `GET /_dashdown/api/ask/{ask_id}`, `<Ask />`, `explain`, and all chart behavior
  untouched.
