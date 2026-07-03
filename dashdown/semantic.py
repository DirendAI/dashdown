"""First-class semantic metric layer on boring-semantic-layer (BSL).

Define metrics + dimensions (+ joins) **once** in a BSL YAML model, then point a
component straight at them —

    <BarChart metric={sales.revenue} by={sales.region} />

— and the framework compiles ``(metric, dimension, current $store.filters)`` into a
``model.query(...)`` that **Ibis pushes down to the warehouse** (the aggregation
and filters run in DuckDB/Postgres/Snowflake/BigQuery, not in Python). One
definition of "revenue" drives every chart; dashboard filters become semantic
filters automatically; joins / fan-out correctness / dialect are BSL's job. Join
planning under aggregation (fan-out / chasm traps) and per-dialect SQL — the hard
core of a semantic layer — are handled by
[boring-semantic-layer](https://github.com/boringdata/boring-semantic-layer) (on
Ibis), which is MIT-licensed.

**How it rides on existing machinery (zero new request paths).** A metric reference
compiles, at render time, into a *synthetic Python query* — a
:class:`dashdown.python_query.PythonQuerySpec` whose ``fn`` builds the BSL query
from the live ``params`` and returns Arrow. It's registered in the same
``_python_def_cache``, so the data API, WS poller, static build, server-side cache,
and the filter re-fetch loop all resolve it with **no changes**.

**Connection.** By default a model's ``table`` is loaded from one of the project's
own ``sources.yaml`` connectors (declared per model with ``connector:``), bridged to
an Ibis backend so there's a **single connection config and real pushdown**. A model
may instead name a native BSL ``profile:`` (escape hatch) — for backends not bridged
yet, or to drop in an existing BSL setup.

**Trust boundary == custom components / Python queries.** A ``semantic/*.yml`` is
declarative, but it's loaded through the same ``python_queries.enabled`` gate (a
managed/multi-tenant host that refuses in-process code execution also refuses the
semantic layer). **Params are data, never code** — filter values reach BSL as a
typed filter list (``{"field","operator","values"}``), never string-interpolated.

**Pluggable backends, one grammar.** The default backend is BSL/Ibis (above), for SQL
warehouses; a model may instead set ``backend: cube`` (or name a ``cube`` connector,
auto-detected) to target a **Cube semantic layer** — the same
``metric=``/``by=``/``series=`` grammar + filter mapping, compiled to Cube's structured
query API. Everything below (``resolve_ref``, ``build_filters``,
``semantic_filter_params``, the synthetic-query registration) is backend-agnostic.
(For Power BI / Fabric, hand-write ``queries/*.dax`` against the ``dax`` connector —
there is no DAX *semantic* backend.)

A chart plots **several metrics of the same model** as distinct series
(``metric="sales.revenue,sales.profit"``) *or* one metric split by a **second
dimension** into a series per value (``by={…} series={…}``); the two are mutually
exclusive.
"""
from __future__ import annotations

import logging
from contextlib import ExitStack
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import yaml

from dashdown.data.base import Connector, QueryResult
from dashdown.data.registry import default_connector_name
from dashdown.python_query import PythonQuerySpec
from dashdown.semantic_base import (
    DEFAULT_BACKEND,
    SemanticBackend,
    detect_backend,
    get_semantic_backend,
    known_semantic_backends,
    register_semantic_backend,
)

log = logging.getLogger(__name__)

# Conventional global-date-filter param names (mirror the GlobalDateFilter default
# start_param/end_param). A model with a time dimension opts into the range
# automatically.
DATE_START_PARAM = "date_start"
DATE_END_PARAM = "date_end"

# Canonical time-grain vocabulary. One neutral, lowercase token set;
# each backend translates it to its native mechanism (ibis → `TIME_GRAIN_<TOKEN>`,
# cube → `timeDimensions[].granularity`). A backend-specific constant must **never**
# leak into this public `grain=` grammar — the tokens below are the whole contract.
GRAIN_TOKENS = ("second", "minute", "hour", "day", "week", "month", "quarter", "year")


def _normalize_grain(value: Any) -> str:
    """Lowercase + validate a grain literal against the canonical vocabulary.

    This is the *shape* check — is it one of the eight neutral tokens — shared by
    every backend (a literal at resolve time, an interactive control value at
    fetch). Whether a given backend / dimension actually *supports* that grain
    (BSL's ``smallest_time_grain``, Cube's ``/meta`` time type) is the backend's
    own validation, deferred to query time. Raises ``ValueError`` on a bad token,
    surfaced as the component's inline error card.
    """
    token = str(value).strip().lower()
    if token not in GRAIN_TOKENS:
        raise ValueError(
            f"unknown time grain {value!r}; expected one of: {', '.join(GRAIN_TOKENS)}"
        )
    return token


