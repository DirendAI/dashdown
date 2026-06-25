"""Built-in authentication: HTTP Basic Auth and static API-key header.

Two modes, both configured under an ``auth:`` block in ``dashdown.yaml``:

    auth:
      type: basic            # browser-friendly; the browser prompts + resends
      username: admin
      password: ${DASH_PASSWORD}   # ${VAR} reads from the environment
      # or, for several accounts:
      # users:
      #   admin: ${ADMIN_PW}
      #   viewer: readonly

    auth:
      type: api_key          # for proxies / programmatic access
      header: X-API-Key      # optional, this is the default
      key: ${DASH_API_KEY}
      # or:  keys: [${KEY_A}, ${KEY_B}]

``type: none`` (the default) leaves the app open. Secrets compare in constant
time (``secrets.compare_digest``).
"""
from __future__ import annotations

import base64
import binascii
import os
import re
from dataclasses import dataclass, field
from secrets import compare_digest
from typing import Any

_ENV_RE = re.compile(r"^\$\{(\w+)\}$")
_VALID_TYPES = ("none", "basic", "api_key")


@dataclass
class AuthConfig:
    """Resolved auth settings. Secrets are already env-expanded."""

    type: str = "none"
    realm: str = "Dashdown"
    users: dict[str, str] = field(default_factory=dict)  # basic: username -> password
    header: str = "X-API-Key"  # api_key: header name to read
    keys: list[str] = field(default_factory=list)  # api_key: accepted keys

    @property
    def enabled(self) -> bool:
        return self.type in ("basic", "api_key")


def _resolve_secret(value: Any) -> str:
    """Expand a ``${VAR}`` reference from the environment, else return as-is."""
    s = str(value)
    m = _ENV_RE.match(s.strip())
    if m:
        env_val = os.environ.get(m.group(1))
        if env_val is None:
            raise ValueError(
                f"auth config references environment variable {m.group(1)!r}, "
                "which is not set"
            )
        return env_val
    return s


def parse_auth_config(raw: dict | None) -> AuthConfig:
    """Build an :class:`AuthConfig` from the ``auth`` block of dashdown.yaml.

    Raises ``ValueError`` on misconfiguration so the server refuses to start
    open when the operator clearly intended it locked down.
    """
    if not raw:
        return AuthConfig()
    if not isinstance(raw, dict):
        raise ValueError("auth config must be a mapping")

    typ = str(raw.get("type", "none")).lower()
    if typ not in _VALID_TYPES:
        raise ValueError(
            f"unknown auth.type {typ!r} (expected one of {', '.join(_VALID_TYPES)})"
        )
    if typ == "none":
        return AuthConfig(type="none")

    realm = str(raw.get("realm", "Dashdown"))

    if typ == "basic":
        users: dict[str, str] = {}
        if raw.get("username") is not None:
            users[str(raw["username"])] = _resolve_secret(raw.get("password", ""))
        extra = raw.get("users") or {}
        if not isinstance(extra, dict):
            raise ValueError("auth.users must be a mapping of username -> password")
        for u, p in extra.items():
            users[str(u)] = _resolve_secret(p)
        if not users:
            raise ValueError(
                "auth.type 'basic' requires a username/password or a users mapping"
            )
        return AuthConfig(type="basic", realm=realm, users=users)

    # api_key
    header = str(raw.get("header", "X-API-Key"))
    keys: list[str] = []
    if raw.get("key") is not None:
        keys.append(_resolve_secret(raw["key"]))
    extra_keys = raw.get("keys") or []
    if not isinstance(extra_keys, (list, tuple)):
        raise ValueError("auth.keys must be a list")
    for k in extra_keys:
        keys.append(_resolve_secret(k))
    if not keys:
        raise ValueError("auth.type 'api_key' requires a key or a keys list")
    return AuthConfig(type="api_key", realm=realm, header=header, keys=keys)


def _check_basic(config: AuthConfig, header_value: str | None) -> bool:
    if not header_value:
        return False
    scheme, _, encoded = header_value.partition(" ")
    if scheme.lower() != "basic" or not encoded:
        return False
    try:
        decoded = base64.b64decode(encoded.strip(), validate=True).decode("utf-8")
    except (binascii.Error, ValueError, UnicodeDecodeError):
        return False
    username, sep, password = decoded.partition(":")
    if not sep:
        return False
    expected = config.users.get(username)
    if expected is None:
        # Compare against the supplied value anyway so a missing username and a
        # wrong password take a similar amount of time.
        compare_digest(password, password)
        return False
    return compare_digest(password, expected)


def _check_api_key(config: AuthConfig, provided: str | None) -> bool:
    if not provided:
        return False
    # Evaluate every key (no short-circuit) to keep the check constant-ish time.
    ok = False
    for k in config.keys:
        if compare_digest(provided, k):
            ok = True
    return ok


def is_authorized(config: AuthConfig, request: Any) -> bool:
    """Return True if the request carries valid credentials for ``config``.

    ``request`` is a Starlette/FastAPI ``Request`` (only ``.headers`` is used).
    """
    if not config.enabled:
        return True
    if config.type == "basic":
        return _check_basic(config, request.headers.get("authorization"))
    if config.type == "api_key":
        return _check_api_key(config, request.headers.get(config.header))
    return True


def challenge_headers(config: AuthConfig) -> dict[str, str]:
    """Headers to return with a 401 so clients know how to authenticate."""
    if config.type == "basic":
        # Realm comes from trusted config; strip quotes to keep the header valid.
        realm = config.realm.replace('"', "")
        return {"WWW-Authenticate": f'Basic realm="{realm}"'}
    return {}
