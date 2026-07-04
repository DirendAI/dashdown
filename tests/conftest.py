"""Shared pytest fixtures."""
from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _isolate_telemetry(tmp_path, monkeypatch):
    """Keep the suite from touching real telemetry state or emitting real events.

    A real PostHog key ships baked in, so any CLI invocation (the first-run-notice
    callback, or a future ``serve``/``build`` test) would otherwise write to the
    developer's ``~/.config`` and POST to PostHog. Every test instead gets a
    throwaway state file with telemetry switched off. ``tests/test_telemetry.py``
    clears these in its own fixture to exercise the real opt-out logic.
    """
    monkeypatch.setenv("DASHDOWN_TELEMETRY_STATE", str(tmp_path / "telemetry-state.json"))
    monkeypatch.setenv("DASHDOWN_TELEMETRY", "0")


@pytest.fixture(autouse=True)
def _enterprise_unlock(monkeypatch):
    """Unlock the enterprise gate (auth/embed) for the whole suite.

    Auth + embedding are gated as enterprise features (``dashdown/enterprise.py``)
    but their implementation stays in-tree, and the suite must keep exercising it
    so it can't rot — so every test runs unlocked. ``tests/test_enterprise.py``
    removes the variable in its own fixture to assert the locked default.
    """
    monkeypatch.setenv("DASHDOWN_ENTERPRISE", "1")
