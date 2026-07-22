"""AI edit-mode session layer: one agent run at a time, streamed to the panel.

:class:`EditHub` (module-global ``edit_hub``, the ``streaming.hub`` precedent)
owns the lifecycle of :class:`EditRun`:

- **Single-flight**: one run per process. A second start while one is active
  is refused (HTTP 409 in server.py); any browser tab can *attach* to the
  active run instead via the replay buffer.
- **Streaming**: the subprocess's stdout is read line-by-line, normalized
  through the preset's parser (agent_presets.normalize_line) into events, and
  fanned out to subscriber queues — with a capped ring buffer so a late (or
  reloaded) panel replays the transcript, deduping by ``seq``.
- **Snapshot + undo**: before the agent runs, the small author files
  (``pages/ queries/ components/ semantic/ dashdown.yaml sources.yaml``) are
  snapshotted under ``.dashdown/edit-undo/<run_id>/``; the post-run diff of
  that snapshot yields the changed/created/deleted file lists (tool-agnostic —
  works for every agent, no output parsing needed), and undo restores it.
  One undo slot (the last run); wiped when the next run starts.
- **Hygiene**: no shell, ``start_new_session=True`` + process-group SIGTERM →
  SIGKILL on cancel/timeout/server-shutdown, stderr kept as a bounded tail,
  and a cheap post-run verify (does the project still load?).
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import shutil
import signal
import time
import uuid
from collections import deque
from pathlib import Path
from typing import Any

from dashdown.agent_presets import normalize_line
from dashdown.edit import EditRuntime, append_audit_log, build_edit_prompt

log = logging.getLogger(__name__)

# The author-file surface the snapshot/undo covers. data/ and assets/ are
# deliberately excluded (potentially huge, and not what an edit run should be
# rewriting); documented in the docs page.
SNAPSHOT_DIRS = ("pages", "queries", "components", "semantic")
SNAPSHOT_FILES = ("dashdown.yaml", "sources.yaml")

# Sentinel pushed on subscriber queues when the hub drops them (server
# shutdown), so a WS sender loop unblocks (the streaming.py DISCONNECT idea).
CLOSED = object()

_STDERR_TAIL_LINES = 40
_KILL_GRACE_SECONDS = 5


def _tree_digest(root: Path) -> dict[str, str]:
    """Relative path -> content hash for every file under the snapshot surface.

    Content hashes (not mtimes): editors and agents rewrite files with
    unchanged content; only real changes should reach the changed-files list.
    The covered trees are small author files, so hashing is cheap.
    """
    out: dict[str, str] = {}
    for name in SNAPSHOT_FILES:
        f = root / name
        if f.is_file():
            out[name] = hashlib.sha256(f.read_bytes()).hexdigest()
    for dirname in SNAPSHOT_DIRS:
        d = root / dirname
        if not d.is_dir():
            continue
        for f in sorted(d.rglob("*")):
            if not f.is_file() or "__pycache__" in f.parts:
                continue
            rel = f.relative_to(root).as_posix()
            out[rel] = hashlib.sha256(f.read_bytes()).hexdigest()
    return out


class EditRun:
    """One agent invocation: subprocess + transcript + snapshot."""

    def __init__(
        self,
        runtime: EditRuntime,
        prompt: str,
        *,
        page: str | None = None,
        page_file: str | None = None,
        params: dict[str, str] | None = None,
        session_id: str | None = None,
        on_event: Any = None,
    ) -> None:
        self.runtime = runtime
        self.prompt = prompt
        self.page = page
        self.page_file = page_file
        self.params = dict(params or {})
        self.resume_session_id = session_id
        # Hub fan-out hook: called as on_event(run_id, seq, event) for every
        # emitted event, so subscribers live on the hub (one WS survives across
        # runs) while the replay buffer stays per-run.
        self._on_event = on_event

        self.run_id = uuid.uuid4().hex[:12]
        self.state = "starting"  # starting|running|done|failed|cancelled|timeout
        self.started_at = time.time()
        self.exit_code: int | None = None
        self.session_id: str | None = None  # captured from the agent's output
        self.changed_files: list[str] = []
        self.created_files: list[str] = []
        self.deleted_files: list[str] = []
        self.config_changed = False
        self.verify: dict[str, Any] | None = None
        self.stderr_tail: deque[str] = deque(maxlen=_STDERR_TAIL_LINES)
        self.truncated = False

        self._seq = 0
        self._events: deque[tuple[int, dict[str, Any]]] = deque(
            maxlen=runtime.max_events
        )
        self._proc: asyncio.subprocess.Process | None = None
        self._task: asyncio.Task | None = None
        self._watchdog: asyncio.Task | None = None
        self._pre_digest: dict[str, str] = {}
        self._cancel_reason: str | None = None

    # ------------------------------------------------------------------ #
    # Event fan-out
    # ------------------------------------------------------------------ #
    def _emit(self, event: dict[str, Any]) -> None:
        self._seq += 1
        if len(self._events) == self._events.maxlen:
            self.truncated = True
        self._events.append((self._seq, event))
        if self._on_event is not None:
            self._on_event(self.run_id, self._seq, event)

    def replay(self) -> list[dict[str, Any]]:
        """The buffered transcript as WS envelopes (a late/reloaded panel
        replays these, deduping by seq)."""
        return [
            {"run_id": self.run_id, "seq": seq, "event": event}
            for seq, event in self._events
        ]

    # ------------------------------------------------------------------ #
    # Introspection
    # ------------------------------------------------------------------ #
    @property
    def active(self) -> bool:
        return self.state in ("starting", "running")

    def summary(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "state": self.state,
            "started_at": int(self.started_at),
            "prompt": self.prompt,
            "page": self.page,
            "exit_code": self.exit_code,
            "changed_files": self.changed_files,
            "created_files": self.created_files,
            "deleted_files": self.deleted_files,
            "config_changed": self.config_changed,
            "verify": self.verify,
            "resume_available": self.session_id is not None,
            "truncated": self.truncated,
        }

    # ------------------------------------------------------------------ #
    # Snapshot / undo
    # ------------------------------------------------------------------ #
    def _snapshot_dir(self) -> Path:
        return self.runtime.project_root / ".dashdown" / "edit-undo" / self.run_id

    def take_snapshot(self) -> None:
        snap = self._snapshot_dir()
        if snap.exists():
            shutil.rmtree(snap)
        snap.mkdir(parents=True)
        root = self.runtime.project_root
        for name in SNAPSHOT_FILES:
            f = root / name
            if f.is_file():
                shutil.copy2(f, snap / name)
        for dirname in SNAPSHOT_DIRS:
            d = root / dirname
            if d.is_dir():
                shutil.copytree(
                    d, snap / dirname, ignore=shutil.ignore_patterns("__pycache__")
                )
        self._pre_digest = _tree_digest(root)

    def _diff_snapshot(self) -> None:
        post = _tree_digest(self.runtime.project_root)
        pre = self._pre_digest
        self.changed_files = sorted(
            p for p in post if p in pre and post[p] != pre[p]
        )
        self.created_files = sorted(p for p in post if p not in pre)
        self.deleted_files = sorted(p for p in pre if p not in post)
        touched = set(self.changed_files) | set(self.created_files) | set(
            self.deleted_files
        )
        self.config_changed = any(p in SNAPSHOT_FILES for p in touched)

    def undo(self) -> dict[str, list[str]]:
        """Restore the pre-run snapshot over everything this run touched.
        Raises RuntimeError when the snapshot is gone (a later run wiped it)."""
        snap = self._snapshot_dir()
        if not snap.is_dir():
            raise RuntimeError("no undo snapshot for this run (superseded?)")
        root = self.runtime.project_root
        restored: list[str] = []
        deleted: list[str] = []
        for rel in self.changed_files + self.deleted_files:
            src = snap / rel
            if not src.is_file():
                continue
            dest = root / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dest)
            restored.append(rel)
        for rel in self.created_files:
            f = root / rel
            if f.is_file():
                f.unlink()
                deleted.append(rel)
        return {"restored": restored, "deleted": deleted}

    def drop_snapshot(self) -> None:
        snap = self._snapshot_dir()
        if snap.is_dir():
            shutil.rmtree(snap, ignore_errors=True)

    # ------------------------------------------------------------------ #
    # Subprocess lifecycle
    # ------------------------------------------------------------------ #
    def start(self) -> None:
        self._task = asyncio.get_running_loop().create_task(self._run())

    async def _run(self) -> None:
        runtime = self.runtime
        assert runtime.preset is not None
        full_prompt = build_edit_prompt(
            runtime, self.prompt, page_file=self.page_file, params=self.params
        )
        argv = runtime.build_argv(full_prompt, session_id=self.resume_session_id)
        env = {**os.environ, **runtime.preset.env}
        self._emit({"type": "status", "state": "starting"})
        try:
            self._proc = await asyncio.create_subprocess_exec(
                *argv,
                cwd=str(runtime.project_root),
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
                start_new_session=True,  # own process group → killpg reaches grandchildren
            )
        except OSError as e:
            self.state = "failed"
            self._emit(
                {"type": "error", "message": f"failed to start {argv[0]!r}: {e}"}
            )
            self._finish(exit_code=None)
            return

        if not self.active:
            # A cancel landed while the subprocess was spawning — _kill() was a
            # no-op then (no proc yet), so kill now and keep the cancelled state.
            self._kill()
        else:
            self.state = "running"
            self._emit({"type": "status", "state": "running"})
        self._watchdog = asyncio.get_running_loop().create_task(self._timeout_watch())

        loop = asyncio.get_running_loop()
        # Start BOTH pipe readers before feeding stdin: a prompt larger than
        # the OS pipe buffer would otherwise deadlock against a child that
        # fills its own stdout/stderr before consuming stdin (nobody draining
        # either side). The stdin feed runs as its own task for the same
        # reason.
        stderr_task = loop.create_task(self._read_stderr())
        assert self._proc.stdin is not None
        stdin_task = None
        if runtime.preset.prompt_via == "stdin":
            stdin_task = loop.create_task(self._feed_stdin(full_prompt))
        else:
            self._proc.stdin.close()

        parser = runtime.preset.parser
        assert self._proc.stdout is not None
        while True:
            line = await self._proc.stdout.readline()
            if not line:
                break
            for event in normalize_line(parser, line.decode("utf-8", "replace")):
                if event.get("type") == "session":
                    # Captured for follow-up turns; not a viewer-facing event.
                    self.session_id = event.get("session_id")
                    continue
                self._emit(event)

        exit_code = await self._proc.wait()
        await stderr_task
        if stdin_task is not None:
            # Process exit breaks the pipe, so a still-blocked drain raises
            # (handled inside) rather than hanging; await for a clean join.
            await stdin_task
        if self._watchdog is not None:
            self._watchdog.cancel()

        if self.state == "running":
            self.state = "done" if exit_code == 0 else "failed"
        # The verify loads the whole project (connectors included) — blocking,
        # so off the event loop like every other blocking call in the server.
        self.verify = await asyncio.to_thread(self._verify_project)
        self._finish(exit_code=exit_code)

    async def _feed_stdin(self, prompt: str) -> None:
        assert self._proc is not None and self._proc.stdin is not None
        try:
            self._proc.stdin.write(prompt.encode("utf-8"))
            await self._proc.stdin.drain()
        except (ConnectionResetError, BrokenPipeError):
            pass
        finally:
            try:
                self._proc.stdin.close()
            except (ConnectionResetError, BrokenPipeError):  # pragma: no cover
                pass

    async def _read_stderr(self) -> None:
        assert self._proc is not None and self._proc.stderr is not None
        while True:
            line = await self._proc.stderr.readline()
            if not line:
                return
            self.stderr_tail.append(line.decode("utf-8", "replace").rstrip("\r\n"))

    async def _timeout_watch(self) -> None:
        try:
            await asyncio.sleep(self.runtime.timeout)
        except asyncio.CancelledError:
            return
        self.state = "timeout"
        self._cancel_reason = f"timed out after {self.runtime.timeout}s"
        self._kill()

    def _finish(self, *, exit_code: int | None) -> None:
        self.exit_code = exit_code
        self._diff_snapshot()
        result: dict[str, Any] = {
            "type": "result",
            "ok": self.state == "done",
            "state": self.state,
            "exit_code": exit_code,
            "changed_files": self.changed_files,
            "created_files": self.created_files,
            "deleted_files": self.deleted_files,
            "config_changed": self.config_changed,
            "verify": self.verify,
            "undo_available": bool(
                self.changed_files or self.created_files or self.deleted_files
            ),
            "resume_available": self.session_id is not None,
            "truncated": self.truncated,
            "duration_ms": int((time.time() - self.started_at) * 1000),
        }
        if self._cancel_reason:
            result["reason"] = self._cancel_reason
        if self.state != "done" and self.stderr_tail:
            result["stderr_tail"] = "\n".join(self.stderr_tail)
        self._emit(result)
        append_audit_log(
            self.runtime.project_root,
            {
                "run_id": self.run_id,
                "agent": self.runtime.preset.name if self.runtime.preset else None,
                "prompt": self.prompt,
                "page": self.page,
                "state": self.state,
                "exit_code": exit_code,
                "changed_files": self.changed_files,
                "created_files": self.created_files,
                "deleted_files": self.deleted_files,
            },
        )

    def _verify_project(self) -> dict[str, Any]:
        """Cheap post-run verify: does the project still load? A supplement to
        the agent's own `dashdown check` loop, not a substitute — it catches a
        broken dashdown.yaml/sources.yaml/query-library even when the agent
        skipped verification."""
        from dashdown.project import load_project

        try:
            proj = load_project(self.runtime.project_root)
            proj.close()
            return {"ok": True}
        except Exception as e:  # noqa: BLE001 — the error IS the payload
            return {"ok": False, "error": f"{type(e).__name__}: {e}"}

    def _kill(self) -> None:
        proc = self._proc
        if proc is None or proc.returncode is not None:
            return
        try:
            pgid = os.getpgid(proc.pid)
        except ProcessLookupError:
            return

        try:
            os.killpg(pgid, signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            return

        async def _escalate() -> None:
            try:
                await asyncio.wait_for(proc.wait(), timeout=_KILL_GRACE_SECONDS)
            except asyncio.TimeoutError:
                try:
                    os.killpg(pgid, signal.SIGKILL)
                except (ProcessLookupError, PermissionError):
                    pass

        asyncio.get_running_loop().create_task(_escalate())

    def cancel(self) -> None:
        if not self.active:
            return
        self.state = "cancelled"
        self._cancel_reason = "cancelled by the author"
        self._kill()

    async def shutdown(self) -> None:
        """Server is stopping: kill the subprocess."""
        if self.active:
            self.state = "cancelled"
            self._cancel_reason = "server shutdown"
            self._kill()


class EditHub:
    """Single-flight owner of the current (and last) run, and the WS fan-out."""

    def __init__(self) -> None:
        self.current: EditRun | None = None
        self._subscribers: set[asyncio.Queue] = set()

    # ------------------------------------------------------------------ #
    # Fan-out (subscribers live here so one WS survives across runs)
    # ------------------------------------------------------------------ #
    def _fanout(self, run_id: str, seq: int, event: dict[str, Any]) -> None:
        envelope = {"run_id": run_id, "seq": seq, "event": event}
        for q in list(self._subscribers):
            q.put_nowait(envelope)

    def subscribe(self) -> tuple[list[dict[str, Any]], asyncio.Queue]:
        """Replay-then-live: the current run's buffered transcript plus a queue
        for everything that follows (including future runs). Synchronous — no
        await between the replay snapshot and the subscription, so no event can
        fall in the gap."""
        q: asyncio.Queue = asyncio.Queue()
        replay = self.current.replay() if self.current is not None else []
        self._subscribers.add(q)
        return replay, q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        self._subscribers.discard(q)

    # ------------------------------------------------------------------ #
    # Run lifecycle
    # ------------------------------------------------------------------ #
    def start_run(
        self,
        runtime: EditRuntime,
        prompt: str,
        *,
        page: str | None = None,
        page_file: str | None = None,
        params: dict[str, str] | None = None,
        resume: bool = False,
    ) -> EditRun:
        """Create + start a run. Raises RuntimeError when one is active
        (server.py maps it to 409 — without leaking the running prompt)."""
        if self.current is not None and self.current.active:
            raise RuntimeError("a run is already active")
        session_id = None
        if resume and self.current is not None:
            session_id = self.current.session_id
        prior = self.current
        run = EditRun(
            runtime,
            prompt,
            page=page,
            page_file=page_file,
            params=params,
            session_id=session_id,
            on_event=self._fanout,
        )
        run.take_snapshot()  # may raise OSError → surfaced by the endpoint
        if prior is not None:
            prior.drop_snapshot()  # one undo slot: the newest run's
        self.current = run
        run.start()
        return run

    async def shutdown(self) -> None:
        if self.current is not None:
            await self.current.shutdown()
        for q in list(self._subscribers):
            q.put_nowait(CLOSED)
        self._subscribers.clear()

    def reset(self) -> None:
        """Test isolation — forget any run state (does not kill; tests await
        their runs)."""
        self.current = None
        self._subscribers.clear()


# Process-wide hub, mirroring streaming.hub.
edit_hub = EditHub()
