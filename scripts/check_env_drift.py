#!/usr/bin/env python3
"""Guard: every name in ``.env.example`` is read by something, and every settings
field is documented in ``.env.example``.

Why this exists
---------------
Both settings classes use ``extra="ignore"`` (pydantic-settings). That is the right
choice — a stray variable must not stop the API booting — but it means a *renamed*
or *misspelled* variable is silently dropped and the field quietly keeps its
development default. That failure mode already bit this repo twice:

* ``.env.example`` shipped ``S3_ENDPOINT``/``S3_KEY`` while ``config.py`` declared
  ``S3_ENDPOINT_URL``/``S3_ACCESS_KEY_ID`` — object storage would have pointed at
  the dev default in every environment that copied the example file.
* ``docker-compose.yml`` set ``RULEPACK_DIR`` while the engine read only
  ``GARH_RULEPACK_DIR``; it worked purely because the fallback filesystem walk
  happened to find ``/app/rulepacks``.

So: fail the build on drift rather than discover it in staging.

What counts as "read"
---------------------
1. A field (or explicit alias) on ``garh_api.config.Settings`` — matched
   case-insensitively, because pydantic-settings maps ``S3_BUCKET`` to ``s3_bucket``.
2. A field (or alias) on ``services.common.config.WorkerSettings``.
3. A literal ``os.environ[...]`` / ``os.environ.get(...)`` / ``os.getenv(...)`` read
   anywhere under ``apps/`` or ``services/``.
4. A name in :data:`INFRASTRUCTURE`, which is the explicit allowlist for variables
   consumed by ``docker-compose.yml``, the container entrypoints, or host tooling
   rather than by application code. Every entry carries a reason.

Anything else is drift and the script exits non-zero, naming the variable.

Deliberately *not* enforced: the reverse direction for infrastructure names, and
comment-only lines. A variable may also be annotated ``NOT READ`` in
``.env.example`` — that is an accepted, documented placeholder and passes.

Usage::

    python3 scripts/check_env_drift.py            # from the repo root
    make env-audit
"""

from __future__ import annotations

import os
import re
import sys
from typing import Dict, Iterable, List, Set, Tuple

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

ENV_EXAMPLE = os.path.join(REPO_ROOT, ".env.example")
API_CONFIG = os.path.join(REPO_ROOT, "apps", "api", "garh_api", "config.py")
WORKER_CONFIG = os.path.join(REPO_ROOT, "services", "common", "config.py")
CODE_ROOTS = ("apps", "services", "packages", "e2e")

#: Variables consumed by infrastructure, not by application code. Each needs a
#: reason — "it's just infra" is how this list becomes a dumping ground.
INFRASTRUCTURE: Dict[str, str] = {
    "COMPOSE_PROJECT_NAME": "docker compose project name (the dir name 'Garh AI' is illegal)",
    "POSTGRES_USER": "postgres:15-alpine image entrypoint",
    "POSTGRES_PASSWORD": "postgres:15-alpine image entrypoint",
    "POSTGRES_DB": "postgres:15-alpine image entrypoint",
    "POSTGRES_PORT": "host port publish in docker-compose.yml",
    "REDIS_PORT": "host port publish in docker-compose.yml",
    "API_PORT": "host port publish in docker-compose.yml",
    "WEB_PORT": "host port publish in docker-compose.yml",
    "VITE_HMR_PORT": "host port publish + vite.config.ts server.hmr.port",
    "MINIO_ROOT_USER": "minio/minio image entrypoint",
    "MINIO_ROOT_PASSWORD": "minio/minio image entrypoint",
    "MINIO_PORT": "host port publish in docker-compose.yml",
    "MINIO_CONSOLE_PORT": "host port publish in docker-compose.yml",
    "API_MIGRATE_ON_BOOT": "read by the api service command in docker-compose.yml",
    "HF_HOME": "huggingface cache dir, exported into the worker image (services/Dockerfile)",
    "WORKER_SOLVER_CONCURRENCY": "docker-compose.yml -> WORKER_CONCURRENCY for worker-solver",
    "WORKER_RENDER_CONCURRENCY": "docker-compose.yml -> WORKER_CONCURRENCY for worker-render",
    "WORKER_DRAWINGS_CONCURRENCY": "docker-compose.yml -> WORKER_CONCURRENCY for worker-drawings",
    "DATABASE_URL_HOST": "host tooling only (psql, host-run pytest); documented as such",
    "REDIS_URL_HOST": "host tooling only (redis-cli); documented as such",
    "HOST_UID": "bind-mount ownership on Linux",
    "HOST_GID": "bind-mount ownership on Linux",
    "WEB_HMR_PORT": "host-side alias, mapped to VITE_HMR_PORT by docker-compose.yml",
    "WORKER_NAME": "set per worker service in docker-compose.yml, not in .env",
    "WORKER_QUEUE": "set per worker service in docker-compose.yml, not in .env",
    "WORKER_CONCURRENCY": "set per worker service from WORKER_<ROLE>_CONCURRENCY",
}

