# Garh AI — developer entrypoints.
#
#     make            # list every target
#     make up         # the whole stack
#
# ── Portability note ──────────────────────────────────────────────────────────
# macOS ships GNU Make 3.81, which has no `.ONESHELL:`. Make therefore runs
# EVERY recipe line in its own shell, so any multi-line logic below is joined
# with `\` into one logical line. Shell variables are written `$$var` so make
# passes the `$` through instead of expanding it itself.
#
# The repo path contains a space ("Garh AI"), so every path that reaches a shell
# is quoted.

SHELL := /bin/sh
.DEFAULT_GOAL := help

COMPOSE ?= docker compose
PNPM    ?= pnpm
PY      ?= python3

# Ruff/mypy scope: strict on the model core and the workers (playbook §1).
PY_STRICT_PATHS := apps/api/garh_model services
PY_ALL_PATHS    := apps/api services

.PHONY: help up up-build down stop restart reset logs logs-api logs-web ps \
        build migrate migrate-down seed env dev-keys install lockfile \
        test test-py test-js golden sheet-goldens e2e e2e-smoke \
        lint lint-py lint-js fmt fmt-check typecheck typecheck-py typecheck-js \
        secret-audit tenancy-audit license-check env-audit asset-audit audit verify \
        rule-fixtures solver-smoke fixture-drift copilot-eval copilot-containment \
        render-mirrors bare \
        shell-api shell-worker psql redis-cli clean

# ══════════════════════════════════════════════════════════════════════════════
# Help
# ══════════════════════════════════════════════════════════════════════════════

help: ## Show this help
	@echo "Garh AI — make targets"
	@echo ""
	@grep -hE '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
	  | sort \
	  | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2}'
	@echo ""
	@echo "First run:  make env && make up"

# ══════════════════════════════════════════════════════════════════════════════
# Stack lifecycle
# ══════════════════════════════════════════════════════════════════════════════

env: ## Create .env from .env.example (never overwrites)
	@if [ -f .env ]; then \
	   echo "  .env already exists — leaving it alone"; \
	 else \
	   cp .env.example .env && echo "  wrote .env from .env.example"; \
	 fi

up: env ## Start the whole stack (postgres, redis, minio, api, web, 3 workers)
	$(COMPOSE) up

up-build: env ## Start the stack, rebuilding images first
	$(COMPOSE) up --build

down: ## Stop the stack and remove containers (keeps volumes/data)
	$(COMPOSE) down --remove-orphans

stop: ## Stop containers without removing them
	$(COMPOSE) stop

restart: ## Restart every service
	$(COMPOSE) restart

reset: ## DESTRUCTIVE: stop the stack and delete all volumes (db, minio, keys)
	@printf "This deletes the database, object storage and dev keys. Type 'yes' to continue: "; \
	 read ans; \
	 if [ "$$ans" = "yes" ]; then \
	   $(COMPOSE) down -v --remove-orphans && echo "  volumes removed"; \
	 else \
	   echo "  aborted"; \
	 fi

logs: ## Tail logs for all services
	$(COMPOSE) logs -f --tail=100

logs-api: ## Tail API logs
	$(COMPOSE) logs -f --tail=200 api

logs-web: ## Tail web (Vite) logs
	$(COMPOSE) logs -f --tail=200 web

ps: ## Show service status and health
	$(COMPOSE) ps

build: ## Build all images
	$(COMPOSE) build

# ══════════════════════════════════════════════════════════════════════════════
# Database
# ══════════════════════════════════════════════════════════════════════════════

migrate: ## Apply Alembic migrations (alembic upgrade head)
	$(COMPOSE) exec api alembic upgrade head

migrate-down: ## Roll back one Alembic revision
	$(COMPOSE) exec api alembic downgrade -1

seed: ## Seed demo firm, catalogs, rule packs and the demo project (§17)
	$(COMPOSE) exec api python -m garh_api.seed