# Our YAML extensions over BSL's model config (stripped before handing to BSL).
# `backend` selects the engine: "ibis" (BSL, default) or "cube" (Cube).
# A date axis is flagged `is_time_dimension: true` *inside* a dimension (same
# as BSL), which survives this stripping, so there's no top-level key to preserve.
_OUR_KEYS = ("connector", "profile", "backend")


def _require_bsl():
    """Import BSL + Ibis lazily with a friendly install hint (extra: semantic)."""
    # BSL pulls in xorq, which logs the repo commit + full git diff at INFO *during
    # import* (it reconfigures its own logger), far too noisy for our serve/build
    # output. Suppress INFO-and-below across the one-time import, then restore.
    prev_disable = logging.root.manager.disable
    logging.disable(logging.INFO)
    try:
        import boring_semantic_layer as bsl  # noqa: F401
        import ibis  # noqa: F401
    except ImportError as e:  # pragma: no cover - exercised when extra absent
        raise ImportError(
            "The semantic metric layer needs boring-semantic-layer + Ibis, which "
            "are not installed. Install them with: pip install 'dashdown-md[semantic]'"
        ) from e
    finally:
        logging.disable(prev_disable)
    logging.getLogger("xorq").setLevel(logging.WARNING)
    return bsl, ibis


# --------------------------------------------------------------------------- #
# Connection bridging — a project Connector -> an Ibis backend (for pushdown)
# --------------------------------------------------------------------------- #


def _connect_ibis_backend(ibis, backend_name: str, ibis_extra: str, *, url=None, **kwargs):
    """Open an Ibis backend, turning a missing-backend import into a friendly hint.

    A warehouse bridge needs the matching Ibis backend (``ibis-framework[postgres]``
    etc.), which the base ``dashdown-md[semantic]`` extra doesn't pull (it ships only
    ``[duckdb]``). If the backend isn't installed, ``ibis.<name>`` /
    ``.connect()`` raises ``ImportError`` — caught here and re-raised pointing at the
    right extra. A *connection* failure (bad host/credentials) is **not** masked —
    it propagates so it surfaces as the component's error card.
    """
    none_stripped = {k: v for k, v in kwargs.items() if v is not None}
    try:
        if url:
            return ibis.connect(url)
        backend = getattr(ibis, backend_name)
        return backend.connect(**none_stripped)
    except (ImportError, AttributeError) as e:
        raise ImportError(
            f"The semantic layer needs the Ibis '{backend_name}' backend to push a "
            f"metric query down to this connector, which is not installed. Install it "
            f"with: pip install 'ibis-framework[{ibis_extra}]'  (underlying error: {e})"
        ) from e


def _bridge_postgres(conn: Connector, ibis):
    cfg = conn.config
    url = cfg.get("url") or cfg.get("dsn")
    if url:
        return _connect_ibis_backend(ibis, "postgres", "postgres", url=url)
    return _connect_ibis_backend(
        ibis, "postgres", "postgres",
        host=cfg.get("host", "localhost"),
        port=int(cfg.get("port", 5432)),
        database=cfg.get("database") or cfg.get("dbname"),
        user=cfg.get("user"),
        password=cfg.get("password"),
    )


def _bridge_mysql(conn: Connector, ibis):
    cfg = conn.config
    url = cfg.get("url") or cfg.get("dsn")
    if url:
        return _connect_ibis_backend(ibis, "mysql", "mysql", url=url)
    return _connect_ibis_backend(
        ibis, "mysql", "mysql",
        host=cfg.get("host", "localhost"),
        port=int(cfg.get("port", 3306)),
        database=cfg.get("database") or cfg.get("db"),
        user=cfg.get("user"),
        password=cfg.get("password"),
    )


def _bridge_snowflake(conn: Connector, ibis):
    cfg = conn.config
    keys = (
        "account", "user", "password", "warehouse",
        "database", "schema", "role", "authenticator",
    )
    return _connect_ibis_backend(
        ibis, "snowflake", "snowflake", **{k: cfg.get(k) for k in keys}
    )


def _bridge_bigquery(conn: Connector, ibis):
    cfg = conn.config
    kwargs: dict[str, Any] = {}
    if cfg.get("project"):
        kwargs["project_id"] = cfg["project"]
    if cfg.get("location"):
        kwargs["location"] = cfg["location"]
    if cfg.get("dataset_id") or cfg.get("dataset"):
        kwargs["dataset_id"] = cfg.get("dataset_id") or cfg.get("dataset")
    cred_path = cfg.get("credentials_path") or cfg.get("credentials_file")
    if cred_path:
        from google.oauth2 import service_account

        root = cfg.get("_project_root", Path("."))
        resolved = (Path(root) / cred_path).resolve()
        kwargs["credentials"] = service_account.Credentials.from_service_account_file(
            str(resolved)
        )
    return _connect_ibis_backend(ibis, "bigquery", "bigquery", **kwargs)


