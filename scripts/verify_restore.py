"""Prove a restored database is the database that was dumped — not merely a schema.

``scripts/backup_db.sh rehearse`` used to check that four tables existed. That is
the check that cannot go red: an empty schema passes it. This script asks the two
questions an operator actually has after a restore:

1. **Are the rows all there?** Every table's count in the restored database is
   compared with the manifest written beside the dump at backup time
   (``<dump>.counts.json``, one snapshot of ``count(*)`` per table). Without a
   manifest, ``--source-url`` compares against the live source instead, which is
   only exact while the source is quiet — the report says which comparison it made.
2. **Does a design still fold to the hash production recorded?** The op log is the
   source of truth (playbook §2); a snapshot is a cache. For the newest versions
   that carry a snapshot, the ops up to the snapshot's ``atIdx`` are folded from an
   empty document through the real model engine and the resulting state hash must
   equal the ``stateHash`` the API wrote into the envelope — and the envelope's own
   sha256 must equal ``design_versions.snapshot_hash``. A restore that lost or
   reordered one op fails here; a restore that dropped the ops entirely fails here.

    PYTHONPATH=.:apps/api python scripts/verify_restore.py \\
        --database-url postgresql://garh:garh@localhost:5432/garh_restore_rehearsal \\
        --manifest backups/garh-20260916T020000Z.counts.json [--projects 3] [--json]

Exit 0 when everything matches, 1 on any mismatch, 2 on usage, 3 when there was no
project with a snapshot to fold (reported as a warning: a fresh environment has
none, a production restore always should — pass ``--require-project`` to make it
fatal).
"""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass, field
from typing import Any

from sqlalchemy import create_engine, make_url, text
from sqlalchemy.engine import Connection

#: Tables whose counts are expected to differ between a live source and a restore
#: taken minutes earlier and are therefore compared with a warning, not a failure,
#: in ``--source-url`` mode. In manifest mode every table is exact.
VOLATILE_TABLES: frozenset[str] = frozenset({"otp_codes", "audit_log", "credit_events"})


@dataclass
class TableCheck:
    table: str
    expected: int | None
    restored: int
    ok: bool


@dataclass
class FoldCheck:
    project_id: str
    design_version_id: str
    at_idx: int
    ops_folded: int
    recorded_state_hash: str | None
    refolded_state_hash: str
    envelope_hash_ok: bool
    ok: bool


@dataclass
class Report:
    database: str
    comparison: str
    tables: list[TableCheck] = field(default_factory=list)
    folds: list[FoldCheck] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return all(t.ok for t in self.tables) and all(f.ok for f in self.folds)

    def to_json(self) -> dict[str, Any]:
        return {
            "database": self.database,
            "comparison": self.comparison,
            "ok": self.ok,
            "tables": [asdict(t) for t in self.tables],
            "folds": [asdict(f) for f in self.folds],
            "warnings": list(self.warnings),
        }


def _sync_url(url: str) -> str:
    return make_url(url).set(drivername="postgresql+psycopg").render_as_string(hide_password=False)


def table_counts(connection: Connection) -> dict[str, int]:
    """``count(*)`` for every ordinary table in ``public``. ``alembic_version`` included."""
    names = [
        str(row[0])
        for row in connection.execute(
            text(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = 'public' AND table_type = 'BASE TABLE' ORDER BY table_name"
            )
        ).all()
    ]
    return {
        name: int(connection.execute(text('SELECT count(*) FROM "%s"' % name)).scalar() or 0)
        for name in names
    }


def compare_counts(
    restored: dict[str, int],
    expected: dict[str, int],
    *,
    tolerate_volatile: bool,
) -> tuple[list[TableCheck], list[str]]:
    """Pure: the verdict per table. A table in the manifest but absent from the
    restore is a failure (count None ≠ anything); a table in the restore the
    manifest never saw is a warning (a migration since the dump)."""
    checks: list[TableCheck] = []
    warnings: list[str] = []
    for table in sorted(set(expected) | set(restored)):
        if table not in restored:
            checks.append(TableCheck(table=table, expected=expected[table], restored=-1, ok=False))
            continue
        if table not in expected:
            warnings.append("table %s is in the restore but not in the expectation" % table)
            checks.append(TableCheck(table=table, expected=None, restored=restored[table], ok=True))
            continue
        same = restored[table] == expected[table]
        if not same and tolerate_volatile and table in VOLATILE_TABLES:
            warnings.append(
                "%s differs (%d expected, %d restored) — volatile table compared against a live source"
                % (table, expected[table], restored[table])
            )
            same = True
        checks.append(
            TableCheck(table=table, expected=expected[table], restored=restored[table], ok=same)
        )
    return checks, warnings


def _unwrap(snapshot: Any) -> tuple[dict[str, Any], int, str | None] | None:
    if not isinstance(snapshot, dict):
        return None
    document = snapshot.get("doc")
    at_idx = snapshot.get("atIdx")
    if not isinstance(document, dict) or not isinstance(at_idx, int):
        return None
    raw_hash = snapshot.get("stateHash")
    return document, at_idx, raw_hash if isinstance(raw_hash, str) else None