psql: ## Open a psql shell on the dev database
	$(COMPOSE) exec postgres sh -c 'psql -U "$$POSTGRES_USER" -d "$$POSTGRES_DB"'

redis-cli: ## Open a redis-cli shell
	$(COMPOSE) exec redis redis-cli

shell-api: ## Shell into the api container
	$(COMPOSE) exec api bash

shell-worker: ## Shell into the solver worker container
	$(COMPOSE) exec worker-solver bash

# ══════════════════════════════════════════════════════════════════════════════
# Dev keys (§13 — JWT RS256)
# ══════════════════════════════════════════════════════════════════════════════

# config.py reads the keys INLINE (single-line PEM, literal \n between lines) —
# there is no *_FILE variant. So this writes the PEMs to .keys/ for reference
# and also sets JWT_PRIVATE_KEY / JWT_PUBLIC_KEY in .env, which is the form the
# app actually consumes. awk ORS="\\n" emits a literal backslash-n; config.py's
# _normalise_pem validator turns it back into real newlines.
dev-keys: env ## Generate a dev RS256 keypair and write it into .env (§13)
	@mkdir -p .keys
	@if [ -f .keys/jwt-private.pem ]; then \
	   echo "  .keys/jwt-private.pem exists — reusing it (delete it to rotate)"; \
	 else \
	   openssl genpkey -algorithm RSA -pkeyopt rsa_keygen_bits:2048 \
	     -out .keys/jwt-private.pem 2>/dev/null && \
	   chmod 600 .keys/jwt-private.pem && \
	   echo "  wrote .keys/jwt-private.pem"; \
	 fi
	@openssl rsa -in .keys/jwt-private.pem -pubout -out .keys/jwt-public.pem 2>/dev/null \
	  && echo "  wrote .keys/jwt-public.pem"
	@priv=$$(awk 'BEGIN{ORS="\\n"}1' .keys/jwt-private.pem); \
	 pub=$$(awk 'BEGIN{ORS="\\n"}1' .keys/jwt-public.pem); \
	 grep -v -e '^JWT_PRIVATE_KEY=' -e '^JWT_PUBLIC_KEY=' .env > .env.tmp; \
	 printf 'JWT_PRIVATE_KEY=%s\n' "$$priv" >> .env.tmp; \
	 printf 'JWT_PUBLIC_KEY=%s\n'  "$$pub"  >> .env.tmp; \
	 mv .env.tmp .env; \
	 echo "  set JWT_PRIVATE_KEY and JWT_PUBLIC_KEY in .env"
	@echo "  restart the stack to pick them up:  make restart"

# ══════════════════════════════════════════════════════════════════════════════
# Install (host toolchain, for editor support and host-run tests)
# ══════════════════════════════════════════════════════════════════════════════

install: ## Install JS workspace deps on the host (needs Node 20 + pnpm 9)
	$(PNPM) install

# The one bootstrap step no machine without Node can do for you. CI installs with
# --frozen-lockfile in all six JS jobs (§13 "Dependencies: lockfiles"), so until
# pnpm-lock.yaml is committed, every one of them fails at the preflight step.
lockfile: ## Generate and report pnpm-lock.yaml (run once, then commit it)
	@if [ -f pnpm-lock.yaml ]; then \
	   echo "==> pnpm-lock.yaml already present ($$(wc -l < pnpm-lock.yaml | tr -d ' ') lines)"; \
	   echo "    Refresh it with 'pnpm install' after editing any package.json."; \
	 else \
	   echo "==> generating pnpm-lock.yaml"; \
	   $(PNPM) install || { echo "    pnpm not found — install Node 20 + pnpm 9.12.0 first"; exit 1; }; \
	   echo ""; \
	   echo "    Now COMMIT it. CI's --frozen-lockfile gate stays red until you do."; \
	 fi

# ══════════════════════════════════════════════════════════════════════════════
# Tests — CI order: lint → typecheck → unit → golden → e2e (playbook §1)
# ══════════════════════════════════════════════════════════════════════════════

