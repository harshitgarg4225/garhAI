"""D-1: revision records, the geometric diff behind the clouds, and the register.

What this file proves, and why each gate is here rather than left to review:

* **The register refuses what a set cannot survive.** A reused revision number, a date
  that runs backwards, a date that is not a real day, an empty author. Every one of those
  is a defect that reaches a signed drawing if it is not caught at construction.
* **The clouds are DERIVED.** Two real folds of the real op log are diffed by the real
  engine, and the cloud lands on the element that actually moved. Nothing here is a
  hand-maintained list, which is the entire difference between this feature and a comment
  in a title block.
* **The arc chain closes exactly.** Every scallop's end point is the next one's start
  point, with zero tolerance, and the chain returns to where it started. That is the
  property the integer-only construction was chosen for, so it is negative-tested:
  ``test_negative_control_*`` breaks the even-chord partition and shows the closure gate
  going red.
* **The clouds reach the paper.** Not the primitive list — the rendered SVG of a sheet
  built by the production ``build_sheet_set``. This repository has shipped a layer that
  tagged its meshes, documented itself as integrated and never called the registry; a
  cloud that exists only in a tuple is the same bug.
* **And they cost nothing when there is no revision.** The same sheet set built without a
  register is byte-identical to the one built before this feature existed — asserted
  against the committed §16 goldens by ``scripts/sheet_goldens.py``, and asserted here by
  counting cloud primitives on an unrevised set.

Runnable two ways, like ``test_schedules.py``::

    pytest -q services/drawings/tests/test_revisions.py     # CI
    python3 services/drawings/tests/test_revisions.py       # no pytest needed

Fixtures are the committed ones: the ``storeys-stair-levels`` case of
``fixtures/model/golden-states.json``, folded by the real ``garh_model``, and edited with
real ops (``wall.move``, ``opening.add``, ``wall.delete``, ``room.assign``) so every
before/after state below came out of the production code path.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[3]
for _path in (str(_REPO_ROOT), str(_REPO_ROOT / "apps" / "api")):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from services.dev_stubs import install_worker_dep_stubs  # noqa: E402

STUBBED = install_worker_dep_stubs()

from garh_model.fold import replay  # noqa: E402

from services.drawings.layers import LAYER_NAMES  # noqa: E402
from services.drawings.render.primitives import Arc, Polyline, Text  # noqa: E402
from services.drawings.render.reference_sheets import build_sheet_set  # noqa: E402
from services.drawings.render.svg import render_sheet_svg  # noqa: E402
from services.drawings.revisions import (  # noqa: E402
    COMPARED_KINDS,
    EXCLUDED_KINDS,
    Revision,
    RevisionHistory,
    cloud_regions,
    cluster_boxes,
    diff_models,
    revision_cloud,
    revision_marks,
    revision_register_table,
    revision_tag,
)
from services.drawings.revisions import cloud as cloud_module  # noqa: E402
from services.drawings.sheets import TitleBlock  # noqa: E402

MODEL_GOLDENS = _REPO_ROOT / "fixtures" / "model" / "golden-states.json"


def _oid(kind: str, suffix: str) -> str:
    """A ULID-shaped id in the same style as the committed golden op log (26 chars)."""
    body = "01J" + "0" * (23 - len(suffix)) + suffix
    assert len(body) == 26, body
    return "%s_%s" % (kind, body)


GF = "storey_01J000000000000000000000GF"
FF = "storey_01J000000000000000000000FF"
PARTITION = "wall_01J00000000000000000000WSP"
GF_SOUTH = "wall_01J000000000000000000000WS"


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------
def _base_ops() -> list[dict[str, Any]]:
    with open(MODEL_GOLDENS, encoding="utf-8") as handle:
        cases = json.load(handle)["cases"]
    for case in cases:
        if case["name"] == "storeys-stair-levels":
            return list(case["ops"])
    raise AssertionError("golden-states.json has no case 'storeys-stair-levels'")


def doc(extra_ops: tuple[dict[str, Any], ...] = ()) -> Any:
    """The committed two-storey fold, plus whatever ops this test wants. Real fold."""
    return replay([*_base_ops(), *extra_ops])


MOVE_PARTITION: dict[str, Any] = {
    "type": "wall.move",
    "payload": {
        "wallId": PARTITION,
        "a": {"x": 3500, "y": 0},
        "b": {"x": 3500, "y": 4000},
    },
}
ADD_DOOR: dict[str, Any] = {
    "type": "opening.add",
    "payload": {
        "id": _oid("opening", "RV1"),
        "wallId": GF_SOUTH,
        "kind": "door",
        "widthMm": 1000,
        "heightMm": 2100,
        "sillMm": 0,
        "offsetMm": 1500,
        "swing": "in-left",
    },
}

HISTORY = RevisionHistory(
    [
        Revision("R1", "05-03-2026", "Issued for sanction", "SG"),
        Revision("R2", "19-03-2026", "Partition wall shifted 500 east per client", "SG"),
    ]
)


# ---------------------------------------------------------------------------
# the record and the register
# ---------------------------------------------------------------------------
def test_a_revision_carries_number_date_description_and_author() -> None:
    revision = Revision("R1", "05-03-2026", "Issued for sanction", "S. Garg")
    assert revision.title_block_row() == ("R1", "05-03-2026", "Issued for sanction")
    assert revision.register_row() == ("R1", "05-03-2026", "Issued for sanction", "S. Garg")
    assert revision.issued_on.day == 5 and revision.issued_on.month == 3
    assert revision.to_json()["author"] == "S. Garg"


def test_a_date_that_is_not_ddmmyyyy_is_refused() -> None:
    for bad in ("5-3-2026", "2026-03-05", "05/03/2026", "05-03-26", ""):
        try:
            Revision("R1", bad, "note", "SG")
        except ValueError as error:
            assert "DD-MM-YYYY" in str(error), (bad, error)
        else:  # pragma: no cover
            raise AssertionError("%r must be refused: a sheet prints it verbatim" % bad)


def test_a_date_that_is_not_a_real_day_is_refused() -> None:
    try:
        Revision("R1", "31-02-2026", "note", "SG")
    except ValueError as error:
        assert "not a real date" in str(error)
    else:  # pragma: no cover
        raise AssertionError("31 February must be refused")


def test_empty_and_unprintable_fields_are_refused_by_name() -> None:
    for kwargs, field in (
        ({"number": "  "}, "number"),
        ({"description": ""}, "description"),
        ({"author": "\t"}, "author"),
    ):
        base = {"number": "R1", "date": "05-03-2026", "description": "note", "author": "SG"}
        base.update(kwargs)
        try:
            Revision(**base)  # type: ignore[arg-type]
        except ValueError as error:
            assert field in str(error), (field, error)
        else:  # pragma: no cover
            raise AssertionError("an empty %s must be refused" % field)
    try:
        Revision("R1", "05-03-2026", "line one\nline two", "SG")
    except ValueError as error:
        assert "control character" in str(error)
    else:  # pragma: no cover
        raise AssertionError("a newline in a table cell must be refused")


def test_an_over_long_description_is_refused_not_truncated() -> None:
    """A silently truncated description loses the thing the reviewer asked to see."""
    try:
        Revision("R1", "05-03-2026", "x" * 200, "SG")
    except ValueError as error:
        assert "limit is 120" in str(error)
    else:  # pragma: no cover
        raise AssertionError("an over-long description must be refused")


def test_a_reused_revision_number_is_refused() -> None:
    try:
        RevisionHistory(
            [
                Revision("R1", "05-03-2026", "first", "SG"),
                Revision("R1", "19-03-2026", "second", "SG"),
            ]
        )
    except ValueError as error:
        assert "used twice" in str(error) and "R1" in str(error)
    else:  # pragma: no cover
        raise AssertionError("a reused number makes every citation of it ambiguous")


def test_a_register_whose_dates_run_backwards_is_refused() -> None:
    try:
        RevisionHistory(
            [
                Revision("R1", "19-03-2026", "first", "SG"),
                Revision("R2", "05-03-2026", "second", "SG"),
            ]
        )
    except ValueError as error:
        assert "before" in str(error)
    else:  # pragma: no cover
        raise AssertionError("the register is in issue order")
    # …but two issues on one day are perfectly normal and must be allowed.
    same_day = RevisionHistory(
        [
            Revision("R1", "19-03-2026", "first", "SG"),
            Revision("R2", "19-03-2026", "second", "SG"),
        ]
    )
    assert len(same_day) == 2


def test_the_register_navigates_by_number() -> None:
    assert HISTORY.latest is not None and HISTORY.latest.number == "R2"
    assert HISTORY.by_number("R1").description.startswith("Issued")
    assert HISTORY.previous_to("R2") is HISTORY[0]
    assert HISTORY.previous_to("R1") is None
    try:
        HISTORY.by_number("R9")
    except KeyError as error:
        assert "R1, R2" in str(error)
    else:  # pragma: no cover
        raise AssertionError("an unknown revision must fail loudly")


def test_the_register_table_carries_the_author_column() -> None:
    """The whole reason the register exists next to the title block's three-column strip."""
    text = revision_register_table(HISTORY).to_text()
    assert "BY" in text
    for record in HISTORY:
        assert record.number in text
        assert record.date in text
        assert record.description in text
        assert record.author in text
    # …and the strip is the same history, minus that column.
    assert HISTORY.title_block_rows() == tuple(row[:3] for row in HISTORY.register_rows())


