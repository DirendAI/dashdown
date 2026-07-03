"""Data connector base classes and registry."""
from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from importlib import metadata
from typing import Any, Callable

log = logging.getLogger(__name__)

#: Entry-point group third-party packages publish connectors under. A plugin
#: declares `[project.entry-points."dashdown.connectors"]` mapping a type name
#: to `module:ConnectorClass`. The built-in csv/duckdb/dax connectors use the
#: exact same mechanism (see pyproject.toml).
ENTRY_POINT_GROUP = "dashdown.connectors"


class IntrospectionUnsupported(Exception):
    """Raised when a connector can't list its tables / describe a table's columns.

    Caught by the ``dashdown query --tables/--schema`` CLI to print a friendly
    hint (e.g. "this connector needs a dataset configured", or "use
    ``dashdown metric --list`` for a semantic model") instead of a traceback.
    """


@dataclass
class QueryResult:
    columns: list[str]
    rows: list[list[Any]]

    def to_records(self) -> list[dict[str, Any]]:
        return [dict(zip(self.columns, r)) for r in self.rows]

    @classmethod
    def from_pandas(cls, df) -> "QueryResult":
        return cls(
            columns=[str(c) for c in df.columns],
            rows=df.where(df.notnull(), None).values.tolist(),
        )

    def to_pandas(self):
        """Return this result as a pandas ``DataFrame``.

        Lazy import: pandas is a core dependency, but materializing a frame is
        only needed when an author's Python query asks for one (via the
        ``connect()`` helper), so it isn't imported at module load.
        """
        import pandas as pd

        return pd.DataFrame(self.rows, columns=self.columns)

    def to_arrow(self):
        """Return this result as a PyArrow ``Table``.

        Lazy import — pyarrow is the author's dependency (the ``dashdown-md[python]``
        / ``[dax]`` extra), brought only when a Python query calls ``.to_arrow()``.
        """
        import pyarrow as pa

        return pa.Table.from_pylist(self.to_records())


class Connector(ABC):
    """Base class for data connectors."""

    name: str = ""
    #: Set by `load_connectors` when the source carries `default: true` in
    #: sources.yaml — see `data/registry.py::default_connector_name`.
    is_default: bool = False

    def __init__(self, name: str, config: dict[str, Any]) -> None:
        self.name = name
        self.config = config

    @abstractmethod
    def query(self, sql: str) -> QueryResult:  # pragma: no cover - abstract
        ...

    def list_tables(self) -> QueryResult:
        """List the tables/views this connector can query.

        Returns a :class:`QueryResult` with ``table`` / ``schema`` / ``type``
        columns. The default speaks ANSI ``information_schema`` (works on DuckDB
        and every DB-API SQL warehouse); a non-SQL connector (Cube, DAX) or one
        that needs qualification (BigQuery) overrides this. Raises
        :class:`IntrospectionUnsupported` when introspection isn't possible.
        """
        from dashdown.data.introspect import information_schema_tables

        return information_schema_tables(self.query)

    def describe_table(self, table: str) -> QueryResult:
        """Describe one table's columns.

        Returns a :class:`QueryResult` with ``column`` / ``type`` / ``nullable``
        columns. The table name is matched as an escaped string literal, never an
        identifier (see ``data/introspect.py``). Same default/override contract as
        :meth:`list_tables`.
        """
        from dashdown.data.introspect import information_schema_columns

        return information_schema_columns(self.query, table)

    def close(self) -> None:  # pragma: no cover - default no-op
        pass


_CONNECTOR_TYPES: dict[str, type[Connector]] = {}

#: Lazily-built map of type name -> entry point, populated on first lookup.
#: ``None`` means discovery has not run yet.
_ENTRY_POINTS: dict[str, metadata.EntryPoint] | None = None


def register_connector(type_name: str) -> Callable[[type[Connector]], type[Connector]]:
    """Decorator to register a connector implementation under a type name.

    Connectors shipped inside this package (or a user's project) can register
    eagerly with this decorator. Connectors distributed as separate PyPI
    packages are discovered via the ``dashdown.connectors`` entry-point group
    instead — both paths land in the same ``_CONNECTOR_TYPES`` registry.
    """

    def deco(cls: type[Connector]) -> type[Connector]:
        _CONNECTOR_TYPES[type_name] = cls
        return cls

    return deco


def _entry_points() -> dict[str, metadata.EntryPoint]:
    """Discover (but do not load) connector entry points, cached after first call."""
    global _ENTRY_POINTS
    if _ENTRY_POINTS is None:
        found: dict[str, metadata.EntryPoint] = {}
        for ep in metadata.entry_points(group=ENTRY_POINT_GROUP):
            # First registration of a name wins; warn on collisions so a
            # plugin can't silently shadow a built-in.
            if ep.name in found:
                log.warning(
                    "Duplicate connector entry point '%s' (%s); keeping %s",
                    ep.name, ep.value, found[ep.name].value,
                )
                continue
            found[ep.name] = ep
        _ENTRY_POINTS = found
    return _ENTRY_POINTS


def _load_entry_point(type_name: str) -> type[Connector] | None:
    """Load and register the connector for ``type_name`` from its entry point."""
    ep = _entry_points().get(type_name)
    if ep is None:
        return None
    try:
        cls = ep.load()
    except ImportError as e:
        raise ImportError(
            f"Connector '{type_name}' is installed but its dependencies are not. "
            f"Install them with: pip install 'dashdown-md[{type_name}]'  (underlying error: {e})"
        ) from e
    if not (isinstance(cls, type) and issubclass(cls, Connector)):
        raise TypeError(
            f"Connector entry point '{type_name}' ({ep.value}) must point to a "
            f"Connector subclass, got {cls!r}"
        )
    _CONNECTOR_TYPES[type_name] = cls  # cache so we only load once
    return cls


def get_connector_type(type_name: str) -> type[Connector]:
    if type_name in _CONNECTOR_TYPES:
        return _CONNECTOR_TYPES[type_name]
    cls = _load_entry_point(type_name)
    if cls is not None:
        return cls
    raise KeyError(
        f"Unknown connector type '{type_name}'. Known: {known_connector_types()}"
    )


def known_connector_types() -> list[str]:
    """All connector type names, whether eagerly registered or available as plugins."""
    return sorted(set(_CONNECTOR_TYPES) | set(_entry_points()))
