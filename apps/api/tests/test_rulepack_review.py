"""The rule-pack review machinery, and proof that every part of it can go red.

``scripts/rulepack_review.py`` is the gate that stands between a seeded bye-law
number and an architect who is about to submit it to a municipal office. A gate
like that is only worth having if it fires, so almost every test below is a
negative control: it takes a *valid* promotion — a rule moved from ``seed`` to
``reviewed`` with a complete review record — breaks exactly one thing about it,
and asserts the audit names the break.

Two of them are the important ones:

* :func:`test_repository_packs_are_all_seed` is the standing guard that nobody
  has quietly promoted a value in the committed packs. It would be a green check
  that cannot go red on its own (everything is seed today and the assertion would
  pass with the checker deleted), which is why
  :func:`test_valid_promotion_moves_the_coverage_count` proves the same code path
  *can* see a promotion.
* :func:`test_valid_promotion_is_accepted` proves the gate is not simply refusing
  everything. A gate that always fails teaches people to switch it off.

The coverage counts get an independent recount from the raw JSON, so a miscount
in the audit shows up as a disagreement rather than as a number nobody checks.
"""

from __future__ import annotations

import copy
import datetime
import importlib.util
import json
import os
import shutil
import sys

import pytest
from garh_rules.errors import SchemaValidationError
from garh_rules.packs import PackLoader


def _repo_root() -> str:
    """Walk up until the repository's own marker directories are both present."""
    here = os.path.dirname(os.path.abspath(__file__))
    while True:
        if os.path.isdir(os.path.join(here, "rulepacks")) and os.path.isdir(
            os.path.join(here, "scripts")
        ):
            return here
        parent = os.path.dirname(here)
        if parent == here:  # pragma: no cover - only on a broken checkout
            raise RuntimeError("no repository root above %s" % os.path.abspath(__file__))
        here = parent


_ROOT = _repo_root()
_SCRIPT = os.path.join(_ROOT, "scripts", "rulepack_review.py")
_PACKS = os.path.join(_ROOT, "rulepacks")

#: Frozen so the date-sensitive checks (future-dated reviews, staleness) assert
#: the same thing next year as they do today.
TODAY = datetime.date(2026, 8, 26)

#: A well-formed sign-off, used as the baseline every negative control breaks.
#: It is confined to this test module on purpose: no committed pack carries a
#: review record, and none may until a real architect signs one.
ROSTER_ENTRY = {
    "name": "Test Reviewer",
    "role": "Empanelled architect, Bengaluru",
    "coaNumber": "CA/2011/52341",
    "signedAt": "2026-08-10",
}
REVIEW_RECORD = {
    "reviewer": "Test Reviewer",
    "coaNumber": "CA/2011/52341",
    "reviewedAt": "2026-08-10",
    "source": "BBMP Building Bye-laws 2020",
    "clause": "Table 6, row 121-240 m2",
    "outcome": "confirmed",
}


