"""Registry-introspected catalog of components and connectors.

A dense, **drift-proof** reference of every built-in (and any plugin-registered)
component and connector, built by introspecting the registries themselves rather
than by hand. It answers the highest-frequency authoring lookups — "what attrs
does ``<BarChart>`` take," "what config keys does ``postgres`` need" — without
re-reading prose docs.

``build_catalog()`` is the single source of truth: the ``dashdown components`` CLI
command prints it (table/JSON), and it is the seam a future MCP server / generated
``references/catalog.md`` wraps with no duplicated logic.

The attribute list for a component is recovered by AST-analyzing its ``render``
method: every ``attr_str(attrs, "x")`` / ``attrs.get("x")`` / ``attrs["x"]`` /
``"x" in attrs`` access — following the shared ``_chart_html`` helper and the
``_util`` accessor/helper functions (``format_config``, ``grid_span_style``,
``resolve_semantic`` …) so charts surface their full attribute set. Connector
config keys are recovered the same way from ``self.config.get("k")`` reads. This
means a new attr/config key shows up in the catalog with **no** hand edit — and
can't drift from the code.
"""
from __future__ import annotations

import ast
import importlib.util
import inspect
from functools import lru_cache
from pathlib import Path
from typing import Any

# --- attribute-access introspection ----------------------------------------

#: Accessor helpers from ``components/builtin/_util.py`` (and one module-local
#: ``_ref_name``): the attr key is the string literal at this positional index
#: (the accessors all take ``(attrs, "key", default=...)``).
_ACCESSOR_KEY_INDEX = {
    "attr_str": 1,
    "attr_bool": 1,
    "attr_int": 1,
    "attr_float": 1,
    "ref_str": 1,
    "ref_or_literal": 1,
    "_ref_name": 1,
}

#: Shared helpers that read a *fixed* set of attrs internally — expanded when a
#: render method calls them (they live in ``_util`` and don't take the key as an
#: argument). ``resolve_semantic`` is handled separately (its keys are
#: overridable per call), so it is not listed here.
_HELPER_ATTRS = {
    "format_config": {"format", "currency", "decimals", "locale", "date_format"},
    "grid_span_style": {"col-span", "span"},
    "resolve_dataset": {"data"},
    "filter_bar_marker": {"bar", "filter_bar"},
    "to_filter_bar": {"bar", "filter_bar"},
    "resolve_debounce": {"debounce"},
    # The multi-measure semantic helper for *role* charts (Candlestick/Heatmap/
    # Sankey/Graph/Parallel/Combo). Its measures/by/series come from the caller's
    # role attrs (surfaced where the render method reads them), but it always reads
    # `grain` itself (via ref_or_literal), so every caller accepts a `grain=`.
    "resolve_semantic_query": {"grain"},
    # The shared placeholder for the SVG geo maps (components/builtin/_map_base.py):
    # data/title plus the common basemap + color attrs every map accepts, plus
    # the `explain` affordance knobs it shares with the charts (the ✨ button
    # + AI commentary footer; `annotations` opts a bubble/dot map's marks off).
    "_map_html": {
        "data", "title", "scheme", "color", "scale", "map", "geojson",
        "id_field", "empty_message", "height", "col-span", "span",
        "explain", "cache_ttl", "max_rows", "annotations",
    },
}

#: ``resolve_semantic`` parameter -> default attr key it reads. Each is
#: overridable at the call site (``metric_key="sparkline"``), and ``series_key``
#: may be ``None`` to disable the second dimension.
_SEMANTIC_KEY_PARAMS = {
    "metric_key": "metric",
    "by_key": "by",
    "series_key": "series",
    "grain_key": "grain",
}


def _call_name(call: ast.Call) -> str | None:
    """Simple function name of a Call (``f(...)`` -> ``"f"``), else None."""
    if isinstance(call.func, ast.Name):
        return call.func.id
    return None


def _is_attrs_get(call: ast.Call) -> bool:
    """``attrs.get("key"[, default])`` access."""
    f = call.func
    return (
        isinstance(f, ast.Attribute)
        and f.attr == "get"
        and isinstance(f.value, ast.Name)
        and f.value.id == "attrs"
    )


