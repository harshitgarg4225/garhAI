#!/usr/bin/env bash
#
# Garh AI — restore rehearsal: prove the newest backup restores AND is the data.
#
#   scripts/restore_rehearsal.sh                 # newest dump under ./backups
#   scripts/restore_rehearsal.sh path/to.dump    # that dump (manifest looked up beside it)
#   scripts/restore_rehearsal.sh --from-s3       # newest dump under BACKUP_S3_PREFIX in the S3_* bucket
#
# What it does, in order:
#   1. pick the dump (and its <stem>.counts.json manifest, when there is one);
#   2. create the scratch database RESTORE_SCRATCH_DB (garh_restore_rehearsal) on
#      the same server DATABASE_URL points at, dropping any previous one;
#   3. pg_restore into it;
#   4. scripts/verify_restore.py: every table's row count against the manifest
#      (or, with RESTORE_SOURCE_URL set and no manifest, against the live source),
#      and the newest snapshot-bearing design version refolded from its op log
#      through the model engine — the state hash must equal what production's row
#      recorded;
#   5. drop the scratch database unless --keep.
#
# Environment:
#   DATABASE_URL         the server to restore INTO (a scratch db beside the source
#                        is fine — this never writes to the source database)
#   RESTORE_SCRATCH_DB   scratch database name (default garh_restore_rehearsal)
#   RESTORE_SOURCE_URL   optional live source, used only when no manifest exists
#   BACKUP_S3_PREFIX     prefix for --from-s3 (default backups/)
#   PYTHON               interpreter (default python); PYTHONPATH must cover apps/api
#
# Exit 0 only when the restore is complete and the refold matches. Anything else
# is a failed rehearsal, and a failed rehearsal is a finding, not noise.

set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON_BIN="${PYTHON:-python}"
SCRATCH="${RESTORE_SCRATCH_DB:-garh_restore_rehearsal}"
BACKUP_S3_PREFIX="${BACKUP_S3_PREFIX:-backups/}"
KEEP=0
FROM_S3=0
DUMP=""

for arg in "$@"; do
    case "$arg" in
    --keep) KEEP=1 ;;
    --from-s3) FROM_S3=1 ;;
    "") ;;
    *) DUMP="$arg" ;;
    esac
done

if [[ -z "${DATABASE_URL:-}" ]]; then
    echo "DATABASE_URL is required (the server to restore into)" >&2
    exit 2
fi
PG_URL="${DATABASE_URL/postgresql+psycopg:\/\//postgresql://}"
PG_URL="${PG_URL/postgresql+asyncpg:\/\//postgresql://}"
ADMIN_URL="${PG_URL%/*}/postgres"
SCRATCH_URL="${PG_URL%/*}/$SCRATCH"

WORK=""
cleanup() {
    # Runs on every exit. Must itself succeed: bash reports the last command's
    # status from an EXIT trap, so a failing `[[ … ]] && …` here turned a green
    # rehearsal into exit 1 on its first run.
    if [[ "$KEEP" -eq 0 ]]; then
        psql "$ADMIN_URL" -q -c "DROP DATABASE IF EXISTS \"$SCRATCH\" WITH (FORCE)" >/dev/null 2>&1 || true
    else
        echo "kept scratch database $SCRATCH ($SCRATCH_URL)"
    fi
    if [[ -n "$WORK" ]]; then
        rm -rf "$WORK"
    fi
    return 0
}
trap cleanup EXIT

# 1. the dump and its manifest
if [[ "$FROM_S3" -eq 1 ]]; then
    WORK="$(mktemp -d)"
    KEY="$("$PYTHON_BIN" "$HERE/backup_s3.py" newest "$BACKUP_S3_PREFIX" --suffix .dump)"
    DUMP="$WORK/$(basename "$KEY")"
    "$PYTHON_BIN" "$HERE/backup_s3.py" get "$KEY" "$DUMP"
    if "$PYTHON_BIN" "$HERE/backup_s3.py" get "${KEY%.dump}.counts.json" "${DUMP%.dump}.counts.json" 2>/dev/null; then
        :
    fi
elif [[ -z "$DUMP" ]]; then
    DUMP="$(ls -1t backups/garh-*.dump 2>/dev/null | head -1 || true)"
fi
if [[ -z "$DUMP" || ! -f "$DUMP" ]]; then
    echo "no dump found — run 'scripts/backup_db.sh backup' first, pass a dump path, or --from-s3" >&2
    exit 2
fi
MANIFEST="${DUMP%.dump}.counts.json"
echo "rehearsal: dump $DUMP"
if [[ -f "$MANIFEST" ]]; then
    echo "rehearsal: manifest $MANIFEST"
else
    echo "rehearsal: no manifest beside the dump${RESTORE_SOURCE_URL:+ — comparing against the live source}"
fi

# 2. scratch database
psql "$ADMIN_URL" -v ON_ERROR_STOP=1 -q \
    -c "DROP DATABASE IF EXISTS \"$SCRATCH\" WITH (FORCE)" \
    -c "CREATE DATABASE \"$SCRATCH\""
echo "rehearsal: created $SCRATCH"

# 3. restore. --exit-on-error: a partially restored database must not pass.
pg_restore --no-owner --no-privileges --exit-on-error --dbname="$SCRATCH_URL" "$DUMP"
echo "rehearsal: pg_restore OK"

# 4. verify — row counts and the refold
VERIFY_ARGS=(--database-url "$SCRATCH_URL" --require-project)
if [[ -f "$MANIFEST" ]]; then
    VERIFY_ARGS+=(--manifest "$MANIFEST")
elif [[ -n "${RESTORE_SOURCE_URL:-}" ]]; then
    VERIFY_ARGS+=(--source-url "$RESTORE_SOURCE_URL")
fi
"$PYTHON_BIN" "$HERE/verify_restore.py" "${VERIFY_ARGS[@]}"
echo "rehearsal OK — $DUMP restores completely and its newest design refolds to the recorded hash"
