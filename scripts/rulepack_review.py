#!/usr/bin/env python3
"""Rule-pack review coverage, and the gate that stops a pack claiming what it has not earned.

Why this exists
---------------
Garh AI sells *citable* compliance. Every one of the 118 rules in ``rulepacks/``
currently carries ``"confidence": "seed"`` — drafted by the Garh team from
secondary summaries, read by no architect, checked against no primary document.
That is stated honestly today in three places (the pack ``disclaimer``, the
``review`` block and the compliance chip). The danger is not the seed values; it
is the day the first ones get promoted. ``confidence`` is a single word in a JSON
file, the UI drops its caution marker the moment that word changes, and nothing
anywhere asks *on what evidence*.

So this script is the answer to "on what evidence". Two jobs:

``coverage``
    Per pack and per city: how many values are still seed, how many are
    reviewed, how many verified, and whether the pack is past its
    ``nextReviewDue``. Empanelling architects across three cities is a months-long
    programme; it needs a number that moves.

``verify``
    The gate. It refuses:

    * a rule at ``reviewed``/``verified`` with no ``review`` record;
    * a review record missing the reviewer, the CoA registration, the date, the
      **source document** or the **clause** — a review with no clause is not a
      review, it is an assertion;
    * a review signed by somebody who is not on the pack's ``review.reviewers``
      roster, or whose CoA number does not match the roster entry;
    * a review citing a source the pack does not list, or lists with
      ``obtained: false`` — you cannot read a clause out of a book nobody has;
    * a review dated in the future;
    * ``outcome: "corrected"`` with no ``previousValue``, which would leave every
      compliance report issued before the correction unexplainable;
    * ``verified`` with no ``verification`` artefact (sanction number or
      municipal confirmation);
    * a pack whose ``review.status`` outruns its rules — ``reviewed`` demands
      *every* rule reviewed-or-better, ``verified`` demands every rule verified,
      and ``unreviewed`` demands no rule has moved;
    * ``rulepacks/index.json`` disagreeing with the packs. This one matters most:
      ``GET /rulepacks`` serves that manifest verbatim and the UI labels every
      citation from it, so an index that says "reviewed" over a seed pack is a
      lie that reaches the architect without passing through the engine at all.

The schema (``rulepacks/schema/rulepack.schema.json``) enforces the first, sixth
and seventh of those at engine load time as well. That duplication is deliberate:
two independent gates, one in the data contract and one here, so weakening either
alone does not open the door.

This script imports nothing outside the standard library and runs on a bare
interpreter, like every other gate in ``make bare``.

Usage::

    python3 scripts/rulepack_review.py                 # verify; exit 1 on findings
    python3 scripts/rulepack_review.py verify --json
    python3 scripts/rulepack_review.py coverage
    python3 scripts/rulepack_review.py coverage --json
    python3 scripts/rulepack_review.py verify --today 2027-01-01
"""

from __future__ import annotations

import argparse
import dataclasses
import datetime
import json
import os
import re
import sys
from collections.abc import Iterable, Sequence
from typing import Any

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
DEFAULT_PACK_DIR = os.path.join(_ROOT, "rulepacks")

#: The ladder, strongest last. Asserted against the schema's own enum in
#: :func:`_check_ladder_matches_schema` — a rung added to the schema without a
#: decision here about which side of :data:`AUTHORITATIVE` it falls on is the
#: exact shape of the bug that once turned 83 rules inert, so it fails loudly
#: instead of defaulting.
LADDER = ("seed", "reviewed", "verified")

#: Rungs the product is allowed to present without a caution marker. This is a
#: policy line, not a schema fact: it is the boundary between "the Garh team
#: thinks this is the number" and "a registered architect put their name on it".
AUTHORITATIVE = frozenset({"reviewed", "verified"})

