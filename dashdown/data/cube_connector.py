"""Cube connector — an HTTP client for a Cube (cube.dev) semantic-layer server.

Unlike every other connector, **Cube is not queried with SQL**. A Cube deployment
exposes a *structured JSON* query API (``POST /cubejs-api/v1/load``) and a model
introspection endpoint (``GET /cubejs-api/v1/meta``), authenticated with a JWT. So
this connector is a thin, configured HTTP client that the **Cube semantic backend**
(:mod:`dashdown.semantic_cube`) drives — it exposes :meth:`load` and :meth:`meta`,
and deliberately leaves the SQL-shaped :meth:`Connector.query` (the ABC's one
abstract method) as ``raise NotImplementedError``.

**Why a connector at all (not the backend owning the socket)?** Connection config —
``url``, the signing ``secret`` (``${ENV}``-expandable), ``security_context``, the
optional ``api_path`` — is exactly what ``sources.yaml`` is for, and reusing the
connector machinery gets ``${ENV}`` expansion, the lazy optional-extra import, and a
single place the deployment is described. The semantic backend stays "how do I get
rows"; the connector stays "how do I reach this service" — a ``type: cube`` source
that is **only** an HTTP client, never a fake ``query(sql)``.

**JWT lifecycle.** A token is minted with HS256 from ``secret`` (RS256 via
``private_key`` + ``algorithm: RS256``; a static pre-minted ``token`` is the escape
hatch), embeds the configured ``security_context`` (the RLS rail — the same
``${_claim_*}`` posture as ``dax_rls``), carries a short TTL, and is re-minted before
it expires. A ``401`` triggers one re-mint-and-retry (the DB-API reconnect posture).

**Failure → a clear ``RuntimeError``.** ``/load`` and ``/meta`` map any 4xx/5xx,
auth failure, or timeout to a ``RuntimeError`` with the server's message — never a
silent empty result. The synthetic semantic ``fn`` runs in the threadpool, so a
raised error surfaces as the component's inline ``_error_card`` (not a 500).

``sources.yaml`` example::

    cube:
      type: cube
      url: https://cube.example.com
      secret: ${CUBE_API_SECRET}        # HS256 signing secret (env-expanded)
      token_ttl: 300                     # seconds (default 300)
      security_context:                  # optional — embedded in every JWT (RLS)
        tenant_id: acme
      api_path: /cubejs-api/v1           # default
"""
from __future__ import annotations

import os
import re
import threading
import time
from typing import Any

from dashdown.data.base import (
    Connector,
    IntrospectionUnsupported,
    QueryResult,
    register_connector,
)

_ENV_RE = re.compile(r"^\$\{(\w+)\}$")

#: How many seconds before ``exp`` we proactively re-mint the token.
_TOKEN_REFRESH_SKEW = 30
#: Cube returns ``{"error": "Continue wait"}`` (HTTP 200) while a long query is still
#: running; poll a bounded number of times before giving up.
_CONTINUE_WAIT = "Continue wait"
_MAX_CONTINUE_WAITS = 30
_CONTINUE_WAIT_SLEEP = 2.0


def _resolve_secret(value: Any) -> str:
    """Expand a ``${VAR}`` reference from the environment, else return as-is.

    Mirrors ``auth._resolve_secret`` / ``embed`` — a missing variable raises so a
    misconfigured deployment fails loudly at startup rather than minting a token
    signed with the literal string ``"${CUBE_API_SECRET}"``.
    """
    s = str(value)
    m = _ENV_RE.match(s.strip())
    if m:
        env_val = os.environ.get(m.group(1))
        if env_val is None:
            raise ValueError(
                f"cube connector references environment variable {m.group(1)!r}, "
                "which is not set"
            )
        return env_val
    return s