#: Connector type name -> a bridge building a *fresh* native Ibis backend from the
#: connector's ``sources.yaml`` config. Keyed by class name (not ``isinstance``) so a
#: lazily-loaded built-in connector matches without importing its driver — the same
#: idiom ``CubeBackend.claims_connector`` uses. A new warehouse is one entry here.
_WAREHOUSE_BRIDGES = {
    "PostgresConnector": _bridge_postgres,
    "MySQLConnector": _bridge_mysql,
    "SnowflakeConnector": _bridge_snowflake,
    "BigQueryConnector": _bridge_bigquery,
}


def ibis_backend_for_connector(conn: Connector, ensure_setup: bool = True):
    """Bridge a project connector to an Ibis backend so BSL can push down.

    Two bridge families:

    - **DuckDB-backed** (``csv``/``duckdb``) share their live in-process DuckDB
      connection with Ibis (``ibis.duckdb.from_connection``) — Ibis sees the exact
      views the connector materialized, so the bridge is zero-copy. That connection
      is **not** concurrency-safe, so the caller serializes Ibis execution on it
      through the connector's ``_lock`` (see :meth:`IbisBackend.build_spec`).
      ``ensure_setup`` runs the connector's one-shot view setup via ``conn.query``
      (which takes ``_lock`` itself, so it must run *outside* that lock — pass
      ``False`` when already holding it).
    - **SQL warehouses** (``postgres``/``mysql``/``snowflake``/``bigquery``) get a
      **fresh, native** Ibis connection built from the connector's config (not the
      connector's own DB-API connection — Ibis drives the warehouse with its own
      driver, e.g. psycopg3 vs the connector's psycopg2, so reusing the live handle
      isn't safe). It's **cached on the connector** (``_ibis_backend``) so a model
      doesn't reconnect per query, and needs the matching ``ibis-framework[…]`` extra
      (a missing one raises a friendly hint). The warehouse's own connection is
      networked + Ibis-managed, so ``ensure_setup`` is irrelevant there.

    Any other connector type raises with a pointer to the ``profile:`` escape hatch.
    """
    _, ibis = _require_bsl()
    cname = type(conn).__name__
    con = getattr(conn, "_con", None)
    if con is not None and cname in ("CSVConnector", "DuckDBConnector"):
        if ensure_setup:
            conn.query("SELECT 1")  # trigger _setup() (view creation) once
        return ibis.duckdb.from_connection(conn._con)
    bridge = _WAREHOUSE_BRIDGES.get(cname)
    if bridge is not None:
        cached = getattr(conn, "_ibis_backend", None)
        if cached is None:
            cached = bridge(conn, ibis)
            setattr(conn, "_ibis_backend", cached)  # reuse across queries
        return cached
    raise ValueError(
        f"semantic layer: no Ibis bridge for connector type {cname!r} yet. Use a "
        f"BSL `profile:` in the model instead, or contribute a bridge in "
        f"semantic.py::_WAREHOUSE_BRIDGES."
    )


# --------------------------------------------------------------------------- #
# Model handle + loader
# --------------------------------------------------------------------------- #


@dataclass
class SemanticModelHandle:
    """A loaded BSL model plus the metadata the pipeline/components need.

    A model is built in the context of its **whole file** (``file_config`` — all
    models declared together), so a BSL ``join`` to a sibling model resolves. The
    BSL model is **rebuilt per query** from the live connectors (:meth:`build`) so
    a connector reconnect can't leave a stale Ibis backend bound. Introspected
    sets (``measures``/``dimensions``/``time_dimension``) are captured once at
    load for validation, ``query_defs``, and the global-date scan.
    """

    name: str
    connector: str                    # this model's connector (lock + query_defs)
    file_config: dict[str, Any]       # the whole file's models, our keys stripped
    table_connectors: dict[str, str]  # table name -> connector name (for bridging)
    profile: str | None
    profile_path: str | None
    measures: set[str] = field(default_factory=set)
    dimensions: set[str] = field(default_factory=set)
    time_dimension: str | None = None
    # measure name (canonical) -> display-format hint (read from the measure's YAML
    # metadata), applied by the component when the author didn't set its own format.
    measure_formats: dict[str, dict[str, Any]] = field(default_factory=dict)
    # Accepted-name -> canonical-name lookups. BSL prefixes field names with the
    # model name once a model has joins (`sales.region`, `region.name`) but leaves
    # them bare otherwise (`region`); these let `by={sales.region}` resolve in both
    # cases, and a joined field via its short segment (`by={sales.name}`).
    dim_lookup: dict[str, str] = field(default_factory=dict)
    measure_lookup: dict[str, str] = field(default_factory=dict)
    # Backend discriminator: "ibis" (BSL/Ibis, default) or "cube" (Cube — see
    # semantic_cube.py).
    backend: str = "ibis"
    # Cube backend only: the catalogue introspected from Cube's /meta — a `members`
    # map (canonical name -> {member, kind, type, granularity}) plus the default
    # granularity and time-member set. Empty for an ibis model. See semantic_cube.py.
    cube_meta: dict[str, Any] = field(default_factory=dict)

    def build(self, connectors: dict[str, Connector], ensure_setup: bool = True):
        """(Re)build this model via BSL, bound, in the context of its whole file.

        Ibis backend only — a ``cube`` model is never built through BSL (it has no
        Ibis model); it's introspected from Cube's ``/meta`` instead.
        """
        models = _build_file_models(
            self.file_config, self.table_connectors, self.profile,
            self.profile_path, connectors, ensure_setup,
        )
        return models[self.name]


