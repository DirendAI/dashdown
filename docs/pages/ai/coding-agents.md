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
