<!-- AUTO-GENERATED from docs/pages/ by tooling/gen-agent-docs.py — do not edit. -->
<!-- Topic: ai. Regenerate with: python tooling/gen-agent-docs.py -->

<!-- source: docs/pages/ai/index.md -->

# AI

Dashdown is built for AI in **two directions** — the LLM that helps your *readers*
understand a dashboard, and the coding agent that helps *you* build one.

## AI in your dashboard — `<Ask />`

Most dashboards stop at the chart. [`<Ask />`](/ai/ask) goes one step further: it
sends a query's result to an LLM and renders the **natural-language read-out** right
beside it — *"downloads are up 12% month-over-month, driven by the `pip` channel."*
One tag turns a table into an explained insight, **cached** so repeat views don't
re-bill, and pointed at any provider (Mistral, Claude, OpenAI, OpenRouter).

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


<!-- source: docs/pages/ai/ask.md -->

# Ask — LLM commentary

`<Ask />` sends a query's result to an LLM and renders its natural-language
commentary inline — the explained-insight layer on top of a chart or table. The
model's markdown answer is rendered with **raw HTML disabled**, and answers are
**cached** so repeat page loads don't re-bill.

```markdown
<Ask data={downloads_by_month} ask="Summarize the download trend in two sentences." />
```

| Attribute   | Purpose                                                          |
| ----------- | --------------------------------------------------------------- |
| `data`      | **Required.** The query whose result the model sees.            |
| `ask`       | **Required.** The instruction / prompt.                         |
| `max_rows`  | Cap on rows sent to the model (default in `llm.py`).            |
| `cache_ttl` | Seconds to cache the answer.                                    |
| `label`     | Optional heading above the answer.                              |