@pytest.fixture(scope="module")
def review():
    """``scripts/rulepack_review.py`` loaded as a module (it is not a package)."""
    spec = importlib.util.spec_from_file_location("rulepack_review", _SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # `dataclasses` resolves string annotations by looking the defining module up
    # in sys.modules, so a hand-loaded module has to be registered before exec.
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
    finally:
        sys.modules.pop(spec.name, None)
    return module


# ---------------------------------------------------------------------------
# helpers: build a throwaway rulepacks/ directory and mutate it
# ---------------------------------------------------------------------------


def _copy_packs(tmp_path) -> str:
    root = os.path.join(str(tmp_path), "rulepacks")
    shutil.copytree(_PACKS, root)
    return root


def _read(root: str, name: str) -> dict:
    with open(os.path.join(root, name), encoding="utf-8") as handle:
        return json.load(handle)


def _write(root: str, name: str, data: dict) -> None:
    with open(os.path.join(root, name), "w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=2)


def _promote(root: str, record: dict | None = None, *, status: str = "in-review") -> None:
    """Move blr's first rule to ``reviewed``, correctly, and update the pack around it.

    A correct promotion touches four things: the reviewer joins the roster, the
    primary document is marked obtained, the rule gains its record, and the pack
    admits it is in review. Each negative control below undoes exactly one of
    them.
    """
    pack = _read(root, "blr.json")
    pack["review"]["reviewers"] = [copy.deepcopy(ROSTER_ENTRY)]
    pack["review"]["status"] = status
    pack["review"]["lastReviewedAt"] = "2026-08-10"
    pack["review"]["nextReviewDue"] = "2027-08-10"
    pack["sources"][0]["obtained"] = True
    pack["rules"][0]["confidence"] = "reviewed"
    pack["rules"][0]["review"] = copy.deepcopy(REVIEW_RECORD if record is None else record)
    _write(root, "blr.json", pack)

    index = _read(root, "index.json")
    for entry in index["packs"]:
        if entry["pack"] == "blr":
            entry["review"] = status
    _write(root, "index.json", index)


def _edit_rule_review(root: str, **changes) -> None:
    """Apply field edits to the promoted rule's review record (None deletes)."""
    pack = _read(root, "blr.json")
    record = pack["rules"][0]["review"]
    for key, value in changes.items():
        if value is None:
            record.pop(key, None)
        else:
            record[key] = value
    _write(root, "blr.json", pack)


def _messages(module, root: str) -> list[str]:
    findings, _rows = module.audit(root, TODAY)
    return [str(f) for f in findings]


def _row(module, root: str, pack_id: str) -> dict:
    _findings, rows = module.audit(root, TODAY)
    return next(row for row in rows if row["pack"] == pack_id)


# ===========================================================================
# 1. the committed repository
# ===========================================================================


def test_committed_packs_pass_the_gate(review):
    """The real rulepacks/ directory must be clean, today and after every edit."""
    assert _messages(review, _PACKS) == []


def test_repository_packs_are_all_seed(review):
    """Nothing has been quietly promoted.

    This is the assertion the whole build item exists to protect: 118 values, not
    one of them reviewed, and the packs saying so out loud. Its ability to fail is
    proven by ``test_valid_promotion_moves_the_coverage_count`` — the same audit
    code, on a directory where one rule *has* moved, reports the move.
    """
    _findings, rows = review.audit(_PACKS, TODAY)
    assert {row["pack"] for row in rows} == {"nbc-core", "blr", "ncr", "hyd", "vastu"}
    for row in rows:
        assert row["counts"]["reviewed"] == 0, row["pack"]
        assert row["counts"]["verified"] == 0, row["pack"]
        assert row["authoritative"] == 0, row["pack"]
        assert row["status"] == "unreviewed", row["pack"]
        assert row["counts"]["seed"] == row["rules"], row["pack"]


def test_coverage_counts_match_an_independent_recount(review):
    """Recount the packs straight from the JSON; a miscount shows up as a mismatch.

    Deliberately does not reuse the audit's own loader or its ladder constants —
    if ``_audit_pack`` skips a rule, double-counts one, or reads the wrong key,
    these two numbers stop agreeing.
    """
    _findings, rows = review.audit(_PACKS, TODAY)
    by_pack = {row["pack"]: row for row in rows}
    grand_total = 0
    for pack_id, row in by_pack.items():
        with open(os.path.join(_PACKS, "%s.json" % pack_id), encoding="utf-8") as handle:
            raw = json.load(handle)
        expected: dict[str, int] = {}
        for rule in raw["rules"]:
            rung = rule.get("confidence", raw["confidenceDefault"])
            expected[rung] = expected.get(rung, 0) + 1
        assert row["rules"] == len(raw["rules"]), pack_id
        assert {k: v for k, v in row["counts"].items() if v} == expected, pack_id
        grand_total += len(raw["rules"])
    assert grand_total == 118


# ===========================================================================
# 2. positive control — the gate accepts a correct promotion
# ===========================================================================


def test_valid_promotion_is_accepted(review, tmp_path):
    """A complete, honest sign-off must pass. A gate that refuses everything is noise."""
    root = _copy_packs(tmp_path)
    _promote(root)
    assert _messages(review, root) == []


def test_valid_promotion_moves_the_coverage_count(review, tmp_path):
    """One rule promoted moves exactly one rule, and only in that pack."""
    root = _copy_packs(tmp_path)
    before = _row(review, root, "blr")
    _promote(root)
    after = _row(review, root, "blr")

    assert before["counts"] == {"seed": 33, "reviewed": 0, "verified": 0}
    assert after["counts"] == {"seed": 32, "reviewed": 1, "verified": 0}
    assert after["authoritative"] == 1
    assert after["rules"] == before["rules"] == 33
    # 1 of 33 is 3.03%, and the project rounds half away from zero.
    assert review._percent(after["authoritative"], after["rules"]) == 3
    assert _row(review, root, "hyd")["counts"]["reviewed"] == 0


def test_verified_promotion_with_its_artefact_is_accepted(review, tmp_path):
    """`verified` is reachable — with a sanction number a third party can look up."""
    root = _copy_packs(tmp_path)
    record = copy.deepcopy(REVIEW_RECORD)
    record["verification"] = {
        "kind": "sanctioned-drawing",
        "reference": "BBMP/ADD/0921/2025-26",
        "date": "2026-08-12",
    }
    _promote(root, record)
    pack = _read(root, "blr.json")
    pack["rules"][0]["confidence"] = "verified"
    _write(root, "blr.json", pack)

    assert _messages(review, root) == []
    assert _row(review, root, "blr")["counts"]["verified"] == 1


# ===========================================================================
# 3. negative controls — one broken thing each
# ===========================================================================


def test_promotion_without_a_review_record_is_refused(review, tmp_path):
    """The core of the gate: a word changed in a JSON file is not evidence."""
    root = _copy_packs(tmp_path)
    pack = _read(root, "blr.json")
    pack["rules"][0]["confidence"] = "reviewed"
    _write(root, "blr.json", pack)
    assert any("no review record" in m for m in _messages(review, root))


@pytest.mark.parametrize(
    "field", ["reviewer", "coaNumber", "reviewedAt", "source", "clause", "outcome"]
)
def test_every_required_review_field_is_load_bearing(review, tmp_path, field):
    """Drop any one of the six and the record stops being a review."""
    root = _copy_packs(tmp_path)
    _promote(root)
    _edit_rule_review(root, **{field: None})
    messages = _messages(review, root)
    assert any("review.%s is missing or blank" % field in m for m in messages), messages


def test_blank_clause_is_refused(review, tmp_path):
    """Whitespace is not a clause reference."""
    root = _copy_packs(tmp_path)
    _promote(root)
    _edit_rule_review(root, clause="   ")
    assert any("review.clause is missing or blank" in m for m in _messages(review, root))


def test_reviewer_off_the_roster_is_refused(review, tmp_path):
    """A signature with no provenance is not a signature."""
    root = _copy_packs(tmp_path)
    _promote(root)
    _edit_rule_review(root, reviewer="Somebody Else")
    assert any("not on the pack's review.reviewers roster" in m for m in _messages(review, root))


def test_coa_number_mismatching_the_roster_is_refused(review, tmp_path):
    """Right name, someone else's registration."""
    root = _copy_packs(tmp_path)
    _promote(root)
    _edit_rule_review(root, coaNumber="CA/2011/99999")
    assert any(
        "does not match" in m and "roster registration" in m for m in _messages(review, root)
    )


def test_a_pre_2000_coa_registration_is_accepted(review, tmp_path):
    """Registrations issued before the 2000s print a two-digit year.

    A senior architect — exactly the person worth empanelling — carries
    ``CA/97/21473``. Rejecting that would get the whole gate switched off, so it
    is asserted here rather than left to the pattern's author to remember.
    """
    root = _copy_packs(tmp_path)
    _promote(root)
    pack = _read(root, "blr.json")
    pack["review"]["reviewers"][0]["coaNumber"] = "CA/97/21473"
    pack["rules"][0]["review"]["coaNumber"] = "CA/97/21473"
    _write(root, "blr.json", pack)
    assert _messages(review, root) == []
    PackLoader(root).load(["blr"])  # and the schema pattern agrees


def test_malformed_coa_number_is_refused(review, tmp_path):
    """A placeholder must not pass as a credential."""
    root = _copy_packs(tmp_path)
    _promote(root)
    pack = _read(root, "blr.json")
    pack["review"]["reviewers"][0]["coaNumber"] = "pending"
    pack["rules"][0]["review"]["coaNumber"] = "pending"
    _write(root, "blr.json", pack)
    messages = _messages(review, root)
    assert any("is not a CoA registration" in m for m in messages), messages


def test_source_not_listed_by_the_pack_is_refused(review, tmp_path):
    root = _copy_packs(tmp_path)
    _promote(root)
    _edit_rule_review(root, source="A summary blog post about BBMP setbacks")
    assert any("is not one of the pack's sources[] labels" in m for m in _messages(review, root))


def test_source_the_pack_says_nobody_obtained_is_refused(review, tmp_path):
    """The check that keeps `reviewed` honest: no primary document, no review."""
    root = _copy_packs(tmp_path)
    _promote(root)
    pack = _read(root, "blr.json")
    pack["sources"][0]["obtained"] = False
    _write(root, "blr.json", pack)
    assert any("obtained: false" in m for m in _messages(review, root))


def test_future_dated_review_is_refused(review, tmp_path):
    root = _copy_packs(tmp_path)
    _promote(root)
    _edit_rule_review(root, reviewedAt="2027-01-01")
    assert any("is in the future" in m for m in _messages(review, root))


def test_correction_without_the_superseded_value_is_refused(review, tmp_path):
    """An old compliance report must stay explainable after a value moves."""
    root = _copy_packs(tmp_path)
    _promote(root)
    _edit_rule_review(root, outcome="corrected")
    assert any("previousValue is blank" in m for m in _messages(review, root))


def test_verified_without_a_verification_artefact_is_refused(review, tmp_path):
    root = _copy_packs(tmp_path)
    _promote(root)
    pack = _read(root, "blr.json")
    pack["rules"][0]["confidence"] = "verified"
    _write(root, "blr.json", pack)
    assert any("needs review.verification" in m for m in _messages(review, root))


def test_verification_without_a_traceable_reference_is_refused(review, tmp_path):
    root = _copy_packs(tmp_path)
    record = copy.deepcopy(REVIEW_RECORD)
    record["verification"] = {"kind": "sanctioned-drawing", "reference": "  ", "date": "2026-08-12"}
    _promote(root, record)
    pack = _read(root, "blr.json")
    pack["rules"][0]["confidence"] = "verified"
    _write(root, "blr.json", pack)
    assert any("reference is blank" in m for m in _messages(review, root))


def test_review_record_on_a_rule_still_marked_seed_is_refused(review, tmp_path):
    """Work done and not claimed is a different failure, and still a failure."""
    root = _copy_packs(tmp_path)
    _promote(root)
    pack = _read(root, "blr.json")
    pack["rules"][0]["confidence"] = "seed"
    _write(root, "blr.json", pack)
    assert any("still 'seed'" in m for m in _messages(review, root))


# ===========================================================================
# 4. negative controls — pack-level claims
# ===========================================================================


def test_pack_claiming_reviewed_without_records_is_refused(review, tmp_path):
    """The headline case: a pack cannot declare itself reviewed on one rule's evidence."""
    root = _copy_packs(tmp_path)
    _promote(root, status="reviewed")
    messages = _messages(review, root)
    assert any("requires every rule at 'reviewed' or better" in m for m in messages), messages
    assert any("32 rule(s) are below it" in m for m in messages), messages


def test_pack_claiming_reviewed_with_no_rules_promoted_at_all_is_refused(review, tmp_path):
    """The cheapest possible lie: edit one word at the top of the file."""
    root = _copy_packs(tmp_path)
    pack = _read(root, "blr.json")
    pack["review"]["status"] = "reviewed"
    _write(root, "blr.json", pack)
    messages = _messages(review, root)
    assert any("33 rule(s) are below it" in m for m in messages), messages
    assert any("empty reviewers roster" in m for m in messages), messages
    assert any("no lastReviewedAt date" in m for m in messages), messages


def test_pack_still_claiming_unreviewed_after_a_promotion_is_refused(review, tmp_path):
    """Status must keep up with the rules in both directions.

    This is the test that found the bug it now guards. ``unreviewed`` was first
    written as a *floor* of ``seed``, and every rung is at or above ``seed``, so
    the condition could never be violated — bug class 1, a gate that silently
    never fires, reproduced exactly. It is a ceiling.
    """
    root = _copy_packs(tmp_path)
    _promote(root, status="unreviewed")
    messages = _messages(review, root)
    assert any("telling the UI nobody has looked" in m for m in messages), messages
    assert any("promoted above 'seed'" in m for m in messages), messages


def test_confidence_default_cannot_outrun_the_rules(review, tmp_path):
    """Promotion by omission: raise the default and every future rule inherits it."""
    root = _copy_packs(tmp_path)
    pack = _read(root, "blr.json")
    pack["confidenceDefault"] = "reviewed"
    _write(root, "blr.json", pack)
    assert any("promoted by omission" in m for m in _messages(review, root))


def test_pack_last_reviewed_before_its_own_newest_evidence_is_refused(review, tmp_path):
    root = _copy_packs(tmp_path)
    _promote(root)
    pack = _read(root, "blr.json")
    pack["review"]["lastReviewedAt"] = "2026-01-01"
    _write(root, "blr.json", pack)
    assert any("predates the newest rule review" in m for m in _messages(review, root))


def test_next_review_due_before_last_reviewed_is_refused(review, tmp_path):
    root = _copy_packs(tmp_path)
    _promote(root)
    pack = _read(root, "blr.json")
    pack["review"]["nextReviewDue"] = "2026-08-09"
    _write(root, "blr.json", pack)
    assert any("is not after lastReviewedAt" in m for m in _messages(review, root))


def test_a_pack_past_its_review_date_reports_stale(review, tmp_path):
    """`nextReviewDue` is not decorative — bye-laws get amended."""
    root = _copy_packs(tmp_path)
    _promote(root)
    assert _row(review, root, "blr")["stale"] is False
    findings, rows = review.audit(root, datetime.date(2027, 9, 1))
    assert findings == []
    assert next(r for r in rows if r["pack"] == "blr")["stale"] is True


def test_roster_entry_without_a_signature_date_is_refused(review, tmp_path):
    root = _copy_packs(tmp_path)
    _promote(root)
    pack = _read(root, "blr.json")
    del pack["review"]["reviewers"][0]["signedAt"]
    _write(root, "blr.json", pack)
    assert any("a sign-off has a date" in m for m in _messages(review, root))


def test_one_coa_number_claimed_by_two_names_is_refused(review, tmp_path):
    root = _copy_packs(tmp_path)
    _promote(root)
    pack = _read(root, "blr.json")
    impostor = copy.deepcopy(ROSTER_ENTRY)
    impostor["name"] = "Another Architect"
    pack["review"]["reviewers"].append(impostor)
    _write(root, "blr.json", pack)
    assert any("is claimed by both" in m for m in _messages(review, root))


# ===========================================================================
# 5. negative controls — index.json, which is what the API serves
# ===========================================================================


def test_index_overclaiming_confidence_is_refused(review, tmp_path):
    """GET /rulepacks copies this manifest; the UI labels every citation from it."""
    root = _copy_packs(tmp_path)
    index = _read(root, "index.json")
    for entry in index["packs"]:
        if entry["pack"] == "hyd":
            entry["confidence"] = "reviewed"
    _write(root, "index.json", index)
    messages = _messages(review, root)
    assert any("advertised as 'reviewed' but its weakest rule is 'seed'" in m for m in messages)


def test_index_overclaiming_review_status_is_refused(review, tmp_path):
    root = _copy_packs(tmp_path)
    index = _read(root, "index.json")
    for entry in index["packs"]:
        if entry["pack"] == "ncr":
            entry["review"] = "verified"
    _write(root, "index.json", index)
    assert any("advertised as review 'verified'" in m for m in _messages(review, root))


def test_index_rule_count_and_version_must_match_the_pack(review, tmp_path):
    root = _copy_packs(tmp_path)
    index = _read(root, "index.json")
    for entry in index["packs"]:
        if entry["pack"] == "vastu":
            entry["ruleCount"] = 99
            entry["version"] = "2019.01"
    _write(root, "index.json", index)
    messages = _messages(review, root)
    assert any("advertises 99 rules, the pack has 9" in m for m in messages), messages
    assert any("advertised at version '2019.01'" in m for m in messages), messages


def test_a_pack_missing_from_the_manifest_is_refused(review, tmp_path):
    """A pack nobody can see is a pack whose review state nobody tracks."""
    root = _copy_packs(tmp_path)
    index = _read(root, "index.json")
    index["packs"] = [e for e in index["packs"] if e["pack"] != "vastu"]
    _write(root, "index.json", index)
    assert any("exists on disk but is not in the manifest" in m for m in _messages(review, root))


# ===========================================================================
# 6. the ladder itself
# ===========================================================================


def test_a_new_rung_in_the_schema_fails_loudly(review, tmp_path):
    """A rung added to the schema with no decision here is bug class 2, exactly.

    ``buildingUse`` once defaulted to a value outside the pack enum and 83 rules
    went `not_applicable` behind a green report. A confidence rung this script
    does not know would land the same way: unranked, uncounted, silently treated
    as not-authoritative or not-seed depending on which comparison ran first.
    """
    root = _copy_packs(tmp_path)
    path = os.path.join(root, "schema", "rulepack.schema.json")
    with open(path, encoding="utf-8") as handle:
        schema = json.load(handle)
    schema["$defs"]["confidence"]["enum"] = ["seed", "sourced", "reviewed", "verified"]
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(schema, handle, indent=2)
    messages = _messages(review, root)
    assert any("does not match this script's ladder" in m for m in messages), messages


def test_confidence_off_the_ladder_is_refused(review, tmp_path):
    root = _copy_packs(tmp_path)
    pack = _read(root, "blr.json")
    pack["rules"][0]["confidence"] = "probably-fine"
    _write(root, "blr.json", pack)
    assert any("is not on the ladder" in m for m in _messages(review, root))


# ===========================================================================
# 7. the same gate at engine load time
# ===========================================================================


def test_the_engine_refuses_to_load_an_unearned_confidence(review, tmp_path):
    """The schema carries the gate too, so the engine cannot serve an unearned rung.

    ``scripts/rulepack_review.py`` runs in CI; this runs on every pack load, in
    every process that evaluates compliance. Weakening one does not open the door.
    """
    root = _copy_packs(tmp_path)
    pack = _read(root, "blr.json")
    pack["rules"][0]["confidence"] = "reviewed"
    _write(root, "blr.json", pack)

    with pytest.raises(SchemaValidationError) as excinfo:
        PackLoader(root).load(["blr"])
    assert any("'review' is missing" in e for e in excinfo.value.errors)


def test_the_engine_loads_a_properly_evidenced_promotion(review, tmp_path):
    """...and the rung reaches the resolved rule, which is what the UI reads."""
    root = _copy_packs(tmp_path)
    _promote(root)
    pack_set = PackLoader(root).load(["blr"])
    promoted = next(r for r in pack_set.rules if r.confidence != "seed")
    assert promoted.confidence == "reviewed"
    assert sum(1 for r in pack_set.rules if r.confidence == "reviewed") == 1


def test_the_engine_refuses_verified_without_its_artefact(review, tmp_path):
    root = _copy_packs(tmp_path)
    _promote(root)
    pack = _read(root, "blr.json")
    pack["rules"][0]["confidence"] = "verified"
    _write(root, "blr.json", pack)
    with pytest.raises(SchemaValidationError) as excinfo:
        PackLoader(root).load(["blr"])
    assert any("'verification' is missing" in e for e in excinfo.value.errors)


# ===========================================================================
# 8. the CLI wrapper
# ===========================================================================


def test_cli_exit_codes_and_output(review, tmp_path, capsys):
    """Exit 1 on findings is what makes this usable as a CI gate."""
    assert review.main(["verify", "--root", _PACKS, "--today", "2026-08-26"]) == 0
    assert "0 of them (0%)" in capsys.readouterr().out

    root = _copy_packs(tmp_path)
    pack = _read(root, "blr.json")
    pack["rules"][0]["confidence"] = "reviewed"
    _write(root, "blr.json", pack)
    assert review.main(["verify", "--root", root, "--today", "2026-08-26"]) == 1
    assert "no review record" in capsys.readouterr().out

    assert review.main(["coverage", "--root", _PACKS, "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert sum(p["rules"] for p in payload["packs"]) == 118

    assert review.main(["verify", "--root", _PACKS, "--today", "not-a-date"]) == 2


def test_coverage_table_names_every_pack_and_totals_them(review, capsys):
    assert review.main(["coverage", "--root", _PACKS]) == 0
    table = capsys.readouterr().out
    for pack_id in ("nbc-core", "blr", "ncr", "hyd", "vastu"):
        assert pack_id in table
    assert "TOTAL" in table
    assert "118" in table