#: Pack-level ``review.status`` values, and the rule-level floor each demands:
#: a pack is only as good as its weakest rule. ``in-review`` demands nothing —
#: a pack is legitimately in-review from the day it is assigned, before any
#: single rule has been signed.
STATUS_FLOOR = {
    "unreviewed": None,
    "in-review": None,
    "reviewed": "reviewed",
    "verified": "verified",
}

#: ...and the ceiling, which only ``unreviewed`` has. A floor cannot express it:
#: every rung is at or above ``seed``, so "unreviewed requires seed" is a
#: condition that can never be violated — a gate that silently never fires, which
#: is the first bug class in CLAUDE.md. The claim being caught here is a pack
#: that has started promoting rules while still telling the UI nobody has looked.
STATUS_CEILING = {"unreviewed": "seed"}

REVIEW_REQUIRED_FIELDS = ("reviewer", "coaNumber", "reviewedAt", "source", "clause", "outcome")


# ---------------------------------------------------------------------------
# loading
# ---------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class Finding:
    """One problem, addressed to whoever has to fix it."""

    where: str
    message: str

    def __str__(self) -> str:
        return "%s: %s" % (self.where, self.message)

    def as_json(self) -> dict[str, str]:
        return {"where": self.where, "message": self.message}


def _load_json(path: str) -> Any:
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def _rank(confidence: str) -> int:
    """Position on the ladder; -1 for anything not on it."""
    try:
        return LADDER.index(confidence)
    except ValueError:
        return -1


def _parse_date(value: Any) -> datetime.date | None:
    """``datetime.date`` or None. None means 'not a date I can compare'."""
    if not isinstance(value, str):
        return None
    try:
        return datetime.date.fromisoformat(value)
    except ValueError:
        return None


def _blank(value: Any) -> bool:
    return not isinstance(value, str) or not value.strip()


# ---------------------------------------------------------------------------
# audit
# ---------------------------------------------------------------------------


def _check_ladder_matches_schema(pack_dir: str, findings: list[Finding]) -> str:
    """Return the CoA pattern from the schema, and assert the ladder still matches.

    Both facts are read out of the schema rather than restated, so the two files
    cannot drift apart quietly. A mismatch is fatal to the run: if the schema
    knows a rung this script does not, every count below is wrong.
    """
    path = os.path.join(pack_dir, "schema", "rulepack.schema.json")
    try:
        schema = _load_json(path)
    except (OSError, ValueError) as exc:
        findings.append(Finding("rulepacks/schema", "cannot read rulepack.schema.json: %s" % exc))
        return ""
    defs = schema.get("$defs") or {} if isinstance(schema, dict) else {}
    declared = tuple((defs.get("confidence") or {}).get("enum") or ())
    if declared != LADDER:
        findings.append(
            Finding(
                "rulepacks/schema/rulepack.schema.json",
                "confidence enum %r does not match this script's ladder %r — decide which side "
                "of the authoritative line the new rung sits on before shipping it"
                % (list(declared), list(LADDER)),
            )
        )
    return str((defs.get("coaNumber") or {}).get("pattern") or "")


def _pack_ids(pack_dir: str, findings: list[Finding]) -> list[str]:
    """Pack ids from index.json, cross-checked against the files on disk."""
    index_path = os.path.join(pack_dir, "index.json")
    try:
        index = _load_json(index_path)
    except (OSError, ValueError) as exc:
        findings.append(Finding("rulepacks/index.json", "cannot read: %s" % exc))
        return []
    listed = [str(entry.get("pack")) for entry in (index or {}).get("packs") or ()]
    on_disk = sorted(
        name[:-5]
        for name in os.listdir(pack_dir)
        if name.endswith(".json") and name != "index.json"
    )
    for pack_id in on_disk:
        if pack_id not in listed:
            findings.append(
                Finding(
                    "rulepacks/index.json",
                    "pack %r exists on disk but is not in the manifest — GET /rulepacks would "
                    "never offer it, and its review state would never be visible" % pack_id,
                )
            )
    for pack_id in listed:
        if pack_id not in on_disk:
            findings.append(
                Finding("rulepacks/index.json", "manifest lists %r, which has no file" % pack_id)
            )
    return [p for p in listed if p in on_disk]


