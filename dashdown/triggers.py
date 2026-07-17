"""Triggers — conditions on query results that fire actions (the *Push* surface).

A trigger is a ``triggers/*.yml`` file (name = file stem) that names a **query**
(a shared-library or Python query), a **condition** on its result, and a list of
**actions** to fire when the condition breaches:

    # triggers/repeat-rate.yml
    query: kpi.repeat_rate      # library or python query name
    connector: demo             # optional → project default
    interval: 300               # seconds between evaluations (min 5)
    when: "value < 0.12"        # value|rows <op> number  — parsed by regex, NEVER eval
    message: "Repeat-purchase rate slipped"
    cooldown: 3600              # optional re-fire seconds while still breached
    params: {}                  # optional fixed query params
    enabled: true
    actions:
      - {type: slack, webhook_url: "${SLACK_WEBHOOK_URL}"}

**Evaluation rides the existing streaming poll loop.** For each enabled trigger
the :class:`TriggerRunner` gets its fetch thunk + poller key from
``streaming.build_query_fetch`` — the *same* builder the WebSocket data endpoint
uses — and subscribes it into ``streaming.hub``, the same fan-out ``_Poller``
that serves live sockets. So N viewers *and* a trigger on the same
query+connector+params provably share one query per interval (the hub runs a
shared poller at the fastest subscriber's cadence). The runner is
**socket-less**: it consumes the poller's queue in an asyncio task instead of
forwarding to a WebSocket, replaying the poller's latest snapshot on join so a
steadily-breached value fires immediately rather than waiting for a change.

**Firing is edge-triggered.** An action fires on a clear→breach transition, and
again every ``cooldown`` seconds while the condition stays breached (no
``cooldown`` → transition-only). Actions run in ``asyncio.to_thread``; an action
raising is logged, never fatal.

The condition grammar is deliberately tiny and **regex-parsed, never
``eval``/``exec``** — ``value <op> number`` (first cell of the first row, coerced
to float) or ``rows <op> number`` (row count), with ``op`` one of
``< <= > >= == !=``.
"""
from __future__ import annotations

import asyncio
import json
import logging
import operator
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import yaml

from dashdown.actions import Action, build_action, validate_action_entry
from dashdown.data.base import QueryResult
from dashdown.streaming import (
    DISCONNECT,
    build_query_fetch,
    hub as stream_hub,
)

log = logging.getLogger(__name__)

# Minimum poll interval (seconds) a trigger may specify — a floor on how often we
# re-run its query, matching the streaming endpoint's protective flooring.
MIN_TRIGGER_INTERVAL = 5
# Default interval when a trigger omits ``interval:`` (5 minutes — a sensible
# cadence for a metric watch, not a real-time stream).
DEFAULT_TRIGGER_INTERVAL = 300
# Rows carried in an event's ``sample_rows`` (readable context for an action).
MAX_SAMPLE_ROWS = 10


# --------------------------------------------------------------------------- #
# Condition grammar (regex, never eval)
# --------------------------------------------------------------------------- #
_OPS: dict[str, Callable[[Any, Any], bool]] = {
    "<": operator.lt,
    "<=": operator.le,
    ">": operator.gt,
    ">=": operator.ge,
    "==": operator.eq,
    "!=": operator.ne,
}

# Two-char operators come first in the alternation so ``<=`` doesn't match ``<``.
_CONDITION_RE = re.compile(
    r"^\s*(value|rows)\s*(<=|>=|==|!=|<|>)\s*(-?\d+(?:\.\d+)?)\s*$"
)


@dataclass
class Condition:
    """A parsed trigger condition — ``subject <op> threshold``.

    ``subject`` is ``"value"`` (first cell of the first result row, coerced to
    float) or ``"rows"`` (the row count). ``_warned`` is a one-shot latch so a
    query that keeps returning an empty/non-numeric ``value`` logs a single
    warning per trigger, not one per poll."""

    subject: str
    op: str
    threshold: float
    raw: str
    _warned: bool = field(default=False, repr=False, compare=False)


