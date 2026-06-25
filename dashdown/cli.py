"""Dashdown CLI."""
from __future__ import annotations

import asyncio
import logging
import re
import shutil
from importlib.resources import files
from pathlib import Path

import typer
import uvicorn

app = typer.Typer(help="Dashdown: markdown-driven analytics pages.")


@app.callback()
def _main() -> None:
    """Dashdown: markdown-driven analytics pages."""
    # Print the one-time anonymous-telemetry notice before any command runs.
    # Best-effort and to stderr, so it never affects machine-readable stdout.
    from dashdown import telemetry

    telemetry.maybe_print_first_run_notice()


telemetry_app = typer.Typer(help="Inspect or opt out of anonymous usage telemetry.")
app.add_typer(telemetry_app, name="telemetry")


@telemetry_app.command("status")
def telemetry_status() -> None:
    """Show whether anonymous usage telemetry is on, and exactly what would be sent.

    No personal data, project contents, queries, paths, or connection details are
    ever sent — only the Dashdown version + OS, keyed to a random install id.
    """
    import json

    from dashdown import telemetry

    enabled = telemetry.is_enabled()
    typer.echo(f"Telemetry: {'enabled' if enabled else 'disabled'}")
    if not enabled:
        typer.echo(f"  reason:  {telemetry.disabled_reason()}")
    typer.echo(f"Install ID:  {telemetry._install_id()}")
    typer.echo(f"Endpoint:    {telemetry._endpoint()}")
    if not telemetry._key_is_real():
        typer.echo("Project key: (not configured — nothing is sent)")
    typer.echo("\nSample payload (sent at the start of `dashdown serve`):")
    typer.echo(json.dumps(telemetry.dry_run_payload("cli_serve"), indent=2))
    typer.echo(
        "\nOpt out: dashdown telemetry off  (or DASHDOWN_TELEMETRY=0 / DO_NOT_TRACK=1,\n"
        "         or telemetry.enabled: false in dashdown.yaml)",
        err=True,
    )


@telemetry_app.command("off")
def telemetry_off() -> None:
    """Disable anonymous usage telemetry on this machine."""
    from dashdown import telemetry

    telemetry.set_enabled(False)
    typer.echo("Telemetry disabled. Re-enable with: dashdown telemetry on")


@telemetry_app.command("on")
def telemetry_on() -> None:
    """Re-enable anonymous usage telemetry on this machine."""
    from dashdown import telemetry

    telemetry.set_enabled(True)
    typer.echo("Telemetry enabled. Disable with: dashdown telemetry off")


@app.command()
def serve(
    project: Path = typer.Argument(Path("."), help="Project directory"),
    host: str = typer.Option("127.0.0.1", help="Bind host"),
    port: int = typer.Option(8000, help="Bind port"),
    no_watch: bool = typer.Option(False, "--no-watch", help="Disable file watcher"),
) -> None:
    """Serve the project locally (live-reload by default)."""
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    from dashdown.server import create_app, reload_project, trigger_reload

    project = project.resolve()
    typer.echo(f"Dashdown server: {project}")
    typer.echo(f"Open http://{host}:{port}")

    fastapi_app = create_app(project)

    from dashdown import telemetry

    telemetry.capture("cli_serve", project_path=project)

    if not no_watch:
        @fastapi_app.on_event("startup")
        async def _start_watcher() -> None:
            asyncio.create_task(
                _watch_loop(project, fastapi_app, reload_project, trigger_reload)
            )

    uvicorn.run(fastapi_app, host=host, port=port, log_level="info")


async def _watch_loop(project: Path, app, reload_project, trigger_reload) -> None:
    from watchfiles import awatch

    watch_paths: list[str] = []
    for sub in ("pages", "components", "data", "assets", "queries", "semantic"):
        p = project / sub
        if p.exists():
            watch_paths.append(str(p))
    for f in ("dashdown.yaml", "sources.yaml"):
        fp = project / f
        if fp.exists():
            watch_paths.append(str(fp))
    if not watch_paths:
        return

    async for changes in awatch(*watch_paths):
        # If config / sources / components / shared queries / data changed,
        # reload the project (a config-tier reload — it re-parses the queries/
        # library and rebuilds its cache keys so a renamed/deleted query leaves
        # no ghost, and re-creates the connectors). data/ is in here because CSV
        # sources are now materialized into tables at connect time, so an edited
        # data file is only picked up when the connector is rebuilt — a bare
        # page reload would keep serving the snapshot from the previous connect.
        if any(
            ("sources.yaml" in c[1])
            or ("dashdown.yaml" in c[1])
            or ((project / "components").as_posix() in Path(c[1]).as_posix())
            or ((project / "queries").as_posix() in Path(c[1]).as_posix())
            or ((project / "semantic").as_posix() in Path(c[1]).as_posix())
            or ((project / "data").as_posix() in Path(c[1]).as_posix())
            for c in changes
        ):
            reload_project(app)
        trigger_reload(app)


