# Production image for a live (non-static) Dashdown dashboard.
#
# Build from the repo root so the build context includes the framework source:
#   docker build -f deploy/Dockerfile --build-arg PROJECT=docs -t my-dashboard .
#
# On a published release you can instead base this on `pip install dashdown-md`
# and only COPY your project — see deploy/README.md.

FROM python:3.12-slim AS base
ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Install the framework. Copy only what the package build needs first so this
# layer is cached across project-only changes.
COPY pyproject.toml README.md ./
COPY dashdown ./dashdown
RUN pip install . && pip install uvicorn

# Copy the dashboard project. Override with --build-arg PROJECT=path/to/project.
ARG PROJECT=docs
COPY ${PROJECT} /srv/dashboard

# dev=False posture: no live-reload SSE, queries pre-registered for all workers.
ENV DASHDOWN_PROJECT=/srv/dashboard \
    WEB_CONCURRENCY=4

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/_dashdown/health').status==200 else 1)"

# uvicorn reads WEB_CONCURRENCY for --workers; override at runtime to scale.
CMD ["sh", "-c", "uvicorn dashdown.asgi:app --host 0.0.0.0 --port 8000 --workers ${WEB_CONCURRENCY}"]
