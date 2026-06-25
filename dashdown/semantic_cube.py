"""Cube backend for the first-class semantic layer.

[Cube](https://cube.dev) is a standalone semantic-layer *server*: a team models
measures/dimensions/joins **in Cube**, and Cube exposes them over a structured JSON
query API. This module makes those models reachable through Dashdown's metric grammar
(``<BarChart metric={orders.revenue} by={orders.status} />``) as a **third
``semantic/`` backend**, alongside BSL/Ibis and Fabric/DAX, via the
:class:`~dashdown.semantic_base.SemanticBackend` registry.

A backend is exactly two methods — ``introspect`` (fill the shared catalogue) and
``build_spec`` (compile a resolved reference into a synthetic
:class:`~dashdown.python_query.PythonQuerySpec`). Everything downstream — the data
API, the streaming poll loop, the static build, the server cache, the per-widget
"filtered by" badge — is inherited unchanged. So Cube is ~2 methods plus a pure query
builder and a ``/meta`` parser.

**Config-free via ``/meta``.** Cube *publishes* its model: ``introspect()`` does a
live ``GET /meta`` and auto-populates the catalogue, so a ``semantic/*.yml`` is as
small as ``orders: { connector: cube }``. An optional
``dimensions:`` block adds **granularity aliases** (``month: { member: orders.createdAt,
granularity: month }``) so a time series can name its bucket.

**No injection surface — JSON, not a query string.** :func:`build_cube_query` assembles
a Python ``dict`` (``measures``/``dimensions``/``timeDimensions``/``filters``/
``order``/``limit``) that the connector serializes as the request body. Filter *values*
arrive from :func:`dashdown.semantic.build_filters` as typed data and stay data — there
is **no ``_substitute_params``, no DAX-style string escaping**, nothing to get wrong.
This is the strongest of the three backends on the security axis (the tests assert no
``${param}`` substitution touches this path).

**Filter routing.** ``build_filters`` already emits the layer's IR
(``{field, operator:"in", values}`` and the time-dim ``>=``/``<=`` pair); Cube maps it
~1:1: ``in`` → ``{member, operator:"equals", values}``, and the ``>=``/``<=`` pair
**collapses** into a single ``timeDimensions[].dateRange``. A time-type ``by``/``series``
dimension routes to ``timeDimensions[].granularity`` (not ``dimensions[]``); ``/meta``
types tell us which is which.

**Column rename.** Cube returns rows keyed by member id (a time grouping keyed
``member.granularity``) plus a parallel ``annotation`` block. :meth:`CubeBackend.build_spec`
renames those to the canonical ``[by, [series], *metrics]`` the component reads — the
same trick the DAX backend uses, with the annotation as the authoritative key set and a
positional fallback under a length guard.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from dashdown.semantic_base import SemanticBackend, register_semantic_backend

if TYPE_CHECKING:  # annotations only — avoid a runtime import cycle with dashdown.semantic
    from dashdown.data.base import Connector
    from dashdown.python_query import PythonQuerySpec
    from dashdown.semantic import SemanticModelHandle, SemanticRef

log = logging.getLogger(__name__)

#: Granularity applied to a time-type ``by``/``series`` dimension that carries no
#: explicit one — so "put a date on the x-axis" produces a grouped series rather than
#: a filter-only time dimension. Overridable per model with ``granularity:``.
_DEFAULT_GRANULARITY = "day"


# --------------------------------------------------------------------------- #
# Introspection — read the catalogue from Cube's /meta
# --------------------------------------------------------------------------- #


def _format_from_cube(spec: dict[str, Any]) -> dict[str, Any]:
    """Map a Cube measure's ``format``/``meta`` to our display-format hint.

    Cube's ``format`` is a string (``"currency"``/``"percent"``/…); a measure's
    ``meta`` may carry explicit ``currency``/``decimals``. Returns the
    ``{format, currency, decimals}`` subset the component understands (empty if
    there's nothing to say).
    """
    out: dict[str, Any] = {}
    fmt = spec.get("format")
    if fmt in ("currency", "percent"):
        out["format"] = fmt
    meta = spec.get("meta") or {}
    if isinstance(meta, dict):
        for k in ("currency", "decimals"):
            if k in meta:
                out[k] = meta[k]
    return out


def parse_cube_meta(meta_json: dict[str, Any]) -> dict[str, Any]:
    """Parse a Cube ``/meta`` document into a backend-neutral catalogue.

    Walks every cube/view in ``meta_json["cubes"]`` and collects its measures and
    dimensions by **member id** (Cube's fully-qualified ``cube.member`` name, which
    is what query results are keyed by). Pure — no handle, no network — so it's
    unit-testable over a captured ``/meta`` JSON.

    Returns ``{"dimensions": {member: {type}}, "measures": {member: {format}},
    "time_members": {member, …}, "time_dimension": member|None}`` — the first
    time-type dimension becomes the primary time dimension (the one the global date
    range maps onto).
    """
    dimensions: dict[str, dict[str, Any]] = {}
    measures: dict[str, dict[str, Any]] = {}
    time_members: set[str] = set()
    time_dimension: str | None = None

    for cube in meta_json.get("cubes") or []:
        if not isinstance(cube, dict):
            continue
        for dim in cube.get("dimensions") or []:
            member = dim.get("name")
            if not member:
                continue
            dtype = dim.get("type")
            dimensions[member] = {"type": dtype}
            if dtype == "time":
                time_members.add(member)
                if time_dimension is None:
                    time_dimension = member
        for meas in cube.get("measures") or []:
            member = meas.get("name")
            if not member:
                continue
            measures[member] = {"format": _format_from_cube(meas)}

    return {
        "dimensions": dimensions,
        "measures": measures,
        "time_members": time_members,
        "time_dimension": time_dimension,
    }


def _alias_member_and_granularity(spec: Any) -> tuple[str | None, str | None]:
    """Read a YAML granularity-alias spec into ``(member, granularity)``.

    A dimension alias is a mapping ``{member: "orders.createdAt", granularity:
    month}`` — naming a time bucket so ``by={orders.month}`` is expressible. Returns
    ``(None, None)`` for an unusable spec (the caller raises with context).
    """
    if isinstance(spec, dict):
        return spec.get("member"), spec.get("granularity")
    return None, None


def build_cube_catalogue(meta_json: dict[str, Any], cfg: dict[str, Any]) -> dict[str, Any]:
    """Combine the live ``/meta`` catalogue with any YAML granularity aliases.

    Returns the ``cube_meta`` payload stashed on the handle: a ``members`` map
    (canonical name → ``{member, kind, type, granularity}``), the default
    granularity, and the time-member set. Pure (no handle / no network) so the merge
    logic is unit-testable.
    """
    parsed = parse_cube_meta(meta_json)
    default_gran = str(cfg.get("granularity", _DEFAULT_GRANULARITY))

    members: dict[str, dict[str, Any]] = {}
    for member, info in parsed["dimensions"].items():
        members[member] = {
            "member": member, "kind": "dimension",
            "type": info.get("type"), "granularity": None,
        }
    for member, info in parsed["measures"].items():
        members[member] = {
            "member": member, "kind": "measure", "type": None, "granularity": None,
        }

    # YAML granularity aliases: friendly name -> a time member at a fixed bucket.
    for alias, spec in (cfg.get("dimensions") or {}).items():
        member, gran = _alias_member_and_granularity(spec)
        if not member:
            raise ValueError(
                f"cube dimension alias {alias!r} must be a mapping with a "
                f"`member:` (and optionally `granularity:`)"
            )
        members[alias] = {
            "member": str(member), "kind": "dimension",
            "type": "time", "granularity": (str(gran) if gran else None),
        }

    return {
        "members": members,
        "default_granularity": default_gran,
        "time_members": parsed["time_members"],
        "time_dimension": parsed["time_dimension"],
        "measure_formats": {
            m: parsed["measures"][m]["format"]
            for m in parsed["measures"]
            if parsed["measures"][m]["format"]
        },
    }


# --------------------------------------------------------------------------- #
# Query builder — (ref, filters) -> a structured Cube JSON query (a dict)
# --------------------------------------------------------------------------- #


def _member_info(handle: SemanticModelHandle, canonical: str) -> dict[str, Any]:
    """Resolve a canonical dimension/measure name to its Cube member metadata."""
    info = (handle.cube_meta.get("members") or {}).get(canonical)
    if info is None:
        raise ValueError(
            f"cube model {handle.name!r}: unknown member {canonical!r} "
            f"(known: {sorted(handle.cube_meta.get('members') or {})})"
        )
    return info


def _is_time(info: dict[str, Any]) -> bool:
    return info.get("type") == "time"


def _time_granularity(
    info: dict[str, Any], grain: str | None, default_gran: str
) -> str:
    """The granularity to bucket a time member at, in precedence order.

    A runtime/literal ``grain=`` wins over the member's fixed YAML granularity
    alias, which wins over the model-level default — so "put a date on
    the x-axis at *this* grain" is expressible without pre-declaring a bucket.
    """
    return grain or info.get("granularity") or default_gran


def cube_result_keys(
    handle: SemanticModelHandle, ref: SemanticRef, grain: str | None = None
) -> list[str]:
    """The keys Cube uses in each result row, aligned with ``[by, [series], *metrics]``.

    A plain dimension/measure is keyed by its member id; a time dimension grouped at a
    granularity is keyed ``member.granularity`` — so the runner can rename Cube's rows
    to the canonical column names the component reads. ``grain`` (a canonical token)
    overrides a time member's bucket, and must match the granularity
    :func:`build_cube_query` sent or the rename misses.
    """
    default_gran = handle.cube_meta.get("default_granularity", _DEFAULT_GRANULARITY)
    keys: list[str] = []
    for d in (ref.by, ref.series):
        if not d:
            continue
        info = _member_info(handle, d)
        if _is_time(info):
            gran = _time_granularity(info, grain, default_gran)
            keys.append(f"{info['member']}.{gran}")
        else:
            keys.append(info["member"])
    for m in ref.metrics:
        keys.append(_member_info(handle, m)["member"])
    return keys


def build_cube_query(
    handle: SemanticModelHandle,
    ref: SemanticRef,
    filters: list[dict[str, Any]],
    grain: str | None = None,
) -> dict[str, Any]:
    """Compile a metric request into a structured Cube query dict.

    ``ref.by`` (then the optional ``ref.series`` split) become ``dimensions`` —
    except time-type ones, which become ``timeDimensions`` with a granularity.
    ``ref.metrics`` become ``measures``. ``filters`` is the generic typed IR from
    :func:`dashdown.semantic.build_filters` (the *same* one the BSL/DAX backends
    consume): an ``in`` becomes ``{member, operator:"equals", values}``; the time-dim
    ``>=``/``<=`` pair **collapses** into a single ``timeDimensions[].dateRange``
    (merged onto the grouping entry when the same member is also a ``by``). Group-by
    members are ordered ascending. ``grain`` (a canonical token, literal or
    control-driven) overrides a time-grouping member's bucket, so it maps directly
    onto Cube's native ``timeDimensions[].granularity``. Everything is JSON data —
    **no string assembly, no escaping, no ``_substitute_params``**.
    """
    default_gran = handle.cube_meta.get("default_granularity", _DEFAULT_GRANULARITY)

    measures = [_member_info(handle, m)["member"] for m in ref.metrics]
    dimensions: list[str] = []
    cube_filters: list[dict[str, Any]] = []
    # Keep time-dimension entries keyed by member so a grouping granularity and a
    # dateRange on the same member merge into one entry (Cube's contract).
    time_dims: dict[str, dict[str, Any]] = {}
    order: list[list[str]] = []

    for d in (ref.by, ref.series):
        if not d:
            continue
        info = _member_info(handle, d)
        member = info["member"]
        if _is_time(info):
            entry = time_dims.setdefault(member, {"dimension": member})
            entry["granularity"] = _time_granularity(info, grain, default_gran)
        else:
            dimensions.append(member)
        order.append([member, "asc"])

    for f in filters:
        info = _member_info(handle, f.get("field"))
        member = info["member"]
        op = f.get("operator")
        if op == "in":
            values = [str(v) for v in (f.get("values") or []) if str(v) != ""]
            if values:
                cube_filters.append(
                    {"member": member, "operator": "equals", "values": values}
                )
        elif op in (">=", "<="):
            # Collapse the date-range pair onto the time dimension's entry.
            entry = time_dims.setdefault(member, {"dimension": member})
            rng = entry.setdefault("dateRange", [None, None])
            rng[0 if op == ">=" else 1] = str(f.get("value"))
        else:
            raise ValueError(f"cube backend: unsupported filter operator {op!r}")

    time_dimensions = [_finalize_time_dim(e) for e in time_dims.values()]

    query: dict[str, Any] = {"measures": measures}
    if dimensions:
        query["dimensions"] = dimensions
    if time_dimensions:
        query["timeDimensions"] = time_dimensions
    if cube_filters:
        query["filters"] = cube_filters
    if order:
        query["order"] = order
    limit = handle.cube_meta.get("limit")
    if limit:
        query["limit"] = int(limit)
    return query


def _finalize_time_dim(entry: dict[str, Any]) -> dict[str, Any]:
    """Normalize a collected time-dimension entry for the wire.

    A ``dateRange`` collected as ``[start|None, end|None]`` becomes a 2-element list
    when both bounds are present, a single-element list when only the start is set
    (Cube reads ``["2024-01-01"]`` as "from this date on"); a lone end with no start
    is dropped (Cube has no open-start single form) — a rare, benign case.
    """
    rng = entry.get("dateRange")
    if rng is not None:
        start, end = rng[0], rng[1]
        if start and end:
            entry["dateRange"] = [start, end]
        elif start:
            entry["dateRange"] = [start]
        else:
            entry.pop("dateRange", None)
    return entry


# --------------------------------------------------------------------------- #
# Backend registration — the SemanticBackend the registry dispatches to
# --------------------------------------------------------------------------- #


@register_semantic_backend("cube")
class CubeBackend(SemanticBackend):
    """Cube (cube.dev) backend — compiles a metric request to a structured JSON query
    run against a live Cube deployment via the ``cube`` connector.

    Auto-detected when a model's ``connector:`` is a ``CubeConnector`` (or set
    explicitly with ``backend: cube``). ``introspect`` reads the model from Cube's
    ``/meta`` (config-free); ``build_spec`` compiles + executes the JSON query. The
    pure helpers (``parse_cube_meta``/``build_cube_query``/``cube_result_keys``) are
    this backend's internals; everything downstream is the shared semantic core.
    Needs ``dashdown-md[cube]`` (httpx + PyJWT).

    **Experimental / preview** — not yet verified against a live Cube deployment.
    Covers the ``measures``/``dimensions``/``timeDimensions``/``filters`` subset of
    Cube's query shape (no segments, no relative dateRange keywords, single time
    dimension for the global date range).
    """

    def claims_connector(self, conn: Connector) -> bool:
        # Name-based (not ``isinstance``) so it matches the lazily-loaded built-in
        # CubeConnector without importing it — the DAX backend's idiom.
        return type(conn).__name__ == "CubeConnector"

    def introspect(
        self, handle: SemanticModelHandle, connectors: dict[str, Connector]
    ) -> None:
        """Populate the catalogue from a live ``GET /meta`` (validates at load).

        Fail-at-startup by default (an unreachable Cube aborts ``serve``/``build``,
        parity with a malformed ``sources.yaml``); ``optional: true`` on the model
        downgrades that to a logged warning + an empty catalogue (so the project
        still loads and a metric reference 404s, rather than wedging an otherwise
        working dashboard during a Cube outage).
        """
        from dashdown.semantic import _name_lookup  # local: avoid import cycle

        conn = connectors.get(handle.connector)
        if conn is None:
            raise ValueError(
                f"cube model {handle.name!r}: unknown connector "
                f"{handle.connector!r} (known: {sorted(connectors)})"
            )
        if not hasattr(conn, "meta"):
            raise ValueError(
                f"cube model {handle.name!r}: connector {handle.connector!r} is not "
                f"a cube connector (type {type(conn).__name__})"
            )

        cfg = handle.file_config.get(handle.name) or {}
        optional = bool(cfg.get("optional"))
        try:
            meta_json = conn.meta()
        except Exception as e:
            if optional:
                log.warning(
                    "cube model %r: /meta unavailable (%s); model skipped "
                    "(optional: true)", handle.name, e,
                )
                handle.cube_meta = {"members": {}, "default_granularity":
                                    _DEFAULT_GRANULARITY, "time_members": set()}
                return
            raise RuntimeError(
                f"cube model {handle.name!r}: could not introspect Cube at load "
                f"(GET /meta failed: {e}). Set `optional: true` on the model to skip "
                f"it when Cube is unavailable."
            ) from e

        catalogue = build_cube_catalogue(meta_json, cfg)
        members = catalogue["members"]
        dims = {n for n, i in members.items() if i["kind"] == "dimension"}
        meas = {n for n, i in members.items() if i["kind"] == "measure"}
        if not meas:
            raise ValueError(
                f"cube model {handle.name!r}: /meta exposed no measures"
            )
        handle.dimensions = dims
        handle.measures = meas
        handle.dim_lookup = _name_lookup(dims)
        handle.measure_lookup = _name_lookup(meas)
        handle.time_dimension = catalogue["time_dimension"]
        handle.measure_formats = catalogue["measure_formats"]
        handle.cube_meta = {
            "members": members,
            "default_granularity": catalogue["default_granularity"],
            "time_members": catalogue["time_members"],
            "limit": cfg.get("limit"),
        }

    def build_spec(
        self,
        handle: SemanticModelHandle,
        ref: SemanticRef,
        connectors: dict[str, Connector],
    ) -> PythonQuerySpec:
        # Lazy imports: build_filters lives in dashdown.semantic (a module-load import
        # would cycle); QueryResult/PythonQuerySpec only needed at query time. Unlike
        # the DAX backend, Cube **captures** ``connectors`` (the connector exposes
        # load(), not the SQL connect() thunk) — the IbisBackend idiom.
        from dashdown.data.base import QueryResult
        from dashdown.python_query import PythonQuerySpec
        from dashdown.semantic import build_filters, resolve_grain_token

        def fn(params: dict[str, str], _connect):
            conn = connectors.get(handle.connector)
            if conn is None:
                raise KeyError(
                    f"cube model {handle.name!r}: connector {handle.connector!r} "
                    f"not available at query time"
                )
            # Time grain: a literal/control token routes straight to
            # Cube's native `timeDimensions[].granularity` (the canonical tokens are
            # Cube's granularities verbatim). The same token feeds the result-key
            # rename so the `member.granularity` keys line up.
            grain = resolve_grain_token(ref, params)
            query = build_cube_query(handle, ref, build_filters(handle, params), grain=grain)
            payload = conn.load(query)
            return _result_from_cube(handle, ref, payload, QueryResult, grain=grain)

        return PythonQuerySpec(
            name=ref.query_name,
            connector=handle.connector,
            fn=fn,
            cache_ttl=None,
            live=False,
            interval=None,
            description=f"semantic (cube): {', '.join(ref.metrics)} by {ref.by} "
                        f"({ref.model})",
        )


def _result_from_cube(
    handle: SemanticModelHandle, ref: SemanticRef, payload: dict[str, Any], QueryResult,
    grain: str | None = None,
):
    """Turn Cube's ``{data, annotation}`` into a canonical-columned ``QueryResult``.

    Cube rows are dicts keyed by member id (a time grouping keyed
    ``member.granularity``). Rename to the canonical ``[by, [series], *metrics]`` the
    component reads, using the ``annotation`` block as the authoritative key set; a
    positional fallback under a length guard handles a shape we didn't anticipate, and
    failing both we hand back the raw rows rather than mislabel them. ``grain`` is the
    effective grain :func:`build_cube_query` used, so the time-member keys match.
    """
    data = payload.get("data") or []
    annotation = payload.get("annotation") or {}
    canonical = [d for d in (ref.by, ref.series) if d] + list(ref.metrics)
    keys = cube_result_keys(handle, ref, grain=grain)

    ann_keys: set[str] = set()
    for section in ("measures", "dimensions", "timeDimensions", "segments"):
        ann_keys |= set((annotation.get(section) or {}).keys())

    if len(keys) == len(canonical) and all(k in ann_keys for k in keys):
        rows = [[row.get(k) for k in keys] for row in data]
        return QueryResult(columns=canonical, rows=rows)

    # Positional fallback: rename by row position under a strict length guard.
    if data and len(data[0]) == len(canonical):
        first_keys = list(data[0].keys())
        rows = [[row.get(k) for k in first_keys] for row in data]
        return QueryResult(columns=canonical, rows=rows)

    # Unanticipated shape (incl. empty data with no annotation): hand back raw rows
    # keyed by their own columns rather than mislabel them.
    cols: list[str] = []
    seen: set[str] = set()
    for row in data:
        for k in row:
            if k not in seen:
                seen.add(k)
                cols.append(k)
    return QueryResult(columns=cols, rows=[[row.get(c) for c in cols] for row in data])
