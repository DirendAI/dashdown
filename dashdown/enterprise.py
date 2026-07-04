"""Enterprise feature gate.

Built-in auth (``auth:``) and embedding (``embed:``) are fully implemented and
tested (``auth.py`` / ``embed.py`` and their suites) but are **postponed as
enterprise features**: activating either block in ``dashdown.yaml`` refuses at
project load until the enterprise unlock is present. The implementation stays
in-tree and the test suite keeps exercising it (see ``tests/conftest.py``) so
it can't rot.

The unlock is the ``DASHDOWN_ENTERPRISE`` environment variable. The source is
public, so this gate is product positioning, not DRM — which is why the error
message names the unlock. When a real enterprise edition ships, its license
check replaces :func:`enterprise_enabled`; call sites stay put.
"""
from __future__ import annotations

import os

ENTERPRISE_ENV = "DASHDOWN_ENTERPRISE"
_TRUTHY = frozenset({"1", "true", "yes", "on"})


def enterprise_enabled() -> bool:
    """Whether enterprise features may activate in this process."""
    return os.environ.get(ENTERPRISE_ENV, "").strip().lower() in _TRUTHY


def require_enterprise(feature: str) -> None:
    """Refuse (``ValueError``) unless the enterprise unlock is present.

    Called *after* the feature's config parsed cleanly, so a misconfigured
    block still fails with its own specific error first. Raising — not
    ignoring — keeps the fail-at-startup policy: silently dropping an
    ``auth:`` block would start the server open when the operator asked for
    it locked down.
    """
    if enterprise_enabled():
        return
    raise ValueError(
        f"'{feature}:' configures an enterprise feature that is not generally "
        f"available yet. Remove the '{feature}:' block from dashdown.yaml, or "
        f"set {ENTERPRISE_ENV}=1 to opt in early (unsupported preview)."
    )
