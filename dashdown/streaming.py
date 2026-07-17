"""Shared poll loop for real-time streaming (fan-out).

Rather than one poll loop *per WebSocket connection* (N viewers of the same live
dashboard → N identical queries every interval), this module runs **one poll loop
per (query, connector, params)** that fans the result out to every subscriber — so
viewer count no longer multiplies DB/API load, and far fewer threadpool slots are
held (one `to_thread` per distinct live query, not per connection).

A `_Poller` owns the loop and a set of per-subscriber `asyncio.Queue`s; it
broadcasts a fresh `{columns, rows}` payload only when the result digest
changes. `StreamHub` reference-counts subscribers per key and stops a poller
once its last subscriber leaves. The whole thing lives on the server's event
loop; the blocking query runs off it via `asyncio.to_thread`.
"""
from __future__ import annotations

import asyncio
import json
import logging
import math
from typing import Any, Callable

from dashdown.data.base import QueryResult
from dashdown.python_query import run_python_query
from dashdown.render.pipeline import (
    _freeze_params,
    _substitute_params,
    get_python_query_def,
    get_query_def,
    payload_digest,
    serialize_result,
)

log = logging.getLogger(__name__)

# Sentinel pushed onto a subscriber's queue to wake its sender loop when the
# client disconnects — the queue otherwise only carries payload strings.
DISCONNECT = object()


def _json_default(obj: object) -> object:
    """JSON fallback. `serialize_result` already coerces cells to JSON-safe
    values; this is belt-and-suspenders for any stray NaN/inf/odd type."""
    if isinstance(obj, float) and (math.isnan(obj) or math.isinf(obj)):
        return None
    return str(obj)


class _Poller:
    """One poll loop for a single (query, connector, params) key, fanning each
    change out to all subscribed queues.

    The actual data fetch is a 0-arg ``fetch`` thunk returning a ``QueryResult``,
    so the poller is agnostic to *how* the rows are produced — a SQL connector
    query (``connector.query(final_sql)``) or a Python query
    (``run_python_query(spec, params, connectors)``). Both are sync and blocking;
    the poller runs them off the event loop via ``asyncio.to_thread``."""

    def __init__(
        self,
        fetch: Callable[[], QueryResult],
        query_name: str,
        interval: int,
    ) -> None:
        self.fetch = fetch
        self.query_name = query_name
        self.interval = interval
        self.subscribers: set[asyncio.Queue] = set()
        # Last *successful* payload text, replayed to late joiners so they paint
        # immediately instead of waiting up to one interval. Error frames don't
        # update this (a new subscriber shouldn't open on a stale error).
        self.latest: str | None = None
        self._last_digest: str | None = None
        self._last_error: str | None = None
        self._task: asyncio.Task | None = None

    def start(self) -> None:
        self._task = asyncio.create_task(self._run())

    def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            self._task = None

    def add(self, q: asyncio.Queue) -> None:
        self.subscribers.add(q)

    def remove(self, q: asyncio.Queue) -> None:
        self.subscribers.discard(q)

    def _broadcast(self, text: str, *, cache: bool) -> None:
        if cache:
            self.latest = text
        for q in list(self.subscribers):
            q.put_nowait(text)

    async def _run(self) -> None:
        while True:
            try:
                # Bypass the result cache (live data must be current) and run the
                # sync, lock-guarded fetch off the event loop.
                result = await asyncio.to_thread(self.fetch)
                payload = serialize_result(result)
                payload["query"] = self.query_name
                digest = payload_digest(payload)
                if digest != self._last_digest:
                    self._last_digest = digest
                    self._broadcast(
                        json.dumps(payload, default=_json_default), cache=True
                    )
                self._last_error = None
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001 — transient; keep polling
                # Self-healing: a failing poll (e.g. a rate-limited API) logs one
                # concise line per *distinct* error and notifies subscribers only
                # on the transition; the loop retries next tick.
                msg = f"{type(e).__name__}: {e}"
                if msg != self._last_error:
                    log.warning(
                        "Live query %r failed (retrying every %ss): %s",
                        self.query_name,
                        self.interval,
                        msg,
                    )
                    self._last_error = msg
                    self._broadcast(
                        json.dumps({"query": self.query_name, "error": msg}),
                        cache=False,
                    )
            await asyncio.sleep(self.interval)


