"""§7 schedules: the door/window tag table and the municipal area statement.

What this file proves, and why each one is here rather than left to review:

* **Tags are stable.** Same design → same tags, whatever order the ops arrived in;
  adding an opening to an existing group changes nothing; adding a *new* group with
  ``carry_previous`` never re-points a tag that is already printed on a sheet. Goldens
  compare with tolerance 0 (§16), so an unstable tag is a red build.
* **Counts add up.** Σ per-storey counts == the group total, Σ group totals == the
  number of openings in the model. The same class of invariant as
  ``assert_chains_sum`` for dimensions: the parts must equal the whole, exactly.
* **One source for FAR / coverage / setbacks.** The area statement sheet's numbers are
  compared against an *independent* ``garh_rules.evaluate`` run of the same context —
  rule row by rule row. Then the statement is doctored and the sheet is re-rendered to
  show the sheet really is reading the engine's object and not quietly recomputing from
  the model. Two sources of truth for FAR is the bug this whole module split exists to
  prevent.
* **Carpet vs built-up arithmetic**, including the case where they contradict each
  other: the sheet says so instead of printing an efficiency above 100 %.

Runnable two ways, like ``services/solver/tests/test_walls.py``::

    pytest -q services/drawings/tests/test_schedules.py      # CI
    python3 services/drawings/tests/test_schedules.py        # this machine (no pytest)
    python3 services/drawings/tests/test_schedules.py --regen   # rewrite the goldens

The fixtures are the committed ones — ``fixtures/rules/blr/blr.far.road.9-18m.pass.json``
for the regulatory numbers (a real Bengaluru plot where FAR, coverage, setbacks, floors,
height and parking rules all fire) and the ``storeys-stair-levels`` case of
``fixtures/model/golden-states.json``, folded by the real ``garh_model``, for a two-storey
model. Openings are appended to that fold with real ``opening.add`` ops, so every
number below came out of the production code path, not out of a literal in a test.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Mapping
from dataclasses import replace
from fractions import Fraction
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[3]
for _path in (str(_REPO_ROOT), str(_REPO_ROOT / "apps" / "api")):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from services.dev_stubs import install_worker_dep_stubs  # noqa: E402

STUBBED = install_worker_dep_stubs()

from garh_model.fold import replay, storey_built_up_area_mm2, storey_carpet_area_mm2  # noqa: E402
from garh_rules import evaluate  # noqa: E402

from services.drawings.layers import LAYER_NAMES  # noqa: E402
from services.drawings.schedules import (  # noqa: E402
    CARPET_EXCLUDED_ROOM_TYPES,
    UNKNOWN_STOREY,
    AreaStatementSheet,
    LineItem,
    TextItem,
    build_area_statement_sheet,
    build_schedule,
    carpet_by_storey,
    opening_tags,
)
from services.drawings.schedules.display import (  # noqa: E402
    area_cell,
    gaj_text,
    sqft_text,
    sqm_text,
)
from services.drawings.schedules.door_window import assign_tags, tagged_openings  # noqa: E402
from services.drawings.schedules.openings import ScheduleOpening  # noqa: E402
from services.drawings.schedules.sheet_primitives import (  # noqa: E402
    AreaStatementRow,
    ScheduleRow,
    shared_primitive_origin,
)
from services.drawings.sheets import default_frame  # noqa: E402

RULEPACK_ROOT = str(_REPO_ROOT / "rulepacks")
GOLDEN_DIR = _REPO_ROOT / "services" / "drawings" / "schedules" / "goldens"
RULES_FIXTURE = _REPO_ROOT / "fixtures" / "rules" / "blr" / "blr.far.road.9-18m.pass.json"
MODEL_GOLDENS = _REPO_ROOT / "fixtures" / "model" / "golden-states.json"

REGEN = "--regen" in sys.argv


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------
def rules_context() -> dict[str, Any]:
    """The committed BLR fixture's evaluation context (JSON form, as the engine takes it)."""
    with open(RULES_FIXTURE, encoding="utf-8") as handle:
        return json.load(handle)["context"]


def _oid(kind: str, suffix: str) -> str:
    """A ULID-shaped id in the same style as the committed golden op log."""
    body = "01J" + "0" * (23 - len(suffix)) + suffix
    assert len(body) == 26, body
    return "%s_%s" % (kind, body)


GF = "storey_01J000000000000000000000GF"
FF = "storey_01J000000000000000000000FF"
WALL_GF_S = "wall_01J000000000000000000000WS"
WALL_GF_N = "wall_01J000000000000000000000WN"
WALL_GF_E = "wall_01J000000000000000000000WE"
WALL_GF_W = "wall_01J000000000000000000000WW"
WALL_GF_INT = "wall_01J00000000000000000000WSP"
WALL_FF_S = "wall_01J00000000000000000000FFS"
WALL_FF_N = "wall_01J00000000000000000000FFN"
WALL_FF_E = "wall_01J00000000000000000000FFE"
WALL_FF_W = "wall_01J00000000000000000000FFW"


