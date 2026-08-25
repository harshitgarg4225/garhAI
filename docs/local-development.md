# Local development

## First run

```bash
cp .env.example .env      # or: make env
docker compose up         # or: make up
make seed                 # demo firm + demo project (§17)
```

No keys, no GPU, no secrets. Providers default to mocks, a dev JWT keypair is minted
on first boot, migrations run before the API serves, and the MinIO bucket is created
by a one-shot init container.

| Service | URL |
|---|---|
| Web | http://localhost:5173 |
| API | http://localhost:8000 (`/healthz`, `/docs`) |
| MinIO console | http://localhost:9001 |
| Postgres | `localhost:5432` |
| Redis | `localhost:6379` |

## Everyday commands

```bash
make            # list every target
make logs       # tail everything
make logs-api   # just the API
make ps         # status + health
make migrate    # alembic upgrade head
make psql       # psql shell
make shell-api  # bash in the api container
make verify     # every gate CI runs except e2e
make down       # stop, keep data
make reset      # DESTRUCTIVE: delete volumes too
```

Hot reload works in both directions: the API bind-mounts `apps/api`, `services`,
`packages/model`, `rulepacks` and `fixtures` and runs uvicorn `--reload`; the web
container bind-mounts the source and runs the Vite dev server. Edit on the host,
both reload.

Workers do **not** auto-reload. After changing solver or drawings code:

```bash
docker compose restart worker-solver
```

## Running things outside Docker

Useful when you want a debugger or a fast test loop. Point the host-side tooling at
the containers' published ports — `.env` carries `DATABASE_URL_HOST` and
`REDIS_URL_HOST` for exactly this.

```bash
docker compose up -d postgres redis minio minio-init

make dev-keys                 # stable keypair in .keys/ + .env
export $(grep -v '^#' .env | grep -v '^$' | xargs)   # careful: see note below
export DATABASE_URL="$DATABASE_URL_HOST"
export REDIS_URL="$REDIS_URL_HOST"

cd apps/api && uvicorn garh_api.main:app --reload
```

> The `export $(...)` line is a convenience, not a recommendation — it breaks on
> values containing spaces, which includes the `\n`-escaped PEM keys. Prefer letting
> `pydantic-settings` read `.env` itself: it looks for `.env` and `../../.env`, so
> running from `apps/api` finds the repo-root file with no exporting at all.

Tests:

```bash
make test-py     # pytest — runs from apps/api so it finds the pytest config
make test-js     # vitest
make golden      # golden files
```

---

## Troubleshooting

### `docker compose up` fails immediately with an invalid project name

The checkout directory is `Garh AI`, and Compose can't derive a legal project name
from it (space, capitals). `docker-compose.yml` sets `name: garh-ai`, so this should
not happen — if it does, you're running an old copy of the file or a Compose older
than v2.24. Check `docker compose version`.

### `env_file` errors, or `required` is not recognised

`env_file: [{path: .env, required: false}]` needs **Compose v2.24+**. Upgrade, or
just make sure `.env` exists (`make env`).

### Web app shows a blank page and the console mentions HMR / websocket

The HMR websocket is published on its own port (24678) because the browser connects
to it directly from the host rather than through Vite. Confirm `WEB_HMR_PORT` is
free and matches `VITE_HMR_PORT`, and that `apps/web/vite.config.ts` sets
`server.hmr.port` from it.

### Edits don't trigger a rebuild

Bind-mounted filesystems on macOS and Windows don't deliver inotify events. The web
container sets `CHOKIDAR_USEPOLLING=true` for this reason. If you added a new watched
directory, make sure it's actually bind-mounted in `docker-compose.yml` — a directory
that isn't mounted simply doesn't exist inside the container.

### A setting in `.env` seems to have no effect

Almost always a **name mismatch**. `apps/api/garh_api/config.py` uses
`pydantic-settings` with `extra="ignore"`, so an unrecognised variable is silently
discarded and the field keeps its default — no error, no warning. Check the field
name in `config.py` against your variable. See [environment.md](./environment.md).

### The API won't start: `ConfigError` listing missing secrets

You're running with `APP_ENV` set to something other than `dev`. Outside `dev`,
`config.py` refuses to boot while `JWT_PRIVATE_KEY`, `DATABASE_URL`, `REDIS_URL`,
the S3 credentials or `APP_URL` are still on their local defaults. That's deliberate:
it fails at boot rather than mid-request. For local work, `APP_ENV=dev`.

Note also that `APP_ENV` is a validated literal — `dev | test | staging | prod`.
`local` is not valid and will fail validation.

### JWT errors after switching between host and container runs

The container mints its own throwaway keypair when `JWT_PRIVATE_KEY` is empty, so a
token signed on the host won't verify inside the stack. Fix it by pinning one
keypair for both:

```bash
make dev-keys      # writes .keys/*.pem and sets the two vars in .env
make restart
```

### `make license-check` reports UNKNOWN packages locally

It inspects **installed** distributions, so outside a venv with the app's
dependencies it's scanning whatever your system Python has. Run it where the deps
live:

```bash
make shell-api
make license-check
```

Genuinely unlicensed-but-fine packages go in `.license-allowlist.txt` (one name per
line) — and per `DECISIONS.md`, adding a name there requires a row in that file.

### `make tenancy-audit` flags a file that legitimately owns persistence

Add it to `TENANCY_LAYER` in the `Makefile` — and keep that list tight. Widening it
casually is how the guard stops being a guard.

### Postgres data is in a weird state

```bash
make reset      # deletes volumes: db, minio, jwt keys
make up
make seed
```

### Ports already in use

Every published port is an env var: `API_PORT`, `WEB_PORT`, `WEB_HMR_PORT`,
`POSTGRES_PORT`, `REDIS_PORT`, `MINIO_PORT`, `MINIO_CONSOLE_PORT`. Change them in
`.env` — container-internal ports stay fixed, so nothing else needs touching.
