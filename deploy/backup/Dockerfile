# syntax=docker/dockerfile:1.7
#
# Garh AI — the scheduled backup service (docs/ops-runbook.md "Backups").
#
# Railway runs this image on a cron schedule (deploy/railway/backup.json):
#     scripts/backup_db.sh backup-s3
# pg_dump → the S3_* bucket under backups/, with a row-count manifest beside each
# dump and a two-sided retention pass (BACKUP_RETENTION_DAYS, BACKUP_KEEP_MIN).
# The same image runs the restore rehearsal (`scripts/restore_rehearsal.sh
# --from-s3`) so the tool that made the dump is the tool that proves it.
#
# Build context is the REPO ROOT, like the other images:
#     docker build -f deploy/backup/Dockerfile .
#
# Two things the api image lacks and this one needs:
#   1. postgresql-client from PGDG, not Debian's — bookworm ships pg_dump 15 and it
#      refuses a 16/17 server ("server version mismatch"). Pinned to a major so a
#      Railway Postgres upgrade is a one-line change here, not a silent failure at
#      02:00.
#   2. the api's Python dependency set, because scripts/backup_s3.py signs S3
#      requests through garh_api.storage and scripts/verify_restore.py refolds a
#      design through garh_model. Same tomllib extraction as apps/api/Dockerfile,
#      same reason (docs/deployment.md).

FROM python:3.11-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_ROOT_USER_ACTION=ignore

# Must be >= the server's major. Railway's managed Postgres is 16 or 17 today.
ARG PG_MAJOR=17

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl gnupg \
    && curl -fsSL https://www.postgresql.org/media/keys/ACCC4CF8.asc \
        | gpg --dearmor -o /usr/share/keyrings/postgresql.gpg \
    && echo "deb [signed-by=/usr/share/keyrings/postgresql.gpg] https://apt.postgresql.org/pub/repos/apt bookworm-pgdg main" \
        > /etc/apt/sources.list.d/pgdg.list \
    && apt-get update \
    && apt-get install -y --no-install-recommends "postgresql-client-${PG_MAJOR}" \
    && apt-get purge -y gnupg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY ["apps/api/pyproject.toml", "/app/apps/api/pyproject.toml"]
RUN python -c "import tomllib;\
api = tomllib.load(open('/app/apps/api/pyproject.toml','rb'));\
print('\n'.join(api['project']['dependencies']))" > /tmp/requirements.txt \
    && python -m pip install --upgrade pip \
    && python -m pip install -r /tmp/requirements.txt

COPY ["apps/api", "/app/apps/api"]
COPY ["packages/model", "/app/packages/model"]
COPY ["scripts/backup_db.sh", "scripts/backup_s3.py", "scripts/restore_rehearsal.sh", "scripts/verify_restore.py", "/app/scripts/"]

ENV PYTHONPATH=/app:/app/apps/api \
    PYTHON=python

RUN chmod +x /app/scripts/backup_db.sh /app/scripts/restore_rehearsal.sh \
    && useradd --create-home --uid 10001 --shell /usr/sbin/nologin garh \
    && chown -R garh:garh /app
USER garh

# The cron service's command; deploy/railway/backup.json repeats it as startCommand.
CMD ["/app/scripts/backup_db.sh", "backup-s3"]