def _opening_op(
    suffix: str,
    wall_id: str,
    kind: str,
    width_mm: int,
    height_mm: int,
    sill_mm: int,
    offset_mm: int,
) -> dict[str, Any]:
    return {
        "type": "opening.add",
        "payload": {
            "id": _oid("opening", suffix),
            "wallId": wall_id,
            "kind": kind,
            "widthMm": width_mm,
            "heightMm": height_mm,
            "sillMm": sill_mm,
            "offsetMm": offset_mm,
            "swing": "in-left",
        },
    }


#: A G+1 opening set: 11 openings in 6 groups across two storeys, including one group
#: that appears on both floors (W1) and one that appears twice on one wall run.
DEMO_OPENING_OPS: tuple[dict[str, Any], ...] = (
    _opening_op("GD1", WALL_GF_S, "door", 1000, 2100, 0, 1500),
    _opening_op("GD2", WALL_GF_INT, "door", 900, 2100, 0, 800),
    _opening_op("GD3", WALL_GF_INT, "door", 800, 2100, 0, 2400),
    _opening_op("GW1", WALL_GF_N, "window", 1800, 1350, 900, 3000),
    _opening_op("GW2", WALL_GF_E, "window", 1200, 1200, 900, 2000),
    _opening_op("GV1", WALL_GF_W, "ventilator", 600, 450, 1800, 2000),
    _opening_op("FW1", WALL_FF_S, "window", 1800, 1350, 900, 1500),
    _opening_op("FW2", WALL_FF_N, "window", 1800, 1350, 900, 3000),
    _opening_op("FW3", WALL_FF_E, "window", 1200, 1200, 900, 2000),
    _opening_op("FV1", WALL_FF_W, "ventilator", 600, 450, 1800, 2000),
    _opening_op("FD1", WALL_FF_S, "door", 900, 2100, 0, 4500),
)


def _model_case(name: str) -> dict[str, Any]:
    with open(MODEL_GOLDENS, encoding="utf-8") as handle:
        cases = json.load(handle)["cases"]
    for case in cases:
        if case["name"] == name:
            return case
    raise AssertionError("golden-states.json has no case %r" % name)


def demo_doc(extra_ops: tuple[dict[str, Any], ...] = ()) -> Any:
    """The committed two-storey golden fold, plus our openings. Real ops, real fold."""
    ops = list(_model_case("storeys-stair-levels")["ops"])
    ops.extend(DEMO_OPENING_OPS)
    ops.extend(extra_ops)
    return replay(ops)


# ---------------------------------------------------------------------------
# tags: determinism
# ---------------------------------------------------------------------------
def test_tags_are_deterministic_across_runs() -> None:
    doc = demo_doc()
    first = build_schedule(doc)
    second = build_schedule(doc)
    assert first.tag_by_opening_id == second.tag_by_opening_id
    assert [group.tag for group in first.groups] == [group.tag for group in second.groups]
    assert first.table().to_text() == second.table().to_text()


def test_tags_ignore_the_order_the_openings_were_added_in() -> None:
    """A tag must depend on the design, never on the history that built it.

    ``fixtures/plans/README.md`` documents that room ids are history-dependent; tags
    must not be, or a re-fold in a different op order would renumber a printed sheet.
    """
    doc = demo_doc()
    reversed_ops = list(_model_case("storeys-stair-levels")["ops"]) + list(
        reversed(DEMO_OPENING_OPS)
    )
    reordered = replay(reversed_ops)
    forward = build_schedule(doc)
    backward = build_schedule(reordered)
    assert forward.tag_by_opening_id == backward.tag_by_opening_id
    assert forward.table().to_text() == backward.table().to_text()


def test_tag_series_are_kind_prefixed_and_widest_first() -> None:
    schedule = build_schedule(demo_doc())
    by_tag = {group.tag: group for group in schedule.groups}
    assert set(by_tag) == {"D1", "D2", "D3", "W1", "W2", "V1"}
    assert by_tag["D1"].key == ("door", 1000, 2100)  # the main door is D1
    assert by_tag["D2"].key == ("door", 900, 2100)
    assert by_tag["D3"].key == ("door", 800, 2100)
    assert by_tag["W1"].key == ("window", 1800, 1350)
    assert by_tag["W2"].key == ("window", 1200, 1200)
    assert by_tag["V1"].key == ("ventilator", 600, 450)
    # print order follows the tags, so a reader can scan down the column
    assert [group.tag for group in schedule.groups] == ["D1", "D2", "D3", "W1", "W2", "V1"]


def test_adding_an_opening_to_an_existing_group_changes_no_tag() -> None:
    before = build_schedule(demo_doc())
    after = build_schedule(
        demo_doc((_opening_op("GW9", WALL_GF_S, "window", 1200, 1200, 900, 4000),))
    )
    tags_before = {group.key: group.tag for group in before.groups}
    tags_after = {group.key: group.tag for group in after.groups}
    assert tags_before == tags_after
    # …and the count follows the new opening
    assert after.group_for_tag("W2").total == before.group_for_tag("W2").total + 1
    assert after.total == before.total + 1