def _audit_reviewers(
    pack_id: str,
    pack: dict[str, Any],
    coa_pattern: str,
    today: datetime.date,
    findings: list[Finding],
) -> dict[str, str]:
    """Validate the pack's reviewer roster; return {name: coaNumber} for cross-checks."""
    where = "rulepacks/%s.json :: review.reviewers" % pack_id
    roster: dict[str, str] = {}
    by_coa: dict[str, str] = {}
    entries = (pack.get("review") or {}).get("reviewers") or ()
    for i, entry in enumerate(entries):
        if not isinstance(entry, dict):
            findings.append(Finding(where, "entry %d is not an object" % i))
            continue
        name = entry.get("name")
        coa = entry.get("coaNumber")
        if _blank(name):
            findings.append(Finding(where, "entry %d has no name" % i))
            continue
        if _blank(coa):
            findings.append(
                Finding(
                    where,
                    "%s has no coaNumber — an unregistered name is not a sign-off" % name,
                )
            )
        elif coa_pattern and not re.match(coa_pattern, str(coa)):
            findings.append(
                Finding(where, "%s: coaNumber %r is not a CoA registration" % (name, coa))
            )
        signed = _parse_date(entry.get("signedAt"))
        if signed is None:
            findings.append(
                Finding(where, "%s has no valid signedAt date — a sign-off has a date" % name)
            )
        elif signed > today:
            findings.append(
                Finding(where, "%s signed at %s, which is in the future" % (name, signed))
            )
        if str(name) in roster:
            findings.append(Finding(where, "%s appears twice on the roster" % name))
        roster[str(name)] = str(coa)
        if not _blank(coa):
            previous = by_coa.get(str(coa))
            if previous is not None and previous != str(name):
                findings.append(
                    Finding(
                        where,
                        "coaNumber %s is claimed by both %r and %r" % (coa, previous, name),
                    )
                )
            by_coa[str(coa)] = str(name)
    return roster


