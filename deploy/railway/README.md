# Railway config-as-code

One file per service. Railway reads a service's config file from the path set under
**Service → Settings → Config-as-code → Railway config file**; set it to the file
below for each service, and the dashboard's build/deploy settings stop being the
source of truth — this directory is. `apps/api/tests/test_deploy_config.py` reads
every file here, `docker-compose.yml` and the runbook's service table, and fails when
they disagree, so the documented start command is the deployed one.

| Service           | Config file            | Image                      | Start command                                                      |
| ----------------- | ---------------------- | -------------------------- | ------------------------------------------------------------------ |
| `api`             | `api.json`             | `apps/api/Dockerfile`      | `python -m garh_api.migrate --seed && exec uvicorn …`              |
| `web`             | `web.json`             | `apps/web/Dockerfile`      | image `CMD` (nginx, `prod-railway` stage)                          |
| `worker-solver`   | `worker-solver.json`   | `services/Dockerfile`      | `python -m services.solver.worker`                                 |
| `worker-render`   | `worker-render.json`   | `services/Dockerfile`      | `python -m services.render.worker`                                 |
| `worker-drawings` | `worker-drawings.json` | `services/Dockerfile`      | `python -m services.drawings.worker`                               |
| `backup`          | `backup.json`          | `deploy/backup/Dockerfile` | `/app/scripts/backup_db.sh backup-s3` on `30 20 * * *` (02:00 IST) |

Railway builds the **last stage** of a Dockerfile — `prod` for the api and workers,
`prod-railway` for the web (the same-origin `/api` proxy). There is no `--target`;
the stage order in each Dockerfile is load-bearing.

The api's start command is safe with `numReplicas > 1`: `garh_api.migrate` takes a
Postgres advisory lock before `alembic upgrade head` (so does a bare
`alembic upgrade head`, the lock lives in `migrations/env.py`), and `--seed` writes
nothing once the demo firm exists. Raise `numReplicas` in `api.json` when the
load says so; nothing else changes.

The `backup` service is a **cron service**: `cronSchedule` in its file makes Railway
run the start command on the schedule and stop the container when it exits. Give it
the same `DATABASE_URL` and `S3_*` variables as the api (reference them from the
Postgres and bucket services), plus the `BACKUP_*` knobs from `.env.example` if the
defaults (14 days, never below 7 dumps, `backups/`) are not right.

Region: the api, workers and Postgres run in `us-west2` today, while spec §15 and
`docs/deployment.md` claim India residency for inference and object storage. That
is an owner decision (move the project to `asia-southeast1`, the nearest Railway
region, or amend the claim) — a config file cannot make it and this README does not
pretend otherwise.