def test_a_legacy_three_column_revision_still_loads() -> None:
    """The API's existing three-string rows must not become a hard error."""
    history = RevisionHistory([("R1", "05-03-2026", "Issued for sanction")])
    assert history[0].author == "-"
    assert history[0].register_row()[3] == "-"


# ---------------------------------------------------------------------------
# the diff — real folds, real ops
# ---------------------------------------------------------------------------
def test_an_unchanged_model_has_an_empty_diff() -> None:
    state = doc()
    diff = diff_models(state, state)
    assert not diff
    assert diff.elements == ()
    assert diff.summary() == "no geometric change"


def test_a_moved_wall_is_reported_with_the_box_it_now_occupies() -> None:
    before, after = doc(), doc((MOVE_PARTITION,))
    diff = diff_models(before, after)
    walls = [e for e in diff.elements if e.kind == "wall"]
    assert len(walls) == 1
    wall = walls[0]
    assert wall.element_id == PARTITION
    assert wall.change == "modified"
    assert wall.fields == ("a", "b")
    assert wall.storey_id == GF
    # The box is the wall's swept rectangle: 115 thick, centred on x=3500, spanning y.
    assert wall.box == (3500 - 57, 0 - 57, 3500 + 57, 4000 + 57)


def test_a_room_polygon_that_only_followed_a_wall_is_recorded_but_not_clouded() -> None:
    """Bug class 1, inverted: a gate that fires on everything is as useless as one that
    never fires. Moving one partition rewrites both rooms' polygons; clouding those would
    put one cloud round the whole floor and point at nothing."""
    diff = diff_models(doc(), doc((MOVE_PARTITION,)))
    rooms = [e for e in diff.elements if e.kind == "room"]
    assert rooms, "the room polygons really did change — this test is not vacuous"
    assert all(room.derived for room in rooms)
    assert all(room.fields == ("polygon",) for room in rooms)

    clouded = cloud_regions(diff, GF, scale_denominator=100)
    assert len(clouded) == 1
    assert clouded[0] == (3443, -57, 3557, 4057)  # the wall, not the floor

    # The caller can still ask for everything, and then it IS the floor.
    everything = cloud_regions(diff, GF, scale_denominator=100, include_derived=True)
    assert everything[0][2] - everything[0][0] > clouded[0][2] - clouded[0][0]