def _audit_review_record(
    where: str,
    record: Any,
    rule_confidence: str,
    pack_sources: dict[str, Any],
    roster: dict[str, str],
    coa_pattern: str,
    today: datetime.date,
    findings: list[Finding],
) -> datetime.date | None:
    """Validate one rule's review record. Returns its reviewedAt date, or None."""
    if not isinstance(record, dict):
        findings.append(Finding(where, "review is not an object"))
        return None

    for key in REVIEW_REQUIRED_FIELDS:
        if _blank(record.get(key)):
            findings.append(
                Finding(where, "review.%s is missing or blank — a review without it is not a "
                               "review, it is an assertion" % key)
            )

    coa = record.get("coaNumber")
    if not _blank(coa) and coa_pattern and not re.match(coa_pattern, str(coa)):
        findings.append(Finding(where, "review.coaNumber %r is not a CoA registration" % coa))

    reviewer = record.get("reviewer")
    if not _blank(reviewer):
        if str(reviewer) not in roster:
            findings.append(
                Finding(
                    where,
                    "review.reviewer %r is not on the pack's review.reviewers roster — the "
                    "signature has no provenance" % reviewer,
                )
            )
        elif not _blank(coa) and roster[str(reviewer)] != str(coa):
            findings.append(
                Finding(
                    where,
                    "review.coaNumber %s does not match %r's roster registration %s"
                    % (coa, reviewer, roster[str(reviewer)]),
                )
            )

    source = record.get("source")
    if not _blank(source):
        entry = pack_sources.get(str(source))
        if entry is None:
            findings.append(
                Finding(
                    where,
                    "review.source %r is not one of the pack's sources[] labels" % source,
                )
            )
        elif not entry.get("obtained"):
            findings.append(
                Finding(
                    where,
                    "review.source %r is listed with obtained: false — a clause cannot be read "
                    "from a document the pack says nobody has" % source,
                )
            )

    reviewed_at = _parse_date(record.get("reviewedAt"))
    if record.get("reviewedAt") is not None and reviewed_at is None:
        findings.append(
            Finding(
                where,
                "review.reviewedAt %r is not a YYYY-MM-DD date" % record.get("reviewedAt"),
            )
        )
    elif reviewed_at is not None and reviewed_at > today:
        findings.append(Finding(where, "review.reviewedAt %s is in the future" % reviewed_at))

    outcome = record.get("outcome")
    if outcome not in (None, "confirmed", "corrected"):
        findings.append(Finding(where, "review.outcome %r is not confirmed/corrected" % outcome))
    if outcome == "corrected" and _blank(record.get("previousValue")):
        findings.append(
            Finding(
                where,
                "review.outcome is 'corrected' but previousValue is blank — a compliance report "
                "issued before the correction would no longer be explainable",
            )
        )

    verification = record.get("verification")
    if rule_confidence == "verified":
        if not isinstance(verification, dict):
            findings.append(
                Finding(
                    where,
                    "confidence 'verified' needs review.verification — reviewing a clause proves "
                    "what the bye-law says, not what the municipal desk accepts",
                )
            )
            verification = None
    if isinstance(verification, dict):
        if verification.get("kind") not in ("sanctioned-drawing", "municipal-confirmation"):
            findings.append(
                Finding(where, "review.verification.kind %r is unknown" % verification.get("kind"))
            )
        if _blank(verification.get("reference")):
            findings.append(
                Finding(
                    where,
                    "review.verification.reference is blank — a sanction number a third party "
                    "cannot look up proves nothing",
                )
            )
        vdate = _parse_date(verification.get("date"))
        if vdate is None:
            findings.append(
                Finding(where, "review.verification.date %r is not a YYYY-MM-DD date"
                        % verification.get("date"))
            )
        elif vdate > today:
            findings.append(Finding(where, "review.verification.date %s is in the future" % vdate))

    return reviewed_at


