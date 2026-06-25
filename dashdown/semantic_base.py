"""Semantic-layer backend protocol + registry.

The first-class metric layer (``<BarChart metric={model.metric} by={model.dim} />``)
runs over a *backend* chosen per model: BSL/Ibis (SQL warehouses), DAX (Power BI /
Fabric), or any third-party engine a package contributes. A backend implements
**exactly two operations** —

- :meth:`SemanticBackend.introspect` — fill the shared :class:`~dashdown.semantic.SemanticModelHandle`
  catalogue (measures/dimensions/lookups/time dimension/formats), and
- :meth:`SemanticBackend.build_spec` — compile a resolved
  :class:`~dashdown.semantic.SemanticRef` into a synthetic
  :class:`~dashdown.python_query.PythonQuerySpec`

— plus an optional :meth:`SemanticBackend.claims_connector` for auto-detection.
**Everything else stays backend-agnostic** and lives in :mod:`dashdown.semantic`:
``resolve_ref``, ``build_filters`` (the generic typed-filter list every backend
consumes — the layer's IR), ``semantic_filter_params``, and the synthetic-query →
``_python_def_cache`` registration. So a new backend is ~2 methods and inherits the
data API, the streaming poll loop, the static build, the server cache, and the
per-widget "filtered by" badge for free.

Backends register two ways into **one** registry, exactly like data connectors
(``dashdown.connectors`` in :mod:`dashdown.data.base`): an eager
:func:`register_semantic_backend` decorator for the in-package built-ins (``ibis``,
``dax``) and the ``dashdown.semantic_backends`` **entry-point group** for third-party
packages (discovered lazily from dist metadata). The eager registration is the
belt-and-suspenders that keeps the built-ins working in a source-tree / editable run
even before the dist metadata for the new group exists.
"""
from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from importlib import metadata
from typing import TYPE_CHECKING, Any, Callable

if TYPE_CHECKING:  # annotations only — avoid an import cycle with dashdown.semantic
    from dashdown.data.base import Connector
    from dashdown.python_query import PythonQuerySpec
    from dashdown.semantic import SemanticModelHandle, SemanticRef

log = logging.getLogger(__name__)

#: Entry-point group third-party packages publish semantic backends under — a plugin
#: declares ``[project.entry-points."dashdown.semantic_backends"]`` mapping a backend
#: name to ``module:BackendClass``. The built-in ibis/dax backends use the same
#: mechanism (see pyproject.toml), with an eager fallback registration on import.
ENTRY_POINT_GROUP = "dashdown.semantic_backends"

#: The backend used when a model declares no ``backend:`` and no registered backend
#: claims its connector — BSL/Ibis, the original first-class layer.
DEFAULT_BACKEND = "ibis"

#: pip extra carrying each *built-in* backend's heavy deps, for the missing-dep hint.
#: The backend name is **not** the extra name (``ibis`` ships in ``dashdown-md[semantic]``),
#: so — unlike the connector registry, where type == extra — the hint can't just
#: interpolate the name.
_BUILTIN_EXTRAS = {"ibis": "semantic", "cube": "cube"}


class SemanticBackend(ABC):
    """A semantic-layer engine: how a model is introspected and queried.

    Stateless singleton — one instance per registered name. Heavy dependencies
    (BSL/Ibis, an HTTP client, …) are imported **lazily inside the methods**, never
    at class load, so registering or auto-detecting a backend never pulls its extra.
    """

    name: str = ""

    def claims_connector(self, conn: Connector) -> bool:
        """True if this backend should auto-handle a model whose ``connector:`` is *conn*.

        Lets a backend own its detection (the DAX backend claims a ``DAXConnector``);
        the default ``ibis`` backend claims nothing and is the fallback. Must be
        cheap and dependency-free — it's called for every model with no explicit
        ``backend:``.
        """
        return False

    @abstractmethod
    def introspect(
        self, handle: SemanticModelHandle, connectors: dict[str, Connector]
    ) -> None:
        """Fill *handle*'s shared catalogue in place (validates the model at load)."""

    @abstractmethod
    def build_spec(
        self,
        handle: SemanticModelHandle,
        ref: SemanticRef,
        connectors: dict[str, Connector],
    ) -> PythonQuerySpec:
        """Compile a resolved reference into a synthetic ``PythonQuerySpec``.

        ``connectors`` is passed to every backend but used differently: the Ibis
        backend *captures* it (BSL owns the connection + lock-ordering), while the
        DAX backend ignores it and uses the runner-supplied ``connect`` thunk. A
        future HTTP backend (e.g. Cube) owns its own transport — the protocol
        assumes none of them in particular.
        """


