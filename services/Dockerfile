# syntax=docker/dockerfile:1.7
#
# Garh AI — worker image, shared by worker-solver, worker-render and
# worker-drawings. One image, three entrypoints: the queue each process consumes
# is picked by the command in docker-compose.yml.
#
#     python -m services.solver.worker     # OR-Tools CP-SAT layout engine (§5)
#     python -m services.render.worker     # provider iface: mock | diffusers (§9)
#     python -m services.drawings.worker   # auto-dim, sheets, ezdxf/PDF (§7)
#
# Build context is the REPO ROOT (workers import garh_model and read
# rulepacks/ + fixtures/):
#     docker build -f services/Dockerfile --target dev .
#
# Deliberately CPU-only and torch-free. PROVIDER_RENDER=mock is the default, so
# the default image stays small and the whole product is testable with no GPU
# and no multi-GB weights. The real diffusers stack is Phase 7: it arrives via
# services/requirements-render.txt in a `render-gpu` stage built on a CUDA base,
# and is gated behind PROVIDER_RENDER=diffusers.

# ══════════════════════════════════════════════════════════════════════════════
FROM python:3.11-slim-bookworm AS base
# ══════════════════════════════════════════════════════════════════════════════

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONFAULTHANDLER=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_ROOT_USER_ACTION=ignore

# libgomp1 → OpenMP runtime OR-Tools CP-SAT links against for parallel search
# libgl1 / libglib2.0-0 → image-processing wheels (Pillow/opencv paths) used by
#                          the render worker's mock compositor
# curl → parity with the other images for ad-hoc probing
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        curl \
        libgomp1 \
        libgl1 \
        libglib2.0-0 \
        # The §7 vector PDF set: export/pdf.py refuses to ship without a real
        # SVG→PDF converter (a rasterised fake would be rejected by a
        # municipality). First execution of the export failed exactly here.
        librsvg2-bin \
        qpdf \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Workers share the API's runtime dependency set (they talk to Postgres and
# Redis and import garh_model) plus their own engine libraries.
#
# BOTH halves are extracted from their pyproject.toml with stdlib tomllib, so
# there is exactly one pinned source of truth per side and no inline list to
# drift. services/pyproject.toml carries ortools (Apache-2.0), ezdxf (MIT),
# pillow (HPND) and pydantic-settings — pillow in particular is NOT optional:
# the default PROVIDER_RENDER=mock provider imports PIL at module scope
# (services/render/mock.py, services/render/imaging.py), so a worker image
# without it dies on boot with ModuleNotFoundError.
#
# The heavy `ml` extra (torch/diffusers) is deliberately NOT installed: the
# locked decision is that the whole product runs with zero GPUs and zero keys.
# The tiny `llm` extra (the `anthropic` SDK) IS installed, so switching
# PROVIDER_LLM=anthropic in production is a config flip plus a key — not an
# image rebuild. The mock remains the default; the SDK sits unimported until
# the env var selects it (services/llm/provider.py imports lazily).
COPY ["apps/api/pyproject.toml", "/app/apps/api/pyproject.toml"]
COPY ["services/pyproject.toml", "/app/services/pyproject.toml"]
RUN python -c "\
import tomllib;\
paths = ('/app/apps/api/pyproject.toml', '/app/services/pyproject.toml');\
deps = [d for p in paths for d in tomllib.load(open(p, 'rb'))['project']['dependencies']];\
deps += tomllib.load(open('/app/services/pyproject.toml', 'rb'))['project']['optional-dependencies']['llm'];\
print('\n'.join(dict.fromkeys(deps)))" > /tmp/requirements.txt \
    && cat /tmp/requirements.txt \
    && python -m pip install --upgrade pip \
    && python -m pip install -r /tmp/requirements.txt

# /app          → `import services.solver...`
# /app/apps/api → `import garh_model...` (its home per playbook §1)
ENV PYTHONPATH=/app:/app/apps/api \
    HF_HOME=/app/.cache/huggingface

# ══════════════════════════════════════════════════════════════════════════════
FROM base AS dev
# ══════════════════════════════════════════════════════════════════════════════
# Source bind-mounted by compose. No COPY: edit a constraint builder on the host
# and restart the one worker.

RUN python -c "import tomllib; d = tomllib.load(open('/app/apps/api/pyproject.toml','rb')); print('\n'.join(d['project']['optional-dependencies']['dev']))" > /tmp/requirements-dev.txt \
    && python -m pip install -r /tmp/requirements-dev.txt

# No default worker — compose supplies the module per service. Failing loudly
# beats silently starting the wrong queue consumer.
CMD ["python", "-c", "import sys; sys.exit('specify a worker module, e.g. python -m services.solver.worker')"]

# ══════════════════════════════════════════════════════════════════════════════
FROM base AS prod
# ══════════════════════════════════════════════════════════════════════════════

COPY ["services", "/app/services"]
COPY ["apps/api", "/app/apps/api"]
COPY ["packages/model", "/app/packages/model"]
COPY ["rulepacks", "/app/rulepacks"]
# The solver's furniture-fit stage opens fixtures/catalog/furniture.json from the
# repo root (services/solver/furniture_fit.py:default_catalog_path). Without this
# line every generate job on a deployed worker died with FileNotFoundError, four
# attempts each, and the api image (which does copy fixtures) looked healthy —
# found on the first trial architect's first Generate, 2026-09-02. Only the
# catalogue: the rest of fixtures/ is test corpora.
COPY ["fixtures/catalog", "/app/fixtures/catalog"]

RUN useradd --create-home --uid 10001 --shell /usr/sbin/nologin garh \
    && mkdir -p /app/.cache/huggingface \
    && chown -R garh:garh /app
USER garh

CMD ["python", "-c", "import sys; sys.exit('specify a worker module, e.g. python -m services.solver.worker')"]