:::warning Needs an `llm:` block
`<Ask>` requires an [`llm:` block](#configuration) in `dashdown.yaml` — so there's
no live example on this docs site. Install the provider extra and add the config
below.
:::

## Works on semantic-layer data too

`<Ask>` accepts a [semantic metric reference](/semantic-layer) as an alternative to
`data={query}` — the same grammar a chart uses:

```markdown
<Ask metric={sales.revenue} by={sales.region} ask="Which region stands out, and why?" />
```

It binds to the *same* synthetic query a chart with those attrs would build, so the
answer rides the same result cache and filter path. A plain `queries/*.py`
[Python query](/python-queries) works as a source too.

## Configuration

Set `llm.provider` to one of `mistral`, `anthropic` (Claude), `openai`, or
`openrouter` (OpenRouter's OpenAI-compatible gateway). Each provider's SDK is an
optional extra — `pip install 'dashdown-md[mistral|anthropic|openai|openrouter]'`.

```yaml
# dashdown.yaml
llm:
  provider: anthropic
  api_key: ${ANTHROPIC_API_KEY}    # ${VAR} reads from the environment
  model: claude-haiku-4-5          # optional (this is the anthropic default)
```

`model` is optional for every provider except `openrouter`, which routes to many
upstream models — name one explicitly (e.g. `model: anthropic/claude-3.5-sonnet`).
The defaults are fast/cheap models; since each uncached request is billed, pin a
more capable one (e.g. `claude-opus-4-8`) via `model` when quality matters more.

The block is **provider-only** (`provider` / `api_key` / `model`) — per-answer knobs
like `max_rows` and `cache_ttl` are `<Ask>` attributes, not config. See
[Configuration → `llm`](/configuration#llm).

## Caching & cost

Each answer is cached by a **deterministic id** — a hash of (connector, query,
prompt, `max_rows`) plus the filter params the SQL actually substitutes — so repeat
page loads and shared filter states reuse one answer instead of billing each view.
`cache_ttl` controls expiry; it isn't part of the id (so changing it doesn't bust
the cache). A reader's ↻ refresh affordance forces a fresh answer past the cache.

## Safety

The prompt is **registered server-side** and addressed by that opaque id, so the
`GET /_dashdown/api/ask/{id}` endpoint can never be fed an arbitrary prompt. The
data payload is capped to `max_rows` rows plus column types, and the model's answer
is rendered as markdown with **raw HTML disabled**.

:::note Data leaves your server
`<Ask>` sends the (capped) query result to your chosen LLM provider. Treat it like
any third-party data processor — don't point it at columns you can't share, or run
an OpenAI-compatible endpoint yourself and target it with the `openai` / `openrouter`
provider.
:::

## Static builds

`dashdown build` bakes one answer JSON per `<Ask>` def into the export, so the
commentary ships in a static site with **no server or API key** at view time — the
answer is computed once at build.

## Try it

Add an `llm:` block to your `dashdown.yaml`, drop an
`<Ask data={your_query} ask="…" />` tag onto a page, and `dashdown serve` it —
the commentary renders inline beneath the data.


<!-- source: docs/pages/ai/coding-agents.md -->

# Coding agents

Dashdown is designed to be authored **with** a coding agent (Claude Code, Cursor,
Codex, …). Every project ships a tool-agnostic guide so an agent opening the folder
knows the platform without you explaining it, and the framework exposes its own
knowledge through the CLI so the agent can check facts instead of guessing.

The guiding principle is **progressive disclosure**: a small map up front, the detail
loaded only when a task needs it. Reading one ~50k-token manual for every change is
slow and expensive; a map plus per-topic shards is not.

## What's in a project

`dashdown new` (and `dashdown skill`, below) drop these into a project:

- **`AGENTS.md`** — the *map*. A preamble, a one-screen cheat-sheet (the most-used
  `:::query` / `${param}` / component syntax), a table of contents linking each
  reference shard, and the "CLI loop" framing. ~120 lines, read first. Any agent that
  reads `AGENTS.md` natively (Claude Code, Cursor, Codex) picks this up.
- **`references/<topic>.md`** — the *shards*. One per documentation topic (components,
  connectors, queries, semantic layer, …), the full flattened docs for that topic. An
  agent opens only the one shard its task needs. `references/catalog.md` is special: it
  is introspected straight from the component/connector registries (the same data
  `dashdown components` prints), so it can't drift.
- **`.claude/skills/dashdown-authoring/SKILL.md`** — a thin Claude Code *router*: a
  decision tree ("editing X → read `references/Y`, verify with `dashdown Z`") plus task
  playbooks (add-a-chart, add-a-connector, define-a-metric, debug-no-data, …). It links
  the map and shards rather than duplicating them.

The whole tree is generated from this documentation site by release tooling, so it
stays in sync with what you're reading now.

## `dashdown skill` — update an existing project

The guide is versioned with the framework. A project scaffolded on an older release
pulls the current guide without re-scaffolding:

```bash
dashdown skill                 # fill in anything missing (keeps your local edits)
dashdown skill --refresh       # overwrite to this version's guide (prunes stale shards)
dashdown skill -p ./dashboard  # target another project directory
```

Without `--refresh`, existing files are left untouched, so your own edits survive an
install that just fills in missing pieces. With `--refresh`, every file is overwritten
to the current version and any `references/*.md` left behind by a renamed topic is
removed.

## The CLI loop — facts from the tool, not from memory

> **Concepts from the references, facts from the CLI.** Don't guess a component's
> attributes or a connector's config keys — ask the tool.

These answer factual questions far cheaper than re-reading prose, and confirm a change
actually works:

```bash
dashdown check                 # config loads + every page renders? (queries never run)
dashdown connectors --test     # each connector reachable? (probes SELECT 1)
dashdown query "SELECT …" -c main   # inspect real data / schema
dashdown components            # dense, introspected attr catalog for every component
dashdown components --connectors    # config keys + install extra per connector type
dashdown metric --list         # semantic metrics & dimensions (if a semantic/ model exists)
dashdown screenshot /page      # PNG + verdict: did the chart canvases actually draw?
```

The last one closes a gap `check` can't: charts paint **client-side**, so a page can
render server-side yet show a blank chart. `dashdown screenshot` drives headless
Chromium, waits for the chart-render handshake, and reports how many canvases drew —
exiting non-zero if any failed, so it works as a verification gate.

A typical loop: **read** the relevant `references/<topic>.md` for the concept →
**edit** the page → **`dashdown check`** it renders → **`dashdown query`** the data is
real → **`dashdown screenshot`** the chart drew.

## `llms.txt` for network-fetch hosts

Some agent hosts can't read bundled project files but will fetch documentation over the
network. A static build of this docs site (`dashdown build`) publishes two files at its
root, following the [llms.txt](https://llms.txt) convention:

- **`/llms.txt`** — the map: a link to each topic page, so a host pulls only what it
  needs.
- **`/llms-full.txt`** — the entire manual (the registry catalog plus every topic) in
  one file, for a host that wants everything at once.

Both are generated from the same documentation, so they never drift from the site. Any
project that ships an `llms.txt` / `llms-full.txt` at its root has them copied to the
build root automatically.