@app.command()
def query(
    sql: str = typer.Argument(
        None, help="SQL (or DAX, for a dax connector) to run. Omit with --tables/--schema."
    ),
    project: Path = typer.Option(
        Path("."), "--project", "-p", help="Project directory"
    ),
    connector: str = typer.Option(
        "main", "--connector", "-c", help="Connector name from sources.yaml"
    ),
    fmt: str = typer.Option(
        "table", "--format", "-f", help="Output format: table, json, or csv"
    ),
    max_rows: int = typer.Option(
        50, "--max-rows", help="Max rows to print (0 = all)"
    ),
    tables: bool = typer.Option(
        False, "--tables", help="List the connector's tables/views and exit"
    ),
    schema: str = typer.Option(
        None, "--schema", help="Describe one table's columns and exit"
    ),
) -> None:
    """Run a query against a connector and print the result.

    Test connectivity and inspect data without opening the app — handy for an
    AI agent verifying a connector or exploring the schema:

        dashdown query "SELECT * FROM sales LIMIT 5" -c main
        dashdown query "SELECT count(*) FROM orders" -p . -c warehouse -f json

    Schema introspection answers "what tables exist?" / "what columns are in T?"
    in one call — no hand-written `SELECT * LIMIT 0`, dialect handled per connector:

        dashdown query --tables -c main
        dashdown query --schema sales -c main -f json

    (For a semantic model's metrics/dimensions, use `dashdown metric --list`.)
    """
    if fmt not in ("table", "json", "csv"):
        raise typer.BadParameter("--format must be one of: table, json, csv")

    # Exactly one mode: a SQL argument, --tables, or --schema <table>.
    modes = [sql is not None, tables, schema is not None]
    if sum(modes) != 1:
        raise typer.BadParameter(
            "Provide exactly one of: a SQL/DAX argument, --tables, or --schema <table>."
        )

    from .data.base import IntrospectionUnsupported
    from .project import load_project

    proj = load_project(project.resolve())
    try:
        conn = proj.connectors.get(connector)
        if conn is None:
            avail = ", ".join(sorted(proj.connectors)) or "(none configured)"
            raise typer.BadParameter(
                f"Connector '{connector}' not found in sources.yaml. Available: {avail}"
            )
        try:
            if tables:
                result = conn.list_tables()
            elif schema is not None:
                result = conn.describe_table(schema)
            else:
                result = conn.query(sql)
        except IntrospectionUnsupported as exc:
            typer.echo(
                f"Schema introspection unavailable for connector '{connector}': {exc}",
                err=True,
            )
            raise typer.Exit(1)
        except Exception as exc:  # surface the connector error, don't traceback
            what = "Schema lookup" if (tables or schema is not None) else "Query"
            typer.echo(f"{what} failed on connector '{connector}': {exc}", err=True)
            raise typer.Exit(1)
        _print_query_result(result, fmt, max_rows)
    finally:
        proj.close()


def _print_query_result(result, fmt: str, max_rows: int) -> None:
    from .render.pipeline import serialize_result

    total = len(result.rows)
    rows = result.rows if max_rows <= 0 else result.rows[:max_rows]

    if fmt == "json":
        import json

        payload = serialize_result(result)
        if max_rows > 0:
            payload["rows"] = payload["rows"][:max_rows]
        typer.echo(json.dumps(payload, indent=2, default=str))
    elif fmt == "csv":
        import csv
        import io

        from .render.pipeline import serialize_value

        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow(result.columns)
        for row in rows:
            writer.writerow([serialize_value(c) for c in row])
        typer.echo(buf.getvalue().rstrip("\r\n"))
    else:  # table
        cols = result.columns
        cells = [[_fmt_cell(c) for c in row] for row in rows]
        widths = [len(c) for c in cols]
        for row in cells:
            for i, val in enumerate(row):
                widths[i] = max(widths[i], len(val))
        sep = "  "
        typer.echo(sep.join(c.ljust(widths[i]) for i, c in enumerate(cols)))
        typer.echo(sep.join("-" * widths[i] for i in range(len(cols))))
        for row in cells:
            typer.echo(sep.join(val.ljust(widths[i]) for i, val in enumerate(row)))

    shown = len(rows)
    note = f"{total} row{'s' if total != 1 else ''}"
    if shown < total:
        note += f" ({shown} shown — raise --max-rows or add LIMIT)"
    typer.echo(note, err=True)


