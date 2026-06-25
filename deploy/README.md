# Deploying a live Dashdown dashboard

This is for the **live, filtered** server (queries run on request) — not the
static export. For a read-only dashboard, `dashdown build` + any static host /
CDN is simpler and scales without limit.

## Quick start (Docker)

From the **repo root**:

```bash
docker compose -f deploy/docker-compose.yml up --build
# → http://localhost:8000
```

Point it at your own project with the `PROJECT` build arg (path relative to the
build context / repo root):

```bash
docker build -f deploy/Dockerfile --build-arg PROJECT=path/to/dashboard -t my-dashboard .
docker run -p 8000:8000 -e WEB_CONCURRENCY=4 my-dashboard
```

On a published release you can slim the image down to just your project:

```dockerfile
FROM python:3.12-slim
RUN pip install --no-cache-dir "dashdown-md" uvicorn
COPY . /srv/dashboard
ENV DASHDOWN_PROJECT=/srv/dashboard WEB_CONCURRENCY=4
CMD ["sh","-c","uvicorn dashdown.asgi:app --host 0.0.0.0 --port 8000 --workers ${WEB_CONCURRENCY}"]
```

## How it runs in production

`dashdown.asgi:app` builds the app with `create_app(dev=False)`, which differs
from `dashdown serve` in two ways that matter at scale:

- **No live-reload SSE.** The dev server opens a persistent `/_dashdown/reload`
  stream per browser; production has no file watcher, so it's suppressed —
  otherwise every viewer would hold one idle connection.
- **Queries pre-registered at startup.** Inline `:::query` defs are otherwise
  registered only when a page is rendered, which 404s under multiple workers
  when a data request lands on a worker that never served that page. `dev=False`
  renders every page once at boot so any worker can answer for any page.

## Sizing for 50–500 concurrent users (CSV/DuckDB)

- **Workers:** `WEB_CONCURRENCY=4` is a good start (≈ CPU cores). Active users
  mostly read; a single worker already sustains ~150–200 req/s, so 4 gives
  comfortable headroom.
- **Memory:** each worker materializes its **own** in-memory copy of the CSVs,
  so plan **≈ workers × dataset size** plus overhead.
- **Caching:** repeated identical `(query, params)` results are cached per
  worker; set `cache_ttl` on hot queries to absorb repeated filter combinations.
- **Front it with a proxy:** put nginx/Caddy in front for TLS, gzip, and to
  cache the static assets under `/_dashdown/static/`.

## Deploying to Hetzner Cloud

A ~€4/mo box (CAX11 ARM, 2 vCPU / 4 GB, or CX22 x86) runs this comfortably for
50–500 users — no sleeping, dedicated RAM. The image is multi-arch, so the ARM
CAX11 works as-is.

**1. Create the server.** Ubuntu 24.04, add your SSH key, and paste
[`deploy/cloud-init.yaml`](cloud-init.yaml) into the "Cloud config" field (it
installs Docker + opens the firewall on first boot).

**2. Point a domain at it (optional but recommended).** Create an `A` record for
e.g. `dash.example.com` → the server's IP. Skip this to start on the bare IP
over HTTP.

**3. Bring it up.** SSH in, get the code, and start the stack:

```bash
git clone <your-repo> dashboard && cd dashboard

# with a domain → automatic HTTPS:
SITE_ADDRESS=dash.example.com docker compose -f deploy/docker-compose.hetzner.yml up -d --build

# or, bare IP over HTTP (no domain yet):
docker compose -f deploy/docker-compose.hetzner.yml up -d --build
```

[Caddy](Caddyfile) fronts the app on ports 80/443 (automatic Let's Encrypt cert
when `SITE_ADDRESS` is a domain), gzips responses, and caches static assets. The
dashboard container isn't exposed to the host — only Caddy is.

**Update later:** `git pull && docker compose -f deploy/docker-compose.hetzner.yml up -d --build`.

Change which project is served via the `PROJECT` build arg in
[`docker-compose.hetzner.yml`](docker-compose.hetzner.yml) (defaults to `docs`),
and tune `WEB_CONCURRENCY` there (2–3 on a 4 GB box).

## Notes

- Auth: set an `auth:` block in `dashdown.yaml` (Basic or API key) — the server
  refuses to start with a malformed block, so it never comes up open.
- This image serves over HTTP/1.1; a proxy that speaks HTTP/2 to browsers lifts
  the per-origin connection cap and further smooths many-widget pages.
