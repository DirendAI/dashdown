"""FastAPI app + live-reload endpoint."""
from __future__ import annotations

import asyncio
import json
import logging
import math
import time
from pathlib import Path, PurePosixPath

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import (
    FileResponse,
    HTMLResponse,
    PlainTextResponse,
    StreamingResponse,
)
from fastapi.staticfiles import StaticFiles
from jinja2 import Environment, FileSystemLoader, select_autoescape

from dashdown.auth import challenge_headers, is_authorized
from dashdown.embed import (
    frame_headers,
    query_key,
    sign_embed_token,
    token_allows_query,
    verify_embed_token,
)
from dashdown.llm import (
    cache_answer,
    generate_answer_html,
    get_ask_def,
    get_cached_answer,
    relevant_params,
    resolve_model_name,
)
from dashdown.project import (
    Project,
    load_project,
    build_breadcrumbs,
    format_config_json,
    resolve_logo_url,
)
from dashdown.render.pipeline import (
    render_page,
    get_query_def,
    get_python_query_def,
    get_stream_interval,
    _freeze_params,
    _substitute_params,
    get_cached_result,
    cache_result,
    serialize_result,
    serialize_value,
    build_options_sql,
    DEFAULT_CACHE_TTL,
    DEFAULT_OPTIONS_LIMIT,
)
from dashdown.python_query import run_python_query
from dashdown.streaming import DISCONNECT, hub as stream_hub, watch_disconnect
from dashdown.data.base import Connector

log = logging.getLogger(__name__)

_PKG_DIR = Path(__file__).parent
_TEMPLATES_DIR = _PKG_DIR / "templates"
_STATIC_DIR = _PKG_DIR / "static"

# Paths always reachable without credentials (liveness probes need this).
_AUTH_EXEMPT_PATHS = frozenset({"/_dashdown/health"})

_DATA_API_PREFIX = "/_dashdown/api/data/"
_OPTIONS_API_PREFIX = "/_dashdown/api/options/"
_ASK_API_PREFIX = "/_dashdown/api/ask/"


def _canonical_page_path(path: str) -> str:
    """Normalize a URL path to the canonical page form used as a token claim
    (mirrors how ``page()`` computes ``current``)."""
    return path.rstrip("/") or "/"


def _page_dir_of(project: "Project", md_path: "Path") -> str:
    """The page's directory under ``pages/`` as a POSIX string ("" at the root),
    used to resolve co-located asset references relative to the page."""
    try:
        rel = md_path.parent.relative_to(project.pages_dir).as_posix()
    except ValueError:
        return ""
    return "" if rel == "." else rel


def _page_asset_path(project: "Project", url_path: str) -> "Path | None":
    """Resolve a request path to a co-located page asset — a non-``.md`` file
    living under ``pages/`` — or None. Confined to ``pages/`` (traversal guard);
    the ``.md`` source itself is never served."""
    rel = url_path.strip("/")
    if not rel:
        return None
    pages_root = project.pages_dir.resolve()
    try:
        candidate = (project.pages_dir / rel).resolve()
    except (OSError, ValueError, RuntimeError):
        return None
    if not candidate.is_relative_to(pages_root):
        return None
    if not candidate.is_file() or candidate.suffix.lower() == ".md":
        return None
    return candidate


def _embed_authorizes(proj: "Project", request: Request) -> bool:
    """Whether a valid, page-scoped embed token authorizes this HTTP request.

    Lets an authenticated-dashboard page be embedded cross-origin (where the
    iframe can't send Basic/API-key headers) without unlocking the rest of the
    app: a token is scoped to one page path and to the ``connector:query`` pairs
    that page reads, so a leaked embed URL only grants what its page already
    shows. Returns False unless embedding is enabled *and* a secret is set.
    """
    embed_cfg = proj.config.embed
    if not embed_cfg.enabled or not embed_cfg.has_secret:
        return False
    payload = verify_embed_token(embed_cfg.secret, request.query_params.get("_embed"))
    if payload is None:
        return False
    path = request.url.path
    # Static assets the embedded page needs to paint (CSS/JS/fonts/world.json,
    # plus any custom component's colocated JS/CSS).
    if (
        path.startswith("/_dashdown/static/")
        or path.startswith("/assets/")
        or path.startswith("/_dashdown/components/")
    ):
        return True
    # Data API: the requested query must be in the token's scope.
    if path.startswith(_DATA_API_PREFIX):
        name = path[len(_DATA_API_PREFIX):]
        connector = str(request.query_params.get("_connector") or proj.default_connector)
        return token_allows_query(payload, connector, name)
    # Options API (Combobox): scoped to the same query it reads from.
    if path.startswith(_OPTIONS_API_PREFIX):
        name = path[len(_OPTIONS_API_PREFIX):]
        connector = str(request.query_params.get("_connector") or proj.default_connector)
        return token_allows_query(payload, connector, name)
    # Ask API: resolve the opaque id to its underlying query, then scope-check.
    if path.startswith(_ASK_API_PREFIX):
        ask = get_ask_def(path[len(_ASK_API_PREFIX):])
        if ask is None:
            return False
        return token_allows_query(payload, ask.connector, ask.query_name)
    # Otherwise it's a page request: the token must be scoped to this exact page.
    return payload.get("path") == _canonical_page_path(path)


