"""Coding-agent CLI presets: how `dashdown serve --edit` drives each agent.

The agent-CLI space churns (claude, codex, gemini, cursor-agent, opencode,
aider, next quarter's tool), so the integration is **data, not code**: an
:class:`AgentPreset` describes how to invoke one tool non-interactively —
binary, argv template, permission-mode argv, env — and names a **line parser**
(the one seam where vendor output-format churn actually lives) that normalizes
the tool's stdout into a small shared event vocabulary. Most tools ride the
two generic parsers (``text``, ``jsonl``); only Claude Code has a bespoke one
for its ``--output-format stream-json`` shape.

Registered like connectors/components/agent-targets: built-ins at import, and
third parties via the ``dashdown.agent_presets`` entry-point group — a new
agent CLI is a preset entry, no core change. A checked-in ``edit.custom``
block in dashdown.yaml can also define one ad hoc (consent-gated by
``--allow-custom``; see dashdown/edit.py).

**The normalized event vocabulary** (plain dicts, versioned as
``dashdown-edit.v1`` in the WS hello):

- ``{"type": "status", "state": "starting"|"running"}``
- ``{"type": "text", "text": str}`` — agent narration
- ``{"type": "tool", "name": str, "target": str|None}`` — a tool call
- ``{"type": "raw", "line": str}`` — anything unparseable (never kill a run
  over a parse error)
- ``{"type": "result", ...}`` / ``{"type": "error", ...}`` — emitted by the
  session layer (edit_session.py), not by parsers.

Flags here WILL go stale as vendors change their CLIs — that's expected and
survivable: ``edit.args`` appends argv, ``edit.binary`` repoints the binary,
and ``edit.custom`` replaces the whole invocation, all without a Dashdown
release.
"""
from __future__ import annotations

import json
import logging
import shutil
from dataclasses import dataclass, field
from typing import Any

log = logging.getLogger(__name__)

PRESET_CONTRACT = 1

# Known line parsers — the vendor-churn seam. A preset names one; the session
# layer calls `normalize_line(parser, line)` per stdout line.
PARSERS = ("text", "jsonl", "claude_json")


@dataclass(frozen=True)
class AgentPreset:
    """One coding-agent CLI, described declaratively."""

    name: str
    summary: str
    binary: str
    install_hint: str
    # Argv template. "{prompt}" is replaced by the (single-argument) prompt
    # when prompt_via == "argv"; with "stdin" the prompt is piped instead and
    # the template must not contain the token.
    argv: tuple[str, ...]
    # Per-permission-mode extra argv: {"safe": (...), "full": (...)}. `safe`
    # should allow file edits + the dashdown verification commands and nothing
    # else the vendor can scope out; `full` is the vendor's bypass flag.
    mode_args: dict[str, tuple[str, ...]] = field(default_factory=dict)
    # Follow-up turn template with a "{session_id}" token, or None when the
    # tool has no non-interactive resume (each request is then independent).
    resume_argv: tuple[str, ...] | None = None
    prompt_via: str = "argv"  # "argv" | "stdin"
    env: dict[str, str] = field(default_factory=dict)
    parser: str = "text"
    version_argv: tuple[str, ...] = ()
    contract: int = PRESET_CONTRACT

    def build_argv(
        self,
        prompt: str,
        *,
        mode: str = "safe",
        extra_args: tuple[str, ...] = (),
        binary: str | None = None,
        session_id: str | None = None,
    ) -> list[str]:
        """The concrete argv for one run. The prompt is substituted as exactly
        one argv element (never shell-joined — there is no shell anywhere in
        this feature); ``extra_args``/``binary`` are the author's overrides."""
        template = self.argv
        if session_id and self.resume_argv is not None:
            template = self.resume_argv
        out: list[str] = []
        for token in template:
            if token == "{prompt}":
                out.append(prompt)
            elif token == "{session_id}":
                out.append(session_id or "")
            else:
                out.append(token)
        out.extend(self.mode_args.get(mode, ()))
        out.extend(extra_args)
        if binary:
            out[0] = binary
        return out


# --------------------------------------------------------------------------- #
# Registry (same shape as connectors / components / agent_targets)
# --------------------------------------------------------------------------- #
_REGISTRY: dict[str, AgentPreset] = {}
_ENTRY_POINTS_LOADED = False


def register_agent_preset(preset: AgentPreset) -> None:
    if preset.parser not in PARSERS:
        raise ValueError(
            f"agent preset {preset.name!r}: unknown parser {preset.parser!r} "
            f"(known: {', '.join(PARSERS)})"
        )
    _REGISTRY[preset.name] = preset


