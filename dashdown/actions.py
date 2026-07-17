"""Trigger actions — what fires when a trigger's condition breaches.

A trigger (see :mod:`dashdown.triggers`) watches a query result on the shared
poll loop; when its condition breaches it fires one or more **actions** with an
answer-shaped ``event`` dict. This module owns the action side of that contract:
the :class:`Action` ABC, a small ``@register_action("type")`` registry (the same
shape as the connector registry in :mod:`dashdown.data.base`, minus the
entry-point discovery — every action here is built in), and two built-ins:

- ``webhook`` — ``POST`` the raw event as JSON to an arbitrary URL.
- ``slack`` — post a readable message to a Slack incoming-webhook (``{"text": …}``).

Both speak plain HTTP through the stdlib (``urllib.request``, 10s timeout), so an
action has **no third-party dependency**. Config values support ``${ENV_VAR}``
expansion (mirroring the ``_resolve_secret`` pattern in ``auth.py`` / ``llm.py``),
resolved once when the action is built at load time — so a missing env var fails
fast at startup, like a malformed ``auth:`` block, rather than silently at fire.
"""
from __future__ import annotations

import json
import logging
import os
import re
import urllib.request
from abc import ABC, abstractmethod
from typing import Any, Callable

log = logging.getLogger(__name__)

# Timeout (seconds) for an action's outbound HTTP call. An unreachable endpoint
# must not wedge the runner's threadpool slot indefinitely.
HTTP_TIMEOUT = 10

_ENV_RE = re.compile(r"^\$\{(\w+)\}$")


def _resolve_secret(value: Any) -> str:
    """Expand a ``${VAR}`` reference from the environment, else return as-is.

    Mirrors ``auth._resolve_secret`` / ``llm._resolve_secret``: a bare
    ``${VAR}`` reads the environment (raising if unset so a misconfigured action
    fails at load), anything else passes through unchanged."""
    s = str(value)
    m = _ENV_RE.match(s.strip())
    if m:
        env_val = os.environ.get(m.group(1))
        if env_val is None:
            raise ValueError(
                f"action config references environment variable {m.group(1)!r}, "
                "which is not set"
            )
        return env_val
    return s


class Action(ABC):
    """Base class for a trigger action.

    An action is constructed with its (already env-expanded) config mapping and
    fires with the trigger's ``event`` dict. ``fire`` runs off the event loop (the
    runner schedules it via ``asyncio.to_thread``) and may raise — the runner logs
    and moves on, so one failing action never kills the trigger.
    """

    def __init__(self, config: dict[str, Any]) -> None:
        self.config = config

    @abstractmethod
    def fire(self, event: dict) -> None:  # pragma: no cover - abstract
        ...


_ACTION_TYPES: dict[str, type[Action]] = {}


def register_action(type_name: str) -> Callable[[type[Action]], type[Action]]:
    """Decorator to register an action implementation under a type name.

    Same registry shape as ``@register_connector`` — a plain module-global map,
    populated eagerly on import (the built-ins register at the bottom of this
    module; a project's ``components/*.py`` could register its own the same way).
    """

    def deco(cls: type[Action]) -> type[Action]:
        _ACTION_TYPES[type_name] = cls
        return cls

    return deco


def get_action_type(type_name: str) -> type[Action]:
    if type_name not in _ACTION_TYPES:
        raise KeyError(
            f"Unknown action type '{type_name}'. Known: {known_action_types()}"
        )
    return _ACTION_TYPES[type_name]


def known_action_types() -> list[str]:
    return sorted(_ACTION_TYPES)


def validate_action_entry(raw: Any) -> str:
    """Structurally validate one ``actions:`` list entry, returning its type.

    The half of :func:`build_action` with no environment dependency — a mapping
    with a known ``type``. Split out so a *disabled* trigger can still fail-hard
    on a typo'd action type at load, while deferring ``${ENV_VAR}`` resolution
    (which may legitimately be unset until the trigger is enabled)."""
    if not isinstance(raw, dict):
        raise ValueError("each action must be a mapping with a 'type' key")
    type_name = str(raw.get("type", "")).strip()
    if not type_name:
        raise ValueError("action requires a 'type'")
    if type_name not in _ACTION_TYPES:
        raise ValueError(
            f"unknown action type {type_name!r} "
            f"(known: {', '.join(known_action_types()) or 'none'})"
        )
    return type_name


def build_action(raw: Any) -> Action:
    """Build one :class:`Action` from a trigger's ``actions:`` list entry.

    The entry is a mapping with a ``type`` key selecting the implementation; every
    other key is its config. ``${ENV_VAR}`` references in string values are
    expanded here (once, at load) so the action carries concrete secrets. Raises
    ``ValueError`` on a malformed entry or an unknown type — fail-at-startup, like
    the rest of config parsing.
    """
    type_name = validate_action_entry(raw)
    config = {
        k: (_resolve_secret(v) if isinstance(v, str) else v)
        for k, v in raw.items()
        if k != "type"
    }
    return _ACTION_TYPES[type_name](config)


def _post_json(url: str, payload: dict) -> None:
    """POST ``payload`` as JSON to ``url`` with a bounded timeout."""
    data = json.dumps(payload, default=str).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:  # noqa: S310
        resp.read()  # drain so the connection can be reused/closed cleanly


@register_action("webhook")
class WebhookAction(Action):
    """POST the raw event payload as JSON to a configured URL.

        actions:
          - type: webhook
            url: https://example.com/hook   # or ${HOOK_URL}
    """

    def fire(self, event: dict) -> None:
        url = str(self.config.get("url", "")).strip()
        if not url:
            raise ValueError("webhook action requires a 'url'")
        _post_json(url, event)


@register_action("slack")
class SlackAction(Action):
    """Post a readable message to a Slack incoming webhook.

        actions:
          - type: slack
            webhook_url: ${SLACK_WEBHOOK_URL}

    Slack incoming webhooks accept ``{"text": …}``; the text is formatted from the
    event (trigger message, condition, current value, row count, fired_at)."""

    def fire(self, event: dict) -> None:
        url = str(self.config.get("webhook_url") or self.config.get("url") or "").strip()
        if not url:
            raise ValueError("slack action requires a 'webhook_url'")
        _post_json(url, {"text": format_slack_message(event)})


def format_slack_message(event: dict) -> str:
    """Render a trigger event as a readable Slack message.

    A compact block: the trigger's message as the headline, then the condition
    that breached, the current observed value, the row count, and the fire time.
    """
    trigger = event.get("trigger", "trigger")
    message = event.get("message") or f"Trigger '{trigger}' fired"
    lines = [f":rotating_light: *{message}*"]
    when = event.get("when")
    if when:
        lines.append(f"Condition: `{when}`")
    lines.append(f"Current value: {event.get('value')}")
    lines.append(f"Rows: {event.get('rows_count')}")
    fired_at = event.get("fired_at")
    if fired_at:
        lines.append(f"Fired at: {fired_at}")
    return "\n".join(lines)