def _str_at(call: ast.Call, index: int) -> str | None:
    if len(call.args) > index:
        arg = call.args[index]
        if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
            return arg.value
    return None


def _const_str(node: ast.AST) -> str | None:
    if isinstance(node, ast.Index):  # pragma: no cover - <py3.9 shape
        node = node.value  # type: ignore[attr-defined]
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _semantic_keys(call: ast.Call) -> set[str]:
    """Attr keys a ``resolve_semantic(...)`` call reads, honoring kw overrides."""
    values = dict(_SEMANTIC_KEY_PARAMS)
    for kw in call.keywords:
        if kw.arg in values and isinstance(kw.value, ast.Constant):
            values[kw.arg] = kw.value.value  # str or None (series_key=None)
    return {v for v in values.values() if isinstance(v, str)}


def _direct_keys(node: ast.AST) -> tuple[set[str], set[str]]:
    """Walk a function body, returning (attr keys read, function names called).

    The caller resolves the called names against module-local functions (to
    recurse, e.g. into ``_chart_html``) and the shared ``_chart_html`` helper.
    """
    keys: set[str] = set()
    calls: set[str] = set()
    for n in ast.walk(node):
        if isinstance(n, ast.Call):
            name = _call_name(n)
            if name == "resolve_semantic":
                keys |= _semantic_keys(n)
            elif name in _HELPER_ATTRS:
                keys |= _HELPER_ATTRS[name]
            elif name in _ACCESSOR_KEY_INDEX:
                k = _str_at(n, _ACCESSOR_KEY_INDEX[name])
                if k:
                    keys.add(k)
            elif _is_attrs_get(n):
                k = _str_at(n, 0)
                if k:
                    keys.add(k)
            elif name is not None:
                calls.add(name)
        elif isinstance(n, ast.Subscript):
            if isinstance(n.value, ast.Name) and n.value.id == "attrs":
                k = _const_str(n.slice)
                if k:
                    keys.add(k)
        elif isinstance(n, ast.Compare):
            # `"key" in attrs`
            if (
                any(isinstance(op, ast.In) for op in n.ops)
                and isinstance(n.left, ast.Constant)
                and isinstance(n.left.value, str)
                and any(
                    isinstance(c, ast.Name) and c.id == "attrs" for c in n.comparators
                )
            ):
                keys.add(n.left.value)
    return keys, calls


@lru_cache(maxsize=None)
def _module_funcs(path: str) -> dict[str, ast.FunctionDef]:
    """Top-level function defs in a source file, keyed by name."""
    tree = ast.parse(Path(path).read_text(encoding="utf-8"))
    return {
        n.name: n
        for n in tree.body
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
    }


@lru_cache(maxsize=None)
def _chart_base_attrs() -> frozenset[str]:
    """Attrs read by the shared ``_chart_html`` helper in ``line_chart.py``.

    Every chart component delegates to it (some import it cross-module), so its
    attr set — data/x/y/series/title/format/… — is grafted onto each chart.
    """
    from dashdown.components.builtin import line_chart

    path = inspect.getsourcefile(line_chart)
    funcs = _module_funcs(path)
    node = funcs.get("_chart_html")
    if node is None:  # pragma: no cover - defensive
        return frozenset()
    return frozenset(_attrs_from_func(node, path, {"_chart_html"}))


@lru_cache(maxsize=None)
def _chart_card_attrs() -> frozenset[str]:
    """Attrs read by the shared ``_chart_card`` shell in ``line_chart.py``
    (height/explain/…). ComboChart calls it cross-module — its bespoke config
    path skips ``_chart_html`` — so it's followed by name the same way."""
    from dashdown.components.builtin import line_chart

    path = inspect.getsourcefile(line_chart)
    funcs = _module_funcs(path)
    node = funcs.get("_chart_card")
    if node is None:  # pragma: no cover - defensive
        return frozenset()
    return frozenset(_attrs_from_func(node, path, {"_chart_card"}))


def _attrs_from_func(
    node: ast.FunctionDef, path: str, visited: set[str]
) -> set[str]:
    """Attrs read by a function, recursing into module-local callees."""
    keys, calls = _direct_keys(node)
    funcs = _module_funcs(path)
    for name in calls:
        if name == "_chart_html":
            keys |= _chart_base_attrs()
        elif name == "_chart_card":
            keys |= _chart_card_attrs()
        elif name in funcs and name not in visited:
            visited.add(name)
            keys |= _attrs_from_func(funcs[name], path, visited)
    return keys