test: test-py test-js ## Run all unit tests

# pytest config lives in apps/api/pyproject.toml ([tool.pytest.ini_options]).
# testpaths covers apps/api/tests, garh_model/tests and garh_rules/tests, so the
# API + model core + rules engine all run from that one invocation. The services
# suites (solver/drawings/render/common) reuse the same config explicitly.
test-py: ## pytest (apps/api, garh_model, garh_rules, then services/*)
	cd apps/api && pytest -q
	@if ls services/*/tests/*.py >/dev/null 2>&1; then \
	   pytest -q -c apps/api/pyproject.toml services; \
	 else \
	   echo "    (no services tests yet — skipping)"; \
	 fi

test-js: ## vitest (model core mirror, stores, units)
	$(PNPM) -r --if-present test

golden: sheet-goldens ## Golden-file suites: plans, dimension chains, DXF/SVG sheets (§16)
	cd apps/api && pytest -q -m golden
	@if ls services/*/tests/*.py >/dev/null 2>&1; then \
	   pytest -q -m golden -c apps/api/pyproject.toml services; \
	 fi
	$(PNPM) -r --if-present test:golden

e2e: ## Playwright: full happy path
	$(PNPM) --filter @garh/e2e test

e2e-smoke: ## Playwright: smoke only (login → demo → edit → undo → chip)
	$(PNPM) --filter @garh/e2e test:smoke

# ══════════════════════════════════════════════════════════════════════════════
# Lint / format / typecheck
# ══════════════════════════════════════════════════════════════════════════════

lint: lint-py lint-js ## Lint everything

lint-py: ## ruff check + ruff format --check
	cd apps/api && ruff check .
	cd apps/api && ruff format --check .
	@if ls services/**/*.py services/*.py >/dev/null 2>&1; then \
	   ruff check --config apps/api/pyproject.toml services && \
	   ruff format --check --config apps/api/pyproject.toml services; \
	 else \
	   echo "    (no python in services/ yet — skipping)"; \
	 fi

lint-js: ## eslint + prettier --check
	$(PNPM) exec eslint .
	$(PNPM) exec prettier --check .

fmt: ## Auto-format everything (ruff format + prettier --write)
	cd apps/api && ruff format . && ruff check --fix .
	$(PNPM) exec prettier --write .

fmt-check: ## Verify formatting without writing
	cd apps/api && ruff format --check .
	$(PNPM) exec prettier --check .

typecheck: typecheck-py typecheck-js ## Typecheck everything

typecheck-py: ## mypy --strict on garh_model and services/* (playbook §1)
	cd apps/api && mypy garh_api
	@if [ -d apps/api/garh_model ]; then cd apps/api && mypy --strict garh_model; \
	 else echo "    (apps/api/garh_model not present yet — skipping)"; fi
	@if ls services/**/*.py >/dev/null 2>&1; then \
	   mypy --strict --config-file apps/api/pyproject.toml services; \
	 else \
	   echo "    (no python in services/ yet — skipping)"; \
	 fi

typecheck-js: ## tsc --noEmit across every workspace package
	$(PNPM) -r --if-present typecheck

# ══════════════════════════════════════════════════════════════════════════════
# Security & supply-chain guards (§13)
#
# These three targets are the single source of truth — CI invokes them directly
# rather than reimplementing the checks in the workflow.
# ══════════════════════════════════════════════════════════════════════════════

# Secret names that must never appear in client-side code. VITE_-prefixed
# occurrences are excluded by the `(^|[^A-Za-z0-9_])` guard, so VITE_SENTRY_DSN
# is fine while a bare SENTRY_DSN is not.
SECRET_NAMES := ANTHROPIC_API_KEY|JWT_PRIVATE_KEY|JWT_PUBLIC_KEY|S3_SECRET_KEY|S3_ACCESS_KEY|POSTGRES_PASSWORD|MINIO_ROOT_PASSWORD|DATABASE_URL|REDIS_URL|RAZORPAY_KEY_SECRET|RAZORPAY_WEBHOOK_SECRET|SMTP_URL|SENTRY_DSN

