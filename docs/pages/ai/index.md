---
title: AI
sidebar_label: AI
sidebar_position: 15
icon: "✨"
---

# AI

Dashdown is built for AI in **two directions** — the LLM that helps your *readers*
understand a dashboard, and the coding agent that helps *you* build one.

## AI in your dashboard — `<Ask />`

Most dashboards stop at the chart. [`<Ask />`](/ai/ask) goes one step further: it
sends a query's result to an LLM and renders the **natural-language read-out** right
beside it — *"downloads are up 12% month-over-month, driven by the `pip` channel."*
One tag turns a table into an explained insight, **cached** so repeat views don't
re-bill, and pointed at any provider (Mistral, Claude, OpenAI, OpenRouter, or a
local Ollama model — data stays on your machine).

```markdown
<Ask data={downloads_by_month} ask="Summarize the download trend in two sentences." />
```

**→ [Ask — LLM commentary](/ai/ask)** for the full component, semantic-layer
support, providers, caching, cost and the safety model.

## AI that builds your dashboard — coding agents

Because a dashboard is just Markdown + SQL + component tags, a coding agent (Claude
Code, Cursor, Codex, …) can author it directly. Every project ships a tool-agnostic
guide, and the CLI exposes the framework's own knowledge so the agent checks facts
instead of guessing.

**→ [Coding agents](/ai/coding-agents)** for `AGENTS.md`, `dashdown skill`, the
CLI loop, and `llms.txt`.

## AI editing, from the browser — `serve --edit`

The two meet in **[edit mode](/ai/edit-mode)**: `dashdown serve --edit` puts an
edit panel on your dashboard that drives whichever agent CLI you have installed —
type *"add a bar chart of revenue by region"*, watch the page live-reload as the
agent writes it, review the changed files, and undo with one click.

**→ [AI edit mode](/ai/edit-mode)** for setup, agent presets, and the safety model.
