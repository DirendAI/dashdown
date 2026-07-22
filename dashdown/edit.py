"""AI edit mode: config + the frozen per-serve runtime.

``dashdown serve --edit`` lets the browser drive a coding-agent CLI (see
dashdown/agent_presets.py) as a subprocess in the project directory; the agent
edits ``pages/``/``queries/``/…, and the **existing** watcher + SSE live-reload
shows the result. This module owns the ``edit:`` config block and the
:class:`EditRuntime` — everything the server needs at request time, resolved
**once per serve** and never re-read on a project reload, so an agent editing
``dashdown.yaml`` mid-run can never swap the command it runs as or escalate
its own permission mode.

Security model (the endpoints enforcing it live in server.py):

- **Arming**: only the local ``--edit`` CLI flag arms the feature. A checked-in
  ``edit:`` block *configures* but never *enables* — cloning a repo with a
  hostile ``edit:`` block and running plain ``dashdown serve`` runs nothing.
- **Loopback-only**: ``serve --edit`` refuses a non-loopback bind, and every
  edit request re-checks the peer + Host + Origin (DNS-rebinding defense).
- **Per-serve token**: minted here (``secrets.token_urlsafe``), injected into
  authed page renders only, required on every edit request (header / first WS
  message — never a URL, so it never lands in access logs).
- **Custom-command consent**: an ``edit.custom`` command, ``edit.binary``, or
  ``edit.args`` from yaml only takes effect with ``--allow-custom`` — the
  hostile-cloned-yaml defense.
- **No shell, prompt as data**: argv exec only; the prompt substitutes as one
  argv element or rides stdin.

The environment passes through to the agent **deliberately**: the subprocess
reads the project tree and ``$HOME`` anyway — the trust boundary is "same as
running that CLI in your terminal", plus visibility (transcript, changed-file
diff, ``config_changed`` flag, the ``.dashdown/edit-log.jsonl`` audit log) and
recoverability (pre-run snapshot + undo, edit_session.py).
"""
from __future__ import annotations

import json
import secrets
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from dashdown.agent_presets import (
    PARSERS,
    AgentPreset,
    get_agent_preset,
    register_agent_preset,
    resolve_agent,
)

PERMISSION_MODES = ("safe", "full")
DEFAULT_TIMEOUT = 900
MAX_TIMEOUT = 3600
# Transcript ring-buffer caps (events + total text bytes) — a runaway agent
# can't balloon server memory or the replay payload.
DEFAULT_MAX_EVENTS = 2000


@dataclass
class EditCustomConfig:
    """``edit.custom`` — an ad-hoc agent command (consent-gated)."""

    command: list[str] = field(default_factory=list)
    resume_command: list[str] | None = None
    output: str = "text"  # a parser name: text | jsonl | claude_json
    env: dict[str, str] = field(default_factory=dict)


@dataclass
class EditConfig:
    """Parsed ``edit:`` block from dashdown.yaml. Never arms anything."""

    agent: str | None = None  # preset name | "custom" | None (auto-detect)
    binary: str | None = None  # preset-binary override (needs --allow-custom)
    args: list[str] = field(default_factory=list)  # extra argv (needs --allow-custom)
    permission_mode: str = "safe"
    custom: EditCustomConfig | None = None
    timeout: int = DEFAULT_TIMEOUT
    context: bool = True  # prepend the page/params/guide preamble
    max_transcript_events: int = DEFAULT_MAX_EVENTS