def test_a_renamed_room_is_clouded_because_the_plan_label_changed() -> None:
    """The other half of the same rule: a non-derived room field must not be filtered out."""
    room_id = doc().house.rooms[0].id
    assign = {
        "type": "room.assign",
        "payload": {"roomId": room_id, "type": "bedroom", "name": "Master Bedroom"},
    }
    diff = diff_models(doc(), doc((assign,)))
    rooms = [e for e in diff.elements if e.kind == "room"]
    assert len(rooms) == 1
    assert not rooms[0].derived
    assert set(rooms[0].fields) == {"type", "name"}
    assert cloud_regions(diff, rooms[0].storey_id, scale_denominator=100)


def test_an_added_opening_is_boxed_on_its_host_wall() -> None:
    before, after = doc(), doc((ADD_DOOR,))
    diff = diff_models(before, after)
    openings = [e for e in diff.elements if e.kind == "opening"]
    assert len(openings) == 1
    opening = openings[0]
    assert opening.change == "added"
    assert opening.storey_id == GF
    # The south wall runs (0,0)->(6000,0), 230 thick; a 1000-wide door centred at 1500.
    # The box is deliberately conservative — the wall's half-thickness is added on both
    # axes, so a cloud drawn from it cannot clip the jamb of a skewed opening.
    assert opening.box == (885, -115, 2115, 115)