env-audit: ## Fail if .env.example drifts from the two settings classes (§18)
	@echo "==> env audit: .env.example <-> Settings / WorkerSettings"
	@$(PY) scripts/check_env_drift.py

# The canvas label font is the reason this exists: a missing file under public/
# does not fail any build, and troika silently falls back to fonts.gstatic.com,
# which the production CSP then blocks. See scripts/check_web_assets.py.
asset-audit: ## Fail if apps/web hard-codes a public asset URL that is not on disk
	@echo "==> asset audit: every /public URL the web app names must exist"
	@$(PY) scripts/check_web_assets.py

secret-audit: ## Fail if a non-VITE_ secret name appears in the web source/bundle (§13)
	@echo "==> secret audit: apps/web must only read import.meta.env.VITE_*"
	@if [ ! -d apps/web ]; then echo "    apps/web not present yet — skipping"; exit 0; fi; \
	 targets="apps/web/src apps/web/index.html apps/web/public apps/web/dist"; \
	 scan=""; for t in $$targets; do [ -e "$$t" ] && scan="$$scan $$t"; done; \
	 if [ -z "$$scan" ]; then echo "    nothing to scan yet — skipping"; exit 0; fi; \
	 bad_env=$$(grep -rInE 'import\.meta\.env\.[A-Za-z_][A-Za-z0-9_]*' $$scan 2>/dev/null \
	   | grep -vE 'import\.meta\.env\.(VITE_[A-Z0-9_]+|MODE|DEV|PROD|SSR|BASE_URL|LEGACY)' \
	   || true); \
	 bad_name=$$(grep -rInE '(^|[^A-Za-z0-9_])($(SECRET_NAMES))' $$scan 2>/dev/null || true); \
	 if [ -n "$$bad_env" ] || [ -n "$$bad_name" ]; then \
	   echo ""; \
	   echo "  FAIL — secrets must not reach the browser bundle."; \
	   if [ -n "$$bad_env" ]; then \
	     echo ""; echo "  Non-VITE_ env access:"; echo "$$bad_env" | sed 's/^/    /'; fi; \
	   if [ -n "$$bad_name" ]; then \
	     echo ""; echo "  Secret variable names referenced:"; echo "$$bad_name" | sed 's/^/    /'; fi; \
	   echo ""; \
	   echo "  Fix: read it server-side and expose only what the browser may know,"; \
	   echo "  as a VITE_-prefixed value. See .env.example (CLIENT BUNDLE section)."; \
	   exit 1; \
	 fi; \
	 echo "    ok — no secret names or non-VITE_ env reads"

# Files that ARE the tenancy/persistence layer, and so are allowed to touch a
# session directly. Everything else — routers, services, jobs — must go through
# them. Keep this list tight: widening it is how the guard stops being a guard.
TENANCY_LAYER := /(repositories|repository|migrations|alembic|tests|scripts)/|(tenancy|db|repositories|repository|conftest|seed)\.py:

tenancy-audit: ## Fail if code outside the repository layer touches the DB (§13)
	@echo "==> tenancy audit: only the repository layer may touch tables"
	@if [ ! -d apps/api ]; then echo "    apps/api not present yet — skipping"; exit 0; fi; \
	 bad=$$(grep -rInE '(session|db)\.(query|execute|scalars|scalar|add|delete|merge)\(' apps/api \
	   --include='*.py' 2>/dev/null \
	   | grep -vE '$(TENANCY_LAYER)' \
	   || true); \
	 if [ -n "$$bad" ]; then \
	   echo ""; \
	   echo "  FAIL — direct table access outside the repository layer:"; \
	   echo "$$bad" | sed 's/^/    /'; \
	   echo ""; \
	   echo "  Every query must go through a repository that requires a TenantCtx,"; \
	   echo "  so firm_id scoping cannot be forgotten (§13 AuthZ)."; \
	   exit 1; \
	 fi; \
	 echo "    ok — no direct session access outside repositories"

