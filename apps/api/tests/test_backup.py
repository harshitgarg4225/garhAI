"""Backups you can trust: the object-store tooling, the retention policy, and the
restore verifier — each with the run that would break it.

* ``garh_api.storage.list_objects`` / ``get_object`` round-trip against the local
  object store (the same SigV4 path the API signs downloads with);
* ``plan_prune`` keeps the newest ``keep_min`` dumps however old, deletes the rest
  past ``keep_days``, and never touches a non-dump key;
* ``verify_restore.compare_counts`` fails on a missing table and on a count that
  moved; ``fold_checks`` refolds a seeded project to its recorded hash and FAILS
  when one op's payload is altered underneath it (the negative control that a
  schema-only check could never have);
* the backup image and its cron entry name the pieces the runbook promises.
"""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import uuid
from datetime import UTC, datetime, timedelta
from types import ModuleType
from typing import Any

import pytest
from garh_api.config import get_settings
from garh_api.storage import (
    StoredObject,
    delete_object,
    get_object,
    list_objects,
    parse_listing,
    put_object,
)
from sqlalchemy import make_url, text

REPO_ROOT = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
)
SCRIPTS = os.path.join(REPO_ROOT, "scripts")


def _load(name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "_%s" % name, os.path.join(SCRIPTS, "%s.py" % name)
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # dataclasses resolves `sys.modules[cls.__module__]` at class creation.
    sys.modules[module.__name__] = module
    spec.loader.exec_module(module)
    return module


def _obj(key: str, age_days: float, *, now: datetime) -> StoredObject:
    return StoredObject(key=key, size=1, last_modified=now - timedelta(days=age_days))


LISTING_XML = """<?xml version="1.0" encoding="UTF-8"?>
<ListBucketResult xmlns="http://s3.amazonaws.com/doc/2006-03-01/">
  <Name>garh</Name><Prefix>backups/</Prefix><KeyCount>2</KeyCount>
  <IsTruncated>true</IsTruncated>
  <NextContinuationToken>abc==</NextContinuationToken>
  <Contents><Key>backups/garh-1.dump</Key><LastModified>2026-09-15T02:00:00.000Z</LastModified><Size>1024</Size></Contents>
  <Contents><Key>backups/garh-1.counts.json</Key><LastModified>2026-09-15T02:00:01Z</LastModified><Size>12</Size></Contents>
</ListBucketResult>"""


# ---------------------------------------------------------------------------
# Listing and retention — pure
# ---------------------------------------------------------------------------


def test_parse_listing_reads_namespaced_xml_and_the_continuation_token() -> None:
    objects, token = parse_listing(LISTING_XML)
    assert [o.key for o in objects] == ["backups/garh-1.dump", "backups/garh-1.counts.json"]
    assert objects[0].size == 1024
    assert objects[0].last_modified == datetime(2026, 9, 15, 2, 0, tzinfo=UTC)
    assert token == "abc=="
    # Not truncated ⇒ no token even if one is present in the document.
    _, none = parse_listing(LISTING_XML.replace("<IsTruncated>true", "<IsTruncated>false"))
    assert none is None


def test_prune_keeps_the_newest_minimum_however_old_and_ignores_non_dumps() -> None:
    backup_s3 = _load("backup_s3")
    now = datetime(2026, 9, 16, 12, 0, tzinfo=UTC)
    objects = [
        _obj("backups/garh-a.dump", 1, now=now),
        _obj("backups/garh-b.dump", 10, now=now),
        _obj("backups/garh-c.dump", 20, now=now),
        _obj("backups/garh-d.dump", 40, now=now),
        _obj("backups/garh-d.counts.json", 40, now=now),
        _obj("backups/README.txt", 400, now=now),
    ]
    keep, delete = backup_s3.plan_prune(objects, keep_days=14, keep_min=2, now=now)
    assert [o.key for o in keep] == ["backups/garh-a.dump", "backups/garh-b.dump"]
    assert [o.key for o in delete] == ["backups/garh-c.dump", "backups/garh-d.dump"]
    assert backup_s3.companions("backups/garh-d.dump", objects) == ["backups/garh-d.counts.json"]

    # keep_min beats age: with keep_min=4 nothing is deleted though two are old.
    keep, delete = backup_s3.plan_prune(objects, keep_days=14, keep_min=4, now=now)
    assert delete == [] and len(keep) == 4

    # A cron that was down for a month must not wipe every dump on its first run.
    ancient = [_obj("backups/garh-%d.dump" % i, 100 + i, now=now) for i in range(5)]
    keep, delete = backup_s3.plan_prune(ancient, keep_days=14, keep_min=7, now=now)
    assert len(keep) == 5 and delete == []


# ---------------------------------------------------------------------------
# The object store — executed against the local store
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_put_list_get_delete_round_trip_under_a_prefix() -> None:
    settings = get_settings()
    prefix = "test-backups/%s/" % uuid.uuid4().hex[:8]
    keys = ["%sgarh-%d.dump" % (prefix, i) for i in range(3)]
    for index, key in enumerate(keys):
        await put_object(
            key, b"dump-%d" % index, content_type="application/octet-stream", settings=settings
        )
    await put_object(
        prefix + "garh-0.counts.json", b"{}", content_type="application/json", settings=settings
    )

    listed = await list_objects(prefix, settings=settings)
    assert sorted(o.key for o in listed) == sorted([*keys, prefix + "garh-0.counts.json"])
    assert all(o.last_modified.tzinfo is not None for o in listed)
    assert await get_object(keys[1], settings=settings) == b"dump-1"

    assert await delete_object(keys[0], settings=settings) is True
    remaining = await list_objects(prefix, settings=settings)
    assert keys[0] not in {o.key for o in remaining}
    # Unrelated prefixes are invisible — the retention pass cannot reach them.
    assert await list_objects("test-backups/nothing-here/", settings=settings) == []
    for obj in remaining:
        await delete_object(obj.key, settings=settings)


@pytest.mark.integration
def test_the_backup_cli_puts_finds_the_newest_and_prunes(tmp_path: Any) -> None:
    prefix = "test-backups/%s/" % uuid.uuid4().hex[:8]
    dump = tmp_path / "garh-x.dump"
    dump.write_bytes(b"pgdump")
    env = dict(os.environ)
    env["PYTHONPATH"] = "%s:%s" % (REPO_ROOT, os.path.join(REPO_ROOT, "apps", "api"))

    def cli(*args: str) -> str:
        done = subprocess.run(
            [sys.executable, os.path.join(SCRIPTS, "backup_s3.py"), *args],
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        assert done.returncode == 0, done.stdout + done.stderr
        return done.stdout

    cli("put", prefix + "garh-x.dump", str(dump))
    assert cli("newest", prefix).strip() == prefix + "garh-x.dump"
    plan = json.loads(cli("prune", prefix, "--keep-days", "0", "--keep-min", "0", "--dry-run"))
    assert plan["deleted"] == [prefix + "garh-x.dump"] and plan["dryRun"] is True
    assert cli("newest", prefix).strip() == prefix + "garh-x.dump", "a dry run deleted"
    for_real = json.loads(cli("prune", prefix, "--keep-days", "0", "--keep-min", "0"))
    assert for_real["deleted"] == [prefix + "garh-x.dump"]


# ---------------------------------------------------------------------------
# The restore verifier
# ---------------------------------------------------------------------------


def test_compare_counts_fails_on_a_missing_table_and_a_moved_count() -> None:
    verify_restore = _load("verify_restore")
    expected = {"firms": 2, "ops": 140, "otp_codes": 5}
    checks, warnings = verify_restore.compare_counts(
        {"firms": 2, "ops": 140, "otp_codes": 5, "new_table": 0}, expected, tolerate_volatile=False
    )
    assert all(c.ok for c in checks)
    assert warnings == ["table new_table is in the restore but not in the expectation"]

    checks, _ = verify_restore.compare_counts(
        {"firms": 2, "ops": 139}, expected, tolerate_volatile=False
    )
    verdict = {c.table: c.ok for c in checks}
    assert verdict == {"firms": True, "ops": False, "otp_codes": False}

    # Against a live source a volatile table may drift — with the flag, and only then.
    lenient, warnings = verify_restore.compare_counts(
        {"firms": 2, "ops": 140, "otp_codes": 9}, expected, tolerate_volatile=True
    )
    assert all(c.ok for c in lenient) and any("otp_codes" in w for w in warnings)
    strict, _ = verify_restore.compare_counts(
        {"firms": 2, "ops": 140, "otp_codes": 9}, expected, tolerate_volatile=False
    )
    assert not all(c.ok for c in strict)


@pytest.mark.integration
async def test_a_seeded_project_refolds_to_its_recorded_hash_and_a_tampered_op_does_not(
    session: Any,
) -> None:
    from garh_api.seed.runner import SeedOptions, seed

    verify_restore = _load("verify_restore")
    result = await seed(session, SeedOptions())
    await session.commit()
    assert result.project_id is not None and result.version_id is not None

    sync_url = make_url(get_settings().database_url).set(drivername="postgresql+psycopg")
    url = sync_url.render_as_string(hide_password=False)

    report = verify_restore.verify(url, manifest=None, source_url=None, projects=3)
    assert report.ok, report.to_json()
    assert len(report.folds) == 1
    fold = report.folds[0]
    assert fold.project_id == str(result.project_id)
    assert fold.ops_folded == fold.at_idx + 1 > 0
    assert fold.refolded_state_hash == fold.recorded_state_hash == result.state_hash
    assert fold.envelope_hash_ok is True

    # The manifest path: exact counts pass, a count off by one fails.
    counts = {c.table: c.restored for c in report.tables}
    assert verify_restore.verify(url, manifest=counts, source_url=None, projects=1).ok
    counts["ops"] += 1
    assert not verify_restore.verify(url, manifest=counts, source_url=None, projects=1).ok

    # Negative control: alter one op under the snapshot and the refold must disagree.
    changed = await session.execute(
        text(
            "UPDATE ops SET payload = jsonb_set(payload, '{name}', '\"tampered\"'::jsonb) "
            "WHERE project_id = :project AND idx = ("
            "  SELECT max(idx) FROM ops WHERE project_id = :project AND type = 'storey.add'"
            ") RETURNING idx"
        ),
        {"project": result.project_id},
    )
    assert changed.scalar() is not None, "no storey.add op to tamper with"
    await session.commit()
    tampered = verify_restore.verify(url, manifest=None, source_url=None, projects=3)
    assert not tampered.ok
    assert tampered.folds[0].refolded_state_hash != tampered.folds[0].recorded_state_hash
    assert tampered.folds[0].envelope_hash_ok is True, "the envelope itself was not touched"

    # And the envelope check catches a snapshot row whose hash column drifted.
    await session.execute(
        text("UPDATE design_versions SET snapshot_hash = repeat('0', 64) WHERE id = :id"),
        {"id": result.version_id},
    )
    await session.commit()
    assert (
        verify_restore.verify(url, manifest=None, source_url=None, projects=3)
        .folds[0]
        .envelope_hash_ok
        is False
    )


# ---------------------------------------------------------------------------
# The artifact: the backup image and its schedule
# ---------------------------------------------------------------------------


def test_the_backup_image_carries_the_client_the_scripts_and_the_cron_command() -> None:
    with open(
        os.path.join(REPO_ROOT, "deploy", "backup", "Dockerfile"), encoding="utf-8"
    ) as handle:
        dockerfile = handle.read()
    assert (
        "postgresql-client-${PG_MAJOR}" in dockerfile and "apt.postgresql.org" in dockerfile
    ), "bookworm's pg_dump 15 refuses a 16/17 server; the image must install PGDG's client"
    for script in ("backup_db.sh", "backup_s3.py", "restore_rehearsal.sh", "verify_restore.py"):
        assert "scripts/%s" % script in dockerfile, script
        assert os.path.exists(os.path.join(SCRIPTS, script)), script
    assert 'CMD ["/app/scripts/backup_db.sh", "backup-s3"]' in dockerfile
    assert "USER garh" in dockerfile

    with open(
        os.path.join(REPO_ROOT, "deploy", "railway", "backup.json"), encoding="utf-8"
    ) as handle:
        cron = json.load(handle)
    assert cron["build"]["dockerfilePath"] == "deploy/backup/Dockerfile"
    assert cron["deploy"]["startCommand"] == "/app/scripts/backup_db.sh backup-s3"
    schedule = cron["deploy"]["cronSchedule"].split()
    assert len(schedule) == 5 and schedule[2:] == ["*", "*", "*"], "daily, not more"

    with open(os.path.join(SCRIPTS, "backup_db.sh"), encoding="utf-8") as handle:
        script_text = handle.read()
    assert "backup-s3)" in script_text and "--keep-min" in script_text
    assert "counts.json" in script_text, "no manifest, no row-count rehearsal"
