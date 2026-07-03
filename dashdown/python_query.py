"""Python queries — ``queries/**/*.py`` defined as a decorated entry function.

The shared query library lets an author define a query once in ``queries/`` and
reference it by name. A ``.py`` file is a **third body language** alongside
``.sql``/``.dax``: a file whose ``@query``-decorated function
*returns a table* (PyArrow / pandas / Polars / ``QueryResult`` / list-of-dicts).
The engine is the author's choice — forecasts, ML scoring, cross-connector joins,
external-API pulls, pandas/Polars reshapes — and **Arrow is the normalization
hub**: whatever the function returns becomes the same ``{columns, rows}`` payload
the wire already speaks.

This file owns the decorator, the loader, the return-normalizer and the runner.
The *registry* (the parallel ``_python_def_cache``) lives in ``render/pipeline.py``
next to ``_query_def_cache``/``_stream_def_cache`` — keeping pipeline free of a
hard dependency on this module (it stores specs as opaque objects) so there's no
import cycle.

**Trust boundary.** A ``queries/*.py`` file is author Python, imported at load
time (``spec_from_file_location`` + ``exec_module``) so its ``@query`` decorator
runs and registers metadata — exactly the same trust boundary as a custom
``components/*.py`` (already ``exec``'d by ``project.py::_import_user_modules``).
No *new* code-execution surface is introduced. A managed / multi-tenant host that
must refuse semi-trusted code turns the whole feature off with
``python_queries: { enabled: false }`` (see ``project.py``).

**Params are data, never code.** The entry function receives ``params`` as a
runtime ``dict[str, str]`` (the merged filter + route values), so the ``${param}``
injection surface that the SQL path defends against simply *does not exist* for a
Python body. Author-built SQL passed to ``connect(name, sql, params=…)`` opts back
into the framework's one blessed ``_substitute_params`` and inherits the identical
escaping; raw ``connect(name, sql)`` with no ``params`` is "you wrote it, you own
it", the same contract as authoring a ``.sql`` file.
"""
from __future__ import annotations

import importlib.util
import logging
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from dashdown.data.base import Connector, QueryResult
from dashdown.query_library import derive_query_name
from dashdown.render.pipeline import _substitute_params

log = logging.getLogger(__name__)

# Attribute the decorator stamps onto the entry function with its metadata. The
# loader scans a module's globals for callables carrying it.
_QUERY_MARKER = "__dashdown_query__"


@dataclass
class PythonQuerySpec:
    """A loaded ``queries/*.py`` entry point — the callable plus its metadata.

    Mirrors :class:`dashdown.render.markdown.QuerySpec` (``name``/``connector``/
    ``cache_ttl``/``live``/``interval``/``description``) so the render pipeline,
    data API, streaming and build paths can treat the two interchangeably for
    everything except *how the rows are produced* — there's no ``sql`` body here,
    just ``fn``. ``connector`` is the identity/cache-key namespace and the default
    target for the ``connect()`` helper; a Python query may read zero or many
    connectors.
    """

    name: str
    connector: str
    fn: Callable[[dict[str, str], Callable[..., QueryResult]], Any]
    cache_ttl: int | None = None
    live: bool = False
    interval: int | None = None
    description: str | None = None


def query(
    *,
    connector: str | None = None,
    cache_ttl: int | None = None,
    live: bool = False,
    interval: int | None = None,
    description: str | None = None,
) -> Callable[[Callable], Callable]:
    """Decorator marking a ``queries/*.py`` function as a Python query entry point.

    Exported as ``from dashdown import query``. Mirrors ``@register_component`` /
    ``@register_connector``: it only attaches metadata (and marks the function) —
    the loader reads it. The query's *name* comes from the file path (dotted, like
    ``.sql``/``.dax``), **not** the function name, so the function may be called
    anything.

    Usage::

        from dashdown import query

        @query(connector="main", cache_ttl=300)
        def revenue_forecast(params, connect):
            history = connect(
                "main",
                "SELECT day, revenue FROM sales WHERE region = ${region}",
                params=params,
            ).to_pandas()
            ...
            return history  # DataFrame / Arrow / Polars / QueryResult / list-of-dicts
    """

    def deco(fn: Callable) -> Callable:
        setattr(
            fn,
            _QUERY_MARKER,
            {
                "connector": str(connector) if connector is not None else "",
                "cache_ttl": cache_ttl,
                "live": bool(live),
                "interval": interval,
                "description": description,
            },
        )
        return fn

    return deco


