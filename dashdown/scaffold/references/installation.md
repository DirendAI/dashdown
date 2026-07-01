<!-- AUTO-GENERATED from docs/pages/ by tooling/gen-agent-docs.py — do not edit. -->
<!-- Topic: installation. Regenerate with: python tooling/gen-agent-docs.py -->

<!-- source: docs/pages/installation.md -->

# Installation

Dashdown is published to PyPI as **`dashdown-md`**, and the CLI it installs is the
command **`dashdown`**. So you `pip install dashdown-md` (or `uv tool install
dashdown-md`) but you *run* `dashdown`. It needs **Python 3.10+**.

:::note
**`dashdown-md` is the package, `dashdown` is the command.** Everywhere you install
or add a dependency, use the hyphenated `dashdown-md`; everywhere you run something,
use `dashdown`. (`dashdown` on its own is *not* the right package name.)
:::

## Install the CLI globally — use it anywhere

If you want `dashdown` on your `PATH` so you can scaffold and serve projects from
**any** directory, install it as a [uv](https://docs.astral.sh/uv/) tool. This is the
"create a project anywhere on my machine" path:

```bash
uv tool install dashdown-md
```

Now `dashdown` works from any folder, in its own isolated environment (it won't
clash with your projects' dependencies):

```bash
cd ~/anywhere
dashdown new my-dashboard      # scaffold a project here
dashdown serve my-dashboard    # → http://127.0.0.1:8000
```

Verify it's installed and on your `PATH`:

```bash
dashdown --version             # prints the installed version
uv tool list                   # shows dashdown-md and the `dashdown` executable
dashdown --help                # lists every command
```

:::tip
If your shell can't find `dashdown` after installing, uv's tool bin directory isn't
on your `PATH` yet. Run `uv tool update-shell` once and restart your terminal (it
adds `~/.local/bin`, where uv places tool executables, to your shell profile).
:::

The same global install with [pipx](https://pipx.pypa.io/) instead of uv:

```bash
pipx install dashdown-md
```

## Run it once, without installing

To try Dashdown without installing anything, `uvx` runs the CLI in a throwaway
environment:

```bash
uvx --from dashdown-md dashdown new my-dashboard
uvx --from dashdown-md dashdown serve my-dashboard
```

`--from dashdown-md` is required because the package and command names differ — it
tells uvx which package provides the `dashdown` command.

## Add it to a single project (pinned & reproducible)

When you want everyone on a team to run the **same** Dashdown version, add it as a
dependency of that project instead of installing globally — the version is pinned in
the project's lockfile:

```bash
# uv
uv add dashdown-md
uv run dashdown serve .        # runs the project's pinned version

# pip (into an active virtualenv)
pip install dashdown-md
dashdown serve .
```

:::tip
`uv tool install` (global) and `uv add` (per-project) solve different problems. Use
the **global** install for a CLI you reach for from anywhere; use **`uv add`** when a
specific dashboard should ship with a known, locked Dashdown version.
:::

## Connectors & features are optional extras

The core install is deliberately lean. Each data connector and heavy feature pulls
its dependencies only when you ask for it, via a pip *extra*:

```bash
uv tool install 'dashdown-md[postgres]'        # Postgres connector
uv tool install 'dashdown-md[postgres,excel,pdf]'   # several at once
```

The same extras work with every install method:

```bash
uvx --from 'dashdown-md[postgres]' dashdown serve .   # one-off
uv add 'dashdown-md[postgres]'                        # per-project
pip install 'dashdown-md[postgres]'                   # pip
```

A few common ones — see **[Connectors](/connectors)** for the full list, or run
`dashdown components --connectors` to print every connector with its install extra:

| Extra | Brings |
|---|---|
| `postgres`, `mysql`, `mssql`, `snowflake`, `bigquery`, `clickhouse` | SQL database connectors |
| `excel`, `sheets` | Spreadsheet connectors |
| `dax` | Microsoft Fabric / Power BI |
| `pdf` | Presentation PDF export (`dashdown pdf`) |
| `semantic` | The semantic metric layer (Ibis backend) |

:::note
With a **global** (`uv tool install`) install, extras are baked in at install time.
To add one later, re-run the install with the full list and `--force` to replace the
existing install:

```bash
uv tool install --force 'dashdown-md[postgres,pdf]'
```
:::

:::tip
The `pdf` extra also needs a one-time browser download after installing:
`playwright install chromium`. See **[Exporting](/exporting)** for details.
:::

## Upgrade & uninstall

```bash
# global (uv tool)
uv tool upgrade dashdown-md
uv tool uninstall dashdown-md

# per-project (uv)
uv add dashdown-md@latest
uv remove dashdown-md

# pip
pip install --upgrade dashdown-md
pip uninstall dashdown-md
```

## Which method should I use?

| You want to… | Use |
|---|---|
| Scaffold & serve projects from anywhere | `uv tool install dashdown-md` |
| Try it once without installing | `uvx --from dashdown-md dashdown …` |
| Pin a version for one dashboard / a team | `uv add dashdown-md` + `uv run dashdown …` |
| Install into an existing virtualenv | `pip install dashdown-md` |
| Hack on Dashdown itself | clone the repo, then `uv sync` |

## Next steps

You have the `dashdown` command. Head to **[Getting started](/getting-started)** to
scaffold a project, understand the project layout, and serve your first dashboard.
