"""The enterprise gate: `auth:` / `embed:` refuse to load without the unlock.

The rest of the suite runs with the unlock set (see ``conftest.py``), so the
implementation behind the gate stays fully exercised; these tests remove it
again to pin down the locked-by-default behavior.
"""
from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from dashdown.enterprise import ENTERPRISE_ENV, enterprise_enabled
from dashdown.project import load_project


def _make_project(tmp: Path, extra_yaml: str = "") -> Path:
    (tmp / "pages").mkdir()
    (tmp / "pages" / "index.md").write_text("# Home\n", encoding="utf-8")
    (tmp / "dashdown.yaml").write_text("title: Test\n" + extra_yaml, encoding="utf-8")
    return tmp


@pytest.fixture
def tmp_project():
    with tempfile.TemporaryDirectory() as d:
        yield Path(d)


@pytest.fixture
def locked(monkeypatch):
    # Undo the suite-wide unlock from conftest.py; this fixture's delenv runs
    # after the autouse setenv, so these tests see the real default.
    monkeypatch.delenv(ENTERPRISE_ENV, raising=False)


_AUTH = "auth:\n  type: basic\n  username: admin\n  password: pw\n"
_EMBED = "embed:\n  enabled: true\n"


class TestGate:
    def test_auth_refused_when_locked(self, tmp_project, locked):
        with pytest.raises(ValueError, match="enterprise"):
            load_project(_make_project(tmp_project, _AUTH))

    def test_embed_refused_when_locked(self, tmp_project, locked):
        with pytest.raises(ValueError, match="enterprise"):
            load_project(_make_project(tmp_project, _EMBED))

    def test_error_names_the_unlock(self, tmp_project, locked):
        with pytest.raises(ValueError, match=ENTERPRISE_ENV):
            load_project(_make_project(tmp_project, _AUTH))

    def test_inert_blocks_load_without_unlock(self, tmp_project, locked):
        root = _make_project(
            tmp_project, "auth:\n  type: none\nembed:\n  enabled: false\n"
        )
        proj = load_project(root)
        assert not proj.config.auth.enabled
        assert not proj.config.embed.enabled

    def test_no_blocks_load_without_unlock(self, tmp_project, locked):
        proj = load_project(_make_project(tmp_project))
        assert not proj.config.auth.enabled

    def test_unlock_env_activates_both(self, tmp_project, locked, monkeypatch):
        monkeypatch.setenv(ENTERPRISE_ENV, "1")
        proj = load_project(_make_project(tmp_project, _AUTH + _EMBED))
        assert proj.config.auth.enabled
        assert proj.config.embed.enabled

    def test_misconfig_fails_with_its_own_error_first(self, tmp_project, locked):
        # A broken block must keep its specific parse error — the gate runs
        # only after config parsed cleanly.
        with pytest.raises(ValueError, match="auth.type"):
            load_project(_make_project(tmp_project, "auth:\n  type: wat\n"))


class TestEnterpriseEnabled:
    @pytest.mark.parametrize("val", ["1", "true", "TRUE", "yes", "on", " 1 "])
    def test_truthy(self, monkeypatch, val):
        monkeypatch.setenv(ENTERPRISE_ENV, val)
        assert enterprise_enabled()

    @pytest.mark.parametrize("val", ["", "0", "false", "off", "nope"])
    def test_falsy(self, monkeypatch, val):
        monkeypatch.setenv(ENTERPRISE_ENV, val)
        assert not enterprise_enabled()

    def test_unset_is_locked(self, monkeypatch):
        monkeypatch.delenv(ENTERPRISE_ENV, raising=False)
        assert not enterprise_enabled()