def _load_entry_point_presets() -> None:
    """Third-party presets via the ``dashdown.agent_presets`` entry-point
    group. Lazy + best-effort: a broken or contract-mismatched entry is
    skipped with a warning, never fatal (same posture as connector loading)."""
    global _ENTRY_POINTS_LOADED
    if _ENTRY_POINTS_LOADED:
        return
    _ENTRY_POINTS_LOADED = True
    from importlib.metadata import entry_points

    try:
        eps = entry_points(group="dashdown.agent_presets")
    except Exception:  # noqa: BLE001 - metadata backend quirks
        return
    for ep in eps:
        try:
            preset = ep.load()
            if callable(preset):
                preset = preset()
            if not isinstance(preset, AgentPreset):
                raise TypeError(f"{ep.value} did not yield an AgentPreset")
            if preset.contract != PRESET_CONTRACT:
                raise ValueError(
                    f"contract {preset.contract} != supported {PRESET_CONTRACT}"
                )
            register_agent_preset(preset)
        except Exception as e:  # noqa: BLE001
            log.warning("Skipping agent preset entry point %r: %s", ep.name, e)


def get_agent_preset(name: str) -> AgentPreset | None:
    _load_entry_point_presets()
    return _REGISTRY.get(name)


def agent_preset_names() -> list[str]:
    _load_entry_point_presets()
    return list(_REGISTRY)


# --------------------------------------------------------------------------- #
# Built-ins
# --------------------------------------------------------------------------- #
# The dashdown verification loop the `safe` modes must permit — the whole point
# of the scaffolded agent guide is that the agent *checks its work*.
_DASHDOWN_VERIFY_TOOLS = (
    "Edit,Write,Read,Glob,Grep,"
    "Bash(dashdown check:*),Bash(dashdown query:*),"
    "Bash(dashdown screenshot:*),Bash(dashdown components:*),"
    "Bash(dashdown metric:*),Bash(dashdown connectors:*)"
)

register_agent_preset(
    AgentPreset(
        name="claude",
        summary="Claude Code (Anthropic)",
        binary="claude",
        install_hint="npm install -g @anthropic-ai/claude-code",
        argv=("claude", "-p", "{prompt}", "--output-format", "stream-json", "--verbose"),
        mode_args={
            "safe": (
                "--permission-mode",
                "acceptEdits",
                "--allowedTools",
                _DASHDOWN_VERIFY_TOOLS,
            ),
            "full": ("--dangerously-skip-permissions",),
        },
        resume_argv=(
            "claude", "-p", "{prompt}", "--output-format", "stream-json",
            "--verbose", "--resume", "{session_id}",
        ),
        parser="claude_json",
        version_argv=("claude", "--version"),
    )
)

register_agent_preset(
    AgentPreset(
        name="codex",
        summary="Codex CLI (OpenAI)",
        binary="codex",
        install_hint="npm install -g @openai/codex",
        argv=("codex", "exec", "{prompt}", "--json"),
        mode_args={
            "safe": ("--sandbox", "workspace-write"),
            "full": ("--dangerously-bypass-approvals-and-sandbox",),
        },
        parser="jsonl",
        version_argv=("codex", "--version"),
    )
)

register_agent_preset(
    AgentPreset(
        name="gemini",
        summary="Gemini CLI (Google)",
        binary="gemini",
        install_hint="npm install -g @google/gemini-cli",
        argv=("gemini", "-p", "{prompt}"),
        mode_args={
            "safe": ("--approval-mode", "auto_edit"),
            "full": ("--yolo",),
        },
        parser="text",
        version_argv=("gemini", "--version"),
    )
)

register_agent_preset(
    AgentPreset(
        name="cursor",
        summary="Cursor Agent CLI",
        binary="cursor-agent",
        install_hint="curl https://cursor.com/install -fsS | bash",
        argv=("cursor-agent", "-p", "{prompt}"),
        mode_args={
            "safe": (),
            "full": ("--force",),
        },
        parser="text",
        version_argv=("cursor-agent", "--version"),
    )
)

register_agent_preset(
    AgentPreset(
        name="opencode",
        summary="OpenCode CLI",
        binary="opencode",
        install_hint="npm install -g opencode-ai",
        argv=("opencode", "run", "{prompt}"),
        parser="text",
        version_argv=("opencode", "--version"),
    )
)

register_agent_preset(
    AgentPreset(
        name="aider",
        summary="Aider",
        binary="aider",
        install_hint="pip install aider-install && aider-install",
        argv=("aider", "--message", "{prompt}", "--yes-always", "--no-check-update"),
        parser="text",
        version_argv=("aider", "--version"),
    )
)