def build_query_fetch(
    project: Any, query_name: str, connector_name: str, params: dict[str, str]
) -> tuple[Callable[[], QueryResult], Any] | None:
    """Build the ``(fetch, poller_key)`` pair for polling one registered query.

    THE single definition of both the python-first fetch thunk and the poller
    key shape — shared by the WS data endpoint and the trigger runner, so a live
    socket and a trigger watching the same query+connector+params provably reuse
    one poll loop (two hand-kept copies of this logic would drift and silently
    double the query load). Returns ``None`` when the query/connector can't be
    resolved (callers decide whether that's a closed socket or a logged skip).
    """
    py_spec = get_python_query_def(query_name, connector_name)
    if py_spec is not None:
        all_params = dict(params)
        fetch: Callable[[], QueryResult] = lambda: run_python_query(  # noqa: E731
            py_spec, all_params, project.connectors
        )
    else:
        query_def = get_query_def(query_name, connector_name)
        connector = project.connectors.get(connector_name)
        if query_def is None or connector is None:
            return None
        sql, default_params, _ = query_def
        all_params = {**default_params, **params}
        final_sql = _substitute_params(sql, all_params)
        fetch = lambda: connector.query(final_sql)  # noqa: E731
    return fetch, (query_name, connector_name, _freeze_params(all_params))


class StreamHub:
    """Reference-counted registry of pollers keyed by (query, connector, params)."""

    def __init__(self) -> None:
        self._pollers: dict[Any, _Poller] = {}

    def subscribe(
        self,
        key: Any,
        fetch: Callable[[], QueryResult],
        query_name: str,
        interval: int,
    ) -> tuple[_Poller, asyncio.Queue]:
        """Join (or create) the poller for ``key`` and return it plus a fresh
        queue that will receive its broadcasts. ``fetch`` is the 0-arg thunk the
        poller runs each interval (SQL or Python query). Synchronous — runs to
        completion on the event loop with no await, so there's no create/teardown
        race. ``fetch`` is only used when *creating* a poller; a key with an
        existing poller reuses it (subscribers share one fetch). A shared poller
        runs at the **fastest** interval any subscriber asked for: a live chart
        (5s) joining a trigger's slow poller (300s) speeds it up on the next
        cycle — first-subscriber-wins would silently starve later ones. (It
        stays fast after the fast subscriber leaves; the next full teardown
        resets it.)"""
        poller = self._pollers.get(key)
        if poller is None:
            poller = _Poller(fetch, query_name, interval)
            self._pollers[key] = poller
            poller.start()
        elif interval < poller.interval:
            poller.interval = interval
        q: asyncio.Queue = asyncio.Queue()
        poller.add(q)
        return poller, q

    def unsubscribe(self, key: Any, q: asyncio.Queue) -> None:
        poller = self._pollers.get(key)
        if poller is None:
            return
        poller.remove(q)
        if not poller.subscribers:
            poller.stop()
            self._pollers.pop(key, None)

    def reset(self) -> None:
        """Stop every poller — used for test isolation between cases."""
        for poller in list(self._pollers.values()):
            try:
                poller.stop()
            except Exception:
                pass
        self._pollers.clear()

    @property
    def active(self) -> int:
        """Number of live pollers (distinct keys) — handy for tests/metrics."""
        return len(self._pollers)


async def watch_disconnect(websocket: Any, queue: asyncio.Queue) -> None:
    """Read from the socket until the client disconnects, then wake the sender.

    A push-only WebSocket that blocks on ``queue.get()`` wouldn't otherwise
    notice a client going away until the next broadcast — for a rarely-changing
    query that could be never. Reading in this side-task surfaces the disconnect
    promptly; we then drop a sentinel on the subscriber's queue so the sender
    loop unblocks and cleans up.
    """
    try:
        while True:
            message = await websocket.receive()
            if message.get("type") == "websocket.disconnect":
                break
    except Exception:
        # WebSocketDisconnect (or any receive error) means the client is gone.
        pass
    finally:
        queue.put_nowait(DISCONNECT)


# Process-wide hub. Consistent with the rest of the framework's module-global
# caches (query defs, results); one per server process.
hub = StreamHub()