def _build_file_models(
    file_config: dict[str, Any],
    table_connectors: dict[str, str],
    profile: str | None,
    profile_path: str | None,
    connectors: dict[str, Connector],
    ensure_setup: bool = True,
):
    """Build every model in one file together — so cross-model ``join``s resolve.

    Profile mode hands the whole config to BSL (it owns the connection). Bridge
    mode pulls each model's ``table`` from its connector's Ibis backend (one
    backend per distinct connector, reused), then BSL wires the joins. A joined
    set must share one connector (Ibis can't join across backends) — the same
    single-connector constraint SQL-only ``ref()`` composition has.
    """
    bsl, _ = _require_bsl()
    if profile is not None:
        return bsl.from_config(file_config, profile=profile, profile_path=profile_path)
    backends: dict[str, Any] = {}
    tables: dict[str, Any] = {}
    for table_name, connector in table_connectors.items():
        conn = connectors.get(connector)
        if conn is None:
            raise ValueError(
                f"semantic model: unknown connector {connector!r} "
                f"(known: {sorted(connectors)})"
            )
        if connector not in backends:
            backends[connector] = ibis_backend_for_connector(conn, ensure_setup=ensure_setup)
        tables[table_name] = backends[connector].table(table_name)
    return bsl.from_config(file_config, tables=tables)


def load_semantic_models(
    semantic_dir: Path, connectors: dict[str, Connector] | None = None
) -> dict[str, SemanticModelHandle]:
    """Load ``semantic/**/*.{yml,yaml}`` into ``{model_name: SemanticModelHandle}``.

    Each YAML file is a BSL model config (``{model_name: {table, dimensions,
    measures, joins, …}}``) plus our per-model ``connector:`` (default ``main``,
    bridged to Ibis) or ``profile:`` (BSL-native escape hatch). ``_``-prefixed
    files are skipped. A duplicate model name across files, an unknown connector,
    or a malformed model raises (fail-at-startup, parity with the other loaders).
    """
    if not semantic_dir.is_dir():
        return {}
    connectors = connectors or {}

    root = semantic_dir.resolve()
    profile_path = None
    for cand in ("profiles.yml", "profiles.yaml"):
        p = root / cand
        if p.is_file():
            profile_path = str(p)
            break

    out: dict[str, SemanticModelHandle] = {}
    sources: dict[str, Path] = {}

    for path in sorted([*root.rglob("*.yml"), *root.rglob("*.yaml")]):
        if not path.is_file() or path.name.startswith("_") or "__pycache__" in path.parts:
            continue
        if path.name in ("profiles.yml", "profiles.yaml"):
            continue
        try:
            path.resolve().relative_to(root)
        except ValueError as exc:  # symlink escape
            raise ValueError(
                f"semantic model file escapes semantic/ directory: {path}"
            ) from exc

        doc = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        if not isinstance(doc, dict):
            raise ValueError(f"semantic model file {path} must be a mapping of models")

        # Build every model in the file together (a join references a sibling
        # model). Collect the cleaned config + each model's table→connector.
        file_config: dict[str, Any] = {}
        table_connectors: dict[str, str] = {}
        model_connectors: dict[str, str] = {}
        model_backends: dict[str, str] = {}
        file_profile = None
        for model_name, raw_cfg in doc.items():
            if not isinstance(raw_cfg, dict):
                raise ValueError(
                    f"semantic model {model_name!r} in {path.name} must be a mapping"
                )
            if model_name in out:
                raise ValueError(
                    f"duplicate semantic model {model_name!r}: defined by both "
                    f"{sources[model_name].name} and {path.name}"
                )
            cfg = {k: v for k, v in raw_cfg.items() if k not in _OUR_KEYS}
            file_config[model_name] = cfg
            connector = str(raw_cfg.get("connector") or default_connector_name(connectors or {}))
            model_connectors[model_name] = connector
            model_backends[model_name] = _detect_backend(
                raw_cfg.get("backend"), connector, connectors
            )
            if raw_cfg.get("profile"):
                file_profile = raw_cfg["profile"]
            table_name = cfg.get("table")
            if table_name:
                table_connectors[table_name] = connector

        for model_name in file_config:
            handle = SemanticModelHandle(
                name=model_name,
                connector=model_connectors[model_name],
                file_config=file_config,
                table_connectors=table_connectors,
                profile=file_profile,
                profile_path=profile_path,
                backend=model_backends[model_name],
            )
            _introspect(handle, connectors)
            out[model_name] = handle
            sources[model_name] = path

    return out