def _fmt_cell(value) -> str:
    return "" if value is None else str(value)


@app.command()
def metric(
    ref: str = typer.Argument(
        None,
        help="Metric reference: `model.metric` (or a comma list on one model, "
        "e.g. `sales.revenue,sales.profit`). Omit with --list.",
    ),
    by: str = typer.Option(
        None, "--by", "-b", help="Group by a dimension: `model.dim` or bare `dim`"
    ),
    series: str = typer.Option(
        None, "--series", "-s", help="Split into a series by a second dimension"
    ),
    grain: str = typer.Option(
        None,
        "--grain",
        "-g",
        help="Time grain for a time `--by`/--series: second…year",
    ),
    param: list[str] = typer.Option(
        [],
        "--param",
        help="Filter param as key=value (repeatable); a dimension name, or "
        "date_start/date_end for the model's time dimension",
    ),
    project: Path = typer.Option(
        Path("."), "--project", "-p", help="Project directory"
    ),
    fmt: str = typer.Option(
        "table", "--format", "-f", help="Output format: table, json, or csv"
    ),
    max_rows: int = typer.Option(
        50, "--max-rows", help="Max rows to print (0 = all)"
    ),
    list_models: bool = typer.Option(
        False, "--list", "-l", help="List semantic models + their measures/dimensions and exit"
    ),
) -> None:
    """Query the **semantic layer** (metrics/groupings) instead of raw SQL.

    The metric/by/grain grammar a component uses (`<BarChart metric={sales.revenue}
    by={sales.region} />`) compiled and pushed down to the warehouse — handy for an
    AI agent or skill to probe a model without opening the app:

        dashdown metric --list
        dashdown metric "sales.revenue" --by sales.region
        dashdown metric "sales.revenue,sales.profit" -b sales.order_date -g month -f json
        dashdown metric "sales.revenue" -b sales.region --param region=East
    """
    if fmt not in ("table", "json", "csv"):
        raise typer.BadParameter("--format must be one of: table, json, csv")

    from .project import load_project

    proj = load_project(project.resolve())
    try:
        models = proj.semantic_models
        if not models:
            typer.echo(
                "No semantic models found. Define semantic/**/*.yml (and ensure "
                "python_queries.enabled is not false).",
                err=True,
            )
            raise typer.Exit(1)

        if list_models:
            _print_semantic_models(models)
            return

        if not ref:
            raise typer.BadParameter(
                "Provide a metric reference (e.g. `sales.revenue`) or use --list."
            )

        params: dict[str, str] = {}
        for item in param:
            if "=" not in item:
                raise typer.BadParameter(f"--param must be key=value, got: {item!r}")
            key, value = item.split("=", 1)
            params[key.strip()] = value

        from .python_query import run_python_query
        from .semantic import build_semantic_spec, resolve_ref

        try:
            sem_ref = resolve_ref(models, ref, by, series, grain)
            spec = build_semantic_spec(models, sem_ref, proj.connectors)
        except Exception as exc:  # unknown model/metric/dim, bad grain, etc.
            typer.echo(f"Semantic resolution failed: {exc}", err=True)
            raise typer.Exit(1)

        try:
            result = run_python_query(spec, params, proj.connectors)
        except Exception as exc:
            typer.echo(f"Metric query failed on model '{sem_ref.model}': {exc}", err=True)
            raise typer.Exit(1)
        _print_query_result(result, fmt, max_rows)
    finally:
        proj.close()


def _print_semantic_models(models) -> None:
    """Print each loaded semantic model with its measures + dimensions."""
    for name in sorted(models):
        handle = models[name]
        time_dim = handle.time_dimension
        typer.echo(f"{name}  (connector: {handle.connector}, backend: {handle.backend})")
        measures = sorted(handle.measure_lookup) or ["(none)"]
        typer.echo(f"  metrics:    {', '.join(measures)}")
        dims = []
        for d in sorted(handle.dim_lookup):
            dims.append(f"{d} [time]" if d == time_dim else d)
        typer.echo(f"  dimensions: {', '.join(dims) or '(none)'}")
        typer.echo("")
    typer.echo(f"{len(models)} model(s)", err=True)


_ERROR_TITLE_RE = re.compile(
    r'dashdown-error-title[^>]*>(.*?)</div>\s*<pre[^>]*>(.*?)</pre>', re.DOTALL
)


