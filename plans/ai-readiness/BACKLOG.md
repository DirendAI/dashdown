# AI-Readiness — implementation backlog

Waves reference ARCHITECTURE.md sections. Owner "agent" = delegated implementation,
reviewed by the orchestrating session before merge.

## Wave 1 (parallel)

- **B1+B2 — Runtime ask backend** (ARCH §A+§B) — `dashdown/ask_engine.py` (new),
  `AskConfig` in project.py, `POST /_dashdown/api/ask` in server.py, `ask_enabled`
  template var, ask log, `dashdown ask` CLI command, `tests/test_ask_engine.py`.
  Touches: ask_engine.py, project.py, server.py, cli.py, tests. **No template/static
  edits** (frontend owns those).
- **B3 — Header ask box frontend** (ARCH §C) — `static/components/ask_box.js` (new),
  `core.js::postJson`, page.html macro + header slot, app.js wiring, dashdown.css
  styles. **No Python edits** (backend threads `ask_enabled`). Builds against the fixed
  §B response contract.
- **B4 — Triggers + actions** (ARCH §D) — `dashdown/actions.py`, `dashdown/triggers.py`
  (new), Project.triggers + load_project wiring, server startup/reload runner wiring,
  watcher list, `tests/test_triggers.py`. Runs in an isolated worktree (shares
  project.py/server.py with B1); merged by the orchestrator.

## Wave 2 (after Wave 1 merge + review)

- **B5 — Demo project** `examples/growth-answers/` (ARCH §E, PLAN "Demo"): seeded CSVs,
  `semantic/growth.yml`, query library, `pages/index.md` (+ authored `<Ask />`,
  `explain` chart), `triggers/repeat-rate.yml`, README with Mistral setup + demo script.
- CLAUDE.md section for the new modules.

## Review loop (orchestrator)

- Full `uv run pytest tests/ -v` green (baseline 1654 passed / 36 skipped).
- Diff review per wave; fix-up agents until satisfied.
- Mock-LLM e2e: serve `examples/growth-answers` with an OpenAI-compatible mock
  (ARCH §F), exercise `POST /_dashdown/api/ask`, `dashdown ask` CLI, trigger firing
  against a local webhook sink; `dashdown screenshot` for chart render proof.

## Follow-ups (explicitly deferred)

- User-facing docs pages (`docs/`) + `tooling/gen-agent-docs.py` re-run.
- SSE streaming for runtime answers without chart context.
- Generated full pages from a question ("keep this answer as a page").
- Semantic-metric refs + delta conditions in triggers; Slack interactive app.
- Auto-drafted `semantic/*.yml` from schema introspection.