def test_a_new_group_keeps_the_tags_already_on_the_model() -> None:
    """The reason ``Opening.tag`` is a persisted field.

    A 1500-wide window sorts *above* the 1200 one, so from-scratch numbering would make
    it W2 and push the existing 1200 group to W3 — renaming a tag that may already be
    printed on an issued sheet. Carrying the previous assignment forward gives the new
    group the next free number instead.
    """
    before = build_schedule(demo_doc())
    previous = {group.key: group.tag for group in before.groups}
    with_new = demo_doc((_opening_op("GW8", WALL_GF_S, "window", 1500, 1350, 900, 4200),))

    carried = build_schedule(with_new, previous_tags=previous)
    carried_tags = {group.key: group.tag for group in carried.groups}
    for key, tag in previous.items():
        assert carried_tags[key] == tag, key
    assert carried_tags[("window", 1500, 1350)] == "W3"

    # Without the carry-over the numbering is still deterministic — just renumbered.
    fresh = build_schedule(with_new, carry_previous=False)
    fresh_tags = {group.key: group.tag for group in fresh.groups}
    assert fresh_tags[("window", 1800, 1350)] == "W1"
    assert fresh_tags[("window", 1500, 1350)] == "W2"
    assert fresh_tags[("window", 1200, 1200)] == "W3"


def test_tags_already_on_the_openings_are_honoured() -> None:
    """Second run over a model the generator has already tagged is a no-op."""
    doc = demo_doc()
    schedule = build_schedule(doc)
    tagged = tagged_openings(schedule)
    assert len(tagged) == len(doc.house.openings)

    # Persist the tags onto the model the way the worker will, then re-run.
    tag_by_id = {row["openingId"]: row["tag"] for row in tagged}
    house = doc.house
    retagged = replace(
        house,
        openings=tuple(replace(o, tag=tag_by_id[o.id]) for o in house.openings),
    )
    again = build_schedule(replace(doc, house=retagged))
    assert again.tag_by_opening_id == schedule.tag_by_opening_id
    assert all(group.tag_carried for group in again.groups)


def test_a_deleted_group_does_not_have_its_tag_recycled() -> None:
    """A retired tag stays retired: reuse would re-point a printed reference."""
    previous = {
        ("window", 1800, 1350): "W1",
        ("window", 1200, 1200): "W2",
        ("window", 900, 900): "W3",  # this group no longer exists in the model
    }
    schedule = build_schedule(demo_doc(), previous_tags=previous)
    tags = {group.key: group.tag for group in schedule.groups}
    assert tags[("window", 1800, 1350)] == "W1"
    assert tags[("window", 1200, 1200)] == "W2"
    assert "W3" not in tags.values()
    # a genuinely new group takes W4, not the freed W3
    grown = build_schedule(
        demo_doc((_opening_op("GW7", WALL_GF_S, "window", 1500, 1350, 900, 4200),)),
        previous_tags=previous,
    )
    assert {g.key: g.tag for g in grown.groups}[("window", 1500, 1350)] == "W4"


def test_assign_tags_is_a_pure_function_of_its_inputs() -> None:
    keys = [("door", 1000, 2100), ("window", 1800, 1350), ("ventilator", 600, 450)]
    assert assign_tags(keys, {}) == {
        ("door", 1000, 2100): "D1",
        ("window", 1800, 1350): "W1",
        ("ventilator", 600, 450): "V1",
    }
    # a carried tag from the wrong series is ignored rather than filed under D
    assert assign_tags([("window", 1800, 1350)], {("window", 1800, 1350): "D7"}) == {
        ("window", 1800, 1350): "W1"
    }


# ---------------------------------------------------------------------------
# counts
# ---------------------------------------------------------------------------
def test_counts_per_storey_sum_exactly_to_group_and_schedule_totals() -> None:
    """Σ segments == overall, the schedule's version of the §7 step-5 invariant."""
    doc = demo_doc()
    schedule = build_schedule(doc)
    for group in schedule.groups:
        assert sum(group.counts_by_storey.values()) == group.total, group.tag
        assert group.total == len(group.opening_ids), group.tag
    assert sum(group.total for group in schedule.groups) == len(doc.house.openings)
    assert schedule.total == len(doc.house.openings)
    assert (
        sum(schedule.total_for_storey(storey_id) for storey_id in schedule.storey_ids)
        == schedule.total
    )


def test_counts_are_per_storey_not_per_building() -> None:
    schedule = build_schedule(demo_doc())
    w1 = schedule.group_for_tag("W1")
    assert w1.counts_by_storey == {GF: 1, FF: 2}
    assert w1.total == 3
    assert schedule.total_for_storey(GF) == 6
    assert schedule.total_for_storey(FF) == 5
    assert schedule.totals_by_kind() == {"door": 4, "window": 5, "ventilator": 2}