@app.command()
def check(
    project: Path = typer.Option(
        Path("."), "--project", "-p", help="Project directory"
    ),
) -> None:
    """Validate the project without serving it or running any queries.

    Loads `dashdown.yaml`/`sources.yaml`, the query library, and the semantic
    models (surfacing any config/parse error), then renders every page —
    queries are **never** executed during render — and reports component/render
    errors (unknown tags, bad attrs). Exits non-zero if anything is wrong: a
    fast edit→validate loop for authors and coding agents.

        dashdown check
        dashdown check -p docs
    """
    from .project import load_project
    from .render.pipeline import render_page

    try:
        proj = load_project(project.resolve())
    except Exception as exc:  # config/sources/query-lib/semantic load error
        typer.echo(f"✗ project failed to load: {exc}", err=True)
        raise typer.Exit(1)

    try:
        pages_dir = proj.pages_dir
        md_paths = sorted(pages_dir.rglob("*.md")) if pages_dir.is_dir() else []
        problems: list[tuple[str, str]] = []  # (page url, message)
        ok = 0
        for md_path in md_paths:
            rel = md_path.relative_to(pages_dir).with_suffix("")
            url = "/" + str(rel).replace("\\", "/")
            if url.endswith("/index"):
                url = url[: -len("index")] or "/"
            try:
                source = md_path.read_text(encoding="utf-8")
                rendered = render_page(
                    source,
                    proj.connectors,
                    params={},
                    current_path=url,
                    include_base=proj.root,
                    library=proj.queries,
                    python_library=proj.python_queries,
                    semantic_models=proj.semantic_models,
                )
            except Exception as exc:  # parse / include / render failure
                problems.append((url, str(exc)))
                continue
            # Component failures (unknown tag, bad attr) render as inline error
            # cards rather than raising — scan for them.
            cards = _ERROR_TITLE_RE.findall(rendered.body_html)
            if cards or rendered.errors:
                for title, detail in cards:
                    problems.append((url, f"{title.strip()}: {detail.strip()}"))
                for e in rendered.errors:
                    problems.append((url, e))
            else:
                ok += 1
        for url, msg in problems:
            typer.echo(f"  ✗ {url}: {msg}", err=True)
        typer.echo(f"{ok}/{len(md_paths)} page(s) OK", err=True)
        if problems:
            raise typer.Exit(1)
        typer.echo("✓ project is valid")
    finally:
        proj.close()


@app.command()
def connectors(
    project: Path = typer.Option(
        Path("."), "--project", "-p", help="Project directory"
    ),
    test: bool = typer.Option(
        False, "--test", help="Probe each connector with a trivial `SELECT 1`"
    ),
) -> None:
    """List the connectors configured in `sources.yaml`.

    With --test, probe each by running `SELECT 1` to confirm it connects before
    you author queries against it. (A `dax` connector takes DAX, not SQL, so its
    probe is skipped.)

        dashdown connectors
        dashdown connectors --test -p docs
    """
    import yaml

    from .project import load_project

    proj = load_project(project.resolve())
    try:
        sources_path = proj.root / "sources.yaml"
        types: dict[str, str] = {}
        if sources_path.is_file():
            raw = yaml.safe_load(sources_path.read_text(encoding="utf-8")) or {}
            for name, cfg in raw.items():
                if isinstance(cfg, dict):
                    types[name] = str(cfg.get("type", "?"))

        names = sorted(proj.connectors)
        if not names:
            typer.echo("No connectors configured in sources.yaml.", err=True)
            raise typer.Exit(1)

        for name in names:
            line = f"{name}  ({types.get(name, '?')})"
            if test:
                if types.get(name) == "dax":
                    line += "  — probe skipped (DAX, not SQL)"
                else:
                    try:
                        proj.connectors[name].query("SELECT 1")
                        line += "  ✓ reachable"
                    except Exception as exc:  # surface, don't traceback
                        line += f"  ✗ {exc}"
            typer.echo(line)
        typer.echo(f"{len(names)} connector(s)", err=True)
    finally:
        proj.close()


@app.command()
def components(
    fmt: str = typer.Option(
        "table", "--format", "-f", help="Output format: table or json"
    ),
    connectors_only: bool = typer.Option(
        False,
        "--connectors",
        help="Show only the connector catalog (types, config keys, install extra)",
    ),
) -> None:
    """Print a dense, introspected catalog of components and connectors.

    Generated from the registries themselves (not hand-written docs), so it can't
    drift: one row per component with its attributes, and per connector its config
    keys + install extra. The fastest way for an author or coding agent to answer
    "what attrs does <BarChart> take" / "what keys does postgres need" without
    re-reading prose. Needs no project.

        dashdown components
        dashdown components --connectors
        dashdown components -f json
    """
    if fmt not in ("table", "json"):
        raise typer.BadParameter("--format must be one of: table, json")

    from .catalog import build_catalog

    catalog = build_catalog()
    if connectors_only:
        catalog = {"connectors": catalog["connectors"]}

    if fmt == "json":
        import json

        typer.echo(json.dumps(catalog, indent=2))
        return

    comps = catalog.get("components")
    if comps is not None:
        _print_component_catalog(comps)
    conns = catalog["connectors"]
    _print_connector_catalog(conns)

    note = f"{len(conns)} connector(s)"
    if comps is not None:
        note = f"{len(comps)} component(s), " + note
    typer.echo(note, err=True)


