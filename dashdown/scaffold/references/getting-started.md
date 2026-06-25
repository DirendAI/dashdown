<!-- AUTO-GENERATED from docs/pages/ by tooling/gen-agent-docs.py — do not edit. -->
<!-- Topic: getting-started. Regenerate with: python tooling/gen-agent-docs.py -->

<!-- source: docs/pages/getting-started.md -->

# Getting started

## Install

Dashdown ships as the `dashdown` pip package with a CLI entry point. Install it
with whichever Python tool you use.

### With [uv](https://docs.astral.sh/uv/) (recommended)

`uv` is a fast Python package manager. Add Dashdown to a project, or run it as a
one-off tool without installing it globally:

```bash
# Add it to the current project's environment
uv add dashdown

# …with extras for a specific connector / feature
uv add 'dashdown-md[postgres]'
uv add 'dashdown-md[pdf]'          # presentation PDF export

# Run the CLI inside the project env
uv run dashdown serve .

# Or run it ad-hoc, no install, in a throwaway env
uvx --from dashdown-md dashdown new my-dashboard
uvx --from dashdown-md dashdown serve .
```

:::tip
`uv run dashdown …` always uses the project's pinned environment, so everyone on
the team runs the same version. `uvx --from dashdown-md dashdown …` is handy for a quick try without
touching your project.
:::

### With pip

```bash
pip install dashdown-md
# …with extras for a specific connector / feature
pip install 'dashdown-md[postgres]'
pip install 'dashdown-md[pdf]'      # presentation PDF export
```

Extras pull in a backend's heavy dependencies only when you need them — see
[Connectors](/connectors) for the full list.

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
├── dashdown.yaml     # project config (title, auth, embed, branding, …)
├── sources.yaml      # data connectors
├── pages/            # one .md per route  →  pages/sales.md = /sales
│   └── index.md      # the home page  →  /
├── queries/          # optional shared query library (*.sql / *.dax)
├── components/        # optional project-local Python components
├── partials/         # optional Markdown includes
├── data/             # files for the csv/duckdb/excel connectors
└── assets/           # custom.css, logos, images
```

`dashdown.yaml` configures the whole dashboard (title, auth, branding,
embedding, …) — see **[Configuration](/configuration)** for every block.

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
- **[Queries](/queries)** — `:::query` blocks, `${param}` substitution, and the shared query library.
- **[Components](/components)** — charts, tables, counters, and pivots.
- **[Connectors](/connectors)** — connect CSVs, Postgres, BigQuery, Fabric, and more.
- **[Configuration](/configuration)** — every `dashdown.yaml` block in one place.
- **[CLI reference](/cli)** — every `dashdown` command, including
  [`query`](/cli#query) (probe a connector with raw SQL) and
  [`metric`](/cli#metric) (probe the semantic layer by metric + grouping) for
  testing connections and inspecting real data while you author.