def parse_edit_config(raw: Any) -> EditConfig:
    """Parse and validate the ``edit:`` block. Raises ValueError when malformed
    so the server refuses to start half-broken (same policy as ``auth:``)."""
    if raw is None:
        return EditConfig()
    if not isinstance(raw, dict):
        raise ValueError("edit: must be a mapping")

    cfg = EditConfig()

    agent = raw.get("agent")
    if agent is not None:
        if not isinstance(agent, str) or not agent.strip():
            raise ValueError("edit.agent must be a preset name string (or 'custom')")
        cfg.agent = agent.strip()

    binary = raw.get("binary")
    if binary is not None:
        if not isinstance(binary, str) or not binary.strip():
            raise ValueError("edit.binary must be a non-empty path string")
        cfg.binary = binary.strip()

    args = raw.get("args")
    if args is not None:
        if not isinstance(args, list) or not all(isinstance(a, str) for a in args):
            raise ValueError("edit.args must be a list of strings")
        cfg.args = list(args)

    mode = raw.get("permission_mode")
    if mode is not None:
        if mode not in PERMISSION_MODES:
            raise ValueError(
                f"edit.permission_mode must be one of: {', '.join(PERMISSION_MODES)}"
            )
        cfg.permission_mode = mode

    custom_raw = raw.get("custom")
    if custom_raw is not None:
        if not isinstance(custom_raw, dict):
            raise ValueError("edit.custom must be a mapping (command / output / env)")
        command = custom_raw.get("command")
        if (
            not isinstance(command, list)
            or not command
            or not all(isinstance(c, str) for c in command)
        ):
            raise ValueError("edit.custom.command must be a non-empty list of strings")
        custom = EditCustomConfig(command=list(command))
        resume = custom_raw.get("resume_command")
        if resume is not None:
            if not isinstance(resume, list) or not all(isinstance(c, str) for c in resume):
                raise ValueError("edit.custom.resume_command must be a list of strings")
            custom.resume_command = list(resume)
        output = custom_raw.get("output", "text")
        if output not in PARSERS:
            raise ValueError(
                f"edit.custom.output must be one of: {', '.join(PARSERS)}"
            )
        custom.output = output
        env = custom_raw.get("env", {})
        if not isinstance(env, dict) or not all(
            isinstance(k, str) and isinstance(v, str) for k, v in env.items()
        ):
            raise ValueError("edit.custom.env must be a string-to-string mapping")
        custom.env = dict(env)
        cfg.custom = custom

    if cfg.agent == "custom" and cfg.custom is None:
        raise ValueError("edit.agent: custom requires an edit.custom block")
    if cfg.custom is not None and cfg.agent not in (None, "custom"):
        raise ValueError(
            "edit.custom and edit.agent are mutually exclusive — set "
            "`agent: custom` (or drop the custom block)"
        )

    timeout = raw.get("timeout")
    if timeout is not None:
        if not isinstance(timeout, int) or isinstance(timeout, bool) or timeout <= 0:
            raise ValueError("edit.timeout must be a positive integer (seconds)")
        cfg.timeout = min(timeout, MAX_TIMEOUT)

    context = raw.get("context")
    if context is not None:
        if not isinstance(context, bool):
            raise ValueError("edit.context must be a boolean")
        cfg.context = context

    max_events = raw.get("max_transcript_events")
    if max_events is not None:
        if not isinstance(max_events, int) or isinstance(max_events, bool) or max_events <= 0:
            raise ValueError("edit.max_transcript_events must be a positive integer")
        cfg.max_transcript_events = max_events

    return cfg


@dataclass(frozen=True)
class EditRuntime:
    """Everything the edit endpoints need, frozen at serve start.

    Deliberately NOT re-read on ``reload_project``: the agent can edit
    dashdown.yaml mid-run, and a hot-applied ``edit:`` change would let it
    swap its own command or permission mode. A changed config still hot-applies
    to the *dashboard* as usual — the run's result just carries
    ``config_changed: true`` so the panel shows a warning banner.
    """

    project_root: Path
    preset: AgentPreset | None  # None => unavailable (setup card)
    probe: str  # how the agent was resolved, or why it wasn't
    token: str
    permission_mode: str = "safe"
    extra_args: tuple[str, ...] = ()
    binary: str | None = None
    timeout: int = DEFAULT_TIMEOUT
    context: bool = True
    max_events: int = DEFAULT_MAX_EVENTS

    @property
    def available(self) -> bool:
        return self.preset is not None

    def build_argv(self, prompt: str, session_id: str | None = None) -> list[str]:
        assert self.preset is not None
        return self.preset.build_argv(
            prompt,
            mode=self.permission_mode,
            extra_args=self.extra_args,
            binary=self.binary,
            session_id=session_id,
        )


def _read_edit_yaml(project_root: Path) -> tuple[EditConfig, list[str]]:
    """The ``edit:`` block + ``agents:`` list from dashdown.yaml. Read directly
    (not via load_project) so the runtime freezes before any reload machinery
    exists, and a broken *other* block doesn't mask an edit-config error."""
    cfg_path = project_root / "dashdown.yaml"
    if not cfg_path.is_file():
        return EditConfig(), []
    raw = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        return EditConfig(), []
    edit_cfg = parse_edit_config(raw.get("edit"))
    agents = raw.get("agents")
    if isinstance(agents, str):
        agents = agents.split(",")
    if not isinstance(agents, list):
        agents = []
    return edit_cfg, [str(a).strip().lower() for a in agents if str(a).strip()]