def _component_attrs(cls: type) -> list[str]:
    """Sorted attr names a component's ``render`` method reads."""
    try:
        path = inspect.getsourcefile(cls)
    except TypeError:  # pragma: no cover - dynamically-created class, no source
        return []
    if path is None:  # pragma: no cover - defensive
        return []
    try:
        tree = ast.parse(Path(path).read_text(encoding="utf-8"))
    except (OSError, SyntaxError):  # pragma: no cover - defensive
        return []
    render = None
    for cnode in ast.walk(tree):
        if isinstance(cnode, ast.ClassDef) and cnode.name == cls.__name__:
            for item in cnode.body:
                if isinstance(item, ast.FunctionDef) and item.name == "render":
                    render = item
                    break
            break
    if render is None:  # render inherited, not defined on this class
        return []
    return sorted(_attrs_from_func(render, path, {"render"}))


def _summary(doc: str | None) -> str:
    """One-line summary from a docstring: its first paragraph, collapsed.

    Joins the leading non-blank lines (up to the first blank line) so a
    hard-wrapped summary sentence isn't truncated mid-clause.
    """
    if not doc:
        return ""
    lines: list[str] = []
    for line in inspect.cleandoc(doc).splitlines():
        if not line.strip():
            break
        lines.append(line.strip())
    summary = " ".join(lines)
    return summary if len(summary) <= 200 else summary[:199].rstrip() + "…"


# --- public API -------------------------------------------------------------


def build_component_catalog() -> list[dict[str, Any]]:
    """One row per registered component: name, summary, is_filter, attrs."""
    from dashdown.components.base import get_component, known_components

    rows: list[dict[str, Any]] = []
    for name in known_components():
        inst = get_component(name)
        cls = type(inst)
        rows.append(
            {
                "name": name,
                "summary": _summary(cls.__doc__),
                "is_filter": bool(getattr(inst, "is_filter", False)),
                "attrs": _component_attrs(cls),
            }
        )
    rows.sort(key=lambda r: r["name"])
    return rows


def _is_config_ref(node: ast.AST, aliases: set[str]) -> bool:
    """Is this node a reference to the connector config dict?

    Matches ``self.config``, the bare ``config`` constructor param, and any local
    alias of them (``cfg = self.config`` — how the mssql connector reads keys).
    """
    if isinstance(node, ast.Name) and node.id in aliases:
        return True
    return (
        isinstance(node, ast.Attribute)
        and node.attr == "config"
        and isinstance(node.value, ast.Name)
        and node.value.id == "self"
    )


def _attr_or_name(node: ast.AST) -> str | None:
    """Simple name of ``self._FIELDS`` -> ``"_FIELDS"`` or ``FIELDS`` -> ``"FIELDS"``."""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
        return node.attr
    return None


