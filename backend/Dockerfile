# UXAgent backend — Flask API + Playwright agent pipeline.
#
# Base image choice: python:3.12-slim + `playwright install --with-deps`,
# rather than mcr.microsoft.com/playwright/python:vX.Y.Z. The Playwright image
# pins the browser version in the image tag, which then has to be kept in sync
# with uv.lock by hand — and this project has already drifted there (uv.lock
# resolves playwright 1.48.0 on old Pythons and 1.59.0 on 3.12). Installing the
# browser from the resolved package guarantees the binary matches the library.
#
# Build (from backend/):    docker build -t uxagent-backend .
# Run locally:              docker run --rm -p 8080:8080 --env-file .env uxagent-backend

FROM python:3.12-slim AS base

# uv, pinned by digest-free tag from the official distroless image.
COPY --from=ghcr.io/astral-sh/uv:0.5.11 /uv /usr/local/bin/uv

ENV PYTHONUNBUFFERED=1 \
    PYTHONIOENCODING=utf-8 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    # Browsers go to a shared path, not ~/.cache, so the non-root runtime user
    # can read what root installed below.
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

WORKDIR /app

# ── Dependencies (cached layer) ──────────────────────────────────────────────
# Copy only the manifests first so a source-only change doesn't reinstall
# ~175 packages. --no-install-project: the project itself comes later.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

# ── Chromium + its system libraries ──────────────────────────────────────────
# --with-deps runs apt-get for the shared libs chromium needs; must happen as
# root, before dropping privileges.
RUN uv run playwright install --with-deps chromium \
    && rm -rf /var/lib/apt/lists/*

# ── Application ──────────────────────────────────────────────────────────────
COPY . .
RUN uv sync --frozen --no-dev

# Serve with gunicorn rather than Flask's dev server. Installed here, not in
# pyproject: it is a deployment concern, not a library dependency.
RUN uv pip install gunicorn

# ── Runtime user ─────────────────────────────────────────────────────────────
# Non-root is required, not just good practice: conf/base.yaml passes no
# --no-sandbox flag, and chromium's sandbox refuses to start as root.
# browser_session/ (user_data_dir) and runs/ are written at runtime, so they
# must be owned by that user.
RUN useradd --create-home --uid 1001 uxagent \
    && mkdir -p /app/runs /app/browser_session /app/browser_cache \
    && chown -R uxagent:uxagent /app \
    && chmod -R a+rX /ms-playwright
USER uxagent

# Cloud Run injects PORT (8080 by default); honour it, with a local fallback.
ENV PORT=8080
EXPOSE 8080

# One worker, many threads — deliberate. The pipeline is kicked off in a
# background thread and tracked in an in-memory RUNS registry (main/app.py);
# a second worker would have its own copy, so a run started on one worker
# would be invisible to /progress served by the other.
# --timeout 0 disables the worker kill: reading a large run's trace can take a
# while, and the audit threads must not be reaped mid-run.
CMD exec uv run gunicorn \
    --bind "0.0.0.0:${PORT}" \
    --workers 1 \
    --threads 8 \
    --timeout 0 \
    --access-logfile - \
    --error-logfile - \
    simulated_web_agent.main.app:app

# ── Deployment notes ─────────────────────────────────────────────────────────
# Cloud Run:
#   gcloud run deploy uxagent-backend --source . \
#     --region europe-central2 --memory 4Gi --cpu 2 \
#     --no-cpu-throttling --timeout 3600 \
#     --min-instances 1 --max-instances 1
#
#   * --no-cpu-throttling (CPU always allocated) is REQUIRED. The audit runs in
#     a background thread after the HTTP response returns; with the default
#     request-scoped CPU it would be throttled to a stop mid-run.
#   * --min/--max-instances 1 for the same reason as --workers 1: run state is
#     in-memory, so it must not be spread across instances.
#   * Memory: chromium is heavy and personas run concurrently. 4Gi is a
#     starting point — raise it if sessions die at higher concurrency.
#
# Credentials: do NOT bake .env or a service-account key into the image. On
# Cloud Run, attach a service account and let Application Default Credentials
# supply Vertex AI — GOOGLE_APPLICATION_CREDENTIALS is then unnecessary. Set
# VERTEXAI_PROJECT / VERTEXAI_LOCATION / GOOGLE_PAGESPEED_API_KEY as env vars
# or Secret Manager references.
#
# Storage: runs/ inside the container is ephemeral and is lost on every
# restart or redeploy. Mount a GCS bucket (gcsfuse) at /app/runs, or move
# TraceStore to a GCS-backed path, before treating any report as durable.
#
# If chromium still fails to launch on Cloud Run's gVisor sandbox, add
# "--no-sandbox" to environment.browser.launch_options.args in conf/base.yaml.