#: Fields declared with AliasChoices: documenting ANY member documents the field.
#: `Settings.env` accepts ENV or APP_ENV; .env.example uses APP_ENV (ENV is too
#: generic to put in a shared .env, and `local` is not a valid value — see D-note).
ALIAS_GROUPS: Dict[str, str] = {"ENV": "APP_ENV"}

ENV_LINE = re.compile(r"^\s*([A-Z][A-Z0-9_]*)\s*=(.*)$")
DIRECT_READ = re.compile(
    r"""(?:os\.environ\.get\(|os\.getenv\(|os\.environ\[)\s*["']([A-Z][A-Z0-9_]*)["']"""
)
#: The SAME read, but through a module-level constant:
#:
#:     DEV_ECHO_OTP_ENV: Final = "DEV_ECHO_OTP"
#:     ...
#:     os.environ.get(DEV_ECHO_OTP_ENV)
#:
#: That is the better style — the name is exported so tests and docs cannot
#: misspell it — and the literal-only regex above could not see it, so this script
#: reported `DEV_ECHO_OTP` as documented-but-unread. Two halves: find the constant
#: names actually passed to an environ read, then resolve each to its literal.
INDIRECT_READ = re.compile(
    r"""(?:os\.environ\.get\(|os\.getenv\(|os\.environ\[)\s*([A-Z][A-Z0-9_]*)\s*[,)\]]"""
)
CONST_LITERAL = re.compile(
    r"""^([A-Z][A-Z0-9_]*)\s*(?::\s*[^=\n]+)?=\s*["']([A-Z][A-Z0-9_]*)["']\s*$""", re.M
)
FIELD = re.compile(r"^\s{4}([a-z][a-z0-9_]*)\s*:", re.M)
#: Client-side reads go through Vite's compile-time substitution, not os.environ.
VITE_READ = re.compile(r"import\.meta\.env\.([A-Z][A-Z0-9_]*)")
#: Only fields declared inside the settings class count — a local variable at the
#: same indentation inside a method would otherwise look like a field (`known`).
SETTINGS_CLASS = re.compile(r"^class \w*Settings\(BaseSettings\):$(.*?)(?=^\S)", re.M | re.S)
ALIAS = re.compile(r"""(?:validation_alias|alias)\s*=\s*["']([A-Za-z][A-Za-z0-9_]*)["']""")


def _read(path: str) -> str:
    with open(path, "r", encoding="utf-8") as handle:
        return handle.read()


def _settings_names(path: str) -> Set[str]:
    """Field names and explicit aliases of a pydantic-settings class, upper-cased."""
    source = _read(path)
    body = "".join(SETTINGS_CLASS.findall(source)) or source
    names = {name.upper() for name in FIELD.findall(body)}
    names |= {name.upper() for name in ALIAS.findall(body)}
    return names


def _direct_reads() -> Set[str]:
    found: Set[str] = set()
    for root_name in CODE_ROOTS:
        root_dir = os.path.join(REPO_ROOT, root_name)
        for base, dirs, files in os.walk(root_dir):
            dirs[:] = [d for d in dirs if d not in ("node_modules", "__pycache__", ".venv")]
            for name in files:
                if not name.endswith((".py", ".ts", ".tsx")):
                    continue
                try:
                    text = _read(os.path.join(base, name))
                except OSError:
                    continue
                found |= set(DIRECT_READ.findall(text))
                # A read through a module-level constant is still a read. Resolve
                # only constants defined in the SAME file: cross-module resolution
                # would need a real import graph, and a name worth exporting is a
                # name worth defining beside its use.
                via_const = set(INDIRECT_READ.findall(text))
                if via_const:
                    literals = dict(CONST_LITERAL.findall(text))
                    found |= {literals[c] for c in via_const if c in literals}
                # `import.meta.env.VITE_X` is the audited client channel (§13):
                # Vite substitutes it at build time, so it never reaches os.environ.
                found |= set(VITE_READ.findall(text))
    return found