def test_a_removed_element_is_boxed_from_the_state_that_still_had_it() -> None:
    """A deletion has no geometry in the *after* state; its box must come from *before*."""
    delete = {"type": "wall.delete", "payload": {"wallId": PARTITION}}
    diff = diff_models(doc(), doc((delete,)))
    walls = [e for e in diff.elements if e.kind == "wall" and e.change == "removed"]
    assert len(walls) == 1
    assert walls[0].element_id == PARTITION
    assert walls[0].box == (3000 - 57, -57, 3000 + 57, 4057)
    assert walls[0].storey_id == GF


def test_the_diff_reads_json_and_objects_identically() -> None:
    """Two shapes, one comparison — or a JSON-vs-object diff would report every field."""
    before, after = doc(), doc((MOVE_PARTITION,))
    assert (
        diff_models(before, after).to_json()
        == diff_models(before.to_json(), after.to_json()).to_json()
    )
    assert diff_models(before.to_json(), after.to_json()).elements


def test_the_diff_is_deterministic_and_storey_ordered() -> None:
    before, after = doc(), doc((MOVE_PARTITION, ADD_DOOR))
    first = diff_models(before, after)
    second = diff_models(before, after)
    assert first.to_json() == second.to_json()
    order = [first.storey_ids.index(e.storey_id) for e in first.elements]
    assert order == sorted(order), "elements must come out ground floor first"


def test_slabs_and_furniture_are_excluded_deliberately() -> None:
    """An exclusion nobody can see is indistinguishable from a bug."""
    assert set(EXCLUDED_KINDS) == {"slab", "furniture"}
    assert all(reason for reason in EXCLUDED_KINDS.values())
    assert "slab" not in COMPARED_KINDS and "furniture" not in COMPARED_KINDS

    place_sofa = {
        "type": "furniture.set",
        "payload": {
            "action": "place",
            "id": _oid("furniture", "SF1"),
            "storeyId": GF,
            "catalogId": "sofa-3seat",
            "pt": {"x": 1500, "y": 2000},
            "rotationDeg": 0,
        },
    }
    before, after = doc(), doc((place_sofa,))
    assert after.house.furniture, "the op really did place furniture"
    assert not diff_models(before, after), "a moved sofa must not cloud a sanction sheet"


def test_clusters_merge_to_a_fixpoint_not_in_one_pass() -> None:
    """A merges into B, and the result then reaches C. One pass would leave two clouds."""
    a = (0, 0, 1000, 1000)
    b = (1100, 0, 2000, 1000)
    c = (2100, 0, 3000, 1000)
    far = (50_000, 0, 51_000, 1000)
    merged = cluster_boxes([a, c, b, far], gap_mm=100)
    assert merged == ((0, 0, 3000, 1000), far)
    assert cluster_boxes([a, c, far], gap_mm=100) == (a, c, far)


# ---------------------------------------------------------------------------
# the clouds themselves
# ---------------------------------------------------------------------------
#: Exact ``(cos, sin)`` at the four cardinal angles. The measurement has to be exact or
#: it cannot assert exactness: ``math.sin(math.radians(180))`` is 1.2e-16, and a closure
#: check with float slop in it would pass on a chain that genuinely does not meet.
_CARDINALS = {0: (1, 0), 90: (0, 1), 180: (-1, 0), 270: (0, -1)}


