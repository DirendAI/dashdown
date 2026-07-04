<!-- AUTO-GENERATED from docs/pages/ by tooling/gen-agent-docs.py — do not edit. -->
<!-- Topic: getting-started. Regenerate with: python tooling/gen-agent-docs.py -->

<!-- source: docs/pages/getting-started.md -->

# Getting started

## Install

Dashdown is published to PyPI as the `dashdown-md` package, with a CLI entry point
named `dashdown`. The fastest way to get going is to install the CLI globally with
[uv](https://docs.astral.sh/uv/) so `dashdown` works from any directory:

```bash
uv tool install dashdown-md      # `dashdown` on your PATH, usable anywhere
```

…or run it once without installing, or pin it to a single project:

```bash
# Run it ad-hoc, no install, in a throwaway env
uvx --from dashdown-md dashdown new my-dashboard

# …or add it to the current project's environment (pins the version)
uv add dashdown-md
uv run dashdown serve .

# …or with plain pip
pip install dashdown-md
```

:::tip
For a connector or feature, install the matching extra
(`uv tool install 'dashdown-md[postgres]'`). See
**[Installation](/installation)** for every method, the full extras list, upgrading,
and fixing a `dashdown: command not found` PATH issue.
:::

### Hacking on Dashdown itself

Cloning the framework to develop it (not just use it):

```bash
uv sync                          # create the venv + install dev deps
uv run pytest tests/ -v          # run the test suite
uv run dashdown serve docs         # preview these docs
```

## Scaffold a project

```bash
dashdown new my-dashboard
cd my-dashboard
dashdown serve .
```

`dashdown serve` serves the dashboard at `http://127.0.0.1:8000` and live-reloads
when you edit a page.

## Project layout

A Dashdown *project* is a directory the CLI points at:

```text
my-dashboard/
├── dashdown.yaml     # project config (title, branding, format, …)
├── sources.yaml      # data connectors
├── pages/            # one .md per route  →  pages/sales.md = /sales
│   └── index.md      # the home page  →  /
├── queries/          # optional shared query library (*.sql / *.dax)
├── components/        # optional project-local Python components
├── partials/         # optional Markdown includes
├── data/             # files for the csv/duckdb/excel connectors
└── assets/           # custom.css, logos, images
```

`dashdown.yaml` configures the whole dashboard (title, branding, formatting,
…) — see **[Configuration](/configuration)** for every block.

:::tip
There are two distinct domains: **the framework** (the `dashdown` package you
install) and **your project** (the directory above). The CLI points the
framework at one project.
:::

## These docs are a live project

This documentation site is itself a Dashdown project — every page you're reading
is a `pages/*.md` rendered by the pipeline it documents. Browse the source under
[`docs/`](https://github.com/DirendAI/dashdown/tree/main/docs) for a complete,
real-world project to learn from.

## Next steps

You have a project running. Where to go from here:

- **[Writing pages](/pages)** — frontmatter, callouts, Mermaid, and includes.
- **[Queries](/queries)** — inline ```` ```sql name ```` blocks, `${param}` substitution, and the shared query library.
- **[Components](/components)** — charts, tables, counters, and pivots.
- **[Connectors](/connectors)** — connect CSVs, Postgres, BigQuery, Fabric, and more.
- **[Configuration](/configuration)** — every `dashdown.yaml` block in one place.
- **[CLI reference](/cli)** — every `dashdown` command, including
  [`query`](/cli#query) (probe a connector with raw SQL) and
  [`metric`](/cli#metric) (probe the semantic layer by metric + grouping) for
  testing connections and inspecting real data while you author.