def _detect_backend(
    explicit: Any, connector: str, connectors: dict[str, Connector]
) -> str:
    """Pick a model's backend (registry-driven — see ``semantic_base.detect_backend``).

    Explicit ``backend:`` wins (validated against the registered set); otherwise the
    backend is inferred from the connector instance via each backend's
    ``claims_connector`` (the Cube backend claims a ``CubeConnector``), falling back to
    :data:`DEFAULT_BACKEND`. Kept as a thin wrapper (same name + ``(explicit,
    connector, connectors)`` signature) for the loader and the tests.
    """
    return detect_backend(explicit, (connectors or {}).get(connector))


def _introspect(handle: SemanticModelHandle, connectors: dict[str, Connector]) -> None:
    """Fill *handle*'s catalogue via its backend (validates the model at load).

    Dispatches to the registered :class:`~dashdown.semantic_base.SemanticBackend`
    (``ibis`` builds the BSL model; ``cube`` reads Cube's ``/meta``; a plugin does its
    own thing) — the catalogue fields it fills are backend-agnostic from here on.
    """
    get_semantic_backend(handle.backend).introspect(handle, connectors)


def _name_lookup(fields) -> dict[str, str]:
    """Map every accepted spelling of a field to its canonical name.

    Keys: the canonical name itself (``sales.region`` / ``region.name`` /
    ``region``) and its last dotted segment (``region`` / ``name``), so a model
    with joins (prefixed names) and one without (bare names) both resolve from a
    short ``by={model.field}`` reference. First writer wins on a short-name clash.
    """
    lookup: dict[str, str] = {}
    for canonical in fields:
        lookup[canonical] = canonical
        lookup.setdefault(canonical.split(".")[-1], canonical)
    return lookup


# --------------------------------------------------------------------------- #
# Reference resolution + compilation to a synthetic Python query
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class SemanticRef:
    """A resolved ``metric={model.metric} by={model.dim}`` reference on a page.

    ``metrics`` is a tuple so one chart can plot **several measures of the same
    model** as distinct, separately-coloured series — ``metric="sales.revenue,
    sales.profit"`` (a comma-separated string; the single ``{model.metric}`` brace
    form stays one metric). ``.metric`` returns the first for the single-metric
    consumers (``Value``/``Counter``/``Table``) that read one column.
    """

    model: str
    metrics: tuple[str, ...]
    by: str | None
    connector: str
    query_name: str = field(default="")
    # An optional **second** dimension that splits one metric into a coloured
    # series per value (`series={model.dim}` — e.g. revenue by month, split by
    # year). Mutually exclusive with multiple ``metrics``.
    series: str | None = field(default=None)
    # Time-grain bucketing for a time-dimension ``by``/``series``. A
    # **literal** ``grain="month"`` is fixed at author time and enters
    # ``query_name`` (so ``grain="day"`` and ``grain="month"`` are distinct
    # ``_python_def_cache`` entries that coexist); a ``grain={control}`` reference
    # instead records the control's param name in ``grain_param`` and is read from
    # the live ``params`` at fetch (one def, shape varies on the existing filter
    # re-fetch path). At most one of the two is set. Grain is a *grouping* modifier,
    # not a filter — it never routes through ``build_filters``.
    grain: str | None = field(default=None)
    grain_param: str | None = field(default=None)

    @property
    def metric(self) -> str:
        """The first (often only) metric — the column single-value widgets read."""
        return self.metrics[0]


def semantic_query_name(
    model: str,
    metric: str | tuple[str, ...] | list[str],
    by: str | None,
    series: str | None = None,
    grain: str | None = None,
) -> str:
    """Deterministic synthetic query name (dotted, ``_sem``-namespaced).

    ``metric`` may be one name or a list/tuple of names (multi-metric chart); each
    becomes its own dotted segment so the name stays unique and stable per
    ``(model, metrics, by, series, grain)``. A ``series`` second dimension adds its
    own segment so a split chart never collides with the un-split one. A **literal**
    ``grain`` adds a ``grain.<token>`` segment, so ``grain="day"`` and
    ``grain="month"`` charts are distinct cache entries that coexist on one page; an
    *interactive* ``grain={control}`` is deliberately **not** passed here (its value
    varies per fetch, like a filter, without changing query identity).
    """
    metrics = [metric] if isinstance(metric, str) else list(metric)
    parts = ["_sem", model, *metrics]
    if by:
        parts += ["by", by]
    if series:
        parts += ["series", series]
    if grain:
        parts += ["grain", grain]
    return ".".join(parts)


