"""Component base + registry."""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Callable


class RenderContext:
    """Context passed to components during render.

    Holds query results so components can reference `data={name}`,
    and route params so components can access `${param}` values.
    """

    def __init__(
        self,
        queries: dict[str, Any],
        params: dict[str, str] | None = None,
        current_path: str = "/",
        static_build: bool = False,
        query_connectors: dict[str, str] | None = None,
        semantic_models: dict[str, Any] | None = None,
    ) -> None:
        self.queries = queries
        self.params = params or {}
        self.current_path = current_path
        # True during `dashdown build`: filter components (which can't re-query a
        # fixed snapshot) are omitted from the output.
        self.static_build = static_build
        # query name -> connector name for this page's queries (page-local
        # :::query blocks plus the shared query library), so components that
        # address the data API server-side (e.g. <Ask />) can bind the right
        # connector at render time.
        self.query_connectors = query_connectors or {}
        # Set by render_components when ANY filter component renders (inline or
        # bar-routed). Informational — placement is decided per-control.
        self.has_filters = False
        # Set by `filter_bar_marker` when a filter control opts INTO the top
        # filter bar (`bar` / `filter_bar=true`). The pipeline emits the
        # filter-bar slot only when this is true, so a page of purely inline
        # controls (the default) gets no top chrome (bar/chips/clear-all/drawer).
        self.has_bar_filters = False
        # Query names referenced by this page's components (each `data={name}`
        # DataRef). render_components fills this during the scan; the pipeline
        # resolves any name not defined by a local :::query against the shared
        # query library (precedence local -> library).
        self.referenced_queries: set[str] = set()
        # AskDefs registered by <Ask /> renders on this page, collected so the
        # pipeline can expose them (the static build generates one commentary
        # snapshot per def).
        self.ask_defs: list[Any] = []
        # The project's semantic models (name -> SemanticModel), so a component
        # with `metric={model.metric}` can resolve + record a
        # semantic reference for the pipeline to compile into a synthetic query.
        self.semantic_models = semantic_models or {}
        # Semantic references found on this page: synthetic query name ->
        # SemanticRef. render_page compiles each into a PythonQuerySpec, registers
        # it, and surfaces it in the client query_defs.
        self.semantic_refs: dict[str, Any] = {}

    def get_query(self, name: str):
        if name not in self.queries:
            raise KeyError(f"Query '{name}' is not defined on this page")
        return self.queries[name]


class Component(ABC):
    name: str = ""
    # Interactive filter controls (Dropdown/Search/DateRange). They drive
    # server-side SQL substitution, so they're meaningless against a static
    # snapshot and get stripped during `dashdown build`.
    is_filter: bool = False

    @abstractmethod
    def render(
        self, attrs: dict[str, Any], ctx: RenderContext, inner: str | None = None
    ) -> str:  # pragma: no cover
        ...


_COMPONENTS: dict[str, Component] = {}


def register_component(name: str) -> Callable[[type[Component]], type[Component]]:
    def deco(cls: type[Component]) -> type[Component]:
        inst = cls()
        inst.name = name
        _COMPONENTS[name] = inst
        return cls

    return deco


def get_component(name: str) -> Component | None:
    return _COMPONENTS.get(name)


def known_components() -> list[str]:
    return sorted(_COMPONENTS)