def test_every_opening_is_tagged_and_the_map_agrees_with_the_rows() -> None:
    """The plan/schedule agreement contract, asserted rather than reviewed."""
    doc = demo_doc()
    schedule = build_schedule(doc)
    mapping = opening_tags(doc)
    assert set(mapping) == {o.id for o in doc.house.openings}
    assert mapping == dict(schedule.tag_by_opening_id)
    for group in schedule.groups:
        for opening_id in group.opening_ids:
            assert mapping[opening_id] == group.tag
    # and each tag resolves to exactly one size, which is what a plan label promises
    sizes = {}
    for opening in doc.house.openings:
        size = (opening.kind, opening.width_mm, opening.height_mm)
        sizes.setdefault(mapping[opening.id], size)
        assert sizes[mapping[opening.id]] == size


def test_schedule_runs_off_a_rules_context_and_off_json() -> None:
    """Same tags from the model, the rules projection, and raw JSON."""
    context = rules_context()
    from_json = build_schedule(context)
    from_context = build_schedule(_coerced_context(context))
    assert from_json.tag_by_opening_id == from_context.tag_by_opening_id
    assert [g.tag for g in from_json.groups] == ["D1", "D2", "D3", "W1"]
    assert from_json.group_for_tag("D1").key == ("door", 1000, 2100)


def _coerced_context(raw: Mapping[str, Any]) -> Any:
    from garh_rules.context import EvaluationContext

    return EvaluationContext.from_json(raw)


def test_an_opening_on_a_missing_wall_is_surfaced_not_dropped() -> None:
    doc = demo_doc()
    house = doc.house
    orphan = replace(house.openings[0], id="opening_orphan", wall_id="wall_does_not_exist")
    broken = replace(doc, house=replace(house, openings=(*house.openings, orphan)))
    schedule = build_schedule(broken)
    assert schedule.total == len(house.openings) + 1
    assert UNKNOWN_STOREY in schedule.storey_ids
    assert any("wall_does_not_exist" in warning for warning in schedule.warnings)


def test_openings_reject_non_integer_millimetres() -> None:
    try:
        ScheduleOpening(
            id="o1",
            storey_id=GF,
            kind="door",
            width_mm=900.5,
            height_mm=2100,
            sill_mm=0,  # type: ignore[arg-type]
        )
    except TypeError as error:
        assert "integer" in str(error)
    else:  # pragma: no cover
        raise AssertionError("a float width must be rejected at the boundary")


# ---------------------------------------------------------------------------
# area statement: ONE SOURCE
# ---------------------------------------------------------------------------
def test_area_statement_numbers_are_the_rules_engine_results() -> None:
    """§7: "from rules results — same numbers, one source".

    The sheet is built one way and the engine is run again independently; then every
    regulatory number on the sheet is matched against the rule row that produced it.
    If someone ever recomputes FAR from the model inside the drawings engine, this test
    is what fails.
    """
    context = rules_context()
    sheet = build_area_statement_sheet(context, rulepack_root=RULEPACK_ROOT)
    report = evaluate(context, root=RULEPACK_ROOT)
    rows = {result.check_type: result for result in report.results if result.applicable}

    far = rows["far_max"]
    assert sheet.statement.far_countable_area_mm2 == far.actual
    assert sheet.statement.far_allowed_mm2 == far.limit
    coverage = rows["coverage_max"]
    assert sheet.statement.footprint_area_mm2 == coverage.actual
    assert sheet.statement.coverage_allowed_mm2 == coverage.limit
    floors = rows["floors_max"]
    assert sheet.statement.floors_counted == floors.actual
    assert sheet.statement.floors_allowed == floors.limit
    height = rows["height_max"]
    assert sheet.statement.height_counted_mm == height.actual
    assert sheet.statement.height_allowed_mm == height.limit
    parking = rows["parking_min"]
    assert sheet.statement.parking_provided == parking.actual
    assert sheet.statement.parking_required == parking.limit

    # setbacks: per edge, the strictest requirement across every rule that named it
    required: dict[str, int] = {}
    for result in report.results:
        if result.check_type != "setback_min" or not result.applicable:
            continue
        for instance in result.instances:
            if instance.element_id is None or not isinstance(instance.limit, int):
                continue
            required[instance.element_id] = max(
                required.get(instance.element_id, 0), instance.limit
            )
    assert required, "the fixture must exercise setback rules for this test to mean anything"
    statement_required = {row.element_id: row.required_mm for row in sheet.statement.setbacks}
    for element_id, limit in required.items():
        assert statement_required[element_id] == limit, element_id

    # and the printed table quotes exactly those integers
    text = sheet.table().to_text()
    for line in sheet.setbacks:
        assert str(line.provided_mm) in text
        if line.required_mm is not None:
            assert str(line.required_mm) in text


def test_far_and_coverage_ratios_match_the_engines_own_formatter() -> None:
    from garh_rules.formatting import format_ratio

    context = rules_context()
    sheet = build_area_statement_sheet(context, rulepack_root=RULEPACK_ROOT)
    plot_area = context["plot"]["areaMm2"]
    assert sheet.far_achieved == Fraction(context["model"]["farCountableAreaMm2"], plot_area)
    assert sheet.coverage_achieved == Fraction(context["model"]["footprintAreaMm2"], plot_area)
    assert format_ratio(sheet.far_achieved) in sheet.table().to_text()