class NoCacheStaticFiles(StaticFiles):
    """StaticFiles that forces revalidation on every request.

    Without an explicit Cache-Control, browsers apply heuristic caching to ES
    modules, so after a framework update a page could load fresh CSS alongside
    a stale cached JS module (e.g. an old ECharts theme painting the previous
    card color). `no-cache` still allows conditional requests — unchanged
    files answer 304 via the ETag — but never serves silently from cache.
    """

    def file_response(self, *args, **kwargs):  # type: ignore[override]
        response = super().file_response(*args, **kwargs)
        response.headers["Cache-Control"] = "no-cache"
        return response


class ComponentStaticFiles(NoCacheStaticFiles):
    """Serve ONLY a custom component's colocated frontend assets (.js/.css),
    never its .py source.

    The ``components/`` dir mixes Python (server-side, must not be web-readable)
    with the JS/CSS that hydrates those components (must be), so this refuses any
    other extension with a 404 — the .py stays on the server. Path traversal is
    already handled by StaticFiles (it resolves and confines paths to the mount
    dir); this only narrows the allowed extensions.
    """

    _ALLOWED = {".js", ".css", ".mjs", ".map"}

    async def get_response(self, path: str, scope):  # type: ignore[override]
        from starlette.responses import Response as _Response

        if PurePosixPath(path).suffix.lower() not in self._ALLOWED:
            return _Response("Not Found", status_code=404)
        return await super().get_response(path, scope)