def fold_checks(connection: Connection, *, projects: int) -> tuple[list[FoldCheck], list[str]]:
    """Refold the newest snapshot-bearing versions and compare hashes."""
    from garh_api.repositories.design_versions import compute_snapshot_hash
    from garh_api.routers.ops import get_model_engine

    engine = get_model_engine()
    rows = connection.execute(
        text(
            "SELECT DISTINCT ON (project_id) id, project_id, version_branch, snapshot, snapshot_hash "
            "FROM design_versions WHERE snapshot IS NOT NULL "
            "ORDER BY project_id, created_at DESC"
        )
    ).all()
    rows = sorted(rows, key=lambda r: str(r[1]))[: max(0, projects)]
    checks: list[FoldCheck] = []
    warnings: list[str] = []
    for version_id, project_id, branch, snapshot, snapshot_hash in rows:
        unwrapped = _unwrap(snapshot)
        if unwrapped is None:
            warnings.append("version %s has a snapshot with no envelope; skipped" % version_id)
            continue
        document_in_envelope, at_idx, recorded = unwrapped
        del document_in_envelope  # the anchor is not trusted; the op log is refolded
        ops = connection.execute(
            text(
                "SELECT type, payload, client_op_id, group_id FROM ops "
                "WHERE project_id = :project AND version_branch = :branch AND idx <= :upto "
                "ORDER BY idx"
            ),
            {"project": project_id, "branch": branch, "upto": at_idx},
        ).all()
        document = engine.empty_document()
        folded = 0
        for op_type, payload, client_op_id, group_id in ops:
            wire: dict[str, Any] = {"type": op_type, "payload": dict(payload)}
            if client_op_id:
                wire["clientOpId"] = client_op_id
            if group_id:
                wire["groupId"] = str(group_id)
            document = engine.fold(document, wire).document
            folded += 1
        refolded = engine.state_hash(document)
        envelope_ok = compute_snapshot_hash(dict(snapshot)) == snapshot_hash
        checks.append(
            FoldCheck(
                project_id=str(project_id),
                design_version_id=str(version_id),
                at_idx=at_idx,
                ops_folded=folded,
                recorded_state_hash=recorded,
                refolded_state_hash=refolded,
                envelope_hash_ok=envelope_ok,
                ok=envelope_ok and recorded is not None and refolded == recorded,
            )
        )
    return checks, warnings


def verify(
    database_url: str,
    *,
    manifest: dict[str, int] | None,
    source_url: str | None,
    projects: int,
) -> Report:
    engine = create_engine(_sync_url(database_url), poolclass=None)
    try:
        with engine.connect() as connection:
            restored = table_counts(connection)
            if manifest is not None:
                comparison = "manifest"
                expected = dict(manifest)
                tolerate = False
            elif source_url is not None:
                comparison = "live source"
                src = create_engine(_sync_url(source_url), poolclass=None)
                try:
                    with src.connect() as source_connection:
                        expected = table_counts(source_connection)
                finally:
                    src.dispose()
                tolerate = True
            else:
                comparison = "none"
                expected = {}
                tolerate = False
            report = Report(database=make_url(database_url).database or "", comparison=comparison)
            if expected:
                report.tables, warnings = compare_counts(
                    restored, expected, tolerate_volatile=tolerate
                )
                report.warnings.extend(warnings)
            else:
                report.warnings.append("no manifest and no source: row counts were not compared")
                report.tables = [
                    TableCheck(table=name, expected=None, restored=count, ok=True)
                    for name, count in sorted(restored.items())
                ]
            report.folds, fold_warnings = fold_checks(connection, projects=projects)
            report.warnings.extend(fold_warnings)
            return report
    finally:
        engine.dispose()


def render(report: Report) -> str:
    lines = ["restore verification — %s (row counts vs %s)" % (report.database, report.comparison)]
    for check in report.tables:
        mark = "ok  " if check.ok else "FAIL"
        expected = "-" if check.expected is None else str(check.expected)
        lines.append(
            "  %s %-24s expected %6s  restored %6d" % (mark, check.table, expected, check.restored)
        )
    if report.folds:
        for fold in report.folds:
            mark = "ok  " if fold.ok else "FAIL"
            lines.append(
                "  %s project %s: %d ops → %s (recorded %s, envelope %s)"
                % (
                    mark,
                    fold.project_id,
                    fold.ops_folded,
                    fold.refolded_state_hash[:16],
                    (fold.recorded_state_hash or "none")[:16],
                    "ok" if fold.envelope_hash_ok else "MISMATCH",
                )
            )
    else:
        lines.append("  WARN no project with a snapshot to refold")
    for warning in report.warnings:
        lines.append("  WARN %s" % warning)
    lines.append("  RESULT %s" % ("OK" if report.ok else "FAILED"))
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--database-url", required=True, help="the RESTORED database")
    parser.add_argument("--manifest", help="<dump>.counts.json written at backup time")
    parser.add_argument("--source-url", help="compare against a live source instead")
    parser.add_argument("--projects", type=int, default=3, help="how many projects to refold")
    parser.add_argument("--require-project", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    manifest: dict[str, int] | None = None
    if args.manifest:
        with open(args.manifest, encoding="utf-8") as handle:
            loaded = json.load(handle)
        manifest = {str(k): int(v) for k, v in loaded.get("tables", loaded).items()}

    report = verify(
        args.database_url, manifest=manifest, source_url=args.source_url, projects=args.projects
    )
    print(json.dumps(report.to_json(), indent=2) if args.json else render(report))
    if not report.ok:
        return 1
    if not report.folds:
        return 3 if args.require_project else 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
