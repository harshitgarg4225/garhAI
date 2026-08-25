# syntax=docker/dockerfile:1.7
#
# Garh AI — API image (FastAPI + SQLAlchemy 2 + Alembic + Pydantic v2).
#
# Build context is the REPO ROOT, not apps/api — the API imports garh_model and
# reads rulepacks/ and fixtures/, so it needs the wider tree:
#     docker build -f apps/api/Dockerfile --target dev .
# (docker-compose.yml already does this.)
#
# Stages:  base → dev (hot reload, bind-mounted source)
#                → prod (source baked in, non-root, multi-worker)

# ══════════════════════════════════════════════════════════════════════════════
FROM python:3.11-slim-bookworm AS base
# ══════════════════════════════════════════════════════════════════════════════

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONFAULTHANDLER=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_ROOT_USER_ACTION=ignore

# curl  → container healthcheck against /healthz (§18: healthz per service)
# openssl → mints the dev JWT keypair on first boot (see compose `api.command`)
# psycopg is installed as psycopg[binary], so libpq is NOT needed here.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        curl \
        openssl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Dependencies first, in their own layer: application code changes constantly,
# the dependency set rarely, and this is what keeps rebuilds to a few seconds.
#
# The manifest is apps/api/pyproject.toml, and only the pinned dependency lists
# are pulled out of it — `pip install .` is deliberately NOT used, because
# hatchling would need both garh_api/ and garh_model/ to exist at build time and
# would fail while the tree is still being filled in. tomllib is stdlib on 3.11.
COPY ["apps/api/pyproject.toml", "/app/apps/api/pyproject.toml"]
RUN python -c "import tomllib; d = tomllib.load(open('/app/apps/api/pyproject.toml','rb')); print('\n'.join(d['project']['dependencies']))" > /tmp/requirements.txt \
    && cat /tmp/requirements.txt \
    && python -m pip install --upgrade pip \
    && python -m pip install -r /tmp/requirements.txt

# garh_model lives at apps/api/garh_model/ (playbook §1) and the API package at
# apps/api/garh_api/. Both are importable from /app/apps/api, so the project
# itself is never pip-installed — PYTHONPATH is the whole mechanism, which is
# also what makes the bind-mounted dev source live without a reinstall.
ENV PYTHONPATH=/app:/app/apps/api

EXPOSE 8000

# ══════════════════════════════════════════════════════════════════════════════
FROM base AS dev
# ══════════════════════════════════════════════════════════════════════════════
# Source is bind-mounted by compose; nothing is COPYed in, so an edit on the
# host is live immediately. Runs as root so it can write .pyc caches and the
# generated dev keypair into the mounted volumes without ownership friction.

# pytest / pytest-asyncio / hypothesis / ruff / mypy — the [dev] extra.
RUN python -c "import tomllib; d = tomllib.load(open('/app/apps/api/pyproject.toml','rb')); print('\n'.join(d['project']['optional-dependencies']['dev']))" > /tmp/requirements-dev.txt \
    && python -m pip install -r /tmp/requirements-dev.txt

WORKDIR /app/apps/api

# Overridden by compose (which also runs migrations first). This CMD is the
# `docker run` fallback.
CMD ["uvicorn", "garh_api.main:app", "--host", "0.0.0.0", "--port", "8000", "--reload"]

# ══════════════════════════════════════════════════════════════════════════════
FROM base AS prod
# ══════════════════════════════════════════════════════════════════════════════
# Single-VM/compose beta target (§18). Workers stay stateless so this can move
# to k8s later without changing the image.

COPY ["apps/api", "/app/apps/api"]
COPY ["services", "/app/services"]
COPY ["packages/model", "/app/packages/model"]
COPY ["rulepacks", "/app/rulepacks"]
# fixtures/: the seeder reads the demo brief and catalog files from here
# (garh_api.seed warns and falls back to compiled-in stubs without them),
# and the drawings pipeline's golden comparisons read fixtures/sheets.
COPY ["fixtures", "/app/fixtures"]

# Non-root: nothing in the request path needs to write to the filesystem.
RUN useradd --create-home --uid 10001 --shell /usr/sbin/nologin garh \
    && chown -R garh:garh /app
USER garh

WORKDIR /app/apps/api

# Migrations are NOT run here — a prod deploy runs `alembic upgrade head` as a
# separate step so N replicas don't race the same migration.
CMD ["uvicorn", "garh_api.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "4"]