def parse_condition(text: Any) -> Condition:
    """Parse a ``when:`` string into a :class:`Condition`, or raise ``ValueError``.

    Regex-only — no ``eval``/``exec``. Accepts ``value <op> N`` and ``rows <op>
    N`` with ``op`` in ``< <= > >= == !=`` and ``N`` an int or float."""
    if not isinstance(text, str):
        raise ValueError("trigger condition (when:) must be a string")
    m = _CONDITION_RE.match(text)
    if not m:
        raise ValueError(
            f"invalid trigger condition {text!r}; expected 'value <op> N' or "
            "'rows <op> N' with op one of < <= > >= == !="
        )
    subject, op, num = m.group(1), m.group(2), m.group(3)
    return Condition(subject=subject, op=op, threshold=float(num), raw=text.strip())


def _coerce_number(cell: Any) -> float | None:
    """Coerce a result cell to ``float``, or ``None`` if it isn't numeric."""
    if cell is None or isinstance(cell, bool):
        return None
    if isinstance(cell, (int, float)):
        return float(cell)
    try:
        return float(str(cell).strip())
    except (ValueError, TypeError):
        return None


def evaluate(cond: Condition, result: QueryResult) -> tuple[bool, float | int]:
    """Evaluate ``cond`` against ``result``, returning ``(breached, observed)``.

    For ``rows`` the observed value is the row count (always numeric). For
    ``value`` it's the first cell of the first row coerced to float; an empty or
    non-numeric result is **not** breached (returns ``(False, 0)``) and logs a
    single warning per trigger."""
    if cond.subject == "rows":
        observed = len(result.rows)
        return bool(_OPS[cond.op](observed, cond.threshold)), observed

    observed_num = _first_cell_number(result)
    if observed_num is None:
        if not cond._warned:
            log.warning(
                "Trigger condition %r: query result is empty or non-numeric; "
                "treating as not-breached",
                cond.raw,
            )
            cond._warned = True
        return False, 0
    return bool(_OPS[cond.op](observed_num, cond.threshold)), observed_num


def _first_cell_number(result: QueryResult) -> float | None:
    if not result.rows or not result.rows[0]:
        return None
    return _coerce_number(result.rows[0][0])


# --------------------------------------------------------------------------- #
# Trigger spec + loader
# --------------------------------------------------------------------------- #
@dataclass
class TriggerSpec:
    """One parsed ``triggers/*.yml`` file (name = stem)."""

    name: str
    query: str
    when: str
    condition: Condition
    interval: int = DEFAULT_TRIGGER_INTERVAL
    connector: str | None = None
    message: str = ""
    cooldown: int | None = None
    params: dict[str, str] = field(default_factory=dict)
    enabled: bool = True
    actions: list[Action] = field(default_factory=list)


def parse_trigger(raw: Any, name: str) -> TriggerSpec:
    """Build a :class:`TriggerSpec` from a parsed YAML mapping.

    Raises ``ValueError`` on anything malformed (missing/invalid ``query`` or
    ``when``, out-of-range ``interval``/``cooldown``, non-mapping ``params``,
    unknown action type) — fail-at-startup, exactly like ``auth:`` parsing.

    A **disabled** trigger still has every action's *structure* validated (a
    typo'd ``type`` fails at load regardless), but skips building them — action
    build is where ``${ENV_VAR}`` expansion happens, and a scaffolded/example
    trigger shipped ``enabled: false`` must load cleanly even when its env vars
    aren't set yet. Flipping ``enabled`` re-parses (fresh start or dev reload),
    so env resolution fail-hards the moment the trigger actually goes live."""
    if not isinstance(raw, dict):
        raise ValueError(f"trigger {name!r} must be a mapping")

    query = raw.get("query")
    if not isinstance(query, str) or not query.strip():
        raise ValueError(f"trigger {name!r} requires a non-empty 'query' name")

    when = raw.get("when")
    if not isinstance(when, str) or not when.strip():
        raise ValueError(f"trigger {name!r} requires a 'when' condition")
    condition = parse_condition(when)

    interval = raw.get("interval", DEFAULT_TRIGGER_INTERVAL)
    if isinstance(interval, bool) or not isinstance(interval, int) or interval < MIN_TRIGGER_INTERVAL:
        raise ValueError(
            f"trigger {name!r} interval must be an integer >= {MIN_TRIGGER_INTERVAL} seconds"
        )

    connector = raw.get("connector")
    if connector is not None:
        if not isinstance(connector, str) or not connector.strip():
            raise ValueError(f"trigger {name!r} connector must be a non-empty string")
        connector = connector.strip()

    cooldown = raw.get("cooldown")
    if cooldown is not None:
        if isinstance(cooldown, bool) or not isinstance(cooldown, int) or cooldown <= 0:
            raise ValueError(
                f"trigger {name!r} cooldown must be a positive integer (seconds)"
            )

    params_raw = raw.get("params") or {}
    if not isinstance(params_raw, dict):
        raise ValueError(f"trigger {name!r} params must be a mapping")
    params = {str(k): str(v) for k, v in params_raw.items()}

    enabled = raw.get("enabled", True)
    if not isinstance(enabled, bool):
        raise ValueError(f"trigger {name!r} enabled must be a boolean")

    actions_raw = raw.get("actions") or []
    if not isinstance(actions_raw, list):
        raise ValueError(f"trigger {name!r} actions must be a list")
    if enabled:
        actions = [build_action(a) for a in actions_raw]
    else:
        # Structure (mapping shape, known type) is validated even when disabled
        # so a typo'd action fails at load; only ${ENV_VAR} resolution — which
        # may legitimately be unset until the trigger goes live — is deferred.
        for a in actions_raw:
            validate_action_entry(a)
        actions = []

    return TriggerSpec(
        name=name,
        query=query.strip(),
        when=when.strip(),
        condition=condition,
        interval=interval,
        connector=connector,
        message=str(raw.get("message", "")),
        cooldown=cooldown,
        params=params,
        enabled=enabled,
        actions=actions,
    )