def _connector_config_keys(class_node: ast.ClassDef) -> list[str]:
    """Config keys a connector class reads: ``self.config.get("k")`` etc.

    Beyond the direct ``self.config.get("k")`` / ``self.config["k"]`` / ``"k" in
    self.config`` accesses, this resolves two indirections the built-ins use:
    a local alias (``cfg = self.config``, mssql) and a class-level string tuple
    iterated to read passthrough keys (``self.config[k] for k in self._FIELDS``,
    snowflake).
    """
    keys: set[str] = set()
    aliases = {"config"}  # the constructor param name
    str_tuples: dict[str, list[str]] = {}  # class-level NAME = ("a", "b", …)

    # Pass 1: collect config aliases and class-level string tuples/lists.
    for n in ast.walk(class_node):
        if not isinstance(n, ast.Assign):
            continue
        if _is_config_ref(n.value, aliases):
            for t in n.targets:
                if isinstance(t, ast.Name):
                    aliases.add(t.id)
        elif isinstance(n.value, (ast.Tuple, ast.List)):
            elts = n.value.elts
            strs = [e.value for e in elts if isinstance(e, ast.Constant) and isinstance(e.value, str)]
            if strs and len(strs) == len(elts):
                for t in n.targets:
                    if isinstance(t, ast.Name):
                        str_tuples[t.id] = strs

    # Pass 2: direct reads + comprehensions that iterate a string tuple to index config.
    for n in ast.walk(class_node):
        if isinstance(n, ast.Call):
            f = n.func
            if isinstance(f, ast.Attribute) and f.attr == "get" and _is_config_ref(f.value, aliases):
                k = _str_at(n, 0)
                if k:
                    keys.add(k)
        elif isinstance(n, ast.Subscript) and _is_config_ref(n.value, aliases):
            k = _const_str(n.slice)
            if k:
                keys.add(k)
        elif isinstance(n, ast.Compare):
            if (
                any(isinstance(op, ast.In) for op in n.ops)
                and isinstance(n.left, ast.Constant)
                and isinstance(n.left.value, str)
                and any(_is_config_ref(c, aliases) for c in n.comparators)
            ):
                keys.add(n.left.value)
        elif isinstance(n, (ast.DictComp, ast.ListComp, ast.SetComp, ast.GeneratorExp)):
            # `{k: self.config[k] for k in self._FIELDS}` — add _FIELDS' strings if
            # the comprehension actually reads config with its loop variable.
            reads_config = any(
                (isinstance(sub, ast.Subscript) and _is_config_ref(sub.value, aliases))
                or (
                    isinstance(sub, ast.Call)
                    and isinstance(sub.func, ast.Attribute)
                    and sub.func.attr == "get"
                    and _is_config_ref(sub.func.value, aliases)
                )
                for sub in ast.walk(n)
            )
            if reads_config:
                for gen in n.generators:
                    name = _attr_or_name(gen.iter)
                    if name in str_tuples:
                        keys.update(str_tuples[name])

    keys.discard("_project_root")  # framework-internal, injected by load_connectors
    return sorted(keys)


@lru_cache(maxsize=1)
def _provides_extras() -> frozenset[str]:
    """Optional-dependency extra names declared by the installed package.

    A connector's install extra is named after its type by convention (the
    missing-dep hint in ``data/base.py`` does the same), so an extra exists for a
    type iff the type name is in this set.
    """
    try:
        from importlib import metadata

        # Distribution (PyPI) name is "dashdown-md"; the import package is "dashdown".
        return frozenset(metadata.metadata("dashdown-md").get_all("Provides-Extra") or [])
    except Exception:  # pragma: no cover - metadata unavailable (uninstalled)
        return frozenset()


def build_connector_catalog() -> list[dict[str, Any]]:
    """One row per connector type: type, extra, summary, config_keys.

    Source files are AST-parsed (not imported), so a connector whose heavy
    driver isn't installed still appears with its config keys.
    """
    from importlib import metadata

    extras = _provides_extras()
    eps = {ep.name: ep for ep in metadata.entry_points(group="dashdown.connectors")}
    rows: list[dict[str, Any]] = []
    for type_name in sorted(eps):
        module_path, _, class_name = eps[type_name].value.partition(":")
        row: dict[str, Any] = {
            "type": type_name,
            "extra": type_name if type_name in extras else None,
            "summary": "",
            "config_keys": [],
        }
        try:
            spec = importlib.util.find_spec(module_path)
        except (ImportError, ValueError):  # pragma: no cover - broken plugin ep
            spec = None
        if spec is not None and spec.origin:
            try:
                source = Path(spec.origin).read_text(encoding="utf-8")
                tree = ast.parse(source)
            except (OSError, SyntaxError):  # pragma: no cover - defensive
                rows.append(row)
                continue
            class_node = next(
                (
                    c
                    for c in ast.walk(tree)
                    if isinstance(c, ast.ClassDef) and c.name == class_name
                ),
                None,
            )
            doc = ast.get_docstring(class_node) if class_node else None
            row["summary"] = _summary(doc) or _summary(ast.get_docstring(tree))
            if class_node is not None:
                row["config_keys"] = _connector_config_keys(class_node)
        rows.append(row)
    return rows


def build_catalog() -> dict[str, list[dict[str, Any]]]:
    """The full introspected catalog: components + connectors.

    The single function the ``dashdown components`` CLI and any future MCP
    wrapper share — no duplicated introspection logic.
    """
    return {
        "components": build_component_catalog(),
        "connectors": build_connector_catalog(),
    }