license-check: ## Fail on GPL/AGPL/unknown licences in app dependencies (§13)
	@echo "==> licence scan: Apache/MIT/BSD/MPL only — never GPL/AGPL"
	@echo "    (python: inspects INSTALLED distributions, so run this where app"
	@echo "     deps are installed — CI, or 'make shell-api' then 'make license-check')"
	@echo "    (signature = License-Expression, else License:: classifiers, else the"
	@echo "     legacy free-text License: field — several pinned deps still use it,"
	@echo "     and treating those as UNKNOWN made this gate unpassable)"
	@$(PY) -c "import sys,re,importlib.metadata as M,os; \
	allow=set(); \
	allow.update(l.strip() for l in open('.license-allowlist.txt') if l.strip() and not l.startswith('#')) if os.path.exists('.license-allowlist.txt') else None; \
	D=re.compile(r'(?<!L)GPL|AFFERO',re.I); \
	sig=lambda m:(m['License-Expression'] or ' | '.join(c[len('License :: '):] for c in (m.get_all('Classifier') or []) if c.startswith('License ::')) or ((m['License'] or '').strip().splitlines() or [''])[0]); \
	rows=sorted({((d.metadata['Name'] or '?'), sig(d.metadata)) for d in M.distributions()}); \
	denied=[(n,l) for n,l in rows if l and D.search(l) and n not in allow]; \
	unknown=[n for n,l in rows if not l and n not in allow]; \
	[print('    DENIED   %-26s %s'%(n,l)) for n,l in denied]; \
	[print('    UNKNOWN  %s'%n) for n in unknown]; \
	print('    scanned %d python distributions: %d denied, %d unknown'%(len(rows),len(denied),len(unknown))); \
	print('    add a package to .license-allowlist.txt (one name per line) only with a DECISIONS.md entry') if (denied or unknown) else None; \
	sys.exit(1 if (denied or unknown) else 0)"
	@if command -v $(PNPM) >/dev/null 2>&1 && [ -f pnpm-lock.yaml ]; then \
	   echo "    scanning JS production dependencies"; \
	   out=$$($(PNPM) licenses list --prod --json 2>/dev/null || echo ''); \
	   if [ -z "$$out" ]; then \
	     echo "    (pnpm licenses produced no output — run 'pnpm install' first)"; \
	   else \
	     bad=$$(printf '%s' "$$out" | grep -oE '"(A?GPL|AGPL)[^"]*"' | grep -vE '"LGPL' | sort -u || true); \
	     if [ -n "$$bad" ]; then \
	       echo "    DENIED JS licences: $$bad"; exit 1; \
	     fi; \
	     echo "    ok — no GPL/AGPL in JS production dependencies"; \
	   fi; \
	 else \
	   echo "    (pnpm or pnpm-lock.yaml missing — JS half skipped)"; \
	 fi

audit: ## Dependency vulnerability audit (pnpm audit + pip-audit)
	@echo "==> pnpm audit (production dependencies)"
	-$(PNPM) audit --prod --audit-level=high
	@echo "==> pip-audit"
# There are no requirements.txt files: pins live in the two pyproject.toml
# manifests and every image extracts them with stdlib tomllib. Do the same here
# rather than auditing a file that does not exist (which the old `-` prefix
# turned into a silent pass). No leading `-`: a real CVE must fail the target.
#
# The extraction is scripts/pinned_deps.py, NOT an inline `import tomllib`: tomllib
# is 3.11+, this target runs on whatever python3 the developer has, and the inline
# version died with a bare ModuleNotFoundError traceback on 3.9/3.10. The script
# falls back to a self-checking line parser there.
	@$(PY) scripts/pinned_deps.py -o /tmp/garh-audit-requirements.txt
	pip-audit --strict --requirement /tmp/garh-audit-requirements.txt