def _print_component_catalog(rows: list[dict]) -> None:
    typer.echo(f"COMPONENTS ({len(rows)})")
    for r in rows:
        tag = "  [filter]" if r["is_filter"] else ""
        typer.echo(f"  {r['name']}{tag}")
        if r["summary"]:
            typer.echo(f"    {r['summary']}")
        if r["attrs"]:
            typer.echo(f"    attrs: {', '.join(r['attrs'])}")
    typer.echo("")


def _print_connector_catalog(rows: list[dict]) -> None:
    typer.echo(f"CONNECTORS ({len(rows)})")
    for r in rows:
        extra = (
            f"  install: pip install 'dashdown-md[{r['extra']}]'"
            if r["extra"]
            else "  (core — no extra)"
        )
        typer.echo(f"  {r['type']}{extra}")
        if r["summary"]:
            typer.echo(f"    {r['summary']}")
        if r["config_keys"]:
            typer.echo(f"    config: {', '.join(r['config_keys'])}")
    typer.echo("")


@app.command()
def build(
    project: Path = typer.Argument(Path("."), help="Project directory"),
    out: Path = typer.Option(
        None, "--out", "-o", help="Output directory (default: <project>/.dist)"
    ),
) -> None:
    """Export the project to a static site (HTML + pre-rendered query data)."""
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    from dashdown.build import build_site

    project = project.resolve()
    out_dir = out.resolve() if out is not None else project / ".dist"

    from dashdown import telemetry

    telemetry.capture("cli_build", project_path=project)

    try:
        result = build_site(project, out_dir)
    except Exception as e:  # noqa: BLE001
        raise typer.BadParameter(str(e))

    typer.echo(f"Built {len(result.pages)} page(s) → {result.out_dir}")
    typer.echo(f"Exported {len(result.queries)} query snapshot(s)")
    if result.asks:
        typer.echo(f"Baked {len(result.asks)} <Ask /> commentary snapshot(s)")
    for url, err in result.failed_pages:
        typer.echo(f"  ⚠ page {url}: {err}")
    for connector, name, err in result.failed_queries:
        typer.echo(f"  ⚠ query {name} ({connector}): {err}")
    for ask_id, name, err in result.failed_asks:
        typer.echo(f"  ⚠ ask {ask_id} ({name}): {err}")
    typer.echo(f"Serve it: python -m http.server -d {result.out_dir}")


@app.command()
def pdf(
    project: Path = typer.Argument(Path("."), help="Project directory"),
    out: Path = typer.Option(
        None, "--out", "-o", help="Output directory (default: <project>/.pdf)"
    ),
    page: list[str] = typer.Option(
        None, "--page", help="Limit to one page URL (repeatable), e.g. --page /sales"
    ),
    dist: Path = typer.Option(
        None,
        "--dist",
        help="Reuse an existing static build instead of rebuilding (e.g. ./.dist)",
    ),
    separate: bool = typer.Option(
        False, "--separate", help="Write one PDF per page instead of a combined deck"
    ),
    orientation: str = typer.Option(
        "portrait", "--orientation", help="Page orientation: portrait | landscape"
    ),
    page_format: str = typer.Option(
        "A4", "--format", help="Page size: A4 | Letter | Legal | A3 | …"
    ),
    scale: float = typer.Option(
        1.0, "--scale", help="Render scale passed to Chromium (0.1–2.0)"
    ),
) -> None:
    """Export the project to a presentation PDF.

    By default the whole project is merged into a single deck (`<title>.pdf`);
    `--page` narrows to specific pages and `--separate` writes one file per page.
    Page geometry is set per-export with --orientation / --format / --scale.

    Renders the static export with headless Chromium, so charts draw exactly as
    in the live app. Requires the `pdf` extra and a one-time browser download:

        pip install 'dashdown-md[pdf]'
        playwright install chromium
    """
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    from dashdown.pdf import export_pdf

    project = project.resolve()
    out_dir = out.resolve() if out is not None else project / ".pdf"

    try:
        result = export_pdf(
            project,
            out_dir,
            pages=page or None,
            dist_dir=dist.resolve() if dist is not None else None,
            combine=not separate,
            orientation=orientation,
            fmt=page_format,
            scale=scale,
        )
    except RuntimeError as e:  # missing-dep hint from pdf.py
        typer.echo(str(e), err=True)
        raise typer.Exit(code=1)
    except Exception as e:  # noqa: BLE001
        raise typer.BadParameter(str(e))

    if result.combined is not None:
        typer.echo(
            f"Exported {len(result.pdfs)} page(s) → {result.combined}"
        )
    else:
        typer.echo(f"Exported {len(result.pdfs)} PDF(s) → {result.out_dir}")
        for url, path in result.pdfs:
            typer.echo(f"  {url} → {path.name}")
    for url, err in result.failed:
        typer.echo(f"  ⚠ {url}: {err}")
    if result.failed and not result.pdfs:
        raise typer.Exit(code=1)


