# Dashdown security audit

Scope: the `dashdown` Python package (framework code + shipped static JS wiring) at the
current tip of `claude/dashdown-security-audit-iscclq`. Focus areas: SQL parameter
substitution, authentication, embed-token crypto, request routing / path traversal,
the data / options / ask / PDF / WebSocket endpoints, the LLM `<Ask />` path, custom-code
trust boundaries, and output encoding (XSS).

The audit is a static review. Findings marked **Confirmed** were reproduced or traced
end-to-end in code; **Plausible** ones are reachable-by-construction but depend on a
configuration or a custom component I could not exercise here.

Severity uses the usual High / Medium / Low bands, weighted for a self-hosted analytics
tool that is frequently deployed **without auth** on a trusted network (the documented
default posture).

---

## Summary

| # | Severity | Title | Status |
|---|----------|-------|--------|
| 1 | **High** | SQL injection via backslash escapes on MySQL / ClickHouse / Snowflake / BigQuery | Confirmed |
| 2 | **Medium** | Reflected XSS in the 404 "Not Found" page | Confirmed |
| 3 | **Medium** | PDF endpoint: SSRF + auth-credential exfiltration via the `Host` header | Confirmed |
| 4 | **Medium** | `python_queries.enabled: false` does not disable `components/**/*.py` execution | Confirmed |
| 5 | Low | `_error_card()` interpolates error text into HTML unescaped (latent XSS) | Confirmed |
| 6 | Low | Internal error/exception details leaked to clients in 500 responses | Confirmed |
| 7 | Low | JSON blobs emitted into `<script>` with `\| safe` + un-`<`-escaped `json.dumps` | Confirmed (not currently exploitable) |
| 8 | Low | No `Host` validation (`TrustedHostMiddleware` absent) | Confirmed |
| 9 | Low | GET endpoints with side effects + Basic-auth ambient credentials (CSRF / cost) | Plausible |
| 10 | Info | Telemetry is on-by-default (anonymized, opt-out) | Confirmed |

A list of things the codebase does **well** is at the end — the security model is mostly
sound; most findings are edges around it.

---

## 1. High — SQL injection via backslash escapes (MySQL / MariaDB / ClickHouse / Snowflake / BigQuery)

**Where:** `dashdown/render/pipeline.py::_substitute_params` (≈line 1011) and
`_expand_in_list` (≈line 925). This is the framework's *only* SQL-injection defense — there
is no bind-parameter path (documented in `CLAUDE.md`).

**What:** `_substitute_params` is context-aware but escapes **only quote characters**:
`'` → `''` inside single quotes, `"` → `""` inside double quotes. It never escapes the
backslash. That is correct for DuckDB and PostgreSQL (which, by default, treat `\` as a
literal character in string literals), but **incorrect for every backend that processes
backslash escape sequences in string literals by default** — MySQL / MariaDB, ClickHouse,
Snowflake, and BigQuery. Dashdown ships first-class connectors for all of these, and the
MySQL connector (`dashdown/data/mysql_connector.py`) opens a plain PyMySQL connection with
no `NO_BACKSLASH_ESCAPES` / `sql_mode` hardening.

**Why it breaks:** a trailing backslash escapes the closing quote the escaper added, so the
attacker's value is no longer confined to the string literal.

Template (the documented pattern for a string filter):

```sql
SELECT * FROM users WHERE name = '${name}'
```

Attacker-supplied filter value (via `?name=…` on `/_dashdown/api/data/{query}`):

```
\' OR 1=1--␠
```

Output of `_substitute_params` (reproduced against the real function logic):

```sql
SELECT * FROM users WHERE name = '\'' OR 1=1-- '
```

On a backslash-honoring engine, `'\''` tokenizes as `'` (open) → `\'` (escaped quote =
literal `'`) → `'` (close), i.e. the string value is a single quote, and `OR 1=1-- ` runs
as live SQL. On DuckDB/Postgres the whole thing stays one string literal and is inert —
which is why the existing `TestSubstituteParams` suite (run against DuckDB semantics) does
not catch it.