_TRIGGER_EXTENSIONS = (".yml", ".yaml")


def load_triggers(triggers_dir: Path) -> dict[str, TriggerSpec]:
    """Scan ``triggers_dir`` for ``*.yml`` / ``*.yaml`` into ``{name: TriggerSpec}``.

    Name = file stem (``triggers/repeat-rate.yml`` → ``repeat-rate``). An
    absent/empty directory yields ``{}``. A malformed file, an unknown action
    type, or a duplicate stem (``foo.yml`` + ``foo.yaml``) raises ``ValueError``
    — fail-at-startup, like the shared query library."""
    if not triggers_dir.is_dir():
        return {}

    out: dict[str, TriggerSpec] = {}
    sources: dict[str, Path] = {}
    for path in sorted(triggers_dir.iterdir()):
        if not path.is_file() or path.suffix.lower() not in _TRIGGER_EXTENSIONS:
            continue
        name = path.stem
        if name in out:
            raise ValueError(
                f"duplicate trigger name {name!r}: defined by both "
                f"{sources[name].name} and {path.name} under {triggers_dir}"
            )
        try:
            raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError as exc:
            raise ValueError(f"trigger {name!r} is not valid YAML: {exc}") from exc
        out[name] = parse_trigger(raw, name)
        sources[name] = path

    return out


# --------------------------------------------------------------------------- #
# Runner
# --------------------------------------------------------------------------- #
@dataclass
class _TriggerState:
    """Per-trigger runtime state carried across poll frames."""

    spec: TriggerSpec
    key: Any
    queue: asyncio.Queue
    breached: bool = False
    last_fired_at: float | None = None
    task: asyncio.Task | None = None