verify: lint typecheck test golden secret-audit tenancy-audit license-check env-audit asset-audit ## Everything CI runs except e2e
	@echo ""
	@echo "  all local gates passed"

# ── Dependency-free proofs ────────────────────────────────────────────────────
# Everything under here runs on a bare interpreter — no pnpm, no Docker, no pip
# install — and `make bare` runs the lot. They are the only gates that work before
# the toolchain exists, so they are the first thing to run after any change and the
# last thing to break. When a claim matters and its only proof needs Postgres, the
# right move is to add a gate here, not to write UNVERIFIED in a doc.

rule-fixtures: ## Run all rule fixtures through the real §6 engine (stdlib only)
	@$(PY) scripts/run_rule_fixtures.py

solver-smoke: ## Execute the ortools-free half of the §5 solver (stdlib only)
	@$(PY) scripts/solver_smoke.py

# Both corpora are DERIVED from `garh_model.testing`'s fixed-id fixtures, and both
# are silently wrong if that fixture moves: the copilot mock would propose ops
# against elements no document has, and the e2e's base plan would fold into a
# different building. Neither failure shows up as a red test — the copilot would
# just start refusing everything, honestly, for a reason nobody could see. So the
# drift is a gate, and it runs on a bare interpreter like the two above.
fixture-drift: ## Re-derive the copilot + e2e fixtures and fail on drift (stdlib only)
	@$(PY) fixtures/llm/copilot-commands/_tools/generate.py --check
	@$(PY) e2e/fixtures/generate.py --check

copilot-eval: ## Run the 40-command copilot corpus through the real pipeline (stdlib only)
	@$(PY) fixtures/llm/copilot-commands/_tools/check.py

# The §13 claims — "no ops bypass validation", "model summaries exclude PII", "one
# self-correction" — were provable only under pytest, which needs a database this
# repo cannot install locally. They are the claims most expensive to get wrong, so
# they get a bare-interpreter gate of their own. Real fold, real rules engine, a
# deliberately hostile provider.
copilot-containment: ## Prove the §13 copilot containment boundary (stdlib only)
	@$(PY) scripts/copilot_containment.py

# services/render is the source of truth for presets/modes/the 8-shot pack; the API
# and the web app each keep a hand-written copy because neither can import it. Drift
# = a preset in the picker no worker can render, or eight dead jobs.
render-mirrors: ## Check the API + web render catalogue mirrors against services/render
	@$(PY) scripts/render_mirrors.py

sheet-goldens: ## Byte-diff the committed sheet corpus: 19 SVGs, 2 DXFs, dims, areas (§16)
	@$(PY) scripts/sheet_goldens.py

bare: rule-fixtures solver-smoke fixture-drift copilot-eval copilot-containment render-mirrors tenancy-audit secret-audit env-audit asset-audit ## Every gate that needs no dependencies
	@echo ""
	@echo "  all dependency-free gates passed"

# ══════════════════════════════════════════════════════════════════════════════
# Housekeeping
# ══════════════════════════════════════════════════════════════════════════════

clean: ## Remove build output and tool caches (keeps node_modules and volumes)
	@rm -rf .cache playwright-report test-results blob-report
	@find . -type d -name __pycache__ -prune -exec rm -rf {} + 2>/dev/null || true
	@find . -type d -name .pytest_cache -prune -exec rm -rf {} + 2>/dev/null || true
	@find . -type d -name .mypy_cache -prune -exec rm -rf {} + 2>/dev/null || true
	@find . -type d -name .ruff_cache -prune -exec rm -rf {} + 2>/dev/null || true
	@find . -type d -name dist -prune -not -path './node_modules/*' -exec rm -rf {} + 2>/dev/null || true
	@find . -type f -name '*.tsbuildinfo' -delete 2>/dev/null || true
	@echo "  cleaned"