def _resolve_dim(
    handle: SemanticModelHandle, model_name: str, ref: str, attr: str = "by"
) -> str:
    """Resolve a ``model.dim`` / bare ``dim`` reference to its canonical name.

    ``attr`` (``"by"``/``"series"``) only shapes the error message. A
    comma-separated reference is rejected with a pointer to ``series=`` — unlike
    ``metric=`` (which takes a list), a chart encodes at most **two** dimensions
    (an x-axis ``by=`` plus an optional ``series=`` split), so a multi-dimension
    ``by=`` has no single-chart form.
    """
    if "," in ref:
        parts = [p.strip() for p in ref.split(",") if p.strip()]
        hint = (
            f" Use `by={{{parts[0]}}} series={{{parts[1]}}}` instead."
            if attr == "by" and len(parts) >= 2
            else ""
        )
        raise ValueError(
            f"semantic {attr}={ref!r}: a dimension reference names ONE dimension. A "
            f"chart groups by a single `by=` (x-axis) plus an optional `series=` (a "
            f"second dimension splitting one metric into a coloured series); "
            f"comma-separated lists are only for `metric=` (multiple measures)." + hint
        )
    # `model.dim` or `model.join.field` -> strip the leading model segment.
    part = ref.split(".", 1)[1] if "." in ref else ref
    name = handle.dim_lookup.get(part)
    if name is None:
        raise ValueError(
            f"unknown dimension {part!r} on model {model_name!r} "
            f"(known: {sorted(handle.dim_lookup)})"
        )
    return name


def resolve_ref(
    models: dict[str, SemanticModelHandle],
    metric_ref: str,
    by_ref: str | None,
    series_ref: str | None = None,
    grain: str | None = None,
    grain_param: str | None = None,
) -> SemanticRef:
    """Validate a ``metric={model.metric}`` / ``by={model.dim}`` reference.

    ``metric_ref`` is ``model.metric`` — or a comma-separated list of metrics on
    the **same** model (``"sales.revenue,sales.profit"``) for a multi-metric chart.
    ``by_ref`` is the x-axis ``model.dim`` (or bare ``dim``); ``series_ref`` is an
    optional **second** dimension (``series={model.dim}``) that splits one metric
    into a coloured series per value. ``grain`` is a **literal** time-grain token
    (``grain="month"``) validated against the canonical :data:`GRAIN_TOKENS` and
    baked into the query name; ``grain_param`` is the name of a **control** that
    drives the grain interactively (``grain={trendGrain}``) — its value is read from
    the live params at fetch, so it does *not* enter the query name. The caller
    (``resolve_semantic``) passes exactly one (the ``key="lit"`` vs ``key={ref}``
    attr convention decides which). Raises ``ValueError`` on an unknown
    model/measure/dimension, an empty list, metrics spanning different models, a
    ``series`` combined with multiple metrics (no clean grouped form), or a bad
    grain token — surfaced as the component's inline error card.
    """
    metric_refs = [m.strip() for m in str(metric_ref).split(",") if m.strip()]
    if not metric_refs:
        raise ValueError("metric=… must name at least one `model.metric`")

    model_name: str | None = None
    handle: SemanticModelHandle | None = None
    metric_names: list[str] = []
    for mref in metric_refs:
        if "." not in mref:
            raise ValueError(
                f"metric={{{mref}}} must be `model.metric` (e.g. `sales.revenue`)"
            )
        m_model, metric_part = mref.split(".", 1)
        if model_name is None:
            model_name = m_model
            handle = models.get(model_name)
            if handle is None:
                raise ValueError(f"unknown semantic model {model_name!r}")
        elif m_model != model_name:
            raise ValueError(
                f"all metrics in one chart must belong to the same model; got "
                f"{model_name!r} and {m_model!r}"
            )
        metric_name = handle.measure_lookup.get(metric_part)
        if metric_name is None:
            raise ValueError(
                f"unknown metric {metric_part!r} on model {model_name!r} "
                f"(known: {sorted(handle.measure_lookup)})"
            )
        if metric_name not in metric_names:  # dedupe, keep author order
            metric_names.append(metric_name)
    metrics = tuple(metric_names)

    by_name = _resolve_dim(handle, model_name, by_ref, "by") if by_ref else None

    series_name: str | None = None
    if series_ref:
        if len(metrics) > 1:
            raise ValueError(
                "series=… (a second dimension) can't combine with multiple "
                "metrics — that's a metric×dimension cross-product with no clean "
                "grouped form. Use one metric with series=, or several metrics "
                "without it."
            )
        series_name = _resolve_dim(handle, model_name, series_ref, "series")

    # Grain shape-check: a literal token is validated here (availability is the
    # backend's job at query time); a control reference is recorded verbatim and
    # resolved per-fetch. A literal enters the query name (distinct cache entries);
    # a reference does not.
    grain_token = _normalize_grain(grain) if grain else None
    grain_param_val = grain_param or None
    # Grain buckets a *time grouping* — it only means something when there's a
    # `by`/`series` to bucket. A scalar reference (no grouping, e.g. a `<Counter>`
    # headline) drops it, so a metric used both as a scalar headline and as a
    # `grain=`-bucketed sparkline keeps the headline's query identity stable
    # (`_sem.model.metric`) regardless of the grain meant for the trend.
    if by_name is None and series_name is None:
        grain_token = None
        grain_param_val = None

    return SemanticRef(
        model=model_name,
        metrics=metrics,
        by=by_name,
        connector=handle.connector,
        query_name=semantic_query_name(
            model_name, metrics, by_name, series_name, grain_token
        ),
        series=series_name,
        grain=grain_token,
        grain_param=grain_param_val,
    )


