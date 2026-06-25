<!-- AUTO-GENERATED from docs/pages/ by tooling/gen-agent-docs.py — do not edit. -->
<!-- Topic: telemetry. Regenerate with: python tooling/gen-agent-docs.py -->

<!-- source: docs/pages/telemetry.md -->

# Telemetry & privacy

Dashdown is pip-installed and self-hosted, with no accounts and no server in the loop.
To understand how many people actually use it — which versions, on which platforms — the
CLI sends a small, **anonymous** usage event when you run `dashdown serve` or
`dashdown build`. It's **on by default** and takes one command to turn off.

:::note
We collect this to answer a single question: *is anyone using Dashdown, and on what?*
It is never used to identify you, and it never touches your data, queries, or dashboards.
:::

## What is sent

Two events, `cli_serve` and `cli_build`, each carrying only:

- the Dashdown version,
- the Python version,
- the operating system and CPU architecture (e.g. `Darwin` / `arm64`),
- a random `install_id` (a UUID generated once and stored locally) so unique installs
  can be counted.

Events are sent with PostHog's `$process_person_profile: false`, so **no person profile
is created** — they are anonymous. The destination is PostHog's EU cloud.

You can see the exact payload that would be sent at any time:

```bash
dashdown telemetry status
```

## What is **never** sent

- Your project's contents, page text, or Markdown.
- Any SQL, DAX, or query results — no data of any kind.
- File paths, project names, hostnames, or directory layout.
- Connector **names** or credentials (not even connector *types*).
- IP-derived identity, cookies, or any personal data.

## Turning it off

Any one of these disables telemetry — pick whichever fits:

```bash
# This machine, persistently:
dashdown telemetry off          # re-enable with: dashdown telemetry on

# Per command / environment:
DASHDOWN_TELEMETRY=0 dashdown serve .
DO_NOT_TRACK=1 dashdown serve .     # the cross-tool standard, also honored
```

Or per project, in `dashdown.yaml`:

```yaml
telemetry:
  enabled: false
```

`dashdown telemetry status` always shows whether telemetry is on and, if off, which of
these switched it off.

:::tip
`DO_NOT_TRACK=1` is honored automatically — if you already set it for other tools,
Dashdown is opted out with no extra step.
:::

## How it works

The sender lives in `dashdown/telemetry.py` and is deliberately decoupled from the render
path: it never runs during a page request, the network call is fire-and-forget on a
background thread with a short timeout, and **every error is swallowed** — telemetry can
never slow down or break a `dashdown` command. If the project key isn't configured in a
given build, nothing is sent at all.