@app.command()
def screenshot(
    page: str = typer.Argument("/", help="Page URL to capture, e.g. /sales (default: /)"),
    out: Path = typer.Option(
        None, "--out", "-o", help="Output PNG (default: <project>/.shots/<page>.png)"
    ),
    project: Path = typer.Option(
        Path("."), "--project", "-p", help="Project directory"
    ),
    dist: Path = typer.Option(
        None, "--dist", help="Reuse an existing static build instead of rebuilding"
    ),
    server: str = typer.Option(
        None,
        "--server",
        help="Capture from a running server (e.g. http://127.0.0.1:8000) instead of building",
    ),
    full_page: bool = typer.Option(
        False, "--full-page", help="Capture the full scroll height, not just the viewport"
    ),
    width: int = typer.Option(1280, "--width", help="Viewport width in px"),
    height: int = typer.Option(800, "--height", help="Viewport height in px"),
) -> None:
    """Capture a page to a PNG and report whether its charts actually drew.

    Charts paint **client-side**, so `dashdown check` confirms a page renders but
    not that a chart *painted*. This drives headless Chromium (same engine as
    `dashdown pdf`) over the interactive page, waits for the chart-render
    handshake, saves a PNG, and prints a verdict — how many chart canvases drew
    vs stayed blank, plus browser console errors. Exits non-zero if a chart
    failed to draw, so it works as a verification gate for an agent or CI.

    Builds + serves a static export by default; `--dist` reuses a build and
    `--server` captures a running `dashdown serve`. Needs the `pdf` extra:

        dashdown screenshot /sales
        dashdown screenshot / --full-page -o home.png
        dashdown screenshot /sales --server http://127.0.0.1:8000
    """
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    from dashdown.screenshot import _shot_output, screenshot_page

    project = project.resolve()
    out_file = out.resolve() if out is not None else project / ".shots" / _shot_output(page)

    try:
        result = screenshot_page(
            project,
            page,
            out_file,
            dist_dir=dist.resolve() if dist is not None else None,
            server_url=server,
            full_page=full_page,
            width=width,
            height=height,
        )
    except RuntimeError as e:  # missing-dep hint from screenshot.py
        typer.echo(str(e), err=True)
        raise typer.Exit(code=1)
    except Exception as e:  # noqa: BLE001 — page not found, bad dist, etc.
        raise typer.BadParameter(str(e))

    typer.echo(f"Saved {result.out_file}")

    if result.charts_total:
        parts = [f"{result.charts_drawn}/{result.charts_total} chart(s) drew"]
        if result.charts_blank:
            parts.append(f"{result.charts_blank} blank")
        if result.charts_errored:
            parts.append(f"{result.charts_errored} errored")
        typer.echo("  " + ", ".join(parts), err=True)
    else:
        typer.echo("  (no charts on this page)", err=True)
    if result.error_cards:
        typer.echo(f"  {result.error_cards} render error card(s)", err=True)
    for msg in result.console_errors:
        typer.echo(f"  console error: {msg}", err=True)

    typer.echo("✓ charts drew" if result.ok else "✗ something didn't draw", err=True)
    if not result.ok:
        raise typer.Exit(code=1)


