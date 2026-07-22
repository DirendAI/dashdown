"""`edit:` config parsing, the consent gate, and prompt assembly."""
from __future__ import annotations

from pathlib import Path

import pytest

from dashdown.edit import (
    EditRuntime,
    build_edit_prompt,
    build_edit_runtime,
    parse_edit_config,
)


class TestParseEditConfig:
    def test_absent_defaults(self):
        cfg = parse_edit_config(None)
        assert cfg.agent is None
        assert cfg.permission_mode == "safe"
        assert cfg.timeout == 900
        assert cfg.context is True

    def test_non_mapping_rejected(self):
        with pytest.raises(ValueError):
            parse_edit_config("claude")

    def test_bad_permission_mode(self):
        with pytest.raises(ValueError, match="permission_mode"):
            parse_edit_config({"permission_mode": "yolo"})

    def test_timeout_capped(self):
        assert parse_edit_config({"timeout": 999999}).timeout == 3600

    def test_timeout_must_be_positive(self):
        with pytest.raises(ValueError):
            parse_edit_config({"timeout": 0})

    def test_custom_requires_command(self):
        with pytest.raises(ValueError, match="custom.command"):
            parse_edit_config({"custom": {"output": "text"}})

    def test_agent_custom_requires_custom_block(self):
        with pytest.raises(ValueError, match="edit.custom block"):
            parse_edit_config({"agent": "custom"})

    def test_custom_excludes_named_agent(self):
        with pytest.raises(ValueError, match="mutually exclusive"):
            parse_edit_config(
                {"agent": "claude", "custom": {"command": ["x", "{prompt}"]}}
            )

    def test_custom_bad_output_parser(self):
        with pytest.raises(ValueError, match="output"):
            parse_edit_config({"custom": {"command": ["x"], "output": "xml"}})

    def test_args_must_be_strings(self):
        with pytest.raises(ValueError, match="edit.args"):
            parse_edit_config({"args": [1, 2]})

    def test_valid_custom(self):
        cfg = parse_edit_config(
            {
                "agent": "custom",
                "custom": {
                    "command": ["my-agent", "run", "{prompt}"],
                    "output": "jsonl",
                    "env": {"NO_COLOR": "1"},
                },
            }
        )
        assert cfg.custom.command == ["my-agent", "run", "{prompt}"]
        assert cfg.custom.output == "jsonl"


class TestConsentGate:
    """Custom-command bits from a (possibly hostile, cloned) dashdown.yaml
    must not execute without the local --allow-custom flag."""

    def _project(self, tmp_path: Path, yaml_text: str) -> Path:
        (tmp_path / "dashdown.yaml").write_text(yaml_text, encoding="utf-8")
        return tmp_path

    def test_custom_command_needs_consent(self, tmp_path):
        root = self._project(
            tmp_path,
            "edit:\n  agent: custom\n  custom:\n    command: [evil, '{prompt}']\n",
        )
        with pytest.raises(ValueError, match="--allow-custom"):
            build_edit_runtime(root, allow_custom=False)

    def test_binary_override_needs_consent(self, tmp_path):
        root = self._project(tmp_path, "edit:\n  binary: /tmp/evil\n")
        with pytest.raises(ValueError, match="--allow-custom"):
            build_edit_runtime(root, allow_custom=False)

    def test_extra_args_need_consent(self, tmp_path):
        root = self._project(tmp_path, "edit:\n  args: ['--dangerous']\n")
        with pytest.raises(ValueError, match="--allow-custom"):
            build_edit_runtime(root, allow_custom=False)

    def test_custom_with_consent_builds(self, tmp_path):
        root = self._project(
            tmp_path,
            "edit:\n  agent: custom\n  custom:\n    command: [echo, '{prompt}']\n",
        )
        runtime = build_edit_runtime(root, allow_custom=True)
        assert runtime.available
        assert runtime.preset.name == "custom"
        assert runtime.preset.prompt_via == "argv"

    def test_plain_config_needs_no_consent(self, tmp_path, monkeypatch):
        monkeypatch.setattr("shutil.which", lambda _: None)
        root = self._project(tmp_path, "title: X\n")
        runtime = build_edit_runtime(root, allow_custom=False)
        assert not runtime.available  # nothing installed — server still starts
        assert "no coding-agent CLI found" in runtime.probe

    def test_unknown_agent_flag_raises(self, tmp_path):
        root = self._project(tmp_path, "title: X\n")
        with pytest.raises(ValueError, match="unknown agent"):
            build_edit_runtime(root, agent="nope")

    def test_tokens_are_unique_per_serve(self, tmp_path, monkeypatch):
        monkeypatch.setattr("shutil.which", lambda _: None)
        root = self._project(tmp_path, "title: X\n")
        a = build_edit_runtime(root)
        b = build_edit_runtime(root)
        assert a.token != b.token
        assert len(a.token) >= 32


class TestBuildEditPrompt:
    def _runtime(self, tmp_path, context=True) -> EditRuntime:
        return EditRuntime(
            project_root=tmp_path, preset=None, probe="", token="t", context=context
        )

    def test_preamble_carries_page_and_params(self, tmp_path):
        prompt = build_edit_prompt(
            self._runtime(tmp_path),
            "add a chart",
            page_file="pages/sales.md",
            params={"region": "East"},
        )
        assert "pages/sales.md" in prompt
        assert "region=East" in prompt
        assert "AGENTS.md" in prompt
        assert "dashdown check" in prompt
        assert prompt.rstrip().endswith("add a chart")

    def test_context_false_sends_bare_prompt(self, tmp_path):
        prompt = build_edit_prompt(
            self._runtime(tmp_path, context=False),
            "add a chart",
            page_file="pages/sales.md",
        )
        assert prompt == "add a chart"


class TestServeEditCli:
    """`serve --edit` refusal rules — all fail before the server would bind."""

    def _project(self, tmp_path: Path, yaml_text: str = "title: X\n") -> Path:
        (tmp_path / "dashdown.yaml").write_text(yaml_text, encoding="utf-8")
        (tmp_path / "pages").mkdir()
        (tmp_path / "pages" / "index.md").write_text("# X\n", encoding="utf-8")
        return tmp_path

    def _invoke(self, *args):
        from typer.testing import CliRunner

        from dashdown.cli import app

        return CliRunner().invoke(app, ["serve", *args])

    def test_edit_refuses_no_watch(self, tmp_path):
        res = self._invoke(str(self._project(tmp_path)), "--edit", "--no-watch")
        assert res.exit_code != 0
        assert "file watcher" in res.output

    def test_edit_refuses_non_loopback_bind(self, tmp_path):
        res = self._invoke(
            str(self._project(tmp_path)), "--edit", "--host", "0.0.0.0"
        )
        assert res.exit_code != 0
        assert "loopback" in res.output

    def test_agent_flag_requires_edit(self, tmp_path):
        res = self._invoke(str(self._project(tmp_path)), "--agent", "claude")
        assert res.exit_code != 0
        assert "--edit" in res.output

    def test_unknown_agent_refused(self, tmp_path):
        res = self._invoke(str(self._project(tmp_path)), "--edit", "--agent", "nope")
        assert res.exit_code != 0
        assert "unknown agent" in res.output

    def test_custom_command_needs_allow_custom_flag(self, tmp_path):
        root = self._project(
            tmp_path,
            "title: X\nedit:\n  agent: custom\n  custom:\n    command: [evil, '{prompt}']\n",
        )
        res = self._invoke(str(root), "--edit")
        assert res.exit_code != 0
        assert "--allow-custom" in res.output
