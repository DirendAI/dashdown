---
title: Coding agents
sidebar_label: Coding agents
sidebar_position: 2
icon: "\U0001F916"
---

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
- **A per-tool *wrapper*** — a thin *router* into the map and shards: a decision tree
  ("editing X → read `references/Y`, verify with `dashdown Z`") plus task playbooks
  (add-a-chart, add-a-connector, define-a-metric, debug-no-data, …). It links the map
  and shards rather than duplicating them. The map and shards above are tool-agnostic and
  always installed; only this wrapper differs per tool, in the format and location each
  expects:

  | `--target` | Wrapper |
  |---|---|
  | `claude` | `.claude/skills/dashdown-authoring/SKILL.md` (a Claude Code skill) |
  | `mistral` | `.vibe/skills/dashdown-authoring/SKILL.md` (same skill layout) |
  | `cursor` | `.cursor/rules/dashdown.mdc` (an always-applied project rule) |
  | `gemini` | `GEMINI.md` |

  Tools that read `AGENTS.md` natively (Codex, …) need no wrapper — the map alone covers
  them. Pick tools with `--target` (below).

The whole tree is generated from this documentation site by release tooling, so it
stays in sync with what you're reading now.

## `dashdown skill` — update an existing project

The guide is versioned with the framework. A project scaffolded on an older release
pulls the current guide without re-scaffolding:

```bash
dashdown skill                 # fill in anything missing (keeps your local edits)
dashdown skill --refresh       # overwrite to this version's guide (prunes stale shards)
dashdown skill -p ./dashboard  # target another project directory
dashdown skill --target cursor # also/instead install the Cursor wrapper
```

Without `--refresh`, existing files are left untouched, so your own edits survive an
install that just fills in missing pieces. With `--refresh`, every file is overwritten
to the current version and any `references/*.md` left behind by a renamed topic is
removed.

### Choosing which tools

You rarely need `--target` by hand. Which wrappers get installed resolves by precedence:

1. an explicit **`--target a,b`** on the command,
2. else the project's **`dashdown.yaml` `agents:`** list,
3. else any tool it **auto-detects** (a marker dir like `.claude/` or `.cursor/` already
   in the project),
4. else **`claude`**.

`dashdown new --target …` is where the choice is usually made: a fresh directory has
nothing to detect, so `new` takes the flag (default `claude`) and **records it** into the
scaffolded `dashdown.yaml`:

```yaml
title: My Analytics
agents: [claude, cursor]   # coding-agent guides to keep in sync via `dashdown skill`
```

From then on, a plain `dashdown skill` in that project honors the list — so a team picks
its tools once at scaffold time and every later refresh keeps them in sync.

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