def _env_example_entries() -> List[Tuple[str, str]]:
    entries: List[Tuple[str, str]] = []
    for line in _read(ENV_EXAMPLE).splitlines():
        if line.lstrip().startswith("#"):
            continue
        match = ENV_LINE.match(line)
        if match:
            name, value = match.group(1), match.group(2)
            # python-dotenv keeps a trailing comment as the VALUE when the value
            # itself is empty — `SENTRY_DSN=   # blank = off` parses as the
            # comment text, which crashed both workers the first time
            # `docker compose up` copied this file to `.env` (CI run 8,
            # 2026-08-27). Comments on empty-valued lines go on their own line.
            if value.strip().startswith("#"):
                raise SystemExit(
                    "env-audit: %s in .env.example has an inline comment on an "
                    "empty value; python-dotenv reads the comment AS the value. "
                    "Move the comment to its own line." % name
                )
            entries.append((name, value))
    return entries


def _flag_names() -> Set[str]:
    """``FLAG_*`` lines are documentation of the seeded `flags` table, not env input.

    The header comment above them in ``.env.example`` says so explicitly. They are
    accepted here so the block can stay greppable without lying about being read.
    """
    return {name for name, _ in _env_example_entries() if name.startswith("FLAG_")}


def main(argv: Iterable[str] = ()) -> int:
    del argv
    entries = _env_example_entries()
    documented = {name for name, _ in entries}
    raw = _read(ENV_EXAMPLE)

    api_names = _settings_names(API_CONFIG)
    worker_names = _settings_names(WORKER_CONFIG)
    direct = _direct_reads()
    flags = _flag_names()

    read_by_code = api_names | worker_names | direct

    # A line may be annotated "NOT READ" — an accepted, explained placeholder.
    # The annotation counts on the assignment line itself OR on the comment
    # line directly above it (empty-valued lines cannot carry inline comments:
    # python-dotenv would read the comment as the value — see
    # _env_example_entries).
    annotated_not_read: Set[str] = set()
    previous = ""
    for line in raw.splitlines():
        match = ENV_LINE.match(line)
        if match and (
            "NOT READ" in line.upper()
            or (previous.lstrip().startswith("#") and "NOT READ" in previous.upper())
        ):
            annotated_not_read.add(match.group(1))
        previous = line

    unexplained = sorted(
        name
        for name in documented
        if name not in read_by_code
        and name not in INFRASTRUCTURE
        and name not in flags
        and name not in annotated_not_read
    )

    # Reverse direction: a settings field with no line in .env.example is a field
    # nobody knows exists. Only the API + worker classes are checked; their fields
    # are the app's whole configuration surface (§18).
    undocumented = sorted(
        name
        for name in (api_names | worker_names)
        if name not in documented
        # An alias sibling being documented is enough.
        and ALIAS_GROUPS.get(name, name) not in documented
        # Per-service infrastructure values are not .env material.
        and name not in INFRASTRUCTURE
    )

    ok = True
    if unexplained:
        ok = False
        print("FAIL  .env.example names that nothing reads:")
        for name in unexplained:
            print("        %s" % name)
        print(
            "      Fix one of: add the field to config.py, add the name to\n"
            "      INFRASTRUCTURE in this script with a reason, annotate the line\n"
            "      'NOT READ YET — <why>', or delete it."
        )
    if undocumented:
        ok = False
        print("FAIL  settings fields with no line in .env.example:")
        for name in undocumented:
            print("        %s" % name)
        print("      Every knob must be discoverable from .env.example (§18).")

    print(
        "env-audit: %d documented names, %d settings fields, %d direct os.environ reads"
        % (len(documented), len(api_names | worker_names), len(direct))
    )
    if ok:
        print("env-audit: ok — no drift between .env.example and the settings classes")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