The same gap exists in the multi-select `IN (${param})` path (`_expand_in_list` only doubles
`'`), and in the bare-`${param}` branch (also only doubles `'`).

**Impact:** full SQL injection (read/exfiltrate/modify per the connection's grants) for any
project whose default or referenced connector is MySQL/MariaDB/ClickHouse/Snowflake/BigQuery
and whose queries interpolate a filter value — the norm. Reachable unauthenticated when the
project runs with the default `auth: none`.

**Fix options (in order of robustness):**
1. Move to real driver-level parameter binding per connector (`cursor.execute(sql, params)`
   / DuckDB prepared statements). This removes the string-substitution surface entirely.
2. If keeping string substitution, make the escaping **dialect-aware**: for backslash-honoring
   backends also escape `\` → `\\` (and be careful about the `IN`-list path). At minimum,
   force MySQL into `NO_BACKSLASH_ESCAPES` and ensure Postgres `standard_conforming_strings`
   is on.
3. Add regression tests that assert the *escaped output* is inert under backslash-escape
   semantics, not just under DuckDB.

---

## 2. Medium — Reflected XSS in the 404 page

**Where:** `dashdown/server.py::_not_found_html` (≈line 1108), called from the catch-all
`page()` route (≈line 993).

```python
def _not_found_html(project, path):
    ...
    f"<h1>404 — no page for /{path}</h1>"
    ...
```

`path` is the raw `full_path` captured by `@app.get("/{full_path:path}")`, inserted into
HTML **without escaping**. Because the `:path` converter matches slashes, the value can carry
a complete tag payload. A victim who follows a crafted link such as:

```
https://dash.example/%3Cimg%20src%3Dx%20onerror%3Dalert(document.domain)%3E
```

is served `<h1>404 — no page for /<img src=x onerror=alert(document.domain)></h1>` and the
script runs. Reachable **unauthenticated** under the default `auth: none`; when Basic/api_key
auth is on, the payload executes in the authenticated victim's context.

**Fix:** `html.escape(path)` before interpolation (and, generally, treat the 404 body as
untrusted output).

---

## 3. Medium — PDF endpoint: SSRF + credential exfiltration via the `Host` header

**Where:** `dashdown/server.py::export_page_pdf` (`GET /_dashdown/api/pdf`, ≈line 830) →
`dashdown/pdf.py::render_url_pdf` (≈line 321).

The endpoint builds the Chromium navigation target from `request.base_url`:

```python
base = str(request.base_url).rstrip("/")          # derived from the Host header
target = f"{base}/{full}" if full else f"{base}/"
```

and then attaches the project's own auth secret so the headless browser can satisfy this
server's auth:

```python
if auth.type == "basic" and auth.users:
    user, password = next(iter(auth.users.items()))   # the FIRST configured user
    http_credentials = {"username": user, "password": password}
elif auth.type == "api_key" and auth.keys:
    extra_headers = {auth.header: auth.keys[0]}
```

`render_url_pdf` passes `http_credentials` to `browser.new_context(...)` and applies
`extra_headers` via `context.set_extra_http_headers(...)` — the latter is sent on **every**
request to whatever host `target` names.

Starlette derives `request.base_url` from the `Host` request header, and there is no
`TrustedHostMiddleware` (see finding 8). So an authenticated user who sends
`Host: attacker.example` to `/_dashdown/api/pdf?_path=/<any-valid-page>` causes the server's
headless Chromium to fetch `http://attacker.example/<page>` **carrying the configured api_key
(sent unconditionally) or the first Basic user's username+password** (sent on the 401 the
attacker's host returns). `_path` is validated to be a real page, but the *host* is not — so
this is also a general SSRF primitive against hosts the server can reach.

**Impact:** disclosure of the shared API key, or of the *first* configured Basic account's
password (often the admin), to an attacker-controlled host — a privilege-escalation /
credential-theft path in multi-user Basic setups — plus blind SSRF. Precondition: auth is
enabled and the attacker holds *some* valid credential.

**Fix:** do not derive the navigation target from the `Host` header — build it from a
configured public base URL (or force `127.0.0.1` + the known bound port, as the CLI `pdf`
path already does). Add `TrustedHostMiddleware` with an allowlist. Scope Playwright
credentials to the intended origin (`http_credentials`'s `origin`, and per-request header
gating) so they can never be sent cross-origin.

---

## 4. Medium — `python_queries.enabled: false` does not disable `components/**/*.py`

**Where:** `dashdown/project.py::load_project`. `_import_user_modules(root / "components")`
runs **unconditionally** (≈line 790), while the `queries/**/*.py` and `semantic/**/*.yml`
loaders are gated behind `if cfg.python_queries.enabled:` (≈line 838+).

`CLAUDE.md` and `python_query.py` both describe `python_queries.enabled: false` as the switch
a "managed / multi-tenant host that must refuse semi-trusted code" flips to disable
in-process author-code execution — and explicitly equate the `queries/*.py` trust boundary
with `components/*.py`. But `components/**/*.py` (and any `@register_connector` module) is
still `exec_module`'d at project load regardless of the flag. So the control is **incomplete**:
a host that sets `python_queries.enabled: false` believing it has turned off arbitrary code
execution still runs any `.py` an author drops in `components/`.

**Impact:** the documented "no semi-trusted code" posture is not actually achieved; RCE via
`components/*.py` remains open. If the threat model is truly untrusted project authors,
in-process import of author code is unsafe irrespective of this flag.

**Fix:** gate `_import_user_modules` (and `_discover_component_assets` for consistency of the
model) on the same switch, or introduce an explicit "no author code" mode that covers both
directories; and update the docs so the switch's guarantee matches its behavior.

---

## 5. Low — `_error_card()` interpolates error text into HTML unescaped

**Where:** `dashdown/render/components.py::_error_card` (≈line 167) and its call sites
(≈lines 81–84, 144–147).

```python
f'<pre class="dashdown-error-detail text-sm">{detail}</pre>'
```

`detail` is frequently `str(e)` — an exception message from a component's `render()`. The
current built-in call sites are mostly safe (tag names are `[A-Z][A-Za-z0-9_]*`), but the
error card renders into `body_html`, which the template emits with `| safe`. A custom
component that raises `ValueError(f"bad value: {value}")` where `value` derives from a
route/filter param (which reach `ctx.params`, and an `<img … onerror=…>` payload needs no
`/`) yields reflected XSS. This is a latent hazard, not a confirmed built-in exploit.

**Fix:** `html.escape()` both `title` and `detail` in `_error_card`.

---

## 6. Low — Internal error details leaked to clients

**Where:** the data / options / ask APIs return `detail=f"…: {type(e).__name__}: {e}"`
(`server.py` ≈lines 371–374, 421–426, 497–502, 751–754) and `page()` returns
`f"<pre>Render error: {type(e).__name__}: {e}</pre>"` (≈line 1025) on a 500.

Raw exception text can expose SQL fragments, schema/column names, filesystem paths, and
driver internals to any client that can reach the endpoint. Low on its own; it also widens
the surface for findings 1 and 5 (errors that reflect input).

**Fix:** log the detail server-side; return a generic message (and a correlation id) to the
client. Escape any error text that must be rendered into HTML.

---

## 7. Low — JSON-in-`<script>` blobs via `| safe` without `<`-escaping

**Where:** `dashdown/templates/page.html` (≈lines 257–283): `datasets_json`,
`query_defs_json`, `route_params_json`, `branding_json`, `format_json` are all emitted as
`{{ … | safe }}` inside `<script type="application/json">` blocks, and the values come from
`json.dumps(...)`, which does not escape `<`, `>`, or `/`.

The only one of these carrying viewer-controlled data is `route_params_json` (dynamic
`[slug]` values). Today this is **not** exploitable, because a `</script>` breakout needs a
`/` and a single route segment cannot contain one. It is nonetheless brittle: any future
change that lets a `/`-bearing or richer value reach one of these blobs becomes an immediate
`</script>` XSS.

**Fix:** serialize with a `<`/`>`/`&`/`U+2028`/`U+2029`-escaping encoder (or post-process
`</` → `<\/`) for anything emitted into a `<script>` context.

---

## 8. Low — No `Host` header validation

No `TrustedHostMiddleware` is registered (`grep` for `add_middleware` / `TrustedHost` returns
nothing). The `Host` header flows unchecked into `request.base_url`, which is the root
enabler for finding 3 and a general Host-spoofing / cache-poisoning risk.

**Fix:** register `TrustedHostMiddleware(allowed_hosts=[…])` for non-dev deployments, or a
configured public base URL used wherever the server needs its own origin.

---

## 9. Low / Plausible — GET side effects with Basic-auth ambient credentials

`/_dashdown/api/ask/{id}` (a **billable** LLM call on cache-miss / `_refresh=1`) and
`/_dashdown/api/pdf` (an expensive Chromium render) are `GET` requests with no CSRF token.
When the project uses `basic` auth, browsers attach the cached credentials automatically, so
a cross-site `<img>`/`fetch` to these URLs can drive cost/resource abuse in an authenticated
victim's context. Impact is limited (no data mutation), so this is Low.

**Fix:** require a same-site check / token for the billable and resource-heavy endpoints, or
move refresh/PDF triggers to `POST` with CSRF protection.

---

## 10. Info — Telemetry is on by default

`dashdown/telemetry.py` sends one event per `serve`/`build` to PostHog EU with only the
Dashdown version, Python version, OS, arch, and a random `install_id`; it is anonymized
(`$process_person_profile: false`), throttled to 24h, prints a first-run notice, and has four
independent opt-outs (`DO_NOT_TRACK`, `DASHDOWN_TELEMETRY`, `dashdown telemetry off`,
`telemetry.enabled: false`). The two call sites (`cli.py`) pass no project data. Not a
vulnerability — flagged only because on-by-default network egress is a deployment/compliance
consideration for some operators.

---

## What the codebase does well

- **Context-aware substitution** with per-item `IN`-list escaping and a `MAX_IN_VALUES` cap;
  values always become quoted literals, never raw concatenated SQL. (The one gap is the
  backslash dialect issue in finding 1.)
- **Auth** compares secrets with `secrets.compare_digest`, mitigates username-enumeration
  timing, and *fails hard* (refuses to start) on a malformed `auth:` block so it never comes
  up open by accident.
- **Embed tokens** are HMAC-SHA256, constant-time compared, scoped to an exact page path
  **and** its `connector:query` pairs; framing is deny-by-default (CSP `frame-ancestors`,
  else `X-Frame-Options: DENY`).
- **WebSocket** streaming does its own `is_authorized` check (HTTP middleware doesn't cover
  WS) and refuses any query not explicitly registered `live`.
- **LLM output** is rendered with markdown-it `html=False`; annotation labels are
  `html.escape`d; `<Ask />` prompts are looked up by opaque, deterministic id so the endpoint
  can't be fed an arbitrary prompt.
- **Path traversal** is guarded consistently — routing, `{% include %}` expansion, the query
  library, and page-asset serving all `resolve()` and confine with `relative_to` /
  `is_relative_to`, which is also symlink-safe; the component static mount serves only
  `.js/.css` and never the `.py` source.
- **YAML** is always `yaml.safe_load`; no `eval`/`exec`/`subprocess`/`pickle` of request data.
- **DoS hygiene:** the server-side result cache is a bounded LRU, and the `IN`-list is capped.

## Suggested remediation order

1. Finding 1 (SQL injection) — the only High; fix the escaping / move to bind parameters and
   add backslash-semantics regression tests.
2. Findings 2 and 3 (404 XSS, PDF SSRF/cred-exfil) — small, self-contained, high-value fixes.
3. Finding 4 — close the `components/*.py` gate (or correct the documented guarantee).
4. Findings 5–9 — output-encoding and hardening cleanups.
