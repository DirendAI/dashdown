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
