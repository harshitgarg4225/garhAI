"""Backup object-store tooling: put, get, list and prune dumps under one prefix.

The backup service (``deploy/backup/Dockerfile``, scheduled by
``deploy/railway/backup.json``) has ``pg_dump`` but no AWS CLI and no boto3 — the
API's own ``garh_api.storage`` speaks SigV4 with httpx and the stdlib, so this
script reuses it and needs only the ``S3_*`` variables every service already has.

    python scripts/backup_s3.py put  backups/garh-20260916T020000Z.dump /tmp/garh.dump
    python scripts/backup_s3.py get  backups/garh-20260916T020000Z.dump /tmp/garh.dump
    python scripts/backup_s3.py list backups/                     # newest first, JSON lines
    python scripts/backup_s3.py newest backups/ --suffix .dump    # one key, for the rehearsal
    python scripts/backup_s3.py prune backups/ --keep-days 14 --keep-min 7

``prune`` is the retention policy and it is deliberately two-sided: delete dumps
older than ``--keep-days``, but never go below ``--keep-min`` dumps however old
they are. A cron that stopped running for a month must not, on its first run
back, delete every backup it has because they are all "too old".

Exit codes: 0 ok, 1 storage refused, 2 usage. ``PYTHONPATH`` must include
``apps/api`` (the images set it; from a checkout use ``PYTHONPATH=.:apps/api``).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

from garh_api.config import get_settings
from garh_api.errors import ServiceUnavailableError
from garh_api.storage import (
    StoredObject,
    delete_object,
    get_object,
    list_objects,
    put_object,
)

DUMP_CONTENT_TYPE = "application/octet-stream"


def _row(obj: StoredObject) -> dict[str, object]:
    return {"key": obj.key, "size": obj.size, "lastModified": obj.last_modified.isoformat()}


def plan_prune(
    objects: list[StoredObject],
    *,
    keep_days: int,
    keep_min: int,
    now: datetime | None = None,
    suffix: str = ".dump",
) -> tuple[list[StoredObject], list[StoredObject]]:
    """Split a listing into (keep, delete). Pure, so the policy is unit-testable.

    Only ``suffix`` objects are candidates (a manifest beside a dump is deleted
    with its dump — see ``companions``); everything else under the prefix is left
    alone. Newest ``keep_min`` are always kept; of the rest, anything older than
    ``keep_days`` goes.
    """
    at = now or datetime.now(UTC)
    cutoff = at - timedelta(days=keep_days)
    dumps = sorted(
        (o for o in objects if o.key.endswith(suffix)), key=lambda o: o.last_modified, reverse=True
    )
    keep: list[StoredObject] = []
    delete: list[StoredObject] = []
    for index, obj in enumerate(dumps):
        if index < keep_min or obj.last_modified >= cutoff:
            keep.append(obj)
        else:
            delete.append(obj)
    return keep, delete


def companions(dump_key: str, objects: list[StoredObject], *, suffix: str = ".dump") -> list[str]:
    """Sidecar keys that belong to a dump: ``<stem>.counts.json`` beside ``<stem>.dump``."""
    stem = dump_key[: -len(suffix)] if dump_key.endswith(suffix) else dump_key
    return [o.key for o in objects if o.key != dump_key and o.key.startswith(stem + ".")]


async def _put(key: str, path: Path) -> int:
    settings = get_settings()
    data = path.read_bytes()
    await put_object(key, data, content_type=DUMP_CONTENT_TYPE, settings=settings)
    print(json.dumps({"put": key, "bytes": len(data), "bucket": settings.s3_bucket}))
    return 0


async def _get(key: str, path: Path) -> int:
    settings = get_settings()
    data = await get_object(key, settings=settings)
    path.write_bytes(data)
    print(json.dumps({"got": key, "bytes": len(data), "path": str(path)}))
    return 0


async def _list(prefix: str) -> int:
    settings = get_settings()
    objects = await list_objects(prefix, settings=settings)
    for obj in sorted(objects, key=lambda o: o.last_modified, reverse=True):
        print(json.dumps(_row(obj)))
    return 0


async def _newest(prefix: str, suffix: str) -> int:
    settings = get_settings()
    objects = [o for o in await list_objects(prefix, settings=settings) if o.key.endswith(suffix)]
    if not objects:
        print("no %s object under %s" % (suffix, prefix), file=sys.stderr)
        return 1
    newest = max(objects, key=lambda o: o.last_modified)
    print(newest.key)
    return 0


async def _prune(prefix: str, keep_days: int, keep_min: int, dry_run: bool) -> int:
    settings = get_settings()
    objects = await list_objects(prefix, settings=settings)
    keep, delete = plan_prune(objects, keep_days=keep_days, keep_min=keep_min)
    removed: list[str] = []
    for obj in delete:
        for key in (obj.key, *companions(obj.key, objects)):
            if not dry_run:
                await delete_object(key, settings=settings)
            removed.append(key)
    print(
        json.dumps(
            {
                "prefix": prefix,
                "kept": [o.key for o in keep],
                "deleted": removed,
                "dryRun": dry_run,
                "keepDays": keep_days,
                "keepMin": keep_min,
            }
        )
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    put = sub.add_parser("put", help="upload a file to a key")
    put.add_argument("key")
    put.add_argument("path", type=Path)
    get = sub.add_parser("get", help="download a key to a file")
    get.add_argument("key")
    get.add_argument("path", type=Path)
    lst = sub.add_parser("list", help="list keys under a prefix, newest first")
    lst.add_argument("prefix")
    newest = sub.add_parser("newest", help="print the newest key under a prefix")
    newest.add_argument("prefix")
    newest.add_argument("--suffix", default=".dump")
    prune = sub.add_parser("prune", help="apply the retention policy")
    prune.add_argument("prefix")
    prune.add_argument("--keep-days", type=int, default=14)
    prune.add_argument("--keep-min", type=int, default=7)
    prune.add_argument("--dry-run", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "put":
            return asyncio.run(_put(args.key, args.path))
        if args.command == "get":
            return asyncio.run(_get(args.key, args.path))
        if args.command == "list":
            return asyncio.run(_list(args.prefix))
        if args.command == "newest":
            return asyncio.run(_newest(args.prefix, args.suffix))
        if args.command == "prune":
            return asyncio.run(_prune(args.prefix, args.keep_days, args.keep_min, args.dry_run))
    except ServiceUnavailableError as exc:
        print("storage: %s" % exc, file=sys.stderr)
        return 1
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