def _endpoints(arc: Arc) -> tuple[tuple[int, int], tuple[int, int]]:
    def at(deg: int) -> tuple[int, int]:
        assert deg in _CARDINALS, "cloud arcs are cardinal by construction, got %d" % deg
        cos_t, sin_t = _CARDINALS[deg]
        return (arc.centre[0] + arc.radius_mm * cos_t, arc.centre[1] + arc.radius_mm * sin_t)

    return (at(arc.start_deg), at(arc.end_deg))


def _closure_gaps(arcs: tuple[Arc, ...]) -> list[int]:
    """Chebyshev gap between each scallop's end and the next one's start, in exact mm."""
    ends = [_endpoints(arc) for arc in arcs]
    gaps: list[int] = []
    for index, (_start, end) in enumerate(ends):
        next_start, _ = ends[(index + 1) % len(ends)]
        gaps.append(max(abs(next_start[0] - end[0]), abs(next_start[1] - end[1])))
    return gaps


def test_the_cloud_chain_closes_exactly() -> None:
    for box in ((0, 0, 5000, 3000), (-1234, 567, 4321, 2999), (0, 0, 901, 1001)):
        arcs = revision_cloud(box, scale_denominator=100, element_id="rev-R2-1")
        assert arcs
        assert all(isinstance(arc, Arc) for arc in arcs)
        assert max(_closure_gaps(arcs)) == 0, box


def test_every_cloud_arc_is_an_exact_integer_and_on_a_real_layer() -> None:
    arcs = revision_cloud((-1234, 567, 4321, 2999), scale_denominator=100, element_id="rev-R2-1")
    for arc in arcs:
        assert isinstance(arc.radius_mm, int) and arc.radius_mm > 0
        assert all(isinstance(value, int) for value in arc.centre)
        assert arc.start_deg in (0, 90, 180, 270) and arc.end_deg in (0, 90, 180, 270)
        assert (arc.end_deg - arc.start_deg) % 360 == 180
        assert arc.layer in LAYER_NAMES
        assert arc.element_id == "rev-R2-1"


def test_the_cloud_encloses_the_box_with_clear_air_around_it() -> None:
    box = (1000, 2000, 4000, 5000)
    arcs = revision_cloud(box, scale_denominator=100, element_id="rev-R2-1")
    xs = [x for arc in arcs for x, _y in arc.points()]
    ys = [y for arc in arcs for _x, y in arc.points()]
    margin = cloud_module.CLOUD_MARGIN_PAPER_MM * 100
    assert min(xs) <= box[0] - margin and min(ys) <= box[1] - margin
    assert max(xs) >= box[2] + margin and max(ys) >= box[3] + margin
    # …and never cuts into the geometry it points at.
    assert min(xs) < box[0] and max(xs) > box[2]


def test_a_cloud_scallop_is_the_same_size_on_paper_at_every_scale() -> None:
    """Notation is sized on the print, like ISO 3098 text and a north arrow."""
    box = (0, 0, 8000, 8000)
    at_100 = revision_cloud(box, scale_denominator=100, element_id="r")
    at_200 = revision_cloud(box, scale_denominator=200, element_id="r")
    assert len(at_100) > len(at_200)
    paper_100 = at_100[0].radius_mm / 100
    paper_200 = at_200[0].radius_mm / 200
    assert abs(paper_100 - paper_200) < 0.5


def test_a_cloud_without_an_element_id_is_refused() -> None:
    try:
        revision_cloud((0, 0, 1000, 1000), scale_denominator=100, element_id="")
    except ValueError as error:
        assert "element_id" in str(error)
    else:  # pragma: no cover
        raise AssertionError("an unidentified cloud cannot be removed when superseded")


def test_the_delta_tag_prints_the_revision_number() -> None:
    primitives = revision_tag((0, 0), "R2", scale_denominator=100, element_id="rev-R2-1")
    triangle = next(p for p in primitives if isinstance(p, Polyline))
    text = next(p for p in primitives if isinstance(p, Text))
    assert triangle.closed and len(triangle.vertices) == 3
    assert text.text == "R2" and text.anchor == "middle"
    assert all(p.element_id == "rev-R2-1" for p in primitives)
    assert all(p.layer in LAYER_NAMES for p in primitives)


