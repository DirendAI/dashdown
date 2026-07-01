"""Coding-agent targets: where each tool wants its guide wrapper, and what to put there.

The *content* of the authoring guide — `AGENTS.md` (the map) + `.references/<topic>.md`
(the shards) — is tool-agnostic and is **always** installed. Only the thin **invocation
wrapper** is per-tool: a Claude Code skill under `.claude/`, a Cursor rule under
`.cursor/rules/`, a `GEMINI.md`, … — and each wrapper is just a router that points back at
that shared content. An `AgentTarget` captures the per-tool knowledge the install path
otherwise can't know: where the wrapper goes (`emit`) and whether a project already uses
that tool (`detect`).

This mirrors the connector/component/semantic-backend registries: built-ins register here
at import; a new tool is a few lines. (A `dashdown.agent_targets` entry-point group could
later let third parties add targets without a core change — same story as connectors.)
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable


@dataclass(frozen=True)
class EmittedFile:
    """One wrapper file to install at ``dest`` (relative to the project root).

    Exactly one source is set: ``source`` copies a shipped file (the real Claude skill),
    ``content`` writes generated text (a pointer stub for "just reads a markdown file" tools).
    """

    dest: Path
    source: Path | None = None
    content: str | None = None


@dataclass(frozen=True)
class AgentTarget:
    """A coding agent the guide can be installed for."""

    name: str
    summary: str
    emit: Callable[[Path], list[EmittedFile]]  # given the scaffold source dir
    detect: Callable[[Path], bool]  # given the project root — is this tool used here?


_REGISTRY: dict[str, AgentTarget] = {}


def register_agent_target(target: AgentTarget) -> None:
    _REGISTRY[target.name] = target


def get_agent_target(name: str) -> AgentTarget | None:
    return _REGISTRY.get(name)


def agent_target_names() -> list[str]:
    return list(_REGISTRY)


def detect_agent_targets(root: Path) -> list[str]:
    """Names of registered tools whose marker file/dir already exists under ``root``."""
    return [t.name for t in _REGISTRY.values() if t.detect(root)]


# --- The shared body every "reads a markdown file" wrapper routes through -----------------

_POINTER_BODY = (
    "This project is a **Dashdown** dashboard (Markdown pages with embedded SQL and\n"
    "`<Component />` tags). The authoring guide is already in this repo, at the project root:\n\n"
    "- **`AGENTS.md`** — the map: a one-screen cheat-sheet plus an index of topics.\n"
    "- **`.references/<topic>.md`** — full docs for one topic; open only the shard a task needs.\n\n"
    "Read `AGENTS.md` first, then the single relevant shard — don't read every shard. Prefer\n"
    "the `dashdown` CLI for facts (`dashdown components`, `dashdown check`, `dashdown query`).\n"
)


def _pointer(dest: str, *, header: str, front: str = "") -> EmittedFile:
    """A wrapper that just redirects into the shared `AGENTS.md` + `.references/`."""
    return EmittedFile(dest=Path(dest), content=f"{front}{header}\n\n{_POINTER_BODY}")


# --- Built-in targets --------------------------------------------------------------------


def _skill_tree(src: Path, prefix: str) -> list[EmittedFile]:
    """The real skill shipped under ``scaffold/claude/``, installed under ``prefix``.

    Stored without the leading dot in the package (setuptools' ``**`` glob skips hidden
    paths); the dotted rename happens here, on install. Mistral's ``.vibe`` uses the same
    ``skills/<name>/SKILL.md`` layout as Claude's ``.claude`` — and the skill's
    ``../../../AGENTS.md`` links sit at the same depth — so both install these same files
    verbatim under their own prefix.
    """
    skill_src = src / "claude"
    out: list[EmittedFile] = []
    if skill_src.is_dir():
        for f in sorted(skill_src.rglob("*")):
            if f.is_file():
                out.append(EmittedFile(dest=Path(prefix) / f.relative_to(skill_src), source=f))
    return out


def _claude_emit(src: Path) -> list[EmittedFile]:
    return _skill_tree(src, ".claude")


def _claude_detect(root: Path) -> bool:
    return (root / ".claude").is_dir() or (root / "CLAUDE.md").is_file()


def _cursor_emit(src: Path) -> list[EmittedFile]:
    # A Cursor project rule: frontmatter (`alwaysApply`) + a body that routes into AGENTS.md.
    front = "---\ndescription: Dashdown dashboard authoring guide\nalwaysApply: true\n---\n\n"
    return [_pointer(".cursor/rules/dashdown.mdc", header="# Dashdown — authoring guide", front=front)]


def _cursor_detect(root: Path) -> bool:
    return (root / ".cursor").is_dir() or (root / ".cursorrules").is_file()


def _gemini_emit(src: Path) -> list[EmittedFile]:
    return [_pointer("GEMINI.md", header="# Dashdown — guide for Gemini")]


def _gemini_detect(root: Path) -> bool:
    return (root / ".gemini").is_dir() or (root / "GEMINI.md").is_file()


def _copilot_emit(src: Path) -> list[EmittedFile]:
    return [_pointer(".github/copilot-instructions.md", header="# Dashdown — authoring guide")]


def _copilot_detect(root: Path) -> bool:
    # The specific instructions file, not `.github/` itself — that exists in many repos
    # (CI, issue templates) without anyone using Copilot custom instructions.
    return (root / ".github" / "copilot-instructions.md").is_file()


def _mistral_emit(src: Path) -> list[EmittedFile]:
    # Mistral's `.vibe/` mirrors `.claude/`'s skill layout, so it ships the same skill.
    return _skill_tree(src, ".vibe")


def _mistral_detect(root: Path) -> bool:
    return (root / ".vibe").is_dir()


# Claude first, so the no-flag default output stays byte-identical to before.
register_agent_target(
    AgentTarget("claude", "Claude Code skill (.claude/skills/)", _claude_emit, _claude_detect)
)
register_agent_target(
    AgentTarget("cursor", "Cursor project rule (.cursor/rules/)", _cursor_emit, _cursor_detect)
)
register_agent_target(
    AgentTarget("gemini", "Gemini guide (GEMINI.md)", _gemini_emit, _gemini_detect)
)
register_agent_target(
    AgentTarget(
        "copilot",
        "GitHub Copilot instructions (.github/copilot-instructions.md)",
        _copilot_emit,
        _copilot_detect,
    )
)
register_agent_target(
    AgentTarget(
        "mistral",
        "Mistral skill (.vibe/skills/) — same layout as .claude",
        _mistral_emit,
        _mistral_detect,
    )
)

# Note: Codex and other tools that read `AGENTS.md` natively need no wrapper — the
# always-installed baseline already covers them.
