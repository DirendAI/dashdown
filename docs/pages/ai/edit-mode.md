---
title: AI edit mode
sidebar_label: Edit mode
sidebar_position: 3
icon: "✏️"
---

# AI edit mode — `dashdown serve --edit`

Edit your dashboard **from the browser, in plain language**. With `--edit`, the
dev server gains a small panel (the ✎ button, bottom-right): type *"add a bar
chart of revenue by region under the table"*, and a coding-agent CLI — Claude
Code, Codex, Gemini CLI, Cursor, OpenCode, Aider, or your own — edits the
project's markdown/queries on disk while you watch. The **existing live-reload
does the magic**: the dashboard visibly updates as the agent saves, and the
panel (which survives those reloads) streams the agent's progress, lists the
changed files, verifies the project still loads, and offers one-click **Undo**.

```bash
dashdown serve . --edit                # auto-detects an installed agent CLI
dashdown serve . --edit --agent codex  # pick one explicitly
```

Because a dashboard is just files, this works with **any** agent — the
integration is a small preset per tool (invocation flags + output parsing), and
the fast-moving agent-CLI space is why: a new tool is a preset entry or an
`edit.custom` block, never a framework rewrite.

## What the agent knows

The panel sends your request with a short context preamble: the page you're
viewing (its `.md` file), your active filter values, a pointer at the
project's [`AGENTS.md` guide](/ai/coding-agents), and the instruction to verify
with `dashdown check` afterwards. Install the guide (`dashdown skill`) if the
project doesn't have it — edits land noticeably better when the agent knows
the platform.

## Choosing the agent

Resolution order: `--agent` flag → `edit.agent` in `dashdown.yaml` → the
project's `agents:` list (first tool whose CLI is installed) → any installed
preset. Nothing installed? The server still starts and the panel shows install
hints instead.

```yaml
edit:                        # configures, never enables — only --edit arms it
  agent: claude              # preset name, or "custom"
  permission_mode: safe      # safe (default) | full
  timeout: 900               # seconds per run
  context: true              # send the page/filters/guide preamble
  # custom:                  # your own tool (requires --allow-custom)
  #   command: ["my-agent", "run", "{prompt}"]
  #   output: text           # text | jsonl | claude_json
```

`permission_mode: safe` runs each preset with its vendor's scoped-permissions
flags — file edits plus the `dashdown` verification commands, no arbitrary
shell. `full` uses the vendor's bypass flag (printed as a warning at startup).

## Safety model

Edit mode can execute code on your machine, so it is locked down accordingly:

- **Arming is local-only.** A checked-in `edit:` block configures but never
  enables; only your `--edit` flag arms the endpoints (they don't exist
  otherwise). A custom command / binary / extra args from the yaml additionally
  require `--allow-custom` — a cloned repo's config can't pick what runs.
- **Loopback-only.** `--edit` refuses a non-loopback bind, and every edit
  request re-checks the peer address, a localhost `Host` header (DNS-rebinding
  defense) and same-machine `Origin` (CSRF defense).
- **Per-serve token.** A random token is minted at startup, delivered only to
  full-shell authed page renders (never embeds or builds), and required on
  every edit request. When `auth:` is on, the normal guard applies on top; an
  embed token never authorizes an edit endpoint.
- **Visibility + recovery.** Every run: streamed transcript, changed-file diff,
  a loud warning when `dashdown.yaml`/`sources.yaml` changed, an audit log
  (`.dashdown/edit-log.jsonl`), a post-run load check — and **Undo**, backed by
  a pre-run snapshot of `pages/ queries/ components/ semantic/` + the two
  config files (`data/` and `assets/` are not snapshotted). One undo slot: the
  last run. Use git for real history — the server hints when the project isn't
  a repository.

One run at a time; a second tab attaches to the running transcript instead.
Follow-ups reuse the agent's session where the CLI supports it (Claude Code),
so "make it green" after "add a chart" just works.
