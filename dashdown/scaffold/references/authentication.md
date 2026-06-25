<!-- AUTO-GENERATED from docs/pages/ by tooling/gen-agent-docs.py — do not edit. -->
<!-- Topic: authentication. Regenerate with: python tooling/gen-agent-docs.py -->

<!-- source: docs/pages/authentication.md -->

# Authentication

Dashdown ships optional built-in auth, configured with an `auth:` block in
`dashdown.yaml`. The default is `none` — the app is open. There are two modes:
`basic` (HTTP Basic, browser-friendly) and `api_key` (a shared secret in a
header).

:::warning
A malformed `auth:` block makes the server **refuse to start**, so it never comes
up open when you meant to lock it down. Fix the config rather than working around
the error.
:::

## Basic auth

Browser-friendly: the browser prompts for a username/password and resends it on
every request.

```yaml
# dashdown.yaml
auth:
  type: basic
  username: admin
  password: ${DASH_PASSWORD}   # ${VAR} reads from the environment
```

For several accounts, use a `users` mapping instead of `username`/`password`:

```yaml
auth:
  type: basic
  users:
    admin: ${ADMIN_PW}
    viewer: readonly
```

## API-key auth

For proxies and programmatic access: the client sends a shared secret in a
header (default `X-API-Key`).

```yaml
auth:
  type: api_key
  header: X-API-Key      # optional — this is the default
  key: ${DASH_API_KEY}
  # or, to accept several keys:
  # keys: [${KEY_A}, ${KEY_B}]
```

## How it works

- **Secrets support `${ENV_VAR}` expansion** — keep real passwords and keys out
  of the file and in the environment. A referenced variable that isn't set is an
  error at startup.
- **Every route is protected** except the `/_dashdown/health` check. An
  unauthorized request gets a `401`; basic mode also returns a
  `WWW-Authenticate` challenge so the browser prompts.
- **Secrets compare in constant time** (`secrets.compare_digest`), so the check
  doesn't leak length or content through timing.
- **Config reloads live** — changing the `auth:` block is picked up without a
  restart.

## Auth and the cross-origin features

Some features can't carry Basic/api-key credentials and need a different path
when auth is on:

- **Embedding** — a cross-origin iframe can't send credentials, so an authed
  page is embedded with a **signed token** instead. See
  [Authenticated embeds](/embedding#authenticated-embeds).
- **Real-time streaming** — browsers can't send credentials on a WebSocket
  handshake from JS, so live queries pair with `auth: none` (or cookie/proxy
  auth in front). See [Real-time data](/realtime).

:::note
OAuth / SSO is intentionally out of scope for the built-in auth. Put Dashdown
behind a reverse proxy (or an identity-aware proxy) for those.
:::