def _audit_pack(
    pack_id: str,
    pack: dict[str, Any],
    coa_pattern: str,
    today: datetime.date,
    findings: list[Finding],
) -> dict[str, Any]:
    """Audit one pack. Returns its coverage row."""
    where = "rulepacks/%s.json" % pack_id
    rules = pack.get("rules") or []
    roster = _audit_reviewers(pack_id, pack, coa_pattern, today, findings)
    pack_sources: dict[str, Any] = {}
    for entry in pack.get("sources") or ():
        if isinstance(entry, dict) and not _blank(entry.get("label")):
            pack_sources[str(entry["label"])] = entry

    default = pack.get("confidenceDefault")
    if _rank(str(default)) < 0:
        findings.append(Finding(where, "confidenceDefault %r is not on the ladder" % default))

    counts = {rung: 0 for rung in LADDER}
    unknown = 0
    latest_review = None
    for raw in rules:
        rule_id = str(raw.get("id"))
        rwhere = "%s :: %s" % (where, rule_id)
        confidence = raw.get("confidence", default)
        rank = _rank(str(confidence))
        if rank < 0:
            findings.append(Finding(rwhere, "confidence %r is not on the ladder" % confidence))
            unknown += 1
            continue
        counts[str(confidence)] += 1

        record = raw.get("review")
        if str(confidence) in AUTHORITATIVE and record is None:
            findings.append(
                Finding(
                    rwhere,
                    "confidence %r with no review record — this value would render to an "
                    "architect without a caution marker on nobody's authority"
                    % confidence,
                )
            )
        elif record is not None and str(confidence) == "seed":
            findings.append(
                Finding(
                    rwhere,
                    "carries a review record but is still 'seed' — promote it or drop the "
                    "record; a record nobody acts on is how a reviewed value stays hidden",
                )
            )
        if record is not None:
            reviewed_at = _audit_review_record(
                rwhere, record, str(confidence), pack_sources, roster, coa_pattern, today, findings
            )
            if reviewed_at is not None and (latest_review is None or reviewed_at > latest_review):
                latest_review = reviewed_at

    # confidenceDefault is what a rule inherits when it omits `confidence`. Letting
    # it claim a rung the rules have not earned would promote every future rule by
    # omission.
    floor = min(
        (_rank(str(r.get("confidence", default))) for r in rules),
        default=_rank(str(default)),
    )
    if _rank(str(default)) > floor and floor >= 0:
        findings.append(
            Finding(
                where,
                "confidenceDefault is %r but the pack's weakest rule is %r — a rule that omits "
                "`confidence` would be promoted by omission"
                % (default, LADDER[floor]),
            )
        )

    review = pack.get("review") or {}
    status = str(review.get("status"))
    if status not in STATUS_FLOOR:
        findings.append(Finding(where, "review.status %r is not a known status" % status))
    else:
        required = STATUS_FLOOR[status]
        if required is not None:
            short = [
                str(r.get("id"))
                for r in rules
                if _rank(str(r.get("confidence", default))) < _rank(required)
            ]
            if short:
                findings.append(
                    Finding(
                        where,
                        "review.status is %r, which requires every rule at %r or better, but "
                        "%d rule(s) are below it (e.g. %s)"
                        % (status, required, len(short), ", ".join(sorted(short)[:3])),
                    )
                )
        ceiling = STATUS_CEILING.get(status)
        if ceiling is not None:
            ahead = [
                str(r.get("id"))
                for r in rules
                if _rank(str(r.get("confidence", default))) > _rank(ceiling)
            ]
            if ahead:
                findings.append(
                    Finding(
                        where,
                        "review.status is %r, but %d rule(s) have already been promoted above "
                        "%r (e.g. %s) — the pack is telling the UI nobody has looked"
                        % (status, len(ahead), ceiling, ", ".join(sorted(ahead)[:3])),
                    )
                )
        if status in ("reviewed", "verified"):
            if not roster:
                findings.append(
                    Finding(where, "review.status is %r with an empty reviewers roster" % status)
                )
            if _parse_date(review.get("lastReviewedAt")) is None:
                findings.append(
                    Finding(where, "review.status is %r with no lastReviewedAt date" % status)
                )

    last_reviewed = _parse_date(review.get("lastReviewedAt"))
    if latest_review is not None and last_reviewed is not None and last_reviewed < latest_review:
        findings.append(
            Finding(
                where,
                "review.lastReviewedAt %s predates the newest rule review %s — the pack claims "
                "it was last touched before its own evidence" % (last_reviewed, latest_review),
            )
        )
    if latest_review is not None and last_reviewed is None:
        findings.append(
            Finding(where, "rules carry review records but review.lastReviewedAt is not a date")
        )
    next_due = _parse_date(review.get("nextReviewDue"))
    if next_due is not None and last_reviewed is not None and next_due <= last_reviewed:
        findings.append(
            Finding(
                where,
                "review.nextReviewDue %s is not after lastReviewedAt %s"
                % (next_due, last_reviewed),
            )
        )

    total = len(rules)
    authoritative = sum(counts[rung] for rung in LADDER if rung in AUTHORITATIVE)
    jurisdiction = pack.get("jurisdiction") or {}
    return {
        "pack": pack_id,
        "city": str(jurisdiction.get("city") or jurisdiction.get("scope") or "-"),
        "rules": total,
        "unknownConfidence": unknown,
        "counts": counts,
        "authoritative": authoritative,
        "status": status,
        "lastReviewedAt": review.get("lastReviewedAt"),
        "nextReviewDue": review.get("nextReviewDue"),
        "stale": bool(next_due is not None and next_due < today),
    }