def test_marks_are_deterministic_and_only_on_the_storey_that_changed() -> None:
    diff = diff_models(doc(), doc((MOVE_PARTITION,)))
    first = revision_marks(diff, GF, revision_number="R2", scale_denominator=100)
    second = revision_marks(diff, GF, revision_number="R2", scale_denominator=100)
    assert first == second
    assert first, "the ground floor changed, so it must carry a cloud"
    assert revision_marks(diff, FF, revision_number="R2", scale_denominator=100) == ()


# ---------------------------------------------------------------------------
# NEGATIVE CONTROLS — break the guarded thing, watch the gate fire
# ---------------------------------------------------------------------------
def test_negative_control_an_odd_chord_breaks_the_closure_gate() -> None:
    """The closure test above must be capable of failing.

    ``_even_chords`` is what makes every radius an exact integer. Replace it with a
    partition that leaves an odd chord and the chain no longer meets itself — which is
    precisely the defect ``test_the_cloud_chain_closes_exactly`` exists to catch. If this
    test cannot produce a gap, that test is a green check that cannot go red.
    """
    original = cloud_module._even_chords

    def odd_chords(length_mm: int, _target_mm: int) -> tuple[int, ...]:
        # A partition that still sums to the side exactly but ignores parity. Both chords
        # are odd, so each radius (chord // 2) rounds down and each scallop's far end
        # lands one millimetre short of the next one's start — the exact defect the even
        # partition exists to prevent.
        return (length_mm - 3, 3)

    try:
        cloud_module._even_chords = odd_chords  # type: ignore[assignment]
        broken = revision_cloud((0, 0, 5001, 3001), scale_denominator=100, element_id="rev-R2-1")
        gaps = _closure_gaps(broken)
        assert max(gaps) == 1, (
            "the negative control did not break anything, so the closure gate is not "
            "actually measuring closure: gaps=%r" % gaps
        )
    finally:
        cloud_module._even_chords = original  # type: ignore[assignment]

    # …and the real implementation is green again.
    assert (
        max(
            _closure_gaps(revision_cloud((0, 0, 5001, 3001), scale_denominator=100, element_id="r"))
        )
        == 0
    )


def test_negative_control_a_diff_that_finds_nothing_draws_nothing() -> None:
    """If the clouds were drawn from a hand-kept list instead of the diff, this passes
    vacuously with clouds on the sheet. It must not."""
    state = doc()
    empty = diff_models(state, state)
    assert revision_marks(empty, GF, revision_number="R2", scale_denominator=100) == ()
    assert cloud_regions(empty, GF, scale_denominator=100) == ()


# ---------------------------------------------------------------------------
# INTEGRATION — the clouds and the register must reach the paper
# ---------------------------------------------------------------------------
def _plan_sheet(drawings: Any, storey_id: str) -> Any:
    for drawing in drawings:
        if drawing.meta.get("storeyId") == storey_id:
            return drawing
    raise AssertionError("no floor plan for %s" % storey_id)


def _cloud_arcs(drawing: Any) -> list[Arc]:
    return [
        primitive
        for group in drawing.groups
        for primitive in group.primitives
        if isinstance(primitive, Arc) and (primitive.element_id or "").startswith("rev-")
    ]


def test_clouds_reach_the_rendered_sheet_not_just_the_primitive_list() -> None:
    """Bug class 4: a module that believed it was registered.

    The furniture layer tagged its meshes, documented itself as integrated and never
    called the registry. A cloud that exists only in a tuple is the same defect, so this
    goes all the way to the SVG bytes the exporter writes.
    """
    before, after = doc(), doc((MOVE_PARTITION,))
    diff = diff_models(before, after)
    sheets = build_sheet_set(
        after,
        title_block=TitleBlock(project_name="Revision test"),
        register=HISTORY,
        diff=diff,
    )
    plan = _plan_sheet(sheets, GF)
    arcs = _cloud_arcs(plan)
    assert arcs, "the ground-floor plan must carry the cloud for R2"

    svg = render_sheet_svg(plan)
    assert svg.count("<path") >= len(arcs)
    assert ">R2<" in svg, "the delta tag's revision number must be on the paper"
    # The first-floor plan did not change and must be clean.
    assert _cloud_arcs(_plan_sheet(sheets, FF)) == []