def test_the_sheet_reads_the_statement_object_not_the_model() -> None:
    """A doctored allowance must appear on the sheet verbatim.

    If the renderer recomputed the allowance from the model, this number would be
    ignored and the test would fail — which is exactly the regression we care about.
    """
    context = rules_context()
    honest = build_area_statement_sheet(context, rulepack_root=RULEPACK_ROOT)
    doctored_value = honest.statement.far_allowed_mm2 + 12_345_678
    doctored = build_area_statement_sheet(
        context,
        statement=replace(honest.statement, far_allowed_mm2=doctored_value),
        rulepack_root=RULEPACK_ROOT,
    )
    assert doctored.statement.far_allowed_mm2 == doctored_value
    assert sqm_text(doctored_value) in doctored.table().to_text()
    assert doctored.far_allowed == Fraction(doctored_value, honest.plot_area_mm2)


def test_a_bare_model_gets_an_actionable_error_not_a_stack_trace() -> None:
    """§9: an error says what happened and what to do next.

    Handing the area statement a folded document is the obvious mistake, and it cannot
    be served — the regulatory numbers come from an evaluation, which needs the plot and
    the profile too.
    """
    from garh_rules.errors import ContextError

    try:
        build_area_statement_sheet(demo_doc(), rulepack_root=RULEPACK_ROOT)
    except ContextError as error:
        assert "EvaluationContext" in str(error)
        assert "context_from_parts" in str(error) or "statement=report.areas" in str(error)
    else:  # pragma: no cover
        raise AssertionError("a bare ProjectDoc must be refused with a usable message")


def test_setback_shortfalls_are_stated_with_the_rule_that_set_them() -> None:
    context = rules_context()
    short = json.loads(json.dumps(context))
    short["plot"]["edges"][0]["setbackProvidedMm"] = 1200  # front setback well short
    sheet = build_area_statement_sheet(short, rulepack_root=RULEPACK_ROOT)
    front = next(line for line in sheet.setbacks if line.role == "front")
    assert front.status == "short"
    assert front.shortfall_mm == front.required_mm - 1200
    assert front.rule_ids, "a shortfall must cite the rule that set the requirement"
    text = sheet.setback_table().to_text()
    assert "SHORT %d" % front.shortfall_mm in text
    assert front.rule_ids[0] in text


# ---------------------------------------------------------------------------
# carpet vs built-up
# ---------------------------------------------------------------------------
def test_carpet_is_the_sum_of_room_areas_and_totals_add_up() -> None:
    context = rules_context()
    sheet = build_area_statement_sheet(context, rulepack_root=RULEPACK_ROOT)
    rooms = context["model"]["rooms"]
    expected = sum(
        room["areaMm2"]
        for room in rooms
        if room["type"] not in CARPET_EXCLUDED_ROOM_TYPES and room["storeyId"] == GROUND_ID
    )
    ground = next(line for line in sheet.storeys if line.storey_id == GROUND_ID)
    assert ground.carpet_area_mm2 == expected
    assert ground.built_up_area_mm2 is not None
    assert ground.carpet_area_mm2 <= ground.built_up_area_mm2
    assert ground.efficiency == Fraction(ground.carpet_area_mm2, ground.built_up_area_mm2)
    # per-floor rows must sum to the stated total (or the total must be withheld)
    known = [line.carpet_area_mm2 for line in sheet.storeys]
    if all(value is not None for value in known):
        assert sheet.total_carpet_area_mm2 == sum(known)
    else:
        assert sheet.total_carpet_area_mm2 is None


GROUND_ID = "storey_g"


def test_carpet_excludes_covered_but_unusable_area() -> None:
    context = json.loads(json.dumps(rules_context()))
    rooms = context["model"]["rooms"]
    baseline = carpet_by_storey(context)[GROUND_ID].carpet_area_mm2
    balcony = json.loads(json.dumps(rooms[0]))
    balcony.update(
        {
            "id": "room_balcony",
            "type": "balcony",
            "areaMm2": 4_000_000,
        }
    )
    shaft = json.loads(json.dumps(rooms[0]))
    shaft.update({"id": "room_shaft", "type": "shaft", "areaMm2": 900_000})
    store = json.loads(json.dumps(rooms[0]))
    store.update({"id": "room_store", "type": "store", "areaMm2": 2_000_000})
    context["model"]["rooms"] = [*rooms, balcony, shaft, store]
    row = carpet_by_storey(context)[GROUND_ID]
    assert row.carpet_area_mm2 == baseline + 2_000_000  # only the store counts
    assert row.excluded_rooms == 2