def build_filters(handle: SemanticModelHandle, params: dict[str, str]) -> list[dict[str, Any]]:
    """Map the live filter params into BSL's JSON filter list.

    - any param whose key is a model **dimension** with a non-empty value →
      ``{"field": key, "operator": "in", "values": [...]}`` (single *and*
      multi-select dropdowns — the value is comma-split);
    - if the model has a time dimension and ``date_start``/``date_end`` are
      present → ``>=`` / ``<=`` conditions on that dimension.

    Values are passed as **data** (typed filter dicts), never string-interpolated
    into SQL — BSL/Ibis parameterizes them.
    """
    filters: list[dict[str, Any]] = []
    for key, value in params.items():
        if key.startswith("_") or not str(value):
            continue
        canonical = handle.dim_lookup.get(key)
        if canonical is not None:
            values = [v for v in str(value).split(",") if v != ""]
            if values:
                filters.append({"field": canonical, "operator": "in", "values": values})
    if handle.time_dimension:
        start = str(params.get(DATE_START_PARAM, ""))
        end = str(params.get(DATE_END_PARAM, ""))
        if start:
            filters.append({"field": handle.time_dimension, "operator": ">=", "value": start})
        if end:
            filters.append({"field": handle.time_dimension, "operator": "<=", "value": end})
    return filters


def semantic_filter_params(handle: SemanticModelHandle) -> list[str]:
    """The filter param names a semantic query reacts to — for the per-widget
    "filtered by" indicator.

    Mirrors :func:`build_filters`' matching surface: every accepted dimension
    name (the keys a dropdown's ``name=`` is matched against) plus the global
    date params when the model has a time dimension (the compiler maps
    ``date_start``/``date_end`` onto it). The client intersects this with the
    live, non-empty filters, so listing every dimension here is safe — only the
    ones actually set surface on the badge.
    """
    names = set(handle.dim_lookup)
    if handle.time_dimension:
        names.update({DATE_START_PARAM, DATE_END_PARAM})
    return sorted(names)


def resolve_grain_token(ref: SemanticRef, params: dict[str, str]) -> str | None:
    """The effective time-grain token for one fetch — literal or control-driven.

    For an interactive ``grain={control}`` the value comes from the live filter
    ``params`` (the control's ``name=``); for a literal ``grain="month"`` it's fixed
    on the ref. Returns a validated canonical token, or ``None`` for no grain / an
    empty control (the chart then groups at the dimension's native granularity).
    Shared by the ibis + cube backends so the literal-vs-control logic lives in one
    place. A bad control value raises (shape check) — surfaced as the error card.
    """
    if ref.grain_param:
        raw = str(params.get(ref.grain_param, "")).strip()
        return _normalize_grain(raw) if raw else None
    return ref.grain


