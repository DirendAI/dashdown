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

    A reserved top-level ``default: <source name>`` key names the project's
    **default source** (recorded on the instance as ``is_default``; ``default``
    is therefore not a usable source name). Which source actually answers a
    query with no ``connector=`` is decided by :func:`default_connector_name`
    — the named default, else a sole source. A ``default:`` naming an unknown
    source fails at load.
    """
    if not sources_path.exists():
        return {}
    data = yaml.safe_load(sources_path.read_text(encoding="utf-8")) or {}
    default_name = data.pop("default", None)
    if default_name is not None and not isinstance(default_name, str):
        raise ValueError(
            "sources.yaml: `default:` must name one of the sources "
            f"(got {default_name!r})"
        )
    connectors: dict[str, Connector] = {}
    for name, raw in data.items():
        if not isinstance(raw, dict) or "type" not in raw:
            raise ValueError(
                f"sources.yaml entry '{name}' must be a mapping with a 'type' key"
            )
        if "default" in raw:
            raise ValueError(
                f"sources.yaml source '{name}': `default` is not a per-source "
                "key — declare the default with a top-level `default: "
                f"{name}` line instead"
            )
        cfg: dict[str, Any] = {k: v for k, v in raw.items() if k != "type"}
        cfg["_project_root"] = project_root
        cls = get_connector_type(raw["type"])
        connectors[name] = cls(name, cfg)

    if default_name is not None:
        if default_name not in connectors:
            avail = ", ".join(sorted(connectors)) or "(none defined)"
            raise ValueError(
                f"sources.yaml: `default: {default_name}` names an unknown "
                f"source. Defined sources: {avail}"
            )
        connectors[default_name].is_default = True
    return connectors


def default_connector_name(connectors: dict[str, Connector]) -> str | None:
    """The source name a query with no explicit ``connector=`` resolves to.

    The source named by sources.yaml's top-level ``default:`` key, else the
    sole source when exactly one is configured. Source *names* carry no
    special meaning. ``None`` when there is no unambiguous default — with
    several sources and no ``default:`` a query must say ``connector=``
    (resolution sites raise a set-the-``default:``-key error).
    """
    for name, conn in connectors.items():
        if getattr(conn, "is_default", False):
            return name
    if len(connectors) == 1:
        return next(iter(connectors))
    return None


def no_default_error(context: str) -> ValueError:
    """The shared no-unambiguous-default error, phrased for its call site."""
    return ValueError(
        f"{context} has no connector= and the project has no default source — "
        "add a top-level `default: <source name>` line to sources.yaml or "
        "name a connector explicitly"
    )
