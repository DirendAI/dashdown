# AI-Readiness: from dashboards to answers

**Branch:** `feat/ai-readiness` · **Status:** in progress

## Thesis

The BI buyer is shifting from analytics teams (who consume dashboards) to operators (who
want an answer and an action, where they already work). Dashdown's response is an
**inversion, not a rewrite**:

> **The answer is the product. A page is an answer someone decided to keep.**

Dashdown's unique lever: a dashboard here is a *text file* — the one artifact an LLM can
produce in a single completion, with no build step. So instead of bolting a chat sidebar
onto a dashboard tool, we make the engine answer questions directly and demote the page
from "the product" to "one output format of an answer."

**One engine, three surfaces:**

| Surface | What the operator does | Status |
|---|---|---|
| **Ask** | Types a question in the header (or POSTs to the API), gets answer + chart + list + provenance | this feature |
| **Push** | A trigger watches a metric and delivers answer + action (webhook/Slack) when it slips | this feature |
| **Pages** | Curated dashboards remain for persistent monitoring; generated pages as answer evidence | exists (curated) / later (generated) |

The engine is what already exists: connectors, the shared query library, the semantic
metric layer, `_substitute_params` as the single injection defense, and the `llm:` gateway.

## Scope of this iteration

1. **Runtime Ask engine** (`dashdown/ask_engine.py`): natural-language question →
   constrained resolution → executed query → LLM answer with provenance.
2. **`POST /_dashdown/api/ask`** endpoint (the author-pinned `GET /_dashdown/api/ask/{id}`
   stays untouched — hybrid by construction).
3. **Header ask box** (frontend): SiteSearch-style input; answer panel = typewriter answer
   text + auto-inferred chart **with LLM chart annotations** + result table + provenance
   line ("computed as …").
4. **Ask log**: every runtime question/resolution/outcome appended to a project-local log —
   the seed of the telemetry moat, and itself queryable by Dashdown.
5. **Triggers + Actions** (`dashdown/actions.py` + `triggers/*.yml`): conditions on query
   results evaluated on the existing streaming poll loop; `webhook` and `slack`
   (incoming-webhook) actions firing an answer-shaped payload.
6. **Demo project** in `examples/growth-answers/`: the essay's Monday-morning scenario —
   campaigns, orders, repeat purchases — runnable with a Mistral key.

### Explicitly out of scope (this iteration)

- Generated full pages from a question (Track 3) — the ask panel is the MVP of that.
- A hosted Slack app (slash command) — the `slack` action covers push-into-Slack via
  incoming webhook; the interactive app is a later, hosted concern.
- Auto-drafting `semantic/*.yml` from schema introspection.
- Any change to author-pinned `<Ask />`, `explain=`, or existing chart behavior.

## Safety model (the resolution ladder)

The runtime ask endpoint never lets the LLM emit free-form SQL by default. Resolution is a
ladder, most-constrained first:

1. **Semantic refs** — the LLM picks `metric` / `by` / `grain` / `filters` from the
   introspected semantic catalog. Values are pure JSON data (the semantic layer has no
   string-interpolation surface at all). Preferred whenever a semantic model exists.
2. **Library / page queries** — the LLM picks an *existing named query* and supplies
   `${param}` values, which pass through the one blessed context-aware
   `_substitute_params` (values become quoted literals; injection-inert).
3. **Raw SQL** — only behind an explicit `ask: allow_sql: true` in `dashdown.yaml`
   (default **false**), and clearly marked in provenance. Off in the demo.

The LLM therefore chooses *from a menu we control*; the ladder is also the trust story:
every answer carries provenance describing exactly which rung and which definition
produced it.

Auth: the endpoint sits behind the same auth middleware as everything else; it is disabled
entirely when no `llm:` block is configured (same graceful-off behavior as `<Ask />`).
Rate/copy control: answers are cached by (question, resolved spec) with a TTL, mirroring
the ask-def answer cache, so repeat questions don't re-bill.

## Feature contracts

*(Filled in from seam analysis — see ARCHITECTURE.md for the exact internal wiring.)*

### `ask:` config (dashdown.yaml)

```yaml
llm:
  provider: mistral
  api_key: ${MISTRAL_API_KEY}
  model: mistral-small-latest

ask:
  enabled: true        # default: true when llm: is configured
  allow_sql: false     # rung 3 opt-in; default false
  max_rows: 50         # rows of result data shown to the model for the answer
  cache_ttl: 3600      # answer cache seconds
  log: true            # append runtime asks to .dashdown/ask_log.jsonl
```

### `POST /_dashdown/api/ask`

Request: `{ "question": "...", "params": {…current filters…} }`
Response: streamed like the existing ask endpoint, preceded by a resolution header event
carrying `{resolved: {kind: semantic|query|sql, spec…, provenance: "…"}, columns, rows}`
so the client can draw chart + table immediately while the answer text streams in.
Chart annotations ride the same annotation machinery as `explain`.

### Triggers

```yaml
# triggers/repeat-rate.yml
query: kpi.repeat_rate        # library query or semantic metric ref
interval: 300                 # seconds between evaluations
when: "value < 0.12"          # condition on the single-value result (or delta forms)
actions:
  - type: slack
    webhook_url: ${SLACK_WEBHOOK_URL}
  - type: webhook
    url: https://example.com/hook
message: "Repeat-purchase rate slipped"
```

Evaluation rides the existing shared poll loop (one poller per query key, digest-gated);
an action fires with an answer-shaped payload: headline value, delta, sample rows, page link.

## Demo (`examples/growth-answers/`)

The essay's scene, made runnable: Monday 9:07, "Which campaign drove repeat purchases
this week?"

- CSV sources: `campaigns.csv`, `orders.csv`, `customers.csv` (seeded, deterministic).
- Semantic model `semantic/growth.yml`: `revenue`, `orders`, `repeat_rate` measures;
  `campaign`, `channel`, `week` dimensions.
- Query library: `kpi.repeat_rate`, `campaign.performance` (+ params).
- `pages/index.md`: KPI row, campaign chart with `explain` annotations, an authored
  `<Ask />` — plus the new header ask box live on every page.
- A `triggers/repeat-rate.yml` example (webhook target documented, off by default).
- `README.md`: 2-minute setup with a Mistral key; the exact demo script to follow.

## Delivery plan

See `BACKLOG.md`. Waves: B1 backend engine → B2 endpoint+log → B3 frontend panel →
B4 triggers/actions (parallel-capable) → B5 examples/demo → review loop (tests green,
code review, live mock-LLM e2e) until satisfied.