def _audit_index(
    pack_dir: str,
    packs: dict[str, Any],
    rows: list[dict[str, Any]],
    findings: list[Finding],
) -> None:
    """``index.json`` is what GET /rulepacks serves, so it must not overclaim.

    The API never opens a pack file to build that response — it copies this
    manifest — so a manifest that says "reviewed" over a seed pack puts an
    unearned label next to every citation in the UI without the engine ever
    seeing it.
    """
    where = "rulepacks/index.json"
    try:
        index = _load_json(os.path.join(pack_dir, "index.json"))
    except (OSError, ValueError):
        return  # already reported by _pack_ids
    by_row = {row["pack"]: row for row in rows}
    for entry in (index or {}).get("packs") or ():
        pack_id = str(entry.get("pack"))
        row = by_row.get(pack_id)
        pack = packs.get(pack_id)
        if row is None or pack is None:
            continue
        # The manifest's single `confidence` stands for the whole pack, so it must
        # be the pack's WEAKEST rung: a pack is only as good as its worst rule.
        floor = LADDER[0]
        for rung in LADDER:
            if row["counts"].get(rung):
                floor = rung
                break
        if str(entry.get("confidence")) != floor:
            findings.append(
                Finding(
                    where,
                    "%s is advertised as %r but its weakest rule is %r — the manifest is served "
                    "verbatim to the UI" % (pack_id, entry.get("confidence"), floor),
                )
            )
        if str(entry.get("review")) != row["status"]:
            findings.append(
                Finding(
                    where,
                    "%s is advertised as review %r but the pack says %r"
                    % (pack_id, entry.get("review"), row["status"]),
                )
            )
        if str(entry.get("version")) != str(pack.get("version")):
            findings.append(
                Finding(
                    where,
                    "%s is advertised at version %r but the pack is %r — compliance reports pin "
                    "the version, so the two must agree"
                    % (pack_id, entry.get("version"), pack.get("version")),
                )
            )
        if entry.get("ruleCount") != row["rules"]:
            findings.append(
                Finding(
                    where,
                    "%s advertises %r rules, the pack has %d"
                    % (pack_id, entry.get("ruleCount"), row["rules"]),
                )
            )
        if entry.get("extends") != pack.get("extends"):
            findings.append(
                Finding(
                    where,
                    "%s advertises extends %r, the pack says %r"
                    % (pack_id, entry.get("extends"), pack.get("extends")),
                )
            )


def audit(
    pack_dir: str = DEFAULT_PACK_DIR, today: datetime.date | None = None
) -> tuple[list[Finding], list[dict[str, Any]]]:
    """Run every check. Returns ``(findings, coverage_rows)``.

    Importable on purpose: ``apps/api/tests/test_rulepack_review.py`` drives this
    against synthetic pack directories so each finding above is proven to be
    reachable.
    """
    if today is None:
        today = datetime.date.today()
    findings: list[Finding] = []
    coa_pattern = _check_ladder_matches_schema(pack_dir, findings)
    pack_ids = _pack_ids(pack_dir, findings)

    packs: dict[str, Any] = {}
    rows: list[dict[str, Any]] = []
    for pack_id in pack_ids:
        path = os.path.join(pack_dir, "%s.json" % pack_id)
        try:
            pack = _load_json(path)
        except (OSError, ValueError) as exc:
            findings.append(Finding("rulepacks/%s.json" % pack_id, "cannot read: %s" % exc))
            continue
        if not isinstance(pack, dict):
            findings.append(Finding("rulepacks/%s.json" % pack_id, "is not a JSON object"))
            continue
        packs[pack_id] = pack
        rows.append(_audit_pack(pack_id, pack, coa_pattern, today, findings))

    _audit_index(pack_dir, packs, rows, findings)
    return findings, rows