@register_connector("cube")
class CubeConnector(Connector):
    """HTTP client for a Cube deployment — driven by the Cube semantic backend.

    Not a SQL connector: :meth:`query` raises. The backend calls :meth:`load`
    (structured JSON query) and :meth:`meta` (model introspection).
    """

    def __init__(self, name: str, config: dict[str, Any]) -> None:
        super().__init__(name, config)
        self.url: str = str(config.get("url", "")).rstrip("/")
        if not self.url:
            raise ValueError("cube connector requires 'url' in sources.yaml")
        self.api_path: str = "/" + str(config.get("api_path", "/cubejs-api/v1")).strip("/")
        self.algorithm: str = str(config.get("algorithm", "HS256"))
        # A static, pre-minted token short-circuits signing entirely.
        self._static_token: str | None = (
            _resolve_secret(config["token"]) if config.get("token") else None
        )
        self._secret: str | None = (
            _resolve_secret(config["secret"]) if config.get("secret") else None
        )
        self._private_key: str | None = (
            _resolve_secret(config["private_key"]) if config.get("private_key") else None
        )
        if not (self._static_token or self._secret or self._private_key):
            raise ValueError(
                "cube connector requires one of 'secret' (HS256), 'private_key' "
                "(RS256), or a static 'token' in sources.yaml"
            )
        self.security_context: dict[str, Any] = dict(config.get("security_context") or {})
        self.token_ttl: int = int(config.get("token_ttl", 300))
        self.timeout: float = float(config.get("timeout", 60))
        self._token: str | None = None
        self._token_exp: float = 0.0
        self._lock = threading.Lock()

    # -- the SQL ABC method, deliberately unsupported -------------------------

    def query(self, sql: str) -> QueryResult:  # noqa: ARG002
        raise NotImplementedError(
            "the cube connector is queried via the semantic layer "
            "(metric={model.metric} by={model.dim}), not SQL. "
            "Define a semantic/*.yml model on this connector."
        )

    # -- schema introspection (from /meta — query() can't run) ----------------
    #
    # Cube has no SQL, so the information_schema default raises. Its model *is*
    # the schema, served over `/meta`: each cube/view maps to a "table", and a
    # cube's measures + dimensions map to its "columns" (carrying their kind).

    def list_tables(self) -> QueryResult:
        rows = []
        for cube in self.meta().get("cubes") or []:
            if not isinstance(cube, dict):
                continue
            name = cube.get("name")
            if name:
                kind = "view" if cube.get("type") == "view" else "cube"
                rows.append([name, None, kind])
        return QueryResult(columns=["table", "schema", "type"], rows=rows)

    def describe_table(self, table: str) -> QueryResult:
        rows = []
        found = False
        for cube in self.meta().get("cubes") or []:
            if not isinstance(cube, dict) or cube.get("name") != table:
                continue
            found = True
            for dim in cube.get("dimensions") or []:
                rows.append([dim.get("name"), dim.get("type"), "dimension"])
            for meas in cube.get("measures") or []:
                rows.append([meas.get("name"), meas.get("type"), "measure"])
        if not found:
            raise IntrospectionUnsupported(
                f"cube '{table}' not found in this deployment's /meta. "
                "Run `dashdown query --tables` to list the available cubes."
            )
        return QueryResult(columns=["member", "type", "kind"], rows=rows)

    # -- JWT minting ----------------------------------------------------------

    def _mint_token(self) -> str:
        """Sign a fresh JWT embedding the security context, or return the static one."""
        if self._static_token:
            return self._static_token
        try:
            import jwt  # PyJWT
        except ImportError as e:  # pragma: no cover - exercised when extra absent
            raise ImportError(
                "The cube connector needs PyJWT (and httpx) to mint tokens, which "
                "are not installed. Install them with: pip install 'dashdown-md[cube]'"
            ) from e
        now = int(time.time())
        payload = build_jwt_payload(self.security_context, now, now + self.token_ttl)
        key = self._private_key if self.algorithm.startswith("RS") else self._secret
        token = jwt.encode(payload, key, algorithm=self.algorithm)
        # PyJWT < 2 returns bytes; normalize to str.
        return token.decode("utf-8") if isinstance(token, bytes) else token

    def _ensure_token(self, force: bool = False) -> str:
        """Return a valid token, (re)minting if missing, expired, or *force*-d."""
        with self._lock:
            now = time.time()
            if force or self._token is None or now >= self._token_exp - _TOKEN_REFRESH_SKEW:
                self._token = self._mint_token()
                # Static tokens don't expire on our clock; otherwise track TTL.
                self._token_exp = float("inf") if self._static_token else now + self.token_ttl
            return self._token

    # -- HTTP plumbing --------------------------------------------------------

    def _client(self):
        try:
            import httpx
        except ImportError as e:  # pragma: no cover - exercised when extra absent
            raise ImportError(
                "The cube connector needs httpx (and PyJWT), which are not "
                "installed. Install them with: pip install 'dashdown-md[cube]'"
            ) from e
        return httpx

    def _request(self, method: str, endpoint: str, *, json: Any = None) -> Any:
        """Issue one authenticated request, re-minting once on a 401.

        Maps any non-2xx (and a transport error) to a ``RuntimeError`` so the
        caller surfaces the component error card rather than a 500 / silent empty.
        """
        httpx = self._client()
        url = f"{self.url}{self.api_path}{endpoint}"
        token = self._ensure_token()
        try:
            resp = httpx.request(
                method, url, headers={"Authorization": token}, json=json,
                timeout=self.timeout,
            )
            if resp.status_code == 401:  # token may have expired — re-mint once + retry
                token = self._ensure_token(force=True)
                resp = httpx.request(
                    method, url, headers={"Authorization": token}, json=json,
                    timeout=self.timeout,
                )
        except Exception as e:  # transport-level failure (DNS, timeout, refused)
            raise RuntimeError(f"cube request to {url} failed: {e}") from e

        if resp.status_code >= 400:
            raise RuntimeError(
                f"cube {method} {endpoint} returned HTTP {resp.status_code}: "
                f"{_short_body(resp)}"
            )
        try:
            return resp.json()
        except Exception as e:  # pragma: no cover - defensive
            raise RuntimeError(
                f"cube {method} {endpoint} returned a non-JSON body: {_short_body(resp)}"
            ) from e

    def load(self, query: dict[str, Any]) -> dict[str, Any]:
        """Run a structured Cube query, returning the ``{data, annotation, …}`` JSON.

        Handles Cube's continue-wait protocol: a long-running query answers HTTP 200
        with ``{"error": "Continue wait"}`` until the result is ready, so this polls
        a bounded number of times. Any ``error`` other than continue-wait raises.
        """
        for _ in range(_MAX_CONTINUE_WAITS):
            payload = self._request("POST", "/load", json={"query": query})
            err = payload.get("error") if isinstance(payload, dict) else None
            if err == _CONTINUE_WAIT:
                time.sleep(_CONTINUE_WAIT_SLEEP)
                continue
            if err:
                raise RuntimeError(f"cube /load error: {err}")
            return payload
        raise RuntimeError(
            f"cube /load still not ready after {_MAX_CONTINUE_WAITS} polls "
            f"(continue-wait timeout)"
        )

    def meta(self) -> dict[str, Any]:
        """Fetch the deployment's model metadata (``GET /meta``)."""
        return self._request("GET", "/meta")


def build_jwt_payload(
    security_context: dict[str, Any], iat: int, exp: int
) -> dict[str, Any]:
    """Assemble a Cube JWT payload: the security context plus ``iat``/``exp``.

    Cube treats the **verified JWT payload itself** as the security context, so the
    configured context is spread at the top level (the common default). Pure +
    time-injected so it's unit-testable without signing or a clock.
    """
    return {**security_context, "iat": iat, "exp": exp}


def _short_body(resp: Any, limit: int = 500) -> str:
    """A trimmed response body for an error message (never raises)."""
    try:
        text = resp.text
    except Exception:  # pragma: no cover - defensive
        return "<unreadable body>"
    text = str(text)
    return text if len(text) <= limit else text[:limit] + "…"