def _find_entry_function(module: Any, path: Path) -> Callable:
    """Return the single ``@query``-decorated callable in ``module``.

    Enforces "one decorated entry function per file" — zero or more than one is a
    fail-at-startup ``ValueError`` (parity with a duplicate-name collision or a
    malformed ``auth:`` block), so a typo'd decorator surfaces loudly instead of
    silently shipping no query.
    """
    found = [
        obj
        for obj in vars(module).values()
        if callable(obj) and hasattr(obj, _QUERY_MARKER)
    ]
    if not found:
        raise ValueError(
            f"python query file {path} defines no @query-decorated function "
            f"(did you forget `from dashdown import query`?)"
        )
    if len(found) > 1:
        names = ", ".join(sorted(fn.__name__ for fn in found))
        raise ValueError(
            f"python query file {path} defines multiple @query functions "
            f"({names}); exactly one entry function per file is allowed"
        )
    return found[0]


def parse_python_query_file(path: Path, name: str) -> PythonQuerySpec:
    """Import one ``queries/**/*.py`` file and capture its entry point.

    Reuses ``project.py::_import_user_modules``' mechanism
    (``spec_from_file_location`` + ``exec_module``, a path-keyed module name so
    same-named files in different folders don't collide). The module is imported
    standalone (no package context) — use absolute imports, not relative ones,
    exactly like a colocated custom component. An import error propagates so the
    project fails to load (fail-fast: a broken query must not ship silently).
    """
    mod_name = "_dashdown_query_" + name.replace(".", "_")
    spec = importlib.util.spec_from_file_location(mod_name, path)
    if spec is None or spec.loader is None:  # pragma: no cover - defensive
        raise ValueError(f"could not load python query module: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = module
    spec.loader.exec_module(module)

    fn = _find_entry_function(module, path)
    meta = getattr(fn, _QUERY_MARKER)
    return PythonQuerySpec(
        name=name,
        connector=str(meta.get("connector") or ""),
        fn=fn,
        cache_ttl=meta.get("cache_ttl"),
        live=bool(meta.get("live")),
        interval=meta.get("interval"),
        description=meta.get("description"),
    )


def load_python_queries(
    queries_dir: Path, reserved_names: set[str] | None = None
) -> dict[str, PythonQuerySpec]:
    """Scan ``queries_dir`` recursively for ``*.py`` into ``{name: PythonQuerySpec}``.

    - Names are derived via :func:`dashdown.query_library.derive_query_name`
      (``ml/churn.py`` → ``ml.churn``), the same path-as-dotted-name rule as
      ``.sql``/``.dax``.
    - ``_``-prefixed files are skipped (shared helpers / ``__init__.py``), mirroring
      ``_import_user_modules``.
    - Path-traversal guard: a resolved file escaping ``queries_dir`` (e.g. a
      symlink) raises ``ValueError`` — same posture as ``load_queries``.
    - ``reserved_names`` are the SQL/DAX query names already loaded; a Python file
      deriving one of them collides and raises (fail-at-startup), so ``foo.sql``
      and ``foo.py`` can't both claim ``foo``.
    - An import error / missing-or-duplicate ``@query`` propagates (fail-fast).
    """
    if not queries_dir.is_dir():
        return {}

    reserved = reserved_names or set()
    root = queries_dir.resolve()
    out: dict[str, PythonQuerySpec] = {}
    sources: dict[str, Path] = {}

    for path in sorted(root.rglob("*.py")):
        if not path.is_file() or path.name.startswith("_") or "__pycache__" in path.parts:
            continue
        resolved = path.resolve()
        try:
            rel = resolved.relative_to(root)
        except ValueError as exc:
            raise ValueError(
                f"python query file escapes queries/ directory: {path}"
            ) from exc

        name = derive_query_name(rel)
        if name in reserved:
            raise ValueError(
                f"python query {name!r} ({path.name}) collides with a "
                f".sql/.dax query of the same derived name"
            )
        if name in out:
            raise ValueError(
                f"duplicate query name {name!r}: defined by both "
                f"{sources[name].name} and {path.name} under {queries_dir}"
            )
        out[name] = parse_python_query_file(resolved, name)
        sources[name] = path

    return out


def _coerce_columns_rows(columns: list[Any], rows: list[list[Any]]) -> QueryResult:
    return QueryResult(columns=[str(c) for c in columns], rows=rows)


def normalize_to_query_result(obj: Any) -> QueryResult:
    """Coerce whatever a Python query returned into a :class:`QueryResult`.

    **Arrow-preferred, duck-typed, no hard imports** — the *author* brings the
    engine (pandas is core; pyarrow/polars are theirs), so this recognizes shapes
    by attribute, never by importing the library. Recognized, in order:

    - a ``QueryResult`` (returned verbatim);
    - **pandas** ``DataFrame`` (``hasattr(obj, "iloc")``) → ``from_pandas``;
    - **Polars** ``DataFrame`` (``hasattr(obj, "to_arrow")`` but not pandas) →
      ``.to_arrow()`` then the Arrow path;
    - **PyArrow** ``Table`` / ``Dataset`` / ``RecordBatchReader`` → its rows as a
      list-of-dicts (``to_pylist`` / ``to_table`` / ``read_all``);
    - a **list of dicts** (column union preserving first-seen order).

    A bare list of lists/tuples is intentionally *not* supported — it carries no
    column names, so list-of-dicts (or a frame/table) is the contract.

    Cells are left as-is; the JSON coercion (Decimal/NaN/datetime/numpy) happens
    later in :func:`dashdown.render.pipeline.serialize_value`.
    """
    if obj is None:
        raise ValueError("python query returned None (expected a table)")

    # 1) Already in the wire shape.
    if isinstance(obj, QueryResult):
        return obj

    # 2) pandas DataFrame — `.iloc` is the discriminator (a Series also has it,
    #    but `from_pandas` reads `.columns`, which a Series lacks → handled below).
    if hasattr(obj, "iloc") and hasattr(obj, "columns"):
        return QueryResult.from_pandas(obj)

    # 3) Polars DataFrame — `.to_arrow()` and NOT pandas (checked above). Convert
    #    to Arrow and fall through to the Arrow handling.
    if hasattr(obj, "to_arrow") and not hasattr(obj, "iloc"):
        obj = obj.to_arrow()

    # 4) PyArrow Table / Dataset / RecordBatchReader → list-of-dicts via to_pylist.
    if hasattr(obj, "to_pylist"):  # pa.Table / pa.RecordBatch
        return _records_to_result(obj.to_pylist())
    if hasattr(obj, "read_all"):  # pa.RecordBatchReader
        return _records_to_result(obj.read_all().to_pylist())
    if hasattr(obj, "to_table"):  # pa.dataset.Dataset
        return _records_to_result(obj.to_table().to_pylist())

    # 5) list-of-dicts (or any sequence of mappings).
    if isinstance(obj, (list, tuple)):
        return _records_to_result(list(obj))

    raise TypeError(
        f"python query returned an unsupported type {type(obj).__name__!r}; "
        f"return a pandas/Polars DataFrame, a PyArrow Table, a QueryResult, "
        f"or a list of dicts"
    )


def _records_to_result(records: list[Any]) -> QueryResult:
    """Build a ``QueryResult`` from a list of dict-like rows.

    Columns are the union of keys in first-seen order (so a ragged result still
    lines up), and each row is laid out in that column order with missing keys as
    ``None``. An empty list yields an empty result.
    """
    if not records:
        return QueryResult(columns=[], rows=[])

    columns: list[str] = []
    seen: set[str] = set()
    for rec in records:
        if not hasattr(rec, "keys"):
            raise TypeError(
                "python query returned a list whose items are not dicts; "
                "return a list of dicts (or a DataFrame / Arrow table)"
            )
        for k in rec.keys():
            ks = str(k)
            if ks not in seen:
                seen.add(ks)
                columns.append(ks)

    rows = [[rec.get(c) for c in columns] for rec in records]
    return _coerce_columns_rows(columns, rows)


def make_connect(connectors: dict[str, Connector]) -> Callable[..., QueryResult]:
    """Build the ``connect(name, sql, params=None)`` helper handed to a query fn.

    Runs ``sql`` against the named project connector and returns its
    ``QueryResult`` (which exposes ``.to_pandas()`` / ``.to_arrow()`` for the
    author's engine). ``params=`` opts the author-built SQL into the framework's
    one blessed, injection-safe ``_substitute_params`` (the *exact* escaping the
    ``.sql`` path uses); omitting ``params`` runs the SQL verbatim — "you wrote
    it, you own it", the same as authoring a ``.sql`` file.
    """

    def connect(
        name: str, sql: str, params: dict[str, str] | None = None
    ) -> QueryResult:
        conn = connectors.get(name)
        if conn is None:
            raise KeyError(
                f"connect(): unknown connector {name!r}. "
                f"Known: {sorted(connectors)}"
            )
        final_sql = _substitute_params(sql, params) if params is not None else sql
        return conn.query(final_sql)

    return connect


def run_python_query(
    spec: PythonQuerySpec,
    params: dict[str, str],
    connectors: dict[str, Connector],
) -> QueryResult:
    """Execute a Python query and normalize its return to a ``QueryResult``.

    Calls ``spec.fn(params, connect)`` where ``params`` is the merged filter+route
    values (a plain ``dict[str, str]`` — data, never substituted into the body)
    and ``connect`` is :func:`make_connect`. The blocking call is the caller's to
    schedule off the event loop (the data API / poller / build all run it in a
    threadpool, like every connector query).
    """
    connect = make_connect(connectors)
    raw = spec.fn(dict(params), connect)
    return normalize_to_query_result(raw)
