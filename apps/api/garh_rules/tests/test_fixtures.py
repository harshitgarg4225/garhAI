"""The gate: every fixture in ``fixtures/rules/index.json``, enumerated from the manifest.

Playbook §16 requires at least one passing and one failing fixture per rule, and
``fixtures/rules/README.md`` adds the reason the corpus is enumerated from the
manifest and never by globbing: *a rule whose fixtures were deleted must break the
suite loudly instead of silently dropping out of it.*

So this module asserts three separate things:

1. **Coverage.** Every rule id in every pack appears in the manifest with a
   ``pass`` and a ``fail`` fixture. A missing fixture is a failure, not a skip.
2. **Agreement.** Each fixture's expected row — status, actual, limit, elements,
   and satisfaction where present — matches what the engine produces. The fixture
   generator and this engine are two independent statements of the check semantics
   (``rulepacks/README.md``'s table is the third and is the tiebreaker), so this is
   where a divergence surfaces.
3. **Integrity.** The manifest, the files on disk and the packs describe the same
   corpus: no orphaned file, no fixture pointing at a rule that no longer exists,
   no ``kind``/``status`` mismatch.
"""

from __future__ import annotations

import json
import os
from typing import Any

import pytest

from garh_rules import evaluate, load_pack_set
from garh_rules.results import FAIL, NOT_APPLICABLE, PASS, WARN

from .conftest import FIXTURE_DIR, PACK_IDS, RULEPACK_DIR, fixture_index, load_fixture, read_json

_INDEX = fixture_index()
_ENTRIES: list[dict[str, Any]] = list(_INDEX["fixtures"])


def _all_pack_rules() -> dict[str, str]:
    """``ruleId -> packId`` straight from the pack files."""
    owner: dict[str, str] = {}
    for pack_id in PACK_IDS:
        pack = read_json(os.path.join(RULEPACK_DIR, "%s.json" % pack_id))
        for rule in pack["rules"]:
            owner[rule["id"]] = pack_id
    return owner


_RULE_OWNER = _all_pack_rules()


# ---------------------------------------------------------------------------
# 1. Coverage
# ---------------------------------------------------------------------------


def test_manifest_counts_match_the_packs() -> None:
    assert _INDEX["counts"]["rules"] == len(_RULE_OWNER)
    assert _INDEX["counts"]["fixtures"] == len(_ENTRIES)
    assert _INDEX["counts"]["packs"] == len(PACK_IDS)


def test_every_rule_has_a_passing_and_a_failing_fixture() -> None:
    seen: dict[str, dict[str, int]] = {}
    for pack_entry in _INDEX["packs"]:
        for rule_entry in pack_entry["rules"]:
            seen[rule_entry["ruleId"]] = {
                "pass": len(rule_entry["pass"]),
                "fail": len(rule_entry["fail"]),
            }
    missing_rule = sorted(set(_RULE_OWNER) - set(seen))
    assert not missing_rule, "rules with no manifest entry: %s" % missing_rule
    stale = sorted(set(seen) - set(_RULE_OWNER))
    assert not stale, "manifest names rules that no longer exist: %s" % stale
    uncovered = sorted(
        rule_id for rule_id, counts in seen.items() if counts["pass"] < 1 or counts["fail"] < 1
    )
    assert not uncovered, "rules missing a pass or fail fixture: %s" % uncovered


def test_no_orphaned_fixture_files() -> None:
    listed = {entry["path"].replace("\\", "/") for entry in _ENTRIES}
    on_disk = set()
    for pack_id in PACK_IDS:
        directory = os.path.join(FIXTURE_DIR, pack_id)
        if not os.path.isdir(directory):
            continue
        for name in os.listdir(directory):
            if name.endswith(".json"):
                on_disk.add("%s/%s" % (pack_id, name))
    assert not (on_disk - listed), "fixture files absent from the manifest: %s" % sorted(
        on_disk - listed
    )
    assert not (listed - on_disk), "manifest names missing files: %s" % sorted(listed - on_disk)


# ---------------------------------------------------------------------------
# 2. Agreement — the real gate
# ---------------------------------------------------------------------------


def _ids(entries: list[dict[str, Any]]) -> list[str]:
    return [entry["fixtureId"] for entry in entries]


@pytest.mark.parametrize("entry", _ENTRIES, ids=_ids(_ENTRIES))
def test_fixture_produces_the_expected_row(entry: dict[str, Any]) -> None:
    doc = load_fixture(entry["path"])
    assert doc["fixtureId"] == entry["fixtureId"]
    assert doc["ruleId"] == entry["ruleId"]

    report = evaluate(doc["context"], root=RULEPACK_DIR)
    row = report.rule(doc["ruleId"])
    assert row is not None, "the engine produced no row for %s" % doc["ruleId"]

    expected = doc["expected"]
    assert row.status == expected["status"], doc["description"]
    assert row.actual == expected["actual"], "actual: %s" % doc["description"]
    assert row.limit == expected["limit"], "limit: %s" % doc["description"]
    assert list(row.elements) == list(expected.get("elements", []))

    if "satisfaction" in expected:
        assert row.satisfaction is not None, "%s should carry a satisfaction" % doc["ruleId"]
        assert row.satisfaction.numerator == expected["satisfaction"]["num"]
        assert row.satisfaction.denominator == expected["satisfaction"]["den"]


