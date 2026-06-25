"""Production ASGI entry point.

Run the live server under multiple workers without the dev CLI:

    DASHDOWN_PROJECT=/srv/dashboard uvicorn dashdown.asgi:app \
        --host 0.0.0.0 --port 8000 --workers 4

Each worker imports this module, so each builds the app in production posture
(``dev=False``): no live-reload SSE and every page's queries pre-registered, so
a worker can answer a data request for any page regardless of which worker
rendered it. ``dashdown serve`` is the dev path and is unaffected.
"""
from __future__ import annotations

import os
from pathlib import Path

from dashdown.server import create_app

_project = os.environ.get("DASHDOWN_PROJECT")
if not _project:
    raise RuntimeError(
        "DASHDOWN_PROJECT is not set. Point it at your project directory "
        "(the one with dashdown.yaml), e.g. DASHDOWN_PROJECT=/srv/dashboard."
    )

_project_root = Path(_project).expanduser().resolve()
if not (_project_root / "dashdown.yaml").is_file():
    raise RuntimeError(
        f"No dashdown.yaml under DASHDOWN_PROJECT={_project_root} — "
        "is it pointing at a Dashdown project directory?"
    )

app = create_app(_project_root, dev=False)
