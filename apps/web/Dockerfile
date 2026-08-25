# syntax=docker/dockerfile:1.7
#
# Garh AI — web image (Vite 5 + React 18 + TS strict + R3F + Zustand + Tailwind).
#
# Build context is the REPO ROOT: this is a pnpm workspace, and installing
# apps/web means resolving @garh/model and @garh/ui too.
#     docker build -f apps/web/Dockerfile --target dev .
#
# Stages:  base → deps → dev (dev server + HMR)
#                      → build → prod (static bundle on nginx)

# ══════════════════════════════════════════════════════════════════════════════
FROM node:20-bookworm-slim AS base
# ══════════════════════════════════════════════════════════════════════════════

# curl → container healthcheck (node:20-slim has neither curl nor wget)
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl \
    && rm -rf /var/lib/apt/lists/*

# Pin pnpm to the exact version in the root package.json `packageManager`
# field. corepack would read that field anyway; pinning here makes the image
# reproducible even if the field is edited without a rebuild.
ENV PNPM_HOME=/pnpm \
    PATH=/pnpm:$PATH \
    npm_config_update_notifier=false
RUN corepack enable && corepack prepare pnpm@9.12.0 --activate

WORKDIR /app

# ══════════════════════════════════════════════════════════════════════════════
FROM base AS deps
# ══════════════════════════════════════════════════════════════════════════════
# Manifests only, so `pnpm install` is cached until a package.json actually
# changes. Each workspace member is copied individually — a `COPY packages/*/`
# glob flattens directories and silently produces the wrong tree.

COPY ["package.json", "pnpm-workspace.yaml", "/app/"]
# Trailing `*` so a checkout WITHOUT a committed lockfile still builds. A plain
# `COPY pnpm-lock.yaml` is a hard build error when the file is absent, and the
# lockfile can only be generated on a machine that has Node — see
# docs/phase-0-verification.md. Once `pnpm-lock.yaml` is committed this glob
# matches it and `--frozen-lockfile` in the build stage below becomes real.
COPY ["pnpm-lock.yaml*", "/app/"]
COPY ["apps/web/package.json", "/app/apps/web/"]
COPY ["packages/model/package.json", "/app/packages/model/"]
COPY ["packages/ui/package.json", "/app/packages/ui/"]
COPY ["e2e/package.json", "/app/e2e/"]

# --prefer-frozen-lockfile: uses the lockfile when it matches the manifests and
# falls back to resolving when it doesn't. `--frozen-lockfile` is the stricter
# choice and is what CI and the build stage below use; here it would hard-fail
# on a fresh clone whose lockfile has not been generated yet.
RUN pnpm install --prefer-frozen-lockfile

# ══════════════════════════════════════════════════════════════════════════════
FROM deps AS dev
# ══════════════════════════════════════════════════════════════════════════════
# Source is bind-mounted by compose; the installed node_modules trees are
# preserved behind named volumes so the mounts don't shadow them.
#
# Ports: 5173 dev server, 24678 HMR websocket. HMR needs its own published port
# because the browser connects to it directly from the host, not through Vite.
EXPOSE 5173 24678

CMD ["pnpm", "--filter", "@garh/web", "dev", "--host", "0.0.0.0", "--port", "5173"]

# ══════════════════════════════════════════════════════════════════════════════
FROM deps AS build
# ══════════════════════════════════════════════════════════════════════════════
# Full source, strict lockfile, real bundle.

COPY ["tsconfig.base.json", "/app/"]
COPY ["packages", "/app/packages"]
COPY ["apps/web", "/app/apps/web"]

# Baked into the bundle (§13: VITE_ values are public forever). The default is
# the same-origin proxy path the prod topology documents — override only for a
# split-origin deploy, which also needs the CSP's connect-src widened.
ARG VITE_API_BASE_URL=/api/v1
ENV VITE_API_BASE_URL=${VITE_API_BASE_URL}

RUN pnpm install --frozen-lockfile --offline || pnpm install --frozen-lockfile
# @garh/model and @garh/ui declare no `build` script on purpose — they are
# consumed as TypeScript source and Vite bundles them through the resolve.alias
# in apps/web/vite.config.ts. `pnpm --filter @garh/model build` would exit
# ERR_PNPM_NO_SCRIPT and fail this stage.
RUN pnpm --filter @garh/web build

# ══════════════════════════════════════════════════════════════════════════════
FROM nginx:1.27-alpine AS prod
# ══════════════════════════════════════════════════════════════════════════════
# Static assets only. Every VITE_ value is already baked into the bundle at this
# point — which is exactly why nothing but VITE_-prefixed vars may be read by
# client code (§13, enforced by `make secret-audit`).
#
# apps/web/nginx.conf supplies the two things the stock config does not: the
# browser-history SPA fallback (without it every deep link and every page
# refresh 404s) and the §13 security headers, CSP included. TLS termination and
# the /api reverse proxy still belong to the layer in front — docs/deployment.md.

COPY ["apps/web/nginx.conf", "/etc/nginx/conf.d/default.conf"]
COPY --from=build /app/apps/web/dist /usr/share/nginx/html

EXPOSE 80
HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
    CMD wget -q --spider http://localhost/ || exit 1

CMD ["nginx", "-g", "daemon off;"]

# ══════════════════════════════════════════════════════════════════════════════
FROM prod AS prod-railway
# ══════════════════════════════════════════════════════════════════════════════
# `prod` plus the same-origin /api reverse proxy docs/deployment.md assigns to
# "the layer in front". On Railway there is no separate layer, so nginx is it:
# the stock config's explicit `location /api/ { return 404; }` gets its body
# swapped for an include, and the include's contents are rendered from
# deploy/railway/api-proxy.conf.template by the stock nginx entrypoint at
# container start (GARH_API_ORIGIN → the API service's private-network URL).
# Defaults in the .envsh keep this stage bootable anywhere; without Railway it
# simply 502s on /api and serves the static site.
#
# This is the LAST stage on purpose: a bare `docker build` (no --target) now
# produces the deployable image. Local/compose builds all pass explicit
# targets, so nothing there changes.

ENV NGINX_ENVSUBST_OUTPUT_DIR=/etc/nginx/conf.d/api-proxy

COPY ["deploy/railway/10-garh-api-proxy.envsh", "/docker-entrypoint.d/10-garh-api-proxy.envsh"]
COPY ["deploy/railway/api-proxy.conf.template", "/etc/nginx/templates/api-proxy.conf.template"]

RUN chmod +x /docker-entrypoint.d/10-garh-api-proxy.envsh \
    && mkdir -p /etc/nginx/conf.d/api-proxy \
    && sed -i 's|return 404;|include /etc/nginx/conf.d/api-proxy/*.conf;|' /etc/nginx/conf.d/default.conf \
    && grep -q 'api-proxy' /etc/nginx/conf.d/default.conf