@pytest.mark.parametrize("entry", _ENTRIES, ids=_ids(_ENTRIES))
def test_fixture_kind_agrees_with_its_status(entry: dict[str, Any]) -> None:
    """A ``pass`` fixture must pass; a ``fail`` fixture must be ``warn`` or ``fail``.

    ``fail`` does not always mean status ``fail``: it is the rule's severity,
    clamped by any scoring-mode ceiling. The three RWH rules are ``warn`` by design
    because they check a declaration rather than a structure.
    """
    doc = load_fixture(entry["path"])
    status = doc["expected"]["status"]
    if doc["kind"] == "pass":
        assert status == PASS
    elif doc["kind"] == "fail":
        assert status in (WARN, FAIL)
    else:
        assert status in (PASS, WARN, FAIL, NOT_APPLICABLE)


@pytest.mark.parametrize("entry", _ENTRIES, ids=_ids(_ENTRIES))
def test_a_passing_fixture_names_no_offender(entry: dict[str, Any]) -> None:
    doc = load_fixture(entry["path"])
    if doc["expected"]["status"] != PASS:
        return
    assert doc["expected"].get("elements", []) == []
    report = evaluate(doc["context"], root=RULEPACK_DIR)
    row = report.rule(doc["ruleId"])
    assert row is not None
    assert row.elements == ()


# ---------------------------------------------------------------------------
# 3. Whole-report invariants, checked on every fixture context
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("entry", _ENTRIES, ids=_ids(_ENTRIES))
def test_report_is_json_serialisable_and_complete(entry: dict[str, Any]) -> None:
    """Every row is JSON-safe and every loaded rule produced exactly one row.

    The JSON check is not ceremony: statuses and satisfactions are
    :class:`~fractions.Fraction` internally, and one leaking into
    ``compliance_reports.results`` would fail at ``json.dumps`` inside a request.
    """
    doc = load_fixture(entry["path"])
    report = evaluate(doc["context"], root=RULEPACK_DIR)
    pack_set = load_pack_set(doc["context"]["packs"], root=RULEPACK_DIR)

    evaluated_ids = [row.rule_id for row in report.results]
    assert len(evaluated_ids) == len(set(evaluated_ids)), "duplicate rows in one report"
    expected_ids = [
        rule.id
        for rule in pack_set.rules
        if not (rule.scoring and doc["context"]["vastuMode"] == "off")
    ]
    assert evaluated_ids == expected_ids, "rows must be one per rule, in resolved pack order"

    encoded = json.dumps(report.to_json(full=True), sort_keys=True)
    assert "Fraction" not in encoded


def test_results_order_is_stable_across_runs() -> None:
    """Determinism: two runs of the same context give byte-identical JSON."""
    doc = load_fixture(_ENTRIES[0]["path"])
    first = json.dumps(evaluate(doc["context"], root=RULEPACK_DIR).to_json(), sort_keys=True)
    second = json.dumps(evaluate(doc["context"], root=RULEPACK_DIR).to_json(), sort_keys=True)
    assert first == second


def test_every_check_type_is_exercised_by_the_corpus() -> None:
    """The corpus must reach all 18 types, or a type is untested in practice."""
    from garh_rules import CHECK_TYPES

    exercised = set()
    for pack_id in PACK_IDS:
        pack = read_json(os.path.join(RULEPACK_DIR, "%s.json" % pack_id))
        for rule in pack["rules"]:
            exercised.add(rule["check"]["type"])
    assert exercised == set(CHECK_TYPES), "types never exercised: %s" % sorted(
        set(CHECK_TYPES) - exercised
    )


def test_every_custom_fn_is_exercised() -> None:
    from garh_rules import CUSTOM_FNS

    used = set()
    for pack_id in PACK_IDS:
        pack = read_json(os.path.join(RULEPACK_DIR, "%s.json" % pack_id))
        for rule in pack["rules"]:
            if rule["check"]["type"] == "custom":
                used.add(rule["check"]["fn"])
    assert used == set(CUSTOM_FNS)


def _pack_version_pairs() -> list[tuple[str, str]]:
    return [
        (pack_id, read_json(os.path.join(RULEPACK_DIR, "%s.json" % pack_id))["version"])
        for pack_id in PACK_IDS
    ]


def test_pack_versions_are_pinned_into_the_report() -> None:
    """A report has to stay explainable by the exact rules that produced it (§6)."""
    declared = dict(_pack_version_pairs())
    doc = load_fixture(_ENTRIES[0]["path"])
    report = evaluate(doc["context"], root=RULEPACK_DIR)
    for pack_id, version in report.pack_versions.items():
        assert version == declared[pack_id]