def test_an_unrevised_set_carries_no_cloud_and_no_register() -> None:
    """The feature is inert by default — which is what keeps the §16 goldens green."""
    sheets = build_sheet_set(doc(), title_block=TitleBlock())
    for drawing in sheets:
        assert _cloud_arcs(drawing) == []
        for group in drawing.groups:
            assert group.id != "revision-register"


def test_every_sheet_carries_the_issue_strip_from_the_one_register() -> None:
    """The strip on each sheet and the register on A-06 cannot disagree about the issues."""
    sheets = build_sheet_set(doc(), title_block=TitleBlock(), register=HISTORY)
    for drawing in sheets:
        texts = {
            primitive.text
            for group in drawing.groups
            for primitive in group.primitives
            if isinstance(primitive, Text)
        }
        for record in HISTORY:
            assert record.number in texts, (drawing.sheet.number, record.number)
            assert record.date in texts, (drawing.sheet.number, record.date)


def _worker_bundle(document: Any, previous: Any = None) -> Any:
    """A bundle built the way ``handler._load_bundle`` builds one.

    Through ``from_payload`` (the register comes out of the payload's revision rows) and
    then ``dataclasses.replace`` for the assets, because that is exactly the two-step the
    handler does — testing a hand-constructed bundle would skip the step that turns three
    JSON rows into a validated register.
    """
    import dataclasses

    from services.drawings.pipeline import SheetBundle

    bundle = SheetBundle.from_payload(
        {"kinds": ["floor"], "revisions": [record.to_json() for record in HISTORY]},
        document=document.to_json(),
    )
    return dataclasses.replace(
        bundle, previous_document=None if previous is None else previous.to_json()
    )


def test_the_worker_pipeline_draws_the_clouds_end_to_end() -> None:
    """The seam the API will hand a job across, exercised as the worker will run it.

    ``SheetBundle`` -> ``build_sheets`` is the production path: JSON documents, the
    payload's revision rows, the previous issue as an asset. A cloud that works only when
    a test calls ``build_sheet_set`` directly is a cloud no user ever sees.
    """
    from services.drawings.pipeline import build_sheets

    before, after = doc(), doc((MOVE_PARTITION,))
    bundle = _worker_bundle(after, before)
    assert bundle.register is not None and bundle.register.latest.number == "R2"

    ground = next(
        sheet for sheet in build_sheets(bundle).sheets if sheet.viewport.get("storeyId") == GF
    )
    assert ">R2<" in ground.svg, "the worker path must draw the delta tag"

    # …and the same job without the previous issue draws no cloud at all.
    plain = next(
        sheet
        for sheet in build_sheets(_worker_bundle(after)).sheets
        if sheet.viewport.get("storeyId") == GF
    )
    assert ">R2<" not in plain.svg


def test_a_payload_whose_revision_rows_are_not_a_valid_register_still_draws() -> None:
    """A typo in a note must not fail a sheet job — it just cannot drive a register."""
    from services.drawings.pipeline import SheetBundle, build_sheets

    bundle = SheetBundle.from_payload(
        {
            "kinds": ["floor"],
            "revisions": [{"revision": "R1", "date": "5-3-26", "note": "sloppy date"}],
        },
        document=doc().to_json(),
    )
    assert bundle.register is None
    assert bundle.revisions == (("R1", "5-3-26", "sloppy date"),)
    assert build_sheets(bundle).sheets


def test_clouds_are_scoped_to_the_storey_that_changed() -> None:
    """A first-floor edit must not cloud the ground floor, and vice versa."""
    move_ff = {
        "type": "wall.move",
        "payload": {
            "wallId": "wall_01J00000000000000000000FFN",
            "a": {"x": 6000, "y": 4200},
            "b": {"x": 0, "y": 4200},
        },
    }
    after = doc((move_ff,))
    diff = diff_models(doc(), after)
    sheets = build_sheet_set(after, title_block=TitleBlock(), register=HISTORY, diff=diff)
    assert _cloud_arcs(_plan_sheet(sheets, FF))
    assert _cloud_arcs(_plan_sheet(sheets, GF)) == []


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
    print(
        "\n%d test(s) failed. Stubbed dependencies: %s" % (failures, ", ".join(STUBBED) or "none")
    )
    sys.exit(1 if failures else 0)