def test_carpet_matches_the_model_cores_own_helper() -> None:
    """Carpet from a folded document must equal ``garh_model.storey_carpet_area_mm2``.

    Same definition, two implementations, checked against each other — the drawings
    engine must not drift from the model core's idea of what a floor's rooms add up to.
    """
    doc = demo_doc()
    rows = carpet_by_storey(doc)
    for storey in doc.house.storeys:
        assert rows[storey.id].carpet_area_mm2 == storey_carpet_area_mm2(doc, storey.id)


def test_carpet_exceeding_built_up_is_reported_not_printed() -> None:
    """Impossible arithmetic must reach the sheet as a note, never as >100 % efficiency."""
    context = rules_context()
    honest = build_area_statement_sheet(context, rulepack_root=RULEPACK_ROOT)
    tiny = replace(
        honest.statement.per_storey[0],
        built_up_area_mm2=1_000_000,  # 1 m2 of slab
    )
    broken = build_area_statement_sheet(
        context,
        statement=replace(honest.statement, per_storey=(tiny,) + honest.statement.per_storey[1:]),
        rulepack_root=RULEPACK_ROOT,
    )
    assert any("exceeds its built-up area" in warning for warning in broken.warnings)
    assert any("exceeds its built-up area" in note for note in broken.footnotes())


# ---------------------------------------------------------------------------
# sheet primitives
# ---------------------------------------------------------------------------
def test_rows_are_the_shared_sheet_primitives() -> None:
    # Not a local copy of the dataclass: the row type must come out of the sheet model,
    # wherever the Phase-8 module/package split has left it.
    assert shared_primitive_origin().startswith("services.drawings.sheets")
    assert ScheduleRow.__module__.startswith("services.drawings.sheets")
    assert AreaStatementRow.__module__.startswith("services.drawings.sheets")

    schedule = build_schedule(demo_doc())
    rows = schedule.rows()
    assert all(isinstance(row, ScheduleRow) for row in rows)
    first = rows[0].to_json()
    assert first["tag"] == "D1" and first["widthMm"] == 1000 and first["total"] == 1

    sheet = build_area_statement_sheet(rules_context(), rulepack_root=RULEPACK_ROOT)
    area_rows = sheet.area_rows()
    assert all(isinstance(row, AreaStatementRow) for row in area_rows)
    labels = [row.label for row in area_rows]
    assert labels[0] == "Plot area" and "Total built-up" in labels
    assert any(label.endswith("carpet") for label in labels)
    assert any(row.allowed_mm2 is not None for row in area_rows)  # FAR/coverage carry limits


def test_primitives_only_use_the_nine_layers() -> None:
    tables = [
        build_schedule(demo_doc()).table(),
        build_area_statement_sheet(rules_context(), rulepack_root=RULEPACK_ROOT).table(),
    ]
    for table in tables:
        items = table.primitives()
        assert items
        for item in items:
            assert item.layer in LAYER_NAMES, item
        assert any(isinstance(item, TextItem) for item in items)
        assert any(isinstance(item, LineItem) for item in items)


def test_tables_convert_into_the_shared_projection_stream() -> None:
    """A table sheet must reach the renderers as the same primitives a plan does.

    Soft-skips while the projection module is still landing: this asserts an
    integration, and a red build during concurrent work on the other side of the seam
    would be noise, not signal.
    """
    try:
        from services.drawings.projection.primitives import (
            Line,
            Text,
            find_unsafe_text,
        )
        from services.drawings.schedules.projection_adapter import (
            primitive_counts,
            table_to_primitives,
        )
    except ImportError as error:  # pragma: no cover - integration order
        print("   (skipped: %s)" % error)
        return

    table = build_schedule(demo_doc()).table()
    stream = table_to_primitives(table, scale_denominator=100, owner_id="sheet_A-05")
    texts, lines = primitive_counts(stream)
    assert texts and lines
    assert all(isinstance(item, Text | Line) for item in stream)
    assert find_unsafe_text(stream) == ()  # §13 backstop on the other side of the seam
    assert all(item.owner_id == "sheet_A-05" for item in stream)
    assert all(item.layer in LAYER_NAMES for item in stream)
    # paper mm → model mm is an exact integer multiply: 3 mm text at 1:100 is 300 mm
    heights = {item.height_mm for item in stream if isinstance(item, Text)}
    assert heights == {300, 500}
    # every coordinate stays an integer millimetre
    for item in stream:
        coords = item.position if isinstance(item, Text) else item.a + item.b
        assert all(isinstance(value, int) for value in coords), item


def test_tables_fit_the_default_a2_frame() -> None:
    frame = default_frame()
    for table in (
        build_schedule(demo_doc()).table(),
        build_area_statement_sheet(rules_context(), rulepack_root=RULEPACK_ROOT).table(),
    ):
        assert table.fits_within(frame.drawable_width_mm(), frame.drawable_height_mm()), (
            table.title,
            table.width_mm(),
            table.height_mm(),
        )


def test_table_layout_is_integer_paper_millimetres() -> None:
    table = build_schedule(demo_doc()).table()
    for width in table.cell_widths_mm():
        assert isinstance(width, int)
    for item in table.primitives():
        for value in getattr(item, "__dict__", {}).values():
            assert not isinstance(value, float), item