@app.command(name="embed-token")
def embed_token(
    project: Path = typer.Argument(Path("."), help="Project directory"),
    page: str = typer.Argument(..., help="Page path to embed, e.g. /sales"),
    ttl: int = typer.Option(
        None, "--ttl", help="Token lifetime in seconds (default: embed.token_ttl)"
    ),
    host: str = typer.Option(
        None, "--host", help="Public dashboard origin for the snippet, e.g. https://dash.example"
    ),
) -> None:
    """Mint a signed embed token + <script> snippet for one page.

    Use this for authenticated dashboards (the cross-origin iframe can't send
    credentials). Requires an `embed:` block with a `secret` in dashdown.yaml.
    """
    import time

    from dashdown.embed import query_key, sign_embed_token
    from dashdown.project import load_project
    from dashdown.render.pipeline import render_page

    proj = load_project(project.resolve())
    embed_cfg = proj.config.embed
    if not embed_cfg.enabled:
        raise typer.BadParameter("embedding is disabled — set embed.enabled in dashdown.yaml")
    if not embed_cfg.has_secret:
        raise typer.BadParameter("no embed.secret configured — required to sign tokens")

    full = page.lstrip("/")
    md_path, params = proj.page_path(full)
    if md_path is None:
        raise typer.BadParameter(f"no page for {page!r}")
    canonical = ("/" + full).rstrip("/") or "/"

    source = md_path.read_text(encoding="utf-8")
    rendered = render_page(
        source,
        proj.connectors,
        params=params,
        current_path=canonical,
        include_base=proj.root,
        library=proj.queries,
        python_library=proj.python_queries,
    )
    queries = [
        query_key(str(d.get("connector", "main")), name)
        for name, d in rendered.query_defs.items()
    ]
    for ask in rendered.ask_defs:
        queries.append(query_key(ask.connector, ask.query_name))

    lifetime = ttl if ttl is not None else embed_cfg.token_ttl
    exp = int(time.time()) + lifetime if lifetime and lifetime > 0 else None
    token = sign_embed_token(embed_cfg.secret, canonical, queries, exp)

    typer.echo(f"Token: {token}")
    if exp:
        typer.echo(f"Expires: {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(exp))}")
    origin = (host or "https://your-dashboard.example").rstrip("/")
    typer.echo("\nSnippet:")
    typer.echo(
        f'<script\n'
        f'  src="{origin}/_dashdown/static/embed.js"\n'
        f'  data-dashdown-page="{canonical}"\n'
        f'  data-dashdown-token="{token}"></script>'
    )


@app.command()
def new(
    name: str = typer.Argument(..., help="Project directory to create"),
) -> None:
    """Scaffold a new Dashdown project."""
    target = Path(name).resolve()
    if target.exists() and any(target.iterdir()):
        raise typer.BadParameter(f"{target} already exists and is not empty")
    target.mkdir(parents=True, exist_ok=True)
    _scaffold(target)
    typer.echo(f"Created {target}")
    typer.echo(f"Run: dashdown serve {target.name}")


def _scaffold(root: Path) -> None:
    (root / "pages").mkdir(exist_ok=True)
    (root / "data").mkdir(exist_ok=True)

    # Exclude build artifacts: `dashdown build` → .dist/, `dashdown pdf` → .pdf/,
    # `dashdown screenshot` → .shots/.
    (root / ".gitignore").write_text(
        "# Dashdown build artifacts\n"
        ".dist/\n"
        ".pdf/\n"
        ".shots/\n",
        encoding="utf-8",
    )

    (root / "dashdown.yaml").write_text(
        "title: My Analytics\n"
        "# branding:\n"
        "#   logo: assets/logo.svg   # shown in the header; path or https URL\n"
        "#   palette: [\"#6366f1\", \"#22c55e\", \"#f59e0b\"]   # chart series colors\n"
        "#\n"
        "# Anonymous usage telemetry (Dashdown version + OS only — no project data) is\n"
        "# on by default. Opt out here, or with `dashdown telemetry off` /\n"
        "# DASHDOWN_TELEMETRY=0 / DO_NOT_TRACK=1.\n"
        "# telemetry:\n"
        "#   enabled: false\n",
        encoding="utf-8",
    )
    (root / "sources.yaml").write_text(
        "main:\n  type: csv\n  directory: data\n",
        encoding="utf-8",
    )
    (root / "data" / "sales.csv").write_text(
        "month,region,amount\n"
        "2024-01,North,1200\n"
        "2024-01,South,900\n"
        "2024-02,North,1500\n"
        "2024-02,South,1100\n"
        "2024-03,North,1700\n"
        "2024-03,South,1300\n",
        encoding="utf-8",
    )
    (root / "pages" / "index.md").write_text(
        "# Welcome\n\n"
        "This is your first Dashdown page.\n\n"
        ":::query name=monthly_sales connector=main\n"
        "SELECT month, region, SUM(amount) AS sales\n"
        "FROM sales\n"
        "GROUP BY month, region\n"
        "ORDER BY month\n"
        ":::\n\n"
        '<Dropdown name="region" data={monthly_sales} column="region" label="Region" />\n\n'
        '<LineChart data={monthly_sales} x="month" y="sales" series="region" title="Monthly Sales" />\n\n'
        '<Table data={monthly_sales} title="Detail" />\n',
        encoding="utf-8",
    )

    _scaffold_agent_docs(root)


