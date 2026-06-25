"""Anonymous product-usage telemetry.

Dashdown is pip-installed and self-hosted, so the only signal we have for "how
many people use this?" is the framework reporting **anonymized** usage back to
us. This module is the whole sender: it fires one event per ``dashdown serve`` /
``dashdown build`` carrying only the Dashdown version + OS — never project data,
paths, query SQL, connector names, or anything identifying.

Design rules (all enforced here, by construction):

* **On by default, trivially opt-out.** Four independent off-switches, checked in
  :func:`is_enabled`: the cross-tool ``DO_NOT_TRACK`` env var, a ``DASHDOWN_TELEMETRY``
  env var, ``dashdown telemetry off`` (a flag in the local state file), and a
  ``telemetry: {enabled: false}`` block in a project's ``dashdown.yaml``.
* **Loud first-run notice.** :func:`maybe_print_first_run_notice` prints once, to
  stderr, what we collect and how to turn it off.
* **Anonymous.** Events carry ``$process_person_profile: false`` (no PostHog person
  profiles / no PII); unique installs are counted by a random, locally-stored
  ``install_id``.
* **Never breaks a command.** Every entry point swallows all exceptions and the
  network call runs in a daemon thread with a short timeout — telemetry failing or
  being slow must never affect the user's ``dashdown`` invocation.

Deliberately self-contained: it does **not** import :mod:`dashdown.project` (no
import cycle, no heavyweight project load just to decide opt-out — the
``dashdown.yaml`` peek is a tiny lenient read).
"""
from __future__ import annotations

import json
import os
import platform
import sys
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

# --- Sink configuration -----------------------------------------------------
# The PostHog *project* key is write-only and safe to ship publicly (it cannot
# read any data — the same key PostHog embeds in public frontend bundles). The EU
# project key is baked in below, so telemetry is active by default. capture() falls
# dormant (a silent no-op) only if a fork blanks the key or restores a "phc_REPLACE…"
# placeholder (see _key_is_real()); DASHDOWN_TELEMETRY_KEY overrides it at runtime.
_DEFAULT_HOST = "https://eu.i.posthog.com"
_BAKED_PROJECT_KEY = "phc_zvH7CF8DWWUuMoZpuKFodkgFqkCLXqQddcyg73XP57c7"
_DEFAULT_PROJECT_KEY = _BAKED_PROJECT_KEY
_TIMEOUT = 1.5  # seconds — best-effort; the request overlaps the command's real work
_THROTTLE = timedelta(hours=24)  # collapse dev restarts; unique installs key on install_id

_DOCS_URL = "https://github.com/DirendAI/dashdown/blob/main/docs/pages/telemetry.md"

_NOTICE = (
    "\n"
    "Dashdown collects anonymous usage stats (Dashdown version, Python version, OS)\n"
    "to understand how many people use it. No personal data, project contents,\n"
    "queries, file paths, or connection details are ever sent.\n"
    "\n"
    "Opt out anytime:  dashdown telemetry off\n"
    "             (or  DASHDOWN_TELEMETRY=0  /  DO_NOT_TRACK=1, "
    "or telemetry.enabled: false in dashdown.yaml)\n"
    f"Details:          {_DOCS_URL}\n"
)

_OFF_VALUES = {"0", "false", "off", "no"}
_ON_VALUES = {"1", "true", "yes", "on"}


# --- Sink helpers -----------------------------------------------------------
def _host() -> str:
    return (os.environ.get("DASHDOWN_TELEMETRY_HOST") or _DEFAULT_HOST).rstrip("/")


def _endpoint() -> str:
    return _host() + "/i/v0/e/"


def _project_key() -> str:
    return os.environ.get("DASHDOWN_TELEMETRY_KEY") or _DEFAULT_PROJECT_KEY


def _key_is_real() -> bool:
    key = _project_key()
    return bool(key) and not key.startswith("phc_REPLACE")


def _version() -> str:
    try:
        from importlib.metadata import version

        # Distribution (PyPI) name is "dashdown-md"; the import package is "dashdown".
        return version("dashdown-md")
    except Exception:
        return "unknown"


# --- Local state (install id + opt-out flag + first-run + throttle) ---------
def _state_path() -> Path:
    override = os.environ.get("DASHDOWN_TELEMETRY_STATE")
    if override:
        return Path(override)
    base = os.environ.get("XDG_CONFIG_HOME")
    root = Path(base) if base else Path.home() / ".config"
    return root / "dashdown" / "telemetry.json"


def _load_state() -> dict:
    try:
        return json.loads(_state_path().read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_state(state: dict) -> None:
    try:
        path = _state_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(state, indent=2), encoding="utf-8")
    except Exception:
        pass  # telemetry state is best-effort; never raise