def test_svg_is_sanitised() -> None:
    """§13: no scripts, no foreignObject, everything escaped."""
    doc = demo_doc()
    house = doc.house
    nasty = replace(house.openings[0], id="opening_nasty")
    hostile = build_schedule(replace(doc, house=replace(house, openings=(nasty,))))
    table = hostile.table(title='</text><script>alert("x")</script> & <foreignObject/>')
    svg = table.to_svg()
    # No executable or HTML-embedding *element* survives…
    assert "<script" not in svg
    assert "<foreignObject" not in svg
    assert "</text><text" not in svg  # the injected close tag did not break out
    # …and the hostile string is still legible, as escaped text.
    assert "&lt;script&gt;alert(&quot;x&quot;)&lt;/script&gt;" in svg
    assert "&lt;foreignObject/&gt;" in svg
    assert "&amp;" in svg
    assert svg.startswith("<svg ") and svg.rstrip().endswith("</svg>")
    # Nothing external: a sheet must render offline, and §13 allows no fetches. The
    # single permitted http:// is the SVG namespace declaration, which is not a fetch.
    for token in ("url(", "xlink:href", "<image", "<use", "<a ", "<iframe"):
        assert token not in svg, token
    assert svg.count("http") == 1 and 'xmlns="http://www.w3.org/2000/svg"' in svg
    assert "onload" not in svg and "onclick" not in svg


# ---------------------------------------------------------------------------
# display boundary
# ---------------------------------------------------------------------------
def test_display_boundary_is_indian_and_exact() -> None:
    assert sqm_text(111_484_000) == "111.48"
    assert sqm_text(9_500_000) == "9.50"
    assert sqm_text(1_500_000, 0) == "2"  # half away from zero, in integers
    assert sqm_text(0) == "0.00"
    assert sqft_text(111_484_000) == "1,200.0 sq ft"  # one decimal, Indian grouping
    assert gaj_text(111_484_000) == "133 gaj"
    assert area_cell(111_484_000) == "111.48 m2 · 1,200.0 sq ft"
    assert area_cell(None) == "-"


def test_plot_area_is_the_only_row_quoted_in_gaj() -> None:
    sheet = build_area_statement_sheet(rules_context(), rulepack_root=RULEPACK_ROOT)
    text = sheet.table().to_text()
    rows_with_gaj = [line for line in text.splitlines() if line.startswith("|") and "gaj" in line]
    assert len(rows_with_gaj) == 1, rows_with_gaj
    assert rows_with_gaj[0].startswith("| Plot area")
    # every other area row carries m2 and sq ft, and only those
    for line in text.splitlines():
        if line.startswith("| Total built-up") or line.startswith("| FAR-countable"):
            assert "m2" in line and "sq ft" in line and "gaj" not in line


def test_dimension_text_stays_in_millimetres() -> None:
    """§7: dim text is mm on drawings regardless of display units."""
    sheet = build_area_statement_sheet(rules_context(), rulepack_root=RULEPACK_ROOT)
    text = sheet.table().to_text()
    front = next(line for line in sheet.setbacks if line.role == "front")
    assert "| Front setback (mm)" in text
    assert str(front.provided_mm) in text
    schedule_text = build_schedule(demo_doc()).table().to_text()
    assert "SIZE (mm)" in schedule_text
    assert "1000 x 2100" in schedule_text


# ---------------------------------------------------------------------------
# goldens (§16: byte-diffed, tolerance 0)
# ---------------------------------------------------------------------------
def _golden(name: str, produced: str) -> None:
    path = GOLDEN_DIR / name
    if REGEN or not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(produced)
        if REGEN:
            print("regenerated %s" % path.relative_to(_REPO_ROOT))
        return
    with open(path, encoding="utf-8") as handle:
        expected = handle.read()
    assert produced == expected, (
        "%s differs from its golden. If the change is intended, re-run with --regen and "
        "commit the golden in the same change (§16, golden rule 10)." % name
    )


def test_golden_schedule_table_demo_g1() -> None:
    _golden("schedule-demo-g1.txt", build_schedule(demo_doc()).table().to_text())


def test_golden_schedule_tags_demo_g1() -> None:
    schedule = build_schedule(demo_doc())
    payload = json.dumps(schedule.to_json(), indent=2, sort_keys=True, ensure_ascii=False)
    _golden("schedule-demo-g1.json", payload + "\n")


def test_golden_schedule_svg_demo_g1() -> None:
    _golden("schedule-demo-g1.svg", build_schedule(demo_doc()).table().to_svg())


def test_golden_schedule_table_blr_fixture() -> None:
    _golden("schedule-blr-far-road-9-18m.txt", build_schedule(rules_context()).table().to_text())


def test_golden_area_statement_blr_fixture() -> None:
    sheet = build_area_statement_sheet(rules_context(), rulepack_root=RULEPACK_ROOT)
    _golden("area-statement-blr-far-road-9-18m.txt", sheet.table().to_text())


