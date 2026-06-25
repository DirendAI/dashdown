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
    """Parse sources.yaml and instantiate each connector."""
    if not sources_path.exists():
        return {}
    data = yaml.safe_load(sources_path.read_text(encoding="utf-8")) or {}
    connectors: dict[str, Connector] = {}
    for name, raw in data.items():
        if not isinstance(raw, dict) or "type" not in raw:
            raise ValueError(
                f"sources.yaml entry '{name}' must be a mapping with a 'type' key"
            )
        cfg: dict[str, Any] = {k: v for k, v in raw.items() if k != "type"}
        cfg["_project_root"] = project_root
        cls = get_connector_type(raw["type"])
        connectors[name] = cls(name, cfg)
    return connectors
