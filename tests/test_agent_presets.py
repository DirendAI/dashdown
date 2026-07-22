"""Agent preset registry, argv building, resolution precedence, line parsers."""
from __future__ import annotations

import json

import pytest

from dashdown.agent_presets import (
    AgentPreset,
    agent_preset_names,
    get_agent_preset,
    normalize_line,
    register_agent_preset,
    resolve_agent,
)


class TestRegistry:
    def test_builtins_registered(self):
        names = agent_preset_names()
        for expected in ("claude", "codex", "gemini", "cursor", "opencode", "aider"):
            assert expected in names

    def test_unknown_parser_rejected(self):
        with pytest.raises(ValueError):
            register_agent_preset(
                AgentPreset(
                    name="bad", summary="", binary="x", install_hint="",
                    argv=("x",), parser="nope",
                )
            )


class TestBuildArgv:
    def test_prompt_is_one_argv_element(self):
        preset = get_agent_preset("claude")
        argv = preset.build_argv("add a chart; rm -rf /")
        # The whole prompt — shell metacharacters and all — is exactly one
        # element; there is no shell anywhere to interpret it.
        assert argv.count("add a chart; rm -rf /") == 1

    def test_safe_mode_args_appended(self):
        argv = get_agent_preset("claude").build_argv("x", mode="safe")
        assert "--permission-mode" in argv
        assert "acceptEdits" in argv
        joined = " ".join(argv)
        assert "dashdown check" in joined  # verification loop stays permitted

    def test_full_mode_uses_bypass_flag(self):
        argv = get_agent_preset("claude").build_argv("x", mode="full")
        assert "--dangerously-skip-permissions" in argv
        assert "--permission-mode" not in argv

    def test_extra_args_and_binary_override(self):
        argv = get_agent_preset("gemini").build_argv(
            "x", extra_args=("--model", "pro"), binary="/opt/gemini"
        )
        assert argv[0] == "/opt/gemini"
        assert argv[-2:] == ["--model", "pro"]

    def test_resume_template_substitutes_session(self):
        argv = get_agent_preset("claude").build_argv("x", session_id="abc123")
        assert "--resume" in argv
        assert "abc123" in argv

    def test_no_resume_template_ignores_session(self):
        argv = get_agent_preset("aider").build_argv("x", session_id="abc123")
        assert "abc123" not in argv


class TestResolveAgent:
    def test_explicit_unknown_raises(self):
        with pytest.raises(ValueError, match="unknown agent"):
            resolve_agent("definitely-not-a-tool", None, [])

    def test_explicit_missing_binary_reports(self, monkeypatch):
        monkeypatch.setattr("shutil.which", lambda _: None)
        preset, probe = resolve_agent("claude", None, [])
        assert preset is None
        assert "not on PATH" in probe

    def test_explicit_found(self, monkeypatch):
        monkeypatch.setattr("shutil.which", lambda b: f"/usr/bin/{b}")
        preset, probe = resolve_agent("codex", None, [])
        assert preset is not None and preset.name == "codex"
        assert "--agent" in probe

    def test_config_agent_second(self, monkeypatch):
        monkeypatch.setattr("shutil.which", lambda b: f"/usr/bin/{b}")
        preset, probe = resolve_agent(None, "gemini", ["claude"])
        assert preset.name == "gemini"
        assert "edit.agent" in probe

    def test_project_agents_list_third(self, monkeypatch):
        monkeypatch.setattr(
            "shutil.which", lambda b: "/usr/bin/gemini" if b == "gemini" else None
        )
        preset, probe = resolve_agent(None, None, ["cursor", "gemini"])
        assert preset.name == "gemini"
        assert "agents:" in probe

    def test_path_probe_last(self, monkeypatch):
        monkeypatch.setattr(
            "shutil.which", lambda b: "/usr/bin/aider" if b == "aider" else None
        )
        preset, probe = resolve_agent(None, None, [])
        assert preset.name == "aider"

    def test_nothing_installed_reports_not_raises(self, monkeypatch):
        monkeypatch.setattr("shutil.which", lambda _: None)
        preset, probe = resolve_agent(None, None, [])
        assert preset is None
        assert "no coding-agent CLI found" in probe


class TestNormalizeLine:
    def test_text_parser(self):
        assert normalize_line("text", "hello\n") == [{"type": "text", "text": "hello"}]

    def test_blank_lines_dropped(self):
        assert normalize_line("text", "   \n") == []

    def test_claude_assistant_text(self):
        line = json.dumps(
            {"type": "assistant", "message": {"content": [{"type": "text", "text": "hi"}]}}
        )
        assert normalize_line("claude_json", line) == [{"type": "text", "text": "hi"}]

    def test_claude_tool_use(self):
        line = json.dumps(
            {
                "type": "assistant",
                "message": {
                    "content": [
                        {"type": "tool_use", "name": "Edit", "input": {"file_path": "pages/a.md"}}
                    ]
                },
            }
        )
        assert normalize_line("claude_json", line) == [
            {"type": "tool", "name": "Edit", "target": "pages/a.md"}
        ]

    def test_claude_result_carries_session(self):
        line = json.dumps({"type": "result", "result": "done", "session_id": "s-1"})
        events = normalize_line("claude_json", line)
        assert {"type": "text", "text": "done"} in events
        assert {"type": "session", "session_id": "s-1"} in events

    def test_claude_system_chatter_dropped(self):
        line = json.dumps({"type": "system", "subtype": "init"})
        assert normalize_line("claude_json", line) == []

    def test_claude_garbage_degrades_to_raw(self):
        assert normalize_line("claude_json", "not json at all") == [
            {"type": "raw", "line": "not json at all"}
        ]

    def test_jsonl_surfaces_text_fields(self):
        assert normalize_line("jsonl", json.dumps({"message": "working"})) == [
            {"type": "text", "text": "working"}
        ]

    def test_jsonl_unknown_shape_is_raw(self):
        line = json.dumps({"weird": 1})
        assert normalize_line("jsonl", line) == [{"type": "raw", "line": line}]