@register_semantic_backend(DEFAULT_BACKEND)  # "ibis"
class IbisBackend(SemanticBackend):
    """Default backend — a BSL model (``semantic/*.yml``) compiled by Ibis and pushed
    down to a ``sources.yaml`` connector (or a BSL ``profile:``).

    ``SemanticModelHandle.build`` / ``ibis_backend_for_connector`` / ``_require_bsl``
    (module-level, above) are this backend's internals — only the ibis path uses
    them; a ``cube`` model has no Ibis model to build.
    """

    def introspect(
        self, handle: SemanticModelHandle, connectors: dict[str, Connector]
    ) -> None:
        # Build the model once (validates exprs + connector) and capture its
        # catalogue — measures/dimensions/lookups/time dim/formats — for the
        # pipeline + components without running a query.
        model = handle.build(connectors)
        measures = model.get_measures() if hasattr(model, "get_measures") else {}
        dims = model.get_dimensions() if hasattr(model, "get_dimensions") else {}
        handle.measures = set(measures)
        handle.dimensions = set(dims)
        handle.dim_lookup = _name_lookup(dims)
        handle.measure_lookup = _name_lookup(measures)
        for dname, d in dims.items():
            if getattr(d, "is_time_dimension", False) or getattr(d, "is_event_timestamp", False):
                handle.time_dimension = dname
                break
        # Read measure display-format hints straight from the YAML we parsed, keyed
        # to the canonical (possibly prefixed) name — independent of BSL's version
        # (older BSL doesn't surface `metadata` on the parsed measure).
        model_cfg = handle.file_config.get(handle.name) or {}
        for short_name, spec in (model_cfg.get("measures") or {}).items():
            if not isinstance(spec, dict):
                continue
            meta = spec.get("metadata") or {}
            fmt = {k: meta[k] for k in ("format", "currency", "decimals") if k in meta}
            if fmt:
                canonical = handle.measure_lookup.get(short_name, short_name)
                handle.measure_formats[canonical] = fmt

    def build_spec(
        self,
        handle: SemanticModelHandle,
        ref: SemanticRef,
        connectors: dict[str, Connector],
    ) -> PythonQuerySpec:
        def fn(params: dict[str, str], _connect: Callable[..., QueryResult]):
            # x-axis dimension first, then the optional series-split dimension; group
            # and order by both so the client's grouped-series chart reads cleanly.
            dims = [d for d in (ref.by, ref.series) if d]
            order_by = [(d, "asc") for d in dims] or None
            # Time grain: BSL truncates whichever grouping dimension is a
            # time dimension to this grain and validates it against that dimension's
            # `smallest_time_grain` (a too-fine grain raises → the per-component error
            # card). The canonical token maps verbatim onto BSL's `TIME_GRAIN_*`
            # constant; `None` leaves the dimension at its native granularity. A grain
            # with no time dimension in `dims` (e.g. on a `<Counter>`) is a no-op.
            token = resolve_grain_token(ref, params)
            time_grain = f"TIME_GRAIN_{token.upper()}" if token else None
            # The bridged DuckDB connection is shared with the connector and unsafe
            # for concurrent queries, so serialize the whole Ibis build+execute
            # through the connector's own lock (the same lock connector.query()
            # takes). A model can read more than one connector (a join), so lock
            # **every** connector the file touches — in a stable (name-sorted) order
            # to stay deadlock-free. Setup ran at load via introspect, so build with
            # ensure_setup=False (conn.query() here would re-enter the lock).
            with ExitStack() as stack:
                if handle.profile is None:
                    for cname in sorted(set(handle.table_connectors.values())):
                        conn = connectors.get(cname)
                        lock = getattr(conn, "_lock", None) if conn is not None else None
                        if lock is not None:
                            stack.enter_context(lock)
                model = handle.build(connectors, ensure_setup=False)
                q = model.query(
                    dimensions=dims,
                    measures=list(ref.metrics),
                    filters=build_filters(handle, params),
                    order_by=order_by,
                    time_grain=time_grain,
                )
                return q.to_pyarrow()  # normalize_to_query_result handles a pa.Table

        return PythonQuerySpec(
            name=ref.query_name,
            connector=handle.connector,
            fn=fn,
            cache_ttl=None,
            live=False,
            interval=None,
            description=f"semantic: {', '.join(ref.metrics)} by {ref.by} ({ref.model})",
        )


def build_semantic_spec(
    models: dict[str, SemanticModelHandle],
    ref: SemanticRef,
    connectors: dict[str, Connector],
) -> PythonQuerySpec:
    """Wrap a resolved :class:`SemanticRef` as a synthetic :class:`PythonQuerySpec`.

    Dispatches to the model's registered backend's
    :meth:`~dashdown.semantic_base.SemanticBackend.build_spec`. The returned spec is
    registered into ``_python_def_cache`` by ``render/pipeline.py``, so the data API,
    WS poller, static build, server cache, and the filter re-fetch path all execute a
    semantic query with **no special-casing** — the seam that makes a new backend
    cheap. Backends differ only in *how* their ``fn`` produces rows: BSL captures
    ``connectors`` and pushes Ibis down; Cube compiles a structured query and drives
    its connector's ``load()``; each is the backend's own concern.
    """
    handle = models[ref.model]
    return get_semantic_backend(handle.backend).build_spec(handle, ref, connectors)


# Register the built-in Cube backend (its ``@register_semantic_backend(...)``
# decorator runs on import). Kept at the bottom so this module is fully defined first;
# the module imports back from here only lazily (inside methods), so there's no import
# cycle. The ``dashdown.semantic_backends`` entry points in pyproject.toml are the same
# registration for an installed dist; this eager import is the belt-and-suspenders
# that keeps ibis+cube working in a source-tree run (and lets ``detect_backend``
# auto-detect them — inference only consults eagerly-registered backends).
from dashdown import semantic_cube as _semantic_cube  # noqa: E402,F401
