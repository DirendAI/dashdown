"""Embeddable pages: config, framing headers, and signed embed tokens.

Configured under an ``embed:`` block in ``dashdown.yaml``:

    embed:
      enabled: true
      frame_ancestors:           # origins permitted to <iframe> these pages
        - https://notion.so
        - https://wiki.example.com
      secret: ${EMBED_SECRET}    # HMAC key for signed tokens (needed when auth is on)
      token_ttl: 3600            # default token lifetime in seconds (0 = no expiry)

Embedding is opt-in (``enabled: false`` by default) and framing is
**deny-by-default**: a page can only be framed once ``frame_ancestors`` lists the
host origin(s), which then become a CSP ``frame-ancestors`` directive.

When the project has ``auth:`` enabled, a cross-origin iframe can't send the
Basic/API-key credentials (the same constraint the WS streaming endpoint has), so
an authed page is embedded with a **signed, page-scoped token** in
the URL (``?_embed=<token>``) minted by the author. The token is an HMAC over the
page path *and* the ``connector:query`` pairs that page is allowed to read, so a
leaked embed URL can't be turned into a key for the rest of the dashboard.

This module is deliberately dependency-light (config + crypto only) so it can be
imported anywhere without a cycle; the request/path scoping that needs the query
registry lives in ``server.py``.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import time
from dataclasses import dataclass, field
from typing import Any

_ENV_RE = re.compile(r"^\$\{(\w+)\}$")
# A conservative origin matcher: scheme://host[:port] with no path/query/fragment,
# or the wildcard '*' (frame from anywhere). Mirrors what CSP frame-ancestors accepts.
_ORIGIN_RE = re.compile(r"^(?:https?://[^/\s]+|\*)$", re.IGNORECASE)


@dataclass
class EmbedConfig:
    """Resolved ``embed:`` settings. ``secret`` is already env-expanded."""

    enabled: bool = False
    frame_ancestors: list[str] = field(default_factory=list)
    secret: str | None = None
    token_ttl: int = 0  # default token lifetime (seconds); 0 = no expiry

    @property
    def has_secret(self) -> bool:
        return bool(self.secret)


def _resolve_secret(value: Any) -> str:
    """Expand a ``${VAR}`` reference from the environment, else return as-is."""
    s = str(value)
    m = _ENV_RE.match(s.strip())
    if m:
        env_val = os.environ.get(m.group(1))
        if env_val is None:
            raise ValueError(
                f"embed config references environment variable {m.group(1)!r}, "
                "which is not set"
            )
        return env_val
    return s


def parse_embed_config(raw: Any) -> EmbedConfig:
    """Build an :class:`EmbedConfig` from the ``embed`` block of dashdown.yaml.

    Raises ``ValueError`` on misconfiguration so the server refuses to start with
    a half-broken embedding setup (same fail-at-startup policy as ``auth:``)."""
    if raw is None:
        return EmbedConfig()
    if not isinstance(raw, dict):
        raise ValueError("embed: must be a mapping")

    enabled = bool(raw.get("enabled", False))

    fa_raw = raw.get("frame_ancestors", [])
    if fa_raw is None:
        fa_raw = []
    if isinstance(fa_raw, str):
        fa_raw = [fa_raw]
    if not isinstance(fa_raw, (list, tuple)):
        raise ValueError(
            "embed.frame_ancestors must be a string or a list of origins"
        )
    frame_ancestors: list[str] = []
    for origin in fa_raw:
        o = str(origin).strip()
        if not _ORIGIN_RE.match(o):
            raise ValueError(
                f"embed.frame_ancestors entry {origin!r} is not an origin like "
                "'https://example.com' (scheme://host[:port], or '*')"
            )
        frame_ancestors.append(o)

    secret_raw = raw.get("secret")
    secret = _resolve_secret(secret_raw) if secret_raw is not None else None
    if secret is not None and not secret:
        raise ValueError("embed.secret must be a non-empty string")

    ttl_raw = raw.get("token_ttl", 0)
    try:
        token_ttl = int(ttl_raw)
    except (TypeError, ValueError):
        raise ValueError("embed.token_ttl must be an integer number of seconds")
    if token_ttl < 0:
        raise ValueError("embed.token_ttl must be >= 0")

    return EmbedConfig(
        enabled=enabled,
        frame_ancestors=frame_ancestors,
        secret=secret,
        token_ttl=token_ttl,
    )


def frame_headers(config: EmbedConfig) -> dict[str, str]:
    """Response headers controlling who may frame a page.

    Deny-by-default: until ``frame_ancestors`` lists origins, the page refuses
    framing entirely. A configured allowlist becomes a CSP ``frame-ancestors``
    directive — the modern replacement for ``X-Frame-Options`` and the only one
    that expresses multiple specific origins."""
    if config.enabled and config.frame_ancestors:
        return {
            "Content-Security-Policy": "frame-ancestors "
            + " ".join(config.frame_ancestors)
        }
    return {"X-Frame-Options": "DENY"}


# --- Signed tokens (Part B) ------------------------------------------------- #


def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64url_decode(s: str) -> bytes:
    pad = "=" * (-len(s) % 4)
    return base64.urlsafe_b64decode(s + pad)


def _sign(secret: str, body: str) -> bytes:
    return hmac.new(
        secret.encode("utf-8"), body.encode("ascii"), hashlib.sha256
    ).digest()


def query_key(connector: str, name: str) -> str:
    """Canonical ``connector:query`` scope token used inside the signed payload."""
    return f"{connector}:{name}"


def sign_embed_token(
    secret: str, path: str, queries: list[str], exp: int | None = None
) -> str:
    """Sign a page-scoped embed token.

    ``path`` is the canonical page path (e.g. ``/sales``); ``queries`` is the list
    of :func:`query_key` strings the page is allowed to read; ``exp`` is an
    optional absolute Unix expiry. Format: ``<b64url(payload)>.<b64url(sig)>``.
    """
    payload: dict[str, Any] = {"path": path, "q": sorted(set(queries))}
    if exp is not None:
        payload["exp"] = int(exp)
    body = _b64url_encode(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    )
    sig = _b64url_encode(_sign(secret, body))
    return f"{body}.{sig}"


def verify_embed_token(
    secret: str, token: str | None, now: int | None = None
) -> dict | None:
    """Return the token payload if the signature is valid and unexpired, else None.

    Constant-time signature comparison (``hmac.compare_digest``). ``now`` is for
    testing; defaults to the current wall-clock time."""
    if not secret or not token or "." not in token:
        return None
    body, _, sig = token.partition(".")
    expected = _b64url_encode(_sign(secret, body))
    if not hmac.compare_digest(sig, expected):
        return None
    try:
        payload = json.loads(_b64url_decode(body))
    except (ValueError, json.JSONDecodeError, UnicodeDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    exp = payload.get("exp")
    if exp is not None:
        current = now if now is not None else int(time.time())
        try:
            if current >= int(exp):
                return None
        except (TypeError, ValueError):
            return None
    return payload


def token_allows_query(payload: dict, connector: str, name: str) -> bool:
    """Whether a verified token payload permits reading ``connector:name``."""
    return query_key(connector, name) in (payload.get("q") or [])