def create_app(project_root: Path, *, dev: bool = True) -> FastAPI:
    """Build the FastAPI app for a project.

    ``dev=True`` (the default, used by ``dashdown serve``) keeps the live-reload
    SSE wired up. ``dev=False`` is the production posture (e.g. the ASGI entry
    point run under multiple workers): it suppresses the client live-reload
    stream — pointless without a file watcher and a wasted persistent connection
    per viewer — and **pre-registers every page's queries at startup** so the
    process can answer a ``/api/data`` request for any page even if it never
    rendered that page itself (inline ``:::query`` defs otherwise only land in
    this process's cache when the page is rendered — fine for one worker, a 404
    source across several).
    """
    project = load_project(project_root)
    reload_event = asyncio.Event()

    env = Environment(
        loader=FileSystemLoader(str(_TEMPLATES_DIR)),
        autoescape=select_autoescape(["html"]),
    )
    env.globals["_render_nav"] = _render_nav_html
    page_template = env.get_template("page.html")

    app = FastAPI(title=project.config.title)
    app.state.project = project
    app.state.reload_event = reload_event
    app.state.dev = dev

    if not dev:
        register_all_page_queries(project)

    @app.middleware("http")
    async def auth_guard(request: Request, call_next):
        # Read the live project so a config reload picks up auth changes.
        proj: Project = request.app.state.project
        auth = proj.config.auth
        if (
            auth.enabled
            and request.url.path not in _AUTH_EXEMPT_PATHS
            and not is_authorized(auth, request)
            # A valid, page-scoped embed token authorizes embed requests that
            # can't carry Basic/API-key creds (cross-origin iframe + its data).
            and not _embed_authorizes(proj, request)
        ):
            return PlainTextResponse(
                "401 Unauthorized",
                status_code=401,
                headers=challenge_headers(auth),
            )
        return await call_next(request)

    app.mount(
        "/_dashdown/static",
        NoCacheStaticFiles(directory=str(_STATIC_DIR)),
        name="static",
    )
    if project.assets_dir.is_dir():
        app.mount(
            "/assets",
            NoCacheStaticFiles(directory=str(project.assets_dir)),
            name="user-assets",
        )
    # Custom components' colocated frontend assets (.js/.css only — the .py
    # source is never served; see ComponentStaticFiles). Injected per page by
    # the template from project.component_js / project.component_css.
    if project.components_dir.is_dir():
        app.mount(
            "/_dashdown/components",
            ComponentStaticFiles(directory=str(project.components_dir)),
            name="components",
        )

    @app.get("/_dashdown/reload")
    async def reload_stream(request: Request):
        async def gen():
            # Poll for client disconnect rather than parking indefinitely on
            # reload_event: a generator blocked forever on .wait() never lets
            # uvicorn notice the client went away, so a navigated-away stream
            # would hold its connection open server-side. Waking ~once a second
            # to re-check is_disconnected() lets the server release the slot
            # promptly (belt-and-suspenders with the client's pagehide close).
            while True:
                if await request.is_disconnected():
                    break
                try:
                    await asyncio.wait_for(reload_event.wait(), timeout=1.0)
                except asyncio.TimeoutError:
                    continue
                reload_event.clear()
                yield "data: reload\n\n"

        return StreamingResponse(gen(), media_type="text/event-stream")

    @app.get("/_dashdown/health")
    async def health():
        return PlainTextResponse("ok")

    @app.get("/_dashdown/api/search-index")
    async def get_search_index(request: Request):
        """Full-text search index for every concrete page.

        Built from the live project on demand so a page/content edit is reflected
        without a server restart. The browser (`site_search.js`) does the actual
        ranking — there is no server-side search execution. The static build bakes
        the equivalent JSON to `_dashdown/search-index.json`.
        """
        from fastapi.responses import JSONResponse

        from dashdown.search import build_search_index

        proj: Project = request.app.state.project
        return JSONResponse(build_search_index(proj))

    @app.get("/_dashdown/api/data/{query_name}")
    async def get_query_data(query_name: str, request: Request):
        """API endpoint to fetch query data for async loading.
        
        Accepts query parameters that will be substituted into SQL using ${param} placeholders.
        The _connector parameter specifies which connector to use.
        Example: /_dashdown/api/data/sales?region=East&_connector=main
        If SQL contains ${region}, it will be replaced with parameterized query.
        """
        from fastapi import HTTPException
        from fastapi.responses import JSONResponse
        from urllib.parse import unquote
        
        proj: Project = request.app.state.project

        # Get connector name from query params (sent by client)
        connector_name = str(request.query_params.get("_connector") or proj.default_connector)

        # Get query parameters from URL - these are the filter values from dropdowns
        # Convert QueryParams to dict, handling multiple values per key
        filter_params = {}
        for key, value in request.query_params.items():
            # Skip internal parameters
            if key.startswith("_"):
                continue
            # URL decode the value (handles spaces, etc.)
            filter_params[key] = unquote(str(value))

        # A Python query (queries/*.py) takes the parallel registry path —
        # checked FIRST, runs the decorated function instead of SQL. `params`
        # reach it as a plain dict (data, never substituted into a body), so the
        # ${param} injection surface doesn't exist for Python.
        py_spec = get_python_query_def(query_name, connector_name)
        if py_spec is not None:
            ttl = py_spec.cache_ttl if py_spec.cache_ttl is not None else DEFAULT_CACHE_TTL
            all_params = dict(filter_params)
            cache_headers = {"Cache-Control": f"max-age={ttl}"}

            def _serialize_py(result) -> JSONResponse:
                payload = serialize_result(result)
                payload["query"] = query_name
                return JSONResponse(payload, headers=cache_headers)

            cached = get_cached_result(query_name, connector_name, all_params)
            if cached is not None:
                return _serialize_py(cached)
            try:
                # Same threadpool discipline as a connector query: the function is
                # author code (and may itself call connect()), all blocking.
                result = await asyncio.to_thread(
                    run_python_query, py_spec, all_params, proj.connectors
                )
                cache_result(query_name, connector_name, all_params, result, ttl)
                return _serialize_py(result)
            except Exception as e:
                log.exception("Python query failed for %s: %s", query_name, e)
                raise HTTPException(
                    status_code=500,
                    detail=f"Query execution failed: {type(e).__name__}: {e}",
                )

        # Get query definition from cache
        query_def = get_query_def(query_name, connector_name)
        if query_def is None:
            raise HTTPException(
                status_code=404,
                detail=f"Query '{query_name}' not found for connector '{connector_name}'"
            )

        sql, default_params, cache_ttl = query_def
        ttl = cache_ttl if cache_ttl is not None else DEFAULT_CACHE_TTL

        connector = proj.connectors.get(connector_name)
        if connector is None:
            raise HTTPException(
                status_code=400,
                detail=f"Connector '{connector_name}' not found"
            )

        # Merge default params with filter params - filter params take precedence
        all_params = {**default_params, **filter_params}

        cache_headers = {"Cache-Control": f"max-age={ttl}"}

        def _serialize(result) -> JSONResponse:
            payload = serialize_result(result)
            payload["query"] = query_name
            return JSONResponse(payload, headers=cache_headers)

        # Check server-side result cache before executing the query
        cached = get_cached_result(query_name, connector_name, all_params)
        if cached is not None:
            return _serialize(cached)

        # Substitute parameters into SQL with proper escaping
        final_sql = _substitute_params(sql, all_params)

        # Execute query off the event loop: connector.query() is blocking
        # (DuckDB/DB-API), and running it inline on this single async loop would
        # stall every other request — notably the next page's HTML render when
        # the user clicks a menu item mid-query. Connectors are internally
        # lock-guarded, so a threadpool call is safe (mirrors streaming.py).
        try:
            result = await asyncio.to_thread(connector.query, final_sql)
            cache_result(query_name, connector_name, all_params, result, ttl)
            return _serialize(result)
        except Exception as e:
            log.exception("Query execution failed for %s: %s", query_name, e)
            raise HTTPException(
                status_code=500,
                detail=f"Query execution failed: {type(e).__name__}: {e}"
            )

    @app.get("/_dashdown/api/options/{query_name}")
    async def get_query_options(query_name: str, request: Request):
        """Distinct, server-side-searchable column values for a ``<Combobox>``.

        Wraps the named query's SQL (the same one a chart/table reads) into a
        DISTINCT lookup (`build_options_sql`) so a high-cardinality column is
        searched **in the warehouse** with a ``LIMIT`` rather than shipping every
        value to the browser — the gap a plain ``<Dropdown>`` can't fill. The
        search term and column are the only new inputs; both go through the same
        injection-safe rules as ``${param}`` substitution (`build_options_sql`).
        SQL connectors only.

        ``_column`` (required), ``_search`` (optional substring), ``_limit``
        (optional) are read from the query string; every other non-``_`` param is
        an active filter value substituted into the wrapped query (so options can
        cascade off other filters), exactly like the data API.
        """
        from fastapi import HTTPException
        from fastapi.responses import JSONResponse
        from urllib.parse import unquote

        proj: Project = request.app.state.project
        connector_name = str(request.query_params.get("_connector") or proj.default_connector)
        column = str(request.query_params.get("_column", ""))
        search = unquote(str(request.query_params.get("_search", "")))
        try:
            limit = int(request.query_params.get("_limit", DEFAULT_OPTIONS_LIMIT))
        except (TypeError, ValueError):
            limit = DEFAULT_OPTIONS_LIMIT

        # SQL-only: a Python query has no SQL body to wrap as a subquery.
        if get_python_query_def(query_name, connector_name) is not None:
            raise HTTPException(
                status_code=400,
                detail="Combobox options are not supported for Python queries",
            )

        query_def = get_query_def(query_name, connector_name)
        if query_def is None:
            raise HTTPException(
                status_code=404,
                detail=f"Query '{query_name}' not found for connector '{connector_name}'",
            )

        connector = proj.connectors.get(connector_name)
        if connector is None:
            raise HTTPException(
                status_code=400, detail=f"Connector '{connector_name}' not found"
            )

        sql, default_params, _ = query_def

        # Active filters (so options can cascade off other controls), merged over
        # the query's defaults — same precedence as the data API.
        filter_params = {}
        for key, value in request.query_params.items():
            if key.startswith("_"):
                continue
            filter_params[key] = unquote(str(value))
        all_params = {**default_params, **filter_params}

        inner_sql = _substitute_params(sql, all_params)
        try:
            options_sql = build_options_sql(inner_sql, column, search, limit)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))

        try:
            result = await asyncio.to_thread(connector.query, options_sql)
        except Exception as e:
            log.exception("Options query failed for %s: %s", query_name, e)
            raise HTTPException(
                status_code=500,
                detail=f"Options query failed: {type(e).__name__}: {e}",
            )

        values = [
            serialize_value(row[0])
            for row in result.rows
            if row and row[0] is not None
        ]
        return JSONResponse(
            {"options": values, "query": query_name},
            headers={"Cache-Control": "max-age=30"},
        )

    @app.websocket("/_dashdown/ws/data/{query_name}")
    async def stream_query_data(websocket: WebSocket, query_name: str):
        """Stream live query results over a WebSocket.

        Additive to the data API: a ``:::query … live`` block opts a query in,
        and a shared poll loop (`dashdown.streaming`) re-runs it on an interval,
        pushing a fresh ``{columns, rows}`` snapshot only when the result
        actually changes. **One loop per (query, connector, params) feeds all
        subscribers**, so N viewers don't multiply the load. Polling (not
        connector change-streams) so it works against every connector.

        Security: Starlette's ``@app.middleware("http")`` auth guard does **not**
        run for WebSocket connections, so this checks ``is_authorized`` itself
        and refuses the handshake when unauthorized — otherwise the data API
        would be locked while the live socket stayed open. It also refuses any
        query not registered ``live``, so the socket can't be turned into an
        arbitrary repeating query runner.
        """
        from urllib.parse import unquote

        proj: Project = websocket.app.state.project

        connector_name = str(websocket.query_params.get("_connector") or proj.default_connector)

        # Auth first — reject before accept() (fails the handshake, no frames).
        # A valid embed token scoped to this query also authorizes the socket
        # (an embedded live page can't send Basic/api_key creds on a WS upgrade).
        auth = proj.config.auth
        if auth.enabled and not is_authorized(auth, websocket):
            embed_cfg = proj.config.embed
            payload = (
                verify_embed_token(embed_cfg.secret, websocket.query_params.get("_embed"))
                if (embed_cfg.enabled and embed_cfg.has_secret)
                else None
            )
            if payload is None or not token_allows_query(
                payload, connector_name, query_name
            ):
                await websocket.close(code=1008)  # policy violation
                return

        # Only queries explicitly marked `live` may stream.
        interval = get_stream_interval(query_name, connector_name)
        if interval is None:
            await websocket.close(code=1008)
            return

        filter_params = {
            key: unquote(str(value))
            for key, value in websocket.query_params.items()
            if not key.startswith("_")
        }

        # A live Python query polls through the same fan-out loop — the only
        # difference is the fetch thunk (run the function vs. SQL). Checked first,
        # like the data API.
        py_spec = get_python_query_def(query_name, connector_name)
        if py_spec is not None:
            all_params = dict(filter_params)
            fetch = lambda: run_python_query(py_spec, all_params, proj.connectors)
        else:
            query_def = get_query_def(query_name, connector_name)
            connector = proj.connectors.get(connector_name)
            if query_def is None or connector is None:
                await websocket.close(code=1008)
                return
            sql, default_params, _ = query_def
            all_params = {**default_params, **filter_params}
            final_sql = _substitute_params(sql, all_params)
            fetch = lambda: connector.query(final_sql)

        # Subscribers sharing the same query+connector+params share one poller.
        key = (query_name, connector_name, _freeze_params(all_params))

        await websocket.accept()
        poller, queue = stream_hub.subscribe(key, fetch, query_name, interval)
        # Push-only socket: a side-task watches for the client disconnecting and
        # drops a sentinel on the queue so this loop unblocks even when the query
        # is changing rarely (otherwise we'd never notice the client left).
        watcher = asyncio.create_task(watch_disconnect(websocket, queue))
        try:
            # Replay the last good snapshot so a late joiner paints immediately
            # instead of waiting up to one interval for the next change.
            if poller.latest is not None:
                await websocket.send_text(poller.latest)
            while True:
                item = await queue.get()
                if item is DISCONNECT:
                    break
                await websocket.send_text(item)
        except WebSocketDisconnect:
            pass
        finally:
            watcher.cancel()
            stream_hub.unsubscribe(key, queue)

    @app.get("/_dashdown/api/ask/{ask_id}")
    def get_ask_commentary(ask_id: str, request: Request):
        """Generate (or serve cached) LLM commentary for an <Ask /> block.

        The id resolves to a prompt registered at page-render time, so this
        endpoint can't be fed arbitrary prompts. Filter params substitute into
        the referenced query exactly like the data API; the answer is cached
        per (ask id, params the SQL actually uses) so repeat page loads don't
        spend LLM credits. `_refresh=1` (the card's ↻ button) bypasses the
        cache read. Deliberately a sync `def`: FastAPI runs it in the
        threadpool, so a multi-second LLM call doesn't block the event loop.
        """
        from fastapi import HTTPException
        from fastapi.responses import JSONResponse
        from urllib.parse import unquote

        proj: Project = request.app.state.project

        ask = get_ask_def(ask_id)
        if ask is None:
            raise HTTPException(status_code=404, detail=f"Ask '{ask_id}' not found")

        llm_cfg = proj.config.llm
        if not llm_cfg.enabled:
            raise HTTPException(
                status_code=503,
                detail="No LLM provider configured — add an `llm:` block to dashdown.yaml",
            )
        # The model that authored the commentary, surfaced to the reader. Derived
        # from config (not the adapter), so a cache hit reports it without
        # constructing/importing the provider SDK.
        model = resolve_model_name(llm_cfg)

        filter_params = {
            key: unquote(str(value))
            for key, value in request.query_params.items()
            if not key.startswith("_")
        }

        # Resolve the data source the same way the data API does: a Python /
        # semantic query (synthetic PythonQuerySpec in `_python_def_cache`) is
        # checked FIRST — it runs its callable instead of SQL — then the SQL path.
        # This is what lets <Ask metric={model.metric} /> comment on semantic-layer
        # data and a plain queries/*.py source work too.
        py_spec = get_python_query_def(ask.query_name, ask.connector)
        if py_spec is not None:
            all_params = dict(filter_params)
            # A Python/semantic body has no SQL text to scan for `${param}`, so the
            # answer cache keys on every filter param (can't narrow to "params the
            # body uses"). It can over-invalidate on an unrelated filter change, but
            # never serves a stale answer — matching the result cache, which also
            # keys on all params for Python queries.
            cache_params = all_params
            result_ttl = (
                py_spec.cache_ttl if py_spec.cache_ttl is not None else DEFAULT_CACHE_TTL
            )

            def _run_query():
                return run_python_query(py_spec, all_params, proj.connectors)
        else:
            query_def = get_query_def(ask.query_name, ask.connector)
            if query_def is None:
                raise HTTPException(
                    status_code=404,
                    detail=f"Query '{ask.query_name}' not found for connector '{ask.connector}'",
                )
            connector = proj.connectors.get(ask.connector)
            if connector is None:
                raise HTTPException(
                    status_code=400, detail=f"Connector '{ask.connector}' not found"
                )

            sql, default_params, cache_ttl = query_def
            all_params = {**default_params, **filter_params}
            # Cache key ignores params the SQL never substitutes, so an unrelated
            # filter change doesn't trigger a fresh (billable) LLM call.
            cache_params = relevant_params(sql, all_params)
            result_ttl = cache_ttl if cache_ttl is not None else DEFAULT_CACHE_TTL

            def _run_query():
                return connector.query(_substitute_params(sql, all_params))

        if request.query_params.get("_refresh") != "1":
            cached_html = get_cached_answer(ask_id, cache_params)
            if cached_html is not None:
                return JSONResponse(
                    {"ask_id": ask_id, "html": cached_html, "cached": True, "model": model}
                )

        # Run the referenced query, sharing the data API's result cache.
        result = get_cached_result(ask.query_name, ask.connector, all_params)
        if result is None:
            try:
                result = _run_query()
            except Exception as e:
                log.exception("Ask query execution failed for %s", ask.query_name)
                raise HTTPException(
                    status_code=500,
                    detail=f"Query execution failed: {type(e).__name__}: {e}",
                )
            cache_result(ask.query_name, ask.connector, all_params, result, result_ttl)

        try:
            adapter = proj.get_llm_adapter()
            answer_html = generate_answer_html(ask, result, adapter)
        except ImportError as e:
            # Missing optional extra — surface the install hint.
            raise HTTPException(status_code=503, detail=str(e))
        except Exception as e:
            log.exception("LLM request failed for ask %s", ask_id)
            raise HTTPException(
                status_code=502, detail=f"LLM request failed: {type(e).__name__}: {e}"
            )

        cache_answer(ask_id, cache_params, answer_html, ask.cache_ttl)
        return JSONResponse(
            {"ask_id": ask_id, "html": answer_html, "cached": False, "model": model}
        )

    @app.get("/_dashdown/api/pdf")
    def export_page_pdf(request: Request):
        """Render the current page to a presentation PDF with headless Chromium —
        the **same** engine as `dashdown pdf`, so the in-app "Export PDF" button
        gets identical output (instead of the browser's own print dialog).

        Control params are `_`-prefixed (`_path`, `_orientation`, `_format`);
        every other query param is forwarded as filter state, so the PDF reflects
        the page's current filters. Deliberately a sync `def`: FastAPI runs it in
        the threadpool, so the multi-second Chromium render (sync Playwright)
        doesn't block the event loop — which stays free to serve the data
        requests that same render makes against this server.
        """
        from urllib.parse import urlencode

        from fastapi import HTTPException
        from fastapi.responses import Response

        from dashdown.pdf import render_url_pdf

        proj: Project = request.app.state.project

        raw_path = request.query_params.get("_path", "/")
        full = raw_path.strip("/")
        md_path, _params = proj.page_path(full)
        if md_path is None:
            raise HTTPException(status_code=404, detail=f"No page for {raw_path!r}")

        orientation = request.query_params.get("_orientation", "portrait")
        if orientation not in ("portrait", "landscape"):
            raise HTTPException(
                status_code=422, detail="_orientation must be 'portrait' or 'landscape'"
            )
        page_format = request.query_params.get("_format", "A4")

        # Forward the current filter state (non-`_` params) so the export matches
        # what the author sees; Playwright loads that exact filtered URL.
        filters = {
            k: v for k, v in request.query_params.multi_items() if not k.startswith("_")
        }
        base = str(request.base_url).rstrip("/")
        target = f"{base}/{full}" if full else f"{base}/"
        if filters:
            target += "?" + urlencode(filters)

        # Let the headless browser satisfy this server's own auth (it's a separate
        # process with no session): replay the configured Basic / api_key secret.
        auth = proj.config.auth
        http_credentials = None
        extra_headers = None
        if auth.type == "basic" and auth.users:
            user, password = next(iter(auth.users.items()))
            http_credentials = {"username": user, "password": password}
        elif auth.type == "api_key" and auth.keys:
            extra_headers = {auth.header: auth.keys[0]}

        try:
            pdf_bytes = render_url_pdf(
                target,
                orientation=orientation,
                fmt=page_format,
                http_credentials=http_credentials,
                extra_headers=extra_headers,
            )
        except RuntimeError as e:  # missing `pdf` extra — friendly install hint
            raise HTTPException(status_code=503, detail=str(e))
        except Exception as e:  # noqa: BLE001
            log.exception("PDF export failed for %s", target)
            raise HTTPException(
                status_code=500, detail=f"PDF export failed: {type(e).__name__}: {e}"
            )

        filename = (full.replace("/", "-") or "index") + ".pdf"
        return Response(
            content=pdf_bytes,
            media_type="application/pdf",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    @app.get("/_dashdown/api/embed-token")
    def mint_embed_token(request: Request):
        """Mint a signed, page-scoped embed token for ``?path=/some/page``.

        Behind the normal auth guard, so only an authenticated author can mint —
        and the endpoint is in no token's scope, so an embed token can't mint
        more. The page is rendered to discover the queries it's allowed to read;
        those (plus any <Ask /> queries) are baked into the token so a leaked
        embed URL only unlocks that page's data. Returns the token + canonical
        path + expiry; the caller builds the <script> snippet with its own host.
        """
        from fastapi import HTTPException
        from fastapi.responses import JSONResponse

        proj: Project = request.app.state.project
        embed_cfg = proj.config.embed
        if not embed_cfg.enabled:
            raise HTTPException(
                status_code=503,
                detail="Embedding is disabled — set embed.enabled in dashdown.yaml",
            )
        if not embed_cfg.has_secret:
            raise HTTPException(
                status_code=503,
                detail="No embed.secret configured — required to mint signed tokens",
            )

        raw_path = request.query_params.get("path", "/")
        full = raw_path.lstrip("/")
        md_path, params = proj.page_path(full)
        if md_path is None:
            raise HTTPException(status_code=404, detail=f"No page for {raw_path!r}")
        canonical = ("/" + full).rstrip("/") or "/"

        try:
            source = md_path.read_text(encoding="utf-8")
            rendered = render_page(
                source,
                proj.connectors,
                params=params,
                current_path=canonical,
                include_base=proj.root,
                library=proj.queries,
                python_library=proj.python_queries,
                semantic_models=proj.semantic_models,
                filter_debounce=proj.config.filters.debounce,
            )
        except Exception as e:  # noqa: BLE001
            log.exception("Embed-token render failed for %s", md_path)
            raise HTTPException(
                status_code=500, detail=f"Render failed: {type(e).__name__}: {e}"
            )

        queries = [
            query_key(str(d.get("connector", "main")), name)
            for name, d in rendered.query_defs.items()
        ]
        for ask in rendered.ask_defs:
            queries.append(query_key(ask.connector, ask.query_name))

        ttl_param = request.query_params.get("ttl")
        try:
            ttl = int(ttl_param) if ttl_param is not None else embed_cfg.token_ttl
        except ValueError:
            raise HTTPException(status_code=400, detail="ttl must be an integer")
        exp = int(time.time()) + ttl if ttl > 0 else None

        token = sign_embed_token(embed_cfg.secret, canonical, queries, exp)
        return JSONResponse(
            {"token": token, "path": canonical, "exp": exp, "queries": sorted(set(queries))}
        )

    @app.get("/{full_path:path}", response_class=HTMLResponse)
    async def page(full_path: str, request: Request):
        proj: Project = request.app.state.project
        md_path, params = proj.page_path(full_path)
        if md_path is None:
            # Not a page — maybe a co-located page asset (image / download next to
            # a .md under pages/). Serve it directly; otherwise 404.
            asset = _page_asset_path(proj, full_path)
            if asset is not None:
                return FileResponse(asset)
            return HTMLResponse(
                _not_found_html(proj, full_path), status_code=404
            )
        # Embed mode: render chrome-less (no header/sidebar/breadcrumbs) when
        # ?_embed is present *and* embedding is enabled. When auth is on, the
        # middleware has already validated the token scopes to this page, so
        # reaching here means it's allowed. Filters travel in body_html, so a
        # chrome-less page stays fully interactive. Computed before render so the
        # global date filter renders as a page filter (not header chrome) in embeds.
        embed_cfg = proj.config.embed
        embed_on = bool(embed_cfg.enabled and request.query_params.get("_embed"))

        try:
            source = md_path.read_text(encoding="utf-8")
            current = ("/" + full_path).rstrip("/") or "/"
            rendered = render_page(
                source,
                proj.connectors,
                params=params,
                current_path=current,
                include_base=proj.root,
                page_dir=_page_dir_of(proj, md_path),
                library=proj.queries,
                python_library=proj.python_queries,
                semantic_models=proj.semantic_models,
                global_date=proj.config.global_date,
                embed=embed_on,
                embed_enabled=embed_cfg.enabled,
                filter_debounce=proj.config.filters.debounce,
            )
        except Exception as e:  # noqa: BLE001
            log.exception("Render failed for %s", md_path)
            return HTMLResponse(
                f"<pre>Render error: {type(e).__name__}: {e}</pre>", status_code=500
            )

        nav = proj.nav_tree()
        page_title = rendered.frontmatter.get("title", md_path.stem)
        breadcrumbs = build_breadcrumbs(current, nav, page_title)

        # For async loading, we still pass datasets for backward compatibility
        # but client will use API for async loading
        branding = proj.config.branding
        # Escape hatch for utility classes the pre-built CSS doesn't include:
        # a project's assets/custom.css is linked last (highest priority).
        custom_css_url = (
            "/assets/custom.css"
            if (proj.assets_dir / "custom.css").is_file()
            else None
        )
        html = page_template.render(
            title=proj.config.title,
            page_title=page_title,
            body_html=rendered.body_html,
            datasets_json=json.dumps(rendered.datasets, default=_json_default),
            query_defs_json=json.dumps(rendered.query_defs, default=_json_default),
            # Dynamic `[slug]` route params, so the client can carry them on every
            # data request (unique URL per record — no cross-record cache hits).
            # None on static pages so the template omits the script entirely.
            route_params_json=(
                json.dumps(rendered.route_params, default=_json_default)
                if rendered.route_params
                else None
            ),
            nav_tree=nav,
            pages=proj.list_pages(),
            current=current,
            breadcrumbs=breadcrumbs,
            logo_url=resolve_logo_url(branding.logo),
            favicon_url=resolve_logo_url(branding.favicon),
            branding_json=json.dumps({"palette": branding.palette}) if branding.palette else None,
            format_json=format_config_json(proj.config.format),
            custom_css_url=custom_css_url,
            # Colocated custom-component assets, relative to /_dashdown/components;
            # the template prefixes them with the asset prefix and emits a
            # <link>/<script type=module> for each. Same for embeds (custom
            # visual components must hydrate there too).
            component_js=proj.component_js,
            component_css=proj.component_css,
            embed=embed_on,
            embed_enabled=embed_cfg.enabled,
            global_date_html=rendered.global_date_html,
            page_actions_html=rendered.page_actions_html,
            search_enabled=proj.config.search.enabled,
            search_placeholder=proj.config.search.placeholder,
            search_max_results=proj.config.search.max_results,
            # Desktop sidebar collapse: `collapsed` seeds the first-visit state (a
            # saved localStorage choice overrides it); `toggle` gates the control.
            # Mobile slide-in is unaffected.
            sidebar_collapsed=proj.config.sidebar.collapsed,
            sidebar_toggle=proj.config.sidebar.toggle,
            # A single-page project has nothing to navigate to, so the nav and its
            # menu buttons are omitted entirely — unless `show_single_page` forces
            # them on.
            show_sidebar=(
                proj.config.sidebar.show_single_page
                or proj.navigable_page_count() > 1
            ),
            live_reload=request.app.state.dev,
        )
        # Framing policy applies to every page (deny-by-default): a page can only
        # be put in an <iframe> once embed.frame_ancestors lists the host origin.
        return HTMLResponse(html, headers=frame_headers(embed_cfg))

    return app


def _not_found_html(project: Project, path: str) -> str:
    links = "".join(f'<li><a href="{p}">{p}</a></li>' for p in project.list_pages())
    return (
        f"<!doctype html><meta charset='utf-8'>"
        f"<title>Not found</title>"
        f"<h1>404 \u2014 no page for /{path}</h1>"
        f"<p>Available pages:</p><ul>{links}</ul>"
    )


def _json_default(obj: object) -> object:
    """JSON fallback serializer — handles Decimal, datetime, numpy, NaN."""
    if isinstance(obj, float) and (math.isnan(obj) or math.isinf(obj)):
        return None
    return str(obj)


def trigger_reload(app: FastAPI) -> None:
    ev: asyncio.Event = app.state.reload_event
    ev.set()


def reload_project(app: FastAPI) -> None:
    """Re-load project config + connectors + user modules."""
    old: Project = app.state.project
    try:
        new = load_project(old.root)
    except Exception as e:  # noqa: BLE001
        log.error("Failed to reload project: %s", e)
        return
    old.close()
    app.state.project = new


def register_all_page_queries(project: Project) -> None:
    """Render every page once (discarding output) so its inline ``:::query``
    defs land in this process's query-def cache.

    Library queries already register at startup (``register_library_queries``),
    but inline page queries only register when their page is rendered — fine for
    a single worker, but under multiple workers a ``/api/data`` request can hit a
    process that never served that page and 404. Calling this at startup in each
    worker (done by ``create_app(dev=False)``) makes any project worker-safe.
    Rendering doesn't execute queries (the SQL is only collected), so this is a
    parse pass, not a data load. Per-page failures are logged, not fatal.
    """
    for md_path in sorted(project.pages_dir.rglob("*.md")):
        try:
            source = md_path.read_text(encoding="utf-8")
            render_page(
                source,
                project.connectors,
                include_base=project.root,
                library=project.queries,
                python_library=project.python_queries,
                semantic_models=project.semantic_models,
                global_date=project.config.global_date,
            )
        except Exception as e:  # noqa: BLE001
            log.warning("Pre-registering queries for %s failed: %s", md_path, e)


def _render_nav_html(nodes: list[dict], current: str, depth: int = 0) -> str:
    """Recursively render navigation nodes into sidebar HTML."""
    from markupsafe import Markup, escape

    from .render.icons import nav_icon_svg

    if not nodes:
        return Markup("")

    ul_class = "dashdown-sidenav-list"
    if depth == 0:
        ul_class += " p-4"
    else:
        ul_class += " pl-4"

    parts: list[str] = [f'<ul class="{ul_class}">']
    for node in nodes:
        # `url` is the canonical app path (used for active-state matching);
        # `href` is the actual link target. They differ in the static build,
        # where href is a relative path but matching still uses canonical urls.
        # The dev server sets only `url`, so href falls back to it.
        url = escape(node.get("href", node.get("url", "#")))
        label = escape(node.get("label", ""))
        raw_icon = node.get("icon", "")
        # A named icon (e.g. `icon: home`) renders a bundled `currentColor` SVG;
        # any other value (emoji / arbitrary text) is escaped and shown verbatim.
        icon_svg = nav_icon_svg(raw_icon)
        icon = icon_svg if icon_svg is not None else escape(raw_icon)
        children = node.get("children", [])
        is_group = node.get("group", False)

        # Active if current matches this URL or any descendant.
        raw_url = node.get("url", "#")
        is_active = current == raw_url or (raw_url != "/" and current.startswith(raw_url + "/"))
        is_exact = current == raw_url

        parts.append('<li class="dashdown-sidenav-item">')
        icon_span = Markup(f'<span class="dashdown-sidenav-icon">{icon}</span> ') if icon else Markup("")
        if is_group and not is_active:
            # Group without its own page (no index.md) — just a label.
            label_html = Markup(
                f'<span class="dashdown-sidenav-link dashdown-sidenav-group">{icon_span}{label}</span>'
            )
        else:
            active_cls = 'active' if is_exact else ''
            label_html = Markup(
                f'<a href="{url}" class="dashdown-sidenav-link {active_cls}">{icon_span}{label}</a>'
            )

        if children:
            # Collapsible group via native <details> — collapsed by default, with
            # the active branch (an ancestor of the current page) opened so the
            # reader's location stays visible. No JS, so it works the same in
            # static builds and embeds, with no flash-of-expanded on first paint.
            open_attr = " open" if is_active else ""
            caret = Markup(
                '<span class="dashdown-sidenav-caret" aria-hidden="true">'
                '<svg viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="2">'
                '<path stroke-linecap="round" stroke-linejoin="round" d="M7 5l6 5-6 5"/></svg>'
                "</span>"
            )
            parts.append(Markup(f'<details class="dashdown-sidenav-details"{open_attr}>'))
            parts.append(
                Markup(f'<summary class="dashdown-sidenav-summary">{label_html}{caret}</summary>')
            )
            parts.append(_render_nav_html(children, current, depth + 1))
            parts.append(Markup("</details>"))
        else:
            parts.append(label_html)
        parts.append("</li>")
    parts.append("</ul>")
    return Markup("\n".join(parts))
