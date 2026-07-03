"""Loads connectors from a project's sources.yaml."""
from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from dashdown.data.base import Connector, get_connector_type

# Built-in connectors (csv, duckdb, dax) are not imported here. They register
# through the `dashdown.connectors` entry-point group declared in pyproject.toml
# — the same mechanism third-party connector plugins use — and are loaded lazily
# by `get_connector_type` only when a project's sources.yaml actually asks for
# that type. This keeps optional, heavy dependencies (msal/pyarrow for `dax`)
# from being imported unless a Fabric source is configured.


def load_connectors(
    sources_path: Path,
    project_root: Path,
) -> dict[str, Connector]:
    """Parse sources.yaml and instantiate each connector.

    A source may mark itself the project's **default** with ``default: true``
    (recorded on the instance as ``is_default``; the key never reaches the
    connector's config). Which source actually answers a query with no
    ``connector=`` is decided by :func:`default_connector_name` — the flag,
    else a sole source, else the conventional name ``main``. More than one
    flagged source is a contradiction and fails at load.
    """
    if not sources_path.exists():
        return {}
    data = yaml.safe_load(sources_path.read_text(encoding="utf-8")) or {}
    connectors: dict[str, Connector] = {}
    default_names: list[str] = []
    for name, raw in data.items():
        if not isinstance(raw, dict) or "type" not in raw:
            raise ValueError(
                f"sources.yaml entry '{name}' must be a mapping with a 'type' key"
            )
        if raw.get("default"):
            default_names.append(name)
        cfg: dict[str, Any] = {k: v for k, v in raw.items() if k not in ("type", "default")}
        cfg["_project_root"] = project_root
        cls = get_connector_type(raw["type"])
        connectors[name] = cls(name, cfg)
        connectors[name].is_default = name in default_names

    if len(default_names) > 1:
        raise ValueError(
            "sources.yaml: only one source may set `default: true` "
            f"(got: {', '.join(default_names)})"
        )
    return connectors


def default_connector_name(connectors: dict[str, Connector]) -> str:
    """The source name a query with no explicit ``connector=`` resolves to.

    Precedence: the source flagged ``default: true`` in sources.yaml → the
    sole source, when exactly one is configured → the conventional name
    ``main`` (which may or may not exist; a miss surfaces downstream as the
    usual unknown-connector error).
    """
    for name, conn in connectors.items():
        if getattr(conn, "is_default", False):
            return name
    if len(connectors) == 1:
        return next(iter(connectors))
    return "main"
