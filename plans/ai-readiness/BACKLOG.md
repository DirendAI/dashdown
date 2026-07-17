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
- **Staged answers** (biggest perceived-latency win): stream resolution →
  provenance → data → answer over SSE instead of one blocking response.
- **Answer permanence**: ask-history dropdown (the ask log already records
  everything) + "keep this answer as a page" (generated pages, Track 3).
- Semantic-metric refs + delta conditions in triggers; Slack interactive app;
  in-app trigger status surface (armed / last fired).
- Auto-drafted `semantic/*.yml` from schema introspection.

## Known issues accepted in review (pre-existing or convention-bound)

- **Stale poller across dev reload** (pre-exists this branch): `reload_project`
  closes the old project's connectors, but a poller kept alive by a live WS
  subscriber retains its old fetch thunk and errors each tick until that
  subscriber leaves. Affects WS + triggers equally; fix belongs in the hub
  (project-generation in the poller key, or forced poller teardown on reload).
- **Comma-bearing filter values** can't ride the comma-joined multi-value param
  convention (`build_filters` splits on comma — the Dropdown contract);
  documented at the overlay site in ask_engine.
- **`_resolve_secret` triple copy** (auth.py / llm.py / actions.py): each carries
  a context-specific error message; consolidation into one helper with a
  context arg is a small cross-cutting refactor best done outside this branch.
- `evaluate()` treats an empty/non-numeric result as not-breached (with a
  once-per-condition warning) — deliberate "no data ≠ zero" semantics.