@app.command()
def skill(
    project: Path = typer.Option(
        Path("."), "--project", "-p", help="Project directory"
    ),
    refresh: bool = typer.Option(
        False,
        "--refresh",
        help="Overwrite existing guide files with this version's (and prune stale shards)",
    ),
) -> None:
    """Install or update the bundled coding-agent guide in an existing project.

    Adds (or, with `--refresh`, updates) `AGENTS.md`, the `references/` shards, and the
    Claude Code authoring skill — the same progressive-disclosure guide `dashdown new`
    scaffolds. The guide is versioned with the framework, so a project scaffolded on an
    older release can pull the current one without re-scaffolding:

        dashdown skill                 # fill in anything missing (keeps your edits)
        dashdown skill --refresh       # overwrite to this version's guide
    """
    root = project.resolve()
    if not root.is_dir():
        raise typer.BadParameter(f"{root} is not a directory")
    if not (root / "dashdown.yaml").is_file():
        typer.echo(f"warning: {root} has no dashdown.yaml (not a Dashdown project?)", err=True)

    written, skipped = _install_agent_docs(root, refresh=refresh)

    verb = "Updated" if refresh else "Installed"
    if written:
        typer.echo(f"{verb} {len(written)} file(s) in {root}:")
        for rel in written:
            typer.echo(f"  + {rel}")
    if skipped:
        typer.echo(f"Kept {len(skipped)} existing file(s) — pass --refresh to update them.")
    if not written and not skipped:
        typer.echo("Nothing to install (no bundled guide found).")
    elif not written:
        typer.echo("Already up to date.")


def _agent_doc_files() -> list[tuple[Path, Path]]:
    """The bundled coding-agent guide as ``(source, dest_relative)`` pairs.

    Ships the progressive-disclosure guide generated from `docs/` by
    `tooling/gen-agent-docs.py`: `AGENTS.md` (the tool-agnostic *map* — cheat-sheet +
    a table of contents) alongside `references/<topic>.md` (the per-topic detail shards
    the map links to), plus a thin Claude Code skill that routes into them. So any agent
    opening the project reads the small map first and loads only the shard a task needs.
    Stored in the package under `scaffold/claude/` (no leading dot) because setuptools'
    `**` glob skips hidden paths; the `.claude` rename happens on copy.
    """
    src = Path(str(files("dashdown") / "scaffold"))
    items: list[tuple[Path, Path]] = []
    agents = src / "AGENTS.md"
    if agents.is_file():
        items.append((agents, Path("AGENTS.md")))
    references = src / "references"
    if references.is_dir():
        for ref in sorted(references.glob("*.md")):
            items.append((ref, Path("references") / ref.name))
    claude = src / "claude"
    if claude.is_dir():
        for f in sorted(claude.rglob("*")):
            if f.is_file():
                items.append((f, Path(".claude") / f.relative_to(claude)))
    return items


def _install_agent_docs(root: Path, *, refresh: bool) -> tuple[list[str], list[str]]:
    """Copy the bundled guide into ``root``; return ``(written, skipped)`` rel paths.

    Without ``refresh`` an existing file is left untouched (so a project's local edits
    survive an install that just fills in missing pieces); with ``refresh`` every file is
    overwritten to the wheel's current version and any ghost `references/*.md` a renamed
    docs topic left behind is pruned (mirroring `gen-agent-docs.py`'s stale-shard evict).
    """
    items = _agent_doc_files()
    if refresh:
        shipped_refs = {
            dest.name for _, dest in items if dest.parent.as_posix() == "references"
        }
        refs_dir = root / "references"
        if refs_dir.is_dir():
            for existing in refs_dir.glob("*.md"):
                if existing.name not in shipped_refs:
                    existing.unlink()

    written: list[str] = []
    skipped: list[str] = []
    for source, dest_rel in items:
        dest = root / dest_rel
        if dest.exists() and not refresh:
            skipped.append(dest_rel.as_posix())
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, dest)
        written.append(dest_rel.as_posix())
    return written, skipped


def _scaffold_agent_docs(root: Path) -> None:
    """Drop the bundled coding-agent guide into a freshly scaffolded project."""
    _install_agent_docs(root, refresh=True)


def main() -> None:  # pragma: no cover
    app()


if __name__ == "__main__":  # pragma: no cover
    main()