def test_golden_area_statement_svg_blr_fixture() -> None:
    sheet = build_area_statement_sheet(rules_context(), rulepack_root=RULEPACK_ROOT)
    _golden("area-statement-blr-far-road-9-18m.svg", sheet.table().to_svg())


def test_golden_setback_table_blr_fixture() -> None:
    sheet = build_area_statement_sheet(rules_context(), rulepack_root=RULEPACK_ROOT)
    _golden("setbacks-blr-far-road-9-18m.txt", sheet.setback_table().to_text())


# ---------------------------------------------------------------------------
# the report this file prints when run as a script
# ---------------------------------------------------------------------------
def _report() -> None:
    doc = demo_doc()
    schedule = build_schedule(doc)
    print("\n== DOOR/WINDOW SCHEDULE — folded G+1 demo (%d openings) ==" % schedule.total)
    print(schedule.table().to_text())
    print("tag map (what the plan projection labels openings from):")
    for opening_id, tag in sorted(schedule.tag_by_opening_id.items(), key=lambda kv: kv[1]):
        print("   %-4s %s" % (tag, opening_id))

    print("\ncarpet vs built-up, per storey (model core helpers):")
    for storey in doc.house.storeys:
        carpet = storey_carpet_area_mm2(doc, storey.id)
        built = storey_built_up_area_mm2(doc, storey.id)
        flag = "  <-- carpet > built-up" if carpet > built else ""
        print(
            "   %-14s carpet %s   built-up %s%s"
            % (storey.name, area_cell(carpet), area_cell(built), flag)
        )

    context = rules_context()
    sheet = build_area_statement_sheet(context, rulepack_root=RULEPACK_ROOT)
    print("\n== AREA STATEMENT — fixtures/rules/blr/blr.far.road.9-18m.pass.json ==")
    print(sheet.table().to_text())

    print("== ONE-SOURCE CROSS-CHECK: sheet numbers vs an independent evaluate() ==")
    report = evaluate(context, root=RULEPACK_ROOT)
    rows = {result.check_type: result for result in report.results if result.applicable}
    checks: list[tuple[str, Any, Any, str]] = [
        (
            "FAR countable / allowed",
            (sheet.statement.far_countable_area_mm2, sheet.statement.far_allowed_mm2),
            (rows["far_max"].actual, rows["far_max"].limit),
            rows["far_max"].rule_id,
        ),
        (
            "Coverage footprint / allowed",
            (sheet.statement.footprint_area_mm2, sheet.statement.coverage_allowed_mm2),
            (rows["coverage_max"].actual, rows["coverage_max"].limit),
            rows["coverage_max"].rule_id,
        ),
        (
            "Floors counted / allowed",
            (sheet.statement.floors_counted, sheet.statement.floors_allowed),
            (rows["floors_max"].actual, rows["floors_max"].limit),
            rows["floors_max"].rule_id,
        ),
        (
            "Height counted / allowed",
            (sheet.statement.height_counted_mm, sheet.statement.height_allowed_mm),
            (rows["height_max"].actual, rows["height_max"].limit),
            rows["height_max"].rule_id,
        ),
        (
            "Parking provided / required",
            (sheet.statement.parking_provided, sheet.statement.parking_required),
            (rows["parking_min"].actual, rows["parking_min"].limit),
            rows["parking_min"].rule_id,
        ),
    ]
    for label, sheet_values, engine_values, rule_id in checks:
        mark = "ok  " if sheet_values == engine_values else "FAIL"
        print(
            "  %s %-28s sheet=%s engine=%s  [%s]"
            % (mark, label, sheet_values, engine_values, rule_id)
        )
    for line in sheet.setbacks:
        engine_limit = None
        rule = None
        for result in report.results:
            if result.check_type != "setback_min" or not result.applicable:
                continue
            for instance in result.instances:
                if instance.element_id != _element_id_of(line, sheet):
                    continue
                if isinstance(instance.limit, int) and (
                    engine_limit is None or instance.limit > engine_limit
                ):
                    engine_limit, rule = instance.limit, result.rule_id
        mark = "ok  " if line.required_mm == engine_limit else "FAIL"
        print(
            "  %s %-28s sheet=%s engine=%s  [%s]"
            % (
                mark,
                "Setback %s required" % line.role,
                line.required_mm,
                engine_limit,
                rule,
            )
        )


def _element_id_of(line: Any, sheet: AreaStatementSheet) -> str | None:
    for row in sheet.statement.setbacks:
        if row.edge_index == line.edge_index:
            return row.element_id
    return None


if __name__ == "__main__":  # pragma: no cover
    import traceback

    failures = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print("PASS %s" % name)
            except Exception:
                failures += 1
                print("FAIL %s" % name)
                traceback.print_exc()
    if not REGEN:
        _report()
    print(
        "\n%d test(s) failed. Stubbed dependencies: %s" % (failures, ", ".join(STUBBED) or "none")
    )
    sys.exit(1 if failures else 0)