def build_edit_runtime(
    project_root: Path,
    *,
    agent: str | None = None,
    allow_custom: bool = False,
) -> EditRuntime:
    """Resolve + freeze the runtime for one ``serve --edit``.

    Raises ValueError on an unknown ``--agent``/``edit.agent`` name or on
    custom-command bits present without ``--allow-custom`` (the consent gate).
    An *installed-agent-missing* situation does NOT raise — the server starts
    with ``available=False`` and the panel renders the probe as a setup card.
    """
    cfg, project_agents = _read_edit_yaml(project_root)

    if not allow_custom:
        offending = []
        if cfg.custom is not None:
            offending.append("edit.custom")
        if cfg.binary is not None:
            offending.append("edit.binary")
        if cfg.args:
            offending.append("edit.args")
        if offending:
            raise ValueError(
                f"{' + '.join(offending)} define a custom command line from "
                "dashdown.yaml — rerun with --allow-custom to consent (a cloned "
                "project's config must not pick what executes on your machine)"
            )

    if cfg.custom is not None:
        # Ad-hoc preset from config; registered under "custom" so the session
        # layer treats it like any other preset.
        preset = AgentPreset(
            name="custom",
            summary="custom command (dashdown.yaml edit.custom)",
            binary=cfg.custom.command[0],
            install_hint="(project-defined)",
            argv=tuple(cfg.custom.command),
            resume_argv=tuple(cfg.custom.resume_command)
            if cfg.custom.resume_command
            else None,
            prompt_via="argv" if "{prompt}" in cfg.custom.command else "stdin",
            env=dict(cfg.custom.env),
            parser=cfg.custom.output,
        )
        register_agent_preset(preset)
        probe = "custom command (edit.custom, --allow-custom)"
    else:
        preset, probe = resolve_agent(agent, cfg.agent, project_agents)

    return EditRuntime(
        project_root=project_root.resolve(),
        preset=preset,
        probe=probe,
        token=secrets.token_urlsafe(32),
        permission_mode=cfg.permission_mode,
        extra_args=tuple(cfg.args),
        binary=cfg.binary,
        timeout=cfg.timeout,
        context=cfg.context,
        max_events=cfg.max_transcript_events,
    )


# --------------------------------------------------------------------------- #
# Prompt assembly
# --------------------------------------------------------------------------- #
def build_edit_prompt(
    runtime: EditRuntime,
    prompt: str,
    *,
    page_file: str | None = None,
    params: dict[str, str] | None = None,
) -> str:
    """The full prompt handed to the agent: a short context preamble (the page
    being viewed, active filter values, the guide + verification loop) and then
    the author's request verbatim. ``edit.context: false`` sends the request
    bare. The platform knowledge itself lives in the scaffolded AGENTS.md /
    ``.references/`` guide — the preamble routes there instead of duplicating."""
    if not runtime.context:
        return prompt
    lines = [
        "You are editing a Dashdown dashboard project (the current working "
        "directory). If an AGENTS.md exists here, read it first — it maps the "
        "platform and the .references/ detail shards.",
    ]
    if page_file:
        lines.append(f"The author is currently viewing the page: {page_file}")
    if params:
        pairs = ", ".join(f"{k}={v}" for k, v in sorted(params.items()))
        lines.append(f"Active filter/route values: {pairs}")
    lines.append(
        "After making changes, verify them: run `dashdown check` (renders "
        "every page without executing queries) and fix anything it reports."
    )
    lines.append("")
    lines.append("The author's request:")
    lines.append(prompt)
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# Audit log
# --------------------------------------------------------------------------- #
def append_audit_log(project_root: Path, entry: dict[str, Any]) -> None:
    """Append one run's summary to ``.dashdown/edit-log.jsonl`` (best-effort —
    an unwritable project dir must not fail the run)."""
    try:
        log_dir = project_root / ".dashdown"
        log_dir.mkdir(exist_ok=True)
        with open(log_dir / "edit-log.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps({"ts": int(time.time()), **entry}, default=str) + "\n")
    except OSError:
        pass