_REGISTRY: dict[str, SemanticBackend] = {}

#: Lazily-built map of backend name -> entry point, populated on first lookup.
_ENTRY_POINTS: dict[str, metadata.EntryPoint] | None = None


def register_semantic_backend(
    name: str,
) -> Callable[[type[SemanticBackend]], type[SemanticBackend]]:
    """Decorator registering a built-in backend eagerly (stores a singleton instance).

    Third-party backends are discovered through the ``dashdown.semantic_backends``
    entry-point group instead — both paths land in the same registry.
    """

    def deco(cls: type[SemanticBackend]) -> type[SemanticBackend]:
        cls.name = name
        _REGISTRY[name] = cls()
        return cls

    return deco


def _entry_points() -> dict[str, metadata.EntryPoint]:
    """Discover (but do not load) backend entry points, cached after first call."""
    global _ENTRY_POINTS
    if _ENTRY_POINTS is None:
        found: dict[str, metadata.EntryPoint] = {}
        for ep in metadata.entry_points(group=ENTRY_POINT_GROUP):
            if ep.name in found:  # first registration wins; warn on a shadow
                log.warning(
                    "Duplicate semantic backend entry point '%s' (%s); keeping %s",
                    ep.name, ep.value, found[ep.name].value,
                )
                continue
            found[ep.name] = ep
        _ENTRY_POINTS = found
    return _ENTRY_POINTS


def _load_entry_point(name: str) -> SemanticBackend | None:
    """Load + register the backend for *name* from its entry point, or ``None``."""
    ep = _entry_points().get(name)
    if ep is None:
        return None
    try:
        cls = ep.load()
    except ImportError as e:
        extra = _BUILTIN_EXTRAS.get(name)
        hint = (
            f"Install it with: pip install 'dashdown-md[{extra}]'  "
            if extra
            else "Install its package's dependencies  "
        )
        raise ImportError(
            f"Semantic backend '{name}' is installed but its dependencies are not. "
            f"{hint}(underlying error: {e})"
        ) from e
    if not (isinstance(cls, type) and issubclass(cls, SemanticBackend)):
        raise TypeError(
            f"Semantic backend entry point '{name}' ({ep.value}) must point to a "
            f"SemanticBackend subclass, got {cls!r}"
        )
    inst = cls()
    inst.name = name
    _REGISTRY[name] = inst  # cache so we only load once
    return inst


def get_semantic_backend(name: str) -> SemanticBackend:
    """Resolve a backend by name (eager registry first, then entry-point load)."""
    if name in _REGISTRY:
        return _REGISTRY[name]
    inst = _load_entry_point(name)
    if inst is not None:
        return inst
    raise KeyError(
        f"Unknown semantic backend '{name}'. Known: {known_semantic_backends()}"
    )


def known_semantic_backends() -> list[str]:
    """All backend names, whether eagerly registered or available as plugins."""
    return sorted(set(_REGISTRY) | set(_entry_points()))


def detect_backend(explicit: Any, conn: Connector | None) -> str:
    """Choose a model's backend.

    An explicit ``backend:`` (case-insensitive) wins and is validated against the
    known set. Otherwise the backend is inferred from the connector: each *eagerly
    registered* backend (the built-ins) is asked ``claims_connector`` — a probe that
    raises is skipped so one broken backend can't break detection — and the first
    claimant wins; failing that, :data:`DEFAULT_BACKEND`. (Auto-detection only
    consults built-ins, never loads a third-party plugin — a plugin model selects
    itself with an explicit ``backend:``.)
    """
    if explicit:
        name = str(explicit).lower()
        if name not in known_semantic_backends():
            raise ValueError(
                f"semantic model: unknown backend {explicit!r} "
                f"(known: {known_semantic_backends()})"
            )
        return name
    if conn is not None:
        for name, backend in list(_REGISTRY.items()):
            if name == DEFAULT_BACKEND:
                continue
            try:
                if backend.claims_connector(conn):
                    return name
            except Exception:  # a probe must never break detection
                continue
    return DEFAULT_BACKEND