class TriggerRunner:
    """Runs every enabled trigger against the shared streaming poll loop.

    Constructed with the live :class:`~dashdown.project.Project`; :meth:`start`
    subscribes each enabled trigger into ``streaming.hub`` and spawns an asyncio
    task consuming its queue. :meth:`stop` unsubscribes and cancels every task —
    called on app shutdown and before a project reload. Must be started from
    within a running event loop (it creates tasks)."""

    def __init__(self, project: Any) -> None:
        self.project = project
        self._states: list[_TriggerState] = []
        self._started = False

    def start(self) -> None:
        if self._started:
            return
        triggers = getattr(self.project, "triggers", {}) or {}
        for spec in triggers.values():
            if not spec.enabled:
                continue
            try:
                state = self._subscribe(spec)
            except Exception as e:  # noqa: BLE001 — one bad trigger shouldn't sink the rest
                log.error("Failed to start trigger %r: %s", spec.name, e)
                continue
            if state is None:
                continue
            state.task = asyncio.create_task(self._consume(state))
            self._states.append(state)
        self._started = True

    def stop(self) -> None:
        for state in self._states:
            if state.task is not None:
                state.task.cancel()
            stream_hub.unsubscribe(state.key, state.queue)
        self._states.clear()
        self._started = False

    @property
    def active(self) -> int:
        """Number of subscribed triggers (handy for tests/metrics)."""
        return len(self._states)

    def _subscribe(self, spec: TriggerSpec) -> _TriggerState | None:
        """Subscribe one trigger into the hub via the shared fetch builder.
        Returns ``None`` (logged) if the referenced query can't be resolved."""
        connector = spec.connector or getattr(self.project, "default_connector", None) or ""
        built = build_query_fetch(self.project, spec.query, connector, dict(spec.params))
        if built is None:
            log.warning(
                "Trigger %r references unknown query %r on connector %r; skipping",
                spec.name,
                spec.query,
                connector or "(none)",
            )
            return None
        fetch, key = built

        interval = max(MIN_TRIGGER_INTERVAL, spec.interval)
        poller, queue = stream_hub.subscribe(key, fetch, spec.query, interval)
        # Replay the poller's current payload (the WS endpoint does the same):
        # broadcasts are digest-gated, so joining an existing poller whose value
        # sits *constantly* breached would otherwise never deliver a frame — and
        # the alert would never fire until the number happened to change.
        if poller.latest is not None:
            queue.put_nowait(poller.latest)
        return _TriggerState(spec=spec, key=key, queue=queue)

    async def _consume(self, state: _TriggerState) -> None:
        """Drain one trigger's queue, handling each frame until cancelled."""
        while True:
            item = await state.queue.get()
            if item is DISCONNECT:  # never sent socket-less, but stay defensive
                break
            try:
                payload = json.loads(item)
            except (ValueError, TypeError):
                continue
            if not isinstance(payload, dict) or "error" in payload:
                continue  # skip transient-error frames
            event = self._handle_frame(state, payload)
            if event is not None:
                await self._dispatch(state.spec, event)

    def _handle_frame(
        self, state: _TriggerState, payload: dict, *, now: float | None = None
    ) -> dict | None:
        """Evaluate one poll frame; return an event dict to fire, or ``None``.

        Edge-triggered: fires on a clear→breach transition, and again once every
        ``cooldown`` seconds while still breached. Updates ``state.breached`` and,
        on a fire, ``state.last_fired_at``. Pure and synchronous (``now`` is
        injectable) so the transition/cooldown logic is unit-testable without real
        sleeps."""
        now = time.monotonic() if now is None else now
        result = QueryResult(
            columns=list(payload.get("columns", [])),
            rows=list(payload.get("rows", [])),
        )
        breached, value = evaluate(state.spec.condition, result)

        should_fire = False
        if breached:
            if not state.breached:
                should_fire = True  # clear → breach transition
            elif (
                state.spec.cooldown is not None
                and state.last_fired_at is not None
                and now - state.last_fired_at >= state.spec.cooldown
            ):
                should_fire = True  # re-fire while still breached

        state.breached = breached
        if not should_fire:
            return None
        state.last_fired_at = now
        return _build_event(state.spec, result, value)

    async def _dispatch(self, spec: TriggerSpec, event: dict) -> None:
        """Fire every action off the event loop; log (never raise) on failure."""
        for action in spec.actions:
            try:
                await asyncio.to_thread(action.fire, event)
            except Exception as e:  # noqa: BLE001 — an action failure is never fatal
                log.error(
                    "Trigger %r action %s failed: %s",
                    spec.name,
                    type(action).__name__,
                    e,
                )


def _build_event(spec: TriggerSpec, result: QueryResult, value: float | int) -> dict:
    """Assemble the answer-shaped event payload handed to each action."""
    records = result.to_records()
    return {
        "trigger": spec.name,
        "message": spec.message,
        "when": spec.when,
        "value": value,
        "rows_count": len(result.rows),
        "columns": list(result.columns),
        "sample_rows": records[:MAX_SAMPLE_ROWS],
        "fired_at": datetime.now(timezone.utc).isoformat(),
    }
