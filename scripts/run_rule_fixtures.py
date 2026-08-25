#!/usr/bin/env python3
"""Run every rule fixture through the real §6 engine. No pytest, no dependencies.

``garh_rules`` is deliberately pure-stdlib, which makes it the one part of the
backend that can be fully proven on a machine with nothing installed. This script
exists so that proof is a single command, re-runnable after every phase, rather
than something reconstructed by hand each time.

It enumerates ``fixtures/rules/index.json`` — never a glob — so a rule whose
fixtures were deleted fails loudly instead of vanishing from the run.

Run:  python3 scripts/run_rule_fixtures.py
Exit: 0 when every fixture matches its expected row, 1 otherwise.
"""

from __future__ import annotations

import collections
import json
import os
import sys
import time

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
_APPS_API = os.path.join(_ROOT, "apps", "api")
if _APPS_API not in sys.path:
    sys.path.insert(0, _APPS_API)

from garh_rules import evaluate  # noqa: E402

FIXTURE_ROOT = os.path.join(_ROOT, "fixtures", "rules")
RULEPACK_ROOT = os.path.join(_ROOT, "rulepacks")


def _fixture_path(relative: str) -> str:
    if os.path.isabs(relative):
        return relative
    if relative.startswith("fixtures"):
        return os.path.join(_ROOT, relative)
    return os.path.join(FIXTURE_ROOT, relative)


def main() -> int:
    with open(os.path.join(FIXTURE_ROOT, "index.json"), "r", encoding="utf-8") as handle:
        manifest = json.load(handle)
    entries = manifest["fixtures"] if isinstance(manifest, dict) else manifest

    started = time.time()
    statuses: collections.Counter = collections.Counter()
    mismatches: list[tuple[str, str]] = []

    for entry in entries:
        with open(_fixture_path(entry["path"]), "r", encoding="utf-8") as handle:
            fixture = json.load(handle)

        report = evaluate(fixture["context"], root=RULEPACK_ROOT)
        rows = report.results if hasattr(report, "results") else report
        row = next((r for r in rows if getattr(r, "rule_id", None) == entry["ruleId"]), None)

        if row is None:
            mismatches.append((entry["path"], "rule produced no row"))
            continue

        expected = fixture["expected"]
        statuses[row.status] += 1

        if row.status != expected["status"]:
            mismatches.append(
                (entry["path"], "status %s != expected %s" % (row.status, expected["status"]))
            )
        elif "actual" in expected and row.actual != expected["actual"]:
            mismatches.append(
                (entry["path"], "actual %s != expected %s" % (row.actual, expected["actual"]))
            )
        elif "limit" in expected and row.limit != expected["limit"]:
            mismatches.append(
                (entry["path"], "limit %s != expected %s" % (row.limit, expected["limit"]))
            )

    elapsed = time.time() - started
    print(
        "%d fixtures in %.2fs :: %s :: %d mismatch(es)"
        % (len(entries), elapsed, dict(statuses), len(mismatches))
    )
    for path, reason in mismatches[:20]:
        print("  FAIL %s — %s" % (path, reason))
    if len(mismatches) > 20:
        print("  ... and %d more" % (len(mismatches) - 20))

    return 1 if mismatches else 0


if __name__ == "__main__":
    raise SystemExit(main())
