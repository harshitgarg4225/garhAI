#!/usr/bin/env bash
#
# Garh AI — Postgres backup, to disk or to the object store, with a manifest.
#
# A backup that has never been restored is a hope, not a backup. This script
# writes the dump AND the evidence a restore is checked against; the checking is
# scripts/restore_rehearsal.sh, which `rehearse` here simply calls.
#
#   backup [out-dir]     pg_dump (custom format) + <stem>.counts.json beside it;
#                        keeps the newest BACKUP_KEEP_MIN (14) dumps in out-dir.
#   backup-s3            the same pair uploaded to the S3_* bucket under
#                        BACKUP_S3_PREFIX (backups/), then the retention pass:
#                        dumps older than BACKUP_RETENTION_DAYS (14) are deleted,
#                        but never below BACKUP_KEEP_MIN (7). This is what the
#                        scheduled backup service runs (deploy/railway/backup.json).
#   rehearse [dump]      restore the newest dump (or the one given) into a scratch
#                        database and verify row counts + a refolded state hash —
#                        scripts/restore_rehearsal.sh, in full.
#
# The manifest is `count(*)` per table taken in the same transaction snapshot as
# the dump would be — close enough on a quiet database, and exact by construction
# for the row-count comparison the rehearsal does, because the rehearsal compares
# the RESTORE against THIS file, not against a moving source.
#
# Needs: pg_dump/psql (postgresql-client, same major as the server or newer),
# python with PYTHONPATH covering apps/api (for backup-s3 and the rehearsal).
# Keep dumps OFF the app containers' ephemeral disks: `backup-s3` is the point.

set -euo pipefail

CMD="${1:-backup}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON_BIN="${PYTHON:-python}"

if [[ -z "${DATABASE_URL:-}" ]]; then
    echo "DATABASE_URL is required (postgresql://user:pass@host:port/db)" >&2
    exit 2
fi

# pg_dump does not understand SQLAlchemy's dialect suffixes.
PG_URL="${DATABASE_URL/postgresql+psycopg:\/\//postgresql://}"
PG_URL="${PG_URL/postgresql+asyncpg:\/\//postgresql://}"

BACKUP_KEEP_MIN="${BACKUP_KEEP_MIN:-7}"
BACKUP_RETENTION_DAYS="${BACKUP_RETENTION_DAYS:-14}"
BACKUP_S3_PREFIX="${BACKUP_S3_PREFIX:-backups/}"

write_manifest() {
    # $1 = manifest path. One JSON document: {"takenAt": ..., "tables": {name: count}}.
    local out="$1"
    psql "$PG_URL" -tA -v ON_ERROR_STOP=1 -c "
        SELECT json_build_object(
            'takenAt', to_char(now() AT TIME ZONE 'UTC', 'YYYY-MM-DD\"T\"HH24:MI:SS\"Z\"'),
            'database', current_database(),
            'tables', (
                SELECT json_object_agg(t.table_name, t.n ORDER BY t.table_name)
                FROM (
                    SELECT c.relname AS table_name,
                           (xpath('/row/cnt/text()',
                                  query_to_xml(format('select count(*) as cnt from %I.%I', n.nspname, c.relname),
                                               false, true, '')))[1]::text::bigint AS n
                    FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
                    WHERE n.nspname = 'public' AND c.relkind = 'r'
                ) t
            )
        )" > "$out"
    # A manifest that is not JSON is worse than none: the rehearsal would compare
    # against nothing and report OK.
    "$PYTHON_BIN" -c "import json,sys; d=json.load(open(sys.argv[1])); assert d['tables'], 'empty manifest'" "$out"
}

dump_to() {
    # $1 = dump path, $2 = manifest path. The manifest is taken right after the dump
    # starts its snapshot; on a quiet database the two agree exactly.
    pg_dump --format=custom --no-owner --no-privileges --file="$1" "$PG_URL"
    write_manifest "$2"
}

case "$CMD" in
backup)
    OUT_DIR="${2:-backups}"
    mkdir -p "$OUT_DIR"
    STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
    STEM="$OUT_DIR/garh-$STAMP"
    dump_to "$STEM.dump" "$STEM.counts.json"
    SIZE="$(du -h "$STEM.dump" | cut -f1)"
    echo "wrote $STEM.dump ($SIZE) and $STEM.counts.json"
    # Keep the newest BACKUP_KEEP_MIN pairs; a runaway cron must not fill the disk.
    ls -1t "$OUT_DIR"/garh-*.dump 2>/dev/null | tail -n +"$((BACKUP_KEEP_MIN + 1))" | while read -r old; do
        rm -v "$old" "${old%.dump}.counts.json" 2>/dev/null || true
    done
    ;;

backup-s3)
    STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
    WORK="$(mktemp -d)"
    trap 'rm -rf "$WORK"' EXIT
    STEM="$WORK/garh-$STAMP"
    dump_to "$STEM.dump" "$STEM.counts.json"
    SIZE="$(du -h "$STEM.dump" | cut -f1)"
    echo "dumped $STEM.dump ($SIZE)"
    "$PYTHON_BIN" "$HERE/backup_s3.py" put "${BACKUP_S3_PREFIX}garh-$STAMP.dump" "$STEM.dump"
    "$PYTHON_BIN" "$HERE/backup_s3.py" put "${BACKUP_S3_PREFIX}garh-$STAMP.counts.json" "$STEM.counts.json"
    "$PYTHON_BIN" "$HERE/backup_s3.py" prune "$BACKUP_S3_PREFIX" \
        --keep-days "$BACKUP_RETENTION_DAYS" --keep-min "$BACKUP_KEEP_MIN"
    echo "backup-s3 OK — ${BACKUP_S3_PREFIX}garh-$STAMP.dump"
    ;;

rehearse)
    exec "$HERE/restore_rehearsal.sh" "${2:-}"
    ;;

*)
    echo "usage: $0 backup [out-dir] | backup-s3 | rehearse [dump-file]" >&2
    exit 2
    ;;
esac