# --------------------------------------------------------------------------- #
# Resolution + probing
# --------------------------------------------------------------------------- #
def resolve_agent(
    explicit: str | None,
    config_agent: str | None,
    project_agents: list[str],
) -> tuple[AgentPreset | None, str]:
    """Pick the preset for this serve, by precedence: the ``--agent`` flag →
    ``edit.agent`` config → the project's ``agents:`` list (first entry with a
    preset whose binary is on PATH — a ``.claude/`` project prefers claude) →
    a PATH probe in registry order. Returns ``(preset, probe_report)``;
    ``preset`` is None when nothing is installed (the server still starts —
    the panel shows the report as a setup card). An *unknown name* raises
    (a typo should fail loudly at startup, not degrade)."""
    _load_entry_point_presets()

    for source, name in (("--agent", explicit), ("edit.agent", config_agent)):
        if not name:
            continue
        preset = _REGISTRY.get(name)
        if preset is None:
            raise ValueError(
                f"unknown agent {name!r} (from {source}) — known: "
                f"{', '.join(_REGISTRY)}"
            )
        if shutil.which(preset.binary) is None:
            return None, (
                f"agent '{preset.name}' selected via {source}, but its binary "
                f"'{preset.binary}' is not on PATH. Install it: {preset.install_hint}"
            )
        return preset, f"agent '{preset.name}' ({source})"

    for name in project_agents:
        preset = _REGISTRY.get(name)
        if preset is not None and shutil.which(preset.binary) is not None:
            return preset, f"agent '{preset.name}' (project agents: list)"

    for name, preset in _REGISTRY.items():
        if shutil.which(preset.binary) is not None:
            return preset, f"agent '{preset.name}' (found on PATH)"

    probes = ", ".join(
        f"{p.name} ({p.binary}: not found)" for p in _REGISTRY.values()
    )
    return None, (
        "no coding-agent CLI found on PATH. Probed: " + probes + ". "
        "Install one (e.g. npm install -g @anthropic-ai/claude-code) or "
        "configure `edit.custom` in dashdown.yaml."
    )


# --------------------------------------------------------------------------- #
# Line parsers (the vendor-churn seam)
# --------------------------------------------------------------------------- #
def normalize_line(parser: str, line: str) -> list[dict[str, Any]]:
    """One stdout line → zero or more normalized events. Unparseable input
    degrades to a ``raw`` event — a parse error must never kill a run."""
    text = line.rstrip("\r\n")
    if not text.strip():
        return []
    if parser == "text":
        return [{"type": "text", "text": text}]
    if parser == "claude_json":
        return _parse_claude_json(text)
    if parser == "jsonl":
        return _parse_generic_jsonl(text)
    return [{"type": "raw", "line": text}]


def _parse_claude_json(text: str) -> list[dict[str, Any]]:
    """Claude Code ``--output-format stream-json`` events → the vocabulary.

    Shapes (as of contract v1): ``{"type": "system", "subtype": "init", ...}``,
    ``{"type": "assistant", "message": {"content": [{"type": "text"|"tool_use",
    ...}]}}``, ``{"type": "result", "result": str, "session_id": str, ...}``.
    Anything else — including a plain non-JSON line — is ``raw``.
    """
    try:
        obj = json.loads(text)
    except ValueError:
        return [{"type": "raw", "line": text}]
    if not isinstance(obj, dict):
        return [{"type": "raw", "line": text}]

    kind = obj.get("type")
    if kind == "assistant":
        events: list[dict[str, Any]] = []
        content = (obj.get("message") or {}).get("content") or []
        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "text" and block.get("text"):
                events.append({"type": "text", "text": str(block["text"])})
            elif block.get("type") == "tool_use":
                inp = block.get("input") or {}
                target = inp.get("file_path") or inp.get("command") or inp.get("path")
                events.append(
                    {
                        "type": "tool",
                        "name": str(block.get("name") or "tool"),
                        "target": str(target) if target else None,
                    }
                )
        return events
    if kind == "result":
        events = []
        if obj.get("result"):
            events.append({"type": "text", "text": str(obj["result"])})
        if obj.get("session_id"):
            # Session capture for follow-up turns (resume_argv).
            events.append({"type": "session", "session_id": str(obj["session_id"])})
        return events
    if kind == "system":
        return []  # init/config chatter — not viewer-facing
    return [{"type": "raw", "line": text}]


def _parse_generic_jsonl(text: str) -> list[dict[str, Any]]:
    """Best-effort JSONL: surface any human-readable text field; else raw."""
    try:
        obj = json.loads(text)
    except ValueError:
        return [{"type": "raw", "line": text}]
    if not isinstance(obj, dict):
        return [{"type": "raw", "line": text}]
    for key in ("text", "message", "content", "output"):
        value = obj.get(key)
        if isinstance(value, str) and value.strip():
            return [{"type": "text", "text": value}]
    return [{"type": "raw", "line": text}]
