#!/usr/bin/env bash
#
# Garh AI — Postgres backup and restore rehearsal.
#
# A backup that has never been restored is a hope, not a backup. This script
# does both: `backup` writes a compressed custom-format dump, and `rehearse`
# proves the latest dump actually restores into a scratch database and that the
# restored schema contains the tables the product cannot live without.
#
# Usage:
#   DATABASE_URL=postgresql://user:pass@host:5432/garh scripts/backup_db.sh backup [out-dir]
#   DATABASE_URL=...                                   scripts/backup_db.sh rehearse [dump-file]
#
# On Railway, run `backup` from a scheduled job (railway run) or any host that
# can reach the database's private domain; keep the dumps OFF the app
# container's ephemeral disk (upload to object storage or download them).
# Railway's own Postgres backups, when enabled on the plan, complement — not
# replace — an owned, restore-rehearsed dump.

set -euo pipefail

CMD="${1:-backup}"

if [[ -z "${DATABASE_URL:-}" ]]; then
    echo "DATABASE_URL is required (postgresql://user:pass@host:port/db)" >&2
    exit 2
fi

# pg_dump does not understand SQLAlchemy's +psycopg dialect suffix.
PG_URL="${DATABASE_URL/postgresql+psycopg:\/\//postgresql://}"

case "$CMD" in
backup)
    OUT_DIR="${2:-backups}"
    mkdir -p "$OUT_DIR"
    STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
    OUT="$OUT_DIR/garh-$STAMP.dump"
    # Custom format: compressed, and restorable table-by-table with pg_restore.
    pg_dump --format=custom --no-owner --no-privileges --file="$OUT" "$PG_URL"
    SIZE="$(du -h "$OUT" | cut -f1)"
    echo "wrote $OUT ($SIZE)"
    # Keep the newest 14 dumps; a runaway cron must not fill the disk.
    ls -1t "$OUT_DIR"/garh-*.dump 2>/dev/null | tail -n +15 | xargs -r rm -v
    ;;

rehearse)
    DUMP="${2:-$(ls -1t backups/garh-*.dump 2>/dev/null | head -1)}"
    if [[ -z "$DUMP" || ! -f "$DUMP" ]]; then
        echo "no dump found — run 'backup' first or pass a dump path" >&2
        exit 2
    fi
    # Restore into a scratch DB on the same server, then interrogate it.
    ADMIN_URL="${PG_URL%/*}/postgres"
    SCRATCH="garh_restore_rehearsal"
    psql "$ADMIN_URL" -v ON_ERROR_STOP=1 -q \
        -c "DROP DATABASE IF EXISTS $SCRATCH" \
        -c "CREATE DATABASE $SCRATCH"
    pg_restore --no-owner --no-privileges --dbname="${PG_URL%/*}/$SCRATCH" "$DUMP"
    # The tables the product cannot live without. Empty is fine (a fresh
    # environment); MISSING is a failed rehearsal.
    MISSING="$(psql "${PG_URL%/*}/$SCRATCH" -tA -c "
        SELECT string_agg(want, ', ')
        FROM unnest(ARRAY['firms','users','projects','ops']) AS want
        WHERE to_regclass('public.' || want) IS NULL")"
    psql "$ADMIN_URL" -q -c "DROP DATABASE $SCRATCH"
    if [[ -n "$MISSING" ]]; then
        echo "REHEARSAL FAILED — restored dump is missing: $MISSING" >&2
        exit 1
    fi
    echo "rehearsal OK — $DUMP restores cleanly and contains the core tables"
    ;;

*)
    echo "usage: $0 backup [out-dir] | rehearse [dump-file]" >&2
    exit 2
    ;;
esac