def _install_id() -> str:
    state = _load_state()
    iid = state.get("install_id")
    if not iid:
        import uuid

        iid = str(uuid.uuid4())
        state["install_id"] = iid
        _save_state(state)
    return iid


def set_enabled(value: bool) -> None:
    """Persist an explicit on/off choice (``dashdown telemetry on|off``)."""
    state = _load_state()
    state["enabled"] = bool(value)
    _save_state(state)


# --- Opt-out decision -------------------------------------------------------
def _truthy(value: str | None) -> bool:
    return (value or "").strip().lower() in _ON_VALUES


def _env_opt_out() -> bool:
    if _truthy(os.environ.get("DO_NOT_TRACK")):
        return True
    return (os.environ.get("DASHDOWN_TELEMETRY") or "").strip().lower() in _OFF_VALUES


def _project_yaml_opt_out(project_path: str | os.PathLike[str]) -> bool:
    """True only if ``<project>/dashdown.yaml`` explicitly sets
    ``telemetry.enabled: false``. Lenient: any read/parse problem ⇒ not opted out
    (telemetry must never crash a command over a config quirk)."""
    try:
        cfg = Path(project_path) / "dashdown.yaml"
        if not cfg.is_file():
            return False
        import yaml

        raw = yaml.safe_load(cfg.read_text(encoding="utf-8")) or {}
        block = raw.get("telemetry")
        return isinstance(block, dict) and block.get("enabled") is False
    except Exception:
        return False


def _decide(project_path: str | os.PathLike[str] | None) -> tuple[bool, str | None]:
    """Return ``(enabled, reason_if_disabled)`` — first matching off-switch wins."""
    if _truthy(os.environ.get("DO_NOT_TRACK")):
        return False, "DO_NOT_TRACK is set"
    if (os.environ.get("DASHDOWN_TELEMETRY") or "").strip().lower() in _OFF_VALUES:
        return False, "DASHDOWN_TELEMETRY env var is off"
    if _load_state().get("enabled") is False:
        return False, "disabled via `dashdown telemetry off`"
    if project_path is not None and _project_yaml_opt_out(project_path):
        return False, "telemetry.enabled: false in dashdown.yaml"
    return True, None


def is_enabled(project_path: str | os.PathLike[str] | None = None) -> bool:
    return _decide(project_path)[0]


def disabled_reason(project_path: str | os.PathLike[str] | None = None) -> str | None:
    return _decide(project_path)[1]


# --- First-run notice -------------------------------------------------------
def maybe_print_first_run_notice() -> None:
    """Print the one-time notice to stderr, then remember we did. No-op if already
    shown, or if telemetry is globally opted out via env (no reason to nag)."""
    try:
        if _env_opt_out():
            return
        state = _load_state()
        if state.get("first_run_done"):
            return
        state["first_run_done"] = True
        _save_state(state)
        print(_NOTICE, file=sys.stderr)
    except Exception:
        pass


# --- Capture ----------------------------------------------------------------
def _base_properties() -> dict:
    return {
        "dashdown_version": _version(),
        "python_version": platform.python_version(),
        "os": platform.system(),
        "arch": platform.machine(),
        "$lib": "dashdown-cli",
    }


def dry_run_payload(event: str, properties: dict | None = None) -> dict:
    """The exact PostHog payload that would be sent (used by ``telemetry status``)."""
    props = {
        **_base_properties(),
        **(properties or {}),
        "$process_person_profile": False,  # anonymous: no person profile / no PII
    }
    return {
        "api_key": _project_key(),
        "event": event,
        "distinct_id": _install_id(),
        "properties": props,
    }


def _post_payload(payload: dict) -> None:
    try:
        requests.post(_endpoint(), json=payload, timeout=_TIMEOUT)
    except Exception:
        pass  # best-effort: a down/slow sink must never surface to the user


def _send(payload: dict) -> None:
    threading.Thread(
        target=_post_payload, args=(payload,), name="dashdown-telemetry", daemon=True
    ).start()


def _recently_sent(state: dict, event: str, now: datetime) -> bool:
    ts = state.get("last_sent", {}).get(event)
    if not ts:
        return False
    try:
        last = datetime.fromisoformat(ts)
    except Exception:
        return False
    return (now - last) < _THROTTLE


def capture(
    event: str,
    properties: dict | None = None,
    *,
    project_path: str | os.PathLike[str] | None = None,
) -> None:
    """Best-effort: record an anonymous usage event. Silent no-op when telemetry is
    disabled, the project key is unset, or the same event fired within the last 24h.
    Never blocks (daemon thread + short timeout) and never raises."""
    try:
        if not _key_is_real() or not is_enabled(project_path):
            return
        now = datetime.now(timezone.utc)
        state = _load_state()
        if _recently_sent(state, event, now):
            return
        state.setdefault("last_sent", {})[event] = now.isoformat()
        _save_state(state)
        _send(dry_run_payload(event, properties))
    except Exception:
        pass