# ---------------------------------------------------------------------------
# reporting
# ---------------------------------------------------------------------------


def _percent(part: int, whole: int) -> int:
    """Whole percent, rounded half away from zero (project-wide rounding rule)."""
    if whole <= 0:
        return 0
    return (200 * part + whole) // (2 * whole)


def format_coverage(rows: list[dict[str, Any]]) -> str:
    header = (
        "pack",
        "city",
        "rules",
        "seed",
        "reviewed",
        "verified",
        "authoritative",
        "status",
        "due",
    )
    lines = [rows_to_line(header)]
    lines.append(rows_to_line(("-" * len(h) for h in header)))
    total = {rung: 0 for rung in LADDER}
    total_rules = 0
    for row in rows:
        counts = row["counts"]
        total_rules += row["rules"]
        for rung in LADDER:
            total[rung] += counts.get(rung, 0)
        due = row["nextReviewDue"] or "-"
        lines.append(
            rows_to_line(
                (
                    row["pack"],
                    row["city"],
                    str(row["rules"]),
                    str(counts.get("seed", 0)),
                    str(counts.get("reviewed", 0)),
                    str(counts.get("verified", 0)),
                    "%d%%" % _percent(row["authoritative"], row["rules"]),
                    row["status"],
                    "%s%s" % (due, " STALE" if row["stale"] else ""),
                )
            )
        )
    authoritative = sum(total[rung] for rung in LADDER if rung in AUTHORITATIVE)
    lines.append(rows_to_line(("-" * len(h) for h in header)))
    lines.append(
        rows_to_line(
            (
                "TOTAL",
                "",
                str(total_rules),
                str(total["seed"]),
                str(total["reviewed"]),
                str(total["verified"]),
                "%d%%" % _percent(authoritative, total_rules),
                "",
                "",
            )
        )
    )
    return "\n".join(lines)


def rows_to_line(cells: Iterable[Any]) -> str:
    widths = (12, 12, 6, 6, 9, 9, 14, 12, 12)
    return "  ".join(str(c).ljust(w) for c, w in zip(cells, widths)).rstrip()


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="rulepack_review.py",
        description="Rule-pack review coverage and the confidence honesty gate.",
    )
    parser.add_argument(
        "command",
        nargs="?",
        default="verify",
        choices=("verify", "coverage"),
        help="verify (default) exits 1 on findings; coverage prints the progress table.",
    )
    parser.add_argument("--root", default=DEFAULT_PACK_DIR, help="rulepacks directory")
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    parser.add_argument(
        "--today",
        default=None,
        help="YYYY-MM-DD to evaluate dates against (staleness, future-dated reviews).",
    )
    args = parser.parse_args(argv)

    today = None
    if args.today is not None:
        today = _parse_date(args.today)
        if today is None:
            sys.stderr.write("--today %r is not a YYYY-MM-DD date\n" % args.today)
            return 2

    findings, rows = audit(args.root, today)

    if args.command == "coverage":
        if args.json:
            print(json.dumps({"packs": rows}, indent=2, sort_keys=True))
        else:
            print(format_coverage(rows))
            if findings:
                print(
                    "\n%d finding(s) — run `verify` before trusting these counts."
                    % len(findings)
                )
        return 0

    if args.json:
        print(json.dumps({"findings": [f.as_json() for f in findings]}, indent=2, sort_keys=True))
    else:
        for finding in findings:
            print(finding)
        if not findings:
            authoritative = sum(row["authoritative"] for row in rows)
            total = sum(row["rules"] for row in rows)
            print(
                "rule-pack review: OK — %d rule(s) across %d pack(s). %d of them (%d%%) are "
                "presented as authoritative, and each of those carries a review record signed "
                "by a rostered architect against a named clause of an obtained document."
                % (total, len(rows), authoritative, _percent(authoritative, total))
            )
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main())
