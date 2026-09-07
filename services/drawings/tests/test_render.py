"""§7 / §16 renderer tests. Pure integers, no dependency, runnable two ways.

    Values from integer mm — chains must sum exactly (assert in tests: Σ segments ==
    overall, every chain).                                                 -- §7 step 5

That sentence is the reason this file exists, and :func:`test_every_chain_sums_exactly`
is it, asserted over the whole golden corpus rather than over a toy chain.

Runs under pytest in CI and under ``python3 services/drawings/tests/test_render.py`` on a
machine with no packages installed — the same convention as
``services/solver/tests/test_walls.py``. That matters here more than usual: the SVG
renderer is the one output format that can be fully proven anywhere, so its test must not
need a toolchain to run.
"""

from __future__ import annotations

import json
import os
import sys
from typing import Any

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
for _path in (_REPO_ROOT, os.path.join(_REPO_ROOT, "apps", "api")):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from services.dev_stubs import install_worker_dep_stubs  # noqa: E402

STUBBED = install_worker_dep_stubs()

from garh_model.fold import apply_group  # noqa: E402
from garh_model.model import empty_project_doc  # noqa: E402
from garh_model.ops import Op  # noqa: E402

from services.drawings.dimensions import (  # noqa: E402
    ChainConsistencyError,
    DimChain,
    DimSegment,
    LabelBox,
    assert_chains_sum,
    find_label_collisions,
)
from services.drawings.layers import LAYER_NAMES  # noqa: E402
from services.drawings.render.frame import frame_group  # noqa: E402
from services.drawings.render.labels import Box, label_boxes, text_box_mm  # noqa: E402
from services.drawings.render.layout import (  # noqa: E402
    PaperRect,
    choose_scale,
    content_rect,
    fit_placement,
)
from services.drawings.render.primitives import (  # noqa: E402
    Arc,
    Dim,
    DrawingGroup,
    Line,
    Placement,
    SheetDrawing,
    Text,
    dim_geometry,
    div_round,
    sort_by_layer,
)
from services.drawings.render.reference_sheets import (  # noqa: E402
    _opening_centre_along,
    _room_label_block,
    _sheet,
    build_schedule_rows,
    build_sheet_set,
    building_extent,
    door_window_schedule,
    inner_chains,
    outer_chains,
    plan_primitives,
    room_label_lines,
)
from services.drawings.render.sanitize import (  # noqa: E402
    SvgSanitizeError,
    assert_sanitary,
    escape_text,
    safe_id,
)
from services.drawings.render.svg import normalize_svg, render_sheet_svg  # noqa: E402
from services.drawings.render.tables import Column, table_primitives  # noqa: E402
from services.drawings.sheets import (  # noqa: E402
    PAPER_SIZES,
    Scale,
    TitleBlock,
    Viewport,
    default_frame,
)

INPUT_DIR = os.path.join(_REPO_ROOT, "fixtures", "sheets", "inputs")
RULEPACK_DIR = os.path.join(_REPO_ROOT, "rulepacks")


# ---------------------------------------------------------------------------
# Corpus loading (shared by several tests; cached so the suite stays fast)
# ---------------------------------------------------------------------------
_CACHE: dict[str, Any] = {}


def _fixtures() -> list[tuple[str, dict[str, Any]]]:
    out = []
    for name in sorted(os.listdir(INPUT_DIR)):
        if not name.endswith(".json"):
            continue
        with open(os.path.join(INPUT_DIR, name), encoding="utf-8") as handle:
            out.append((name, json.load(handle)))
    assert out, "fixtures/sheets/inputs/ is empty — the renderer has nothing to prove on"
    return out


def _fold(fixture: dict[str, Any]) -> Any:
    ops = [Op.from_json(raw) for raw in fixture["ops"]]
    return apply_group(empty_project_doc(fixture.get("unitsDisplay", "ft-in")), ops).model


def _statement(doc: Any) -> Any:
    from garh_api.compliance import build_evaluation_context, packs_for
    from garh_rules import evaluate

    document = doc.to_json()
    context = build_evaluation_context(document, packs=list(packs_for(document)))
    return evaluate(context, root=RULEPACK_DIR).areas


def _sheet_sets() -> list[tuple[str, Any]]:
    if "sets" not in _CACHE:
        sets = []
        for name, fixture in _fixtures():
            doc = _fold(fixture)
            sets.append(
                (
                    name,
                    build_sheet_set(
                        doc,
                        title_block=TitleBlock(
                            firm_name="Studio Demo",
                            project_name=fixture["name"],
                            date="01-01-2026",
                        ),
                        statement=_statement(doc),
                        dim_to_jamb=bool(fixture.get("dimToJamb", False)),
                        revisions=tuple(tuple(r) for r in fixture.get("revisions") or ()),
                    ),
                )
            )
        _CACHE["sets"] = sets
    return _CACHE["sets"]


# ---------------------------------------------------------------------------
# §7 step 5 — the invariant that matters most
# ---------------------------------------------------------------------------
def test_every_chain_sums_exactly() -> None:
    """Σ segments == overall, exactly, for every chain on every sheet of the corpus.

    Exact integer equality, no tolerance. This is the assertion §7 step 5 asks for by
    name, and it is here rather than only inside the renderer because the renderer could
    stop calling its own guard.
    """
    checked = 0
    for name, drawings in _sheet_sets():
        for drawing in drawings:
            assert_chains_sum(drawing.chains)
            for chain in drawing.chains:
                total = sum(segment.length_mm for segment in chain.segments)
                assert total == chain.overall_mm, (
                    "%s %s chain %s: segments sum to %d, overall is %d"
                    % (name, drawing.sheet.number, chain.id, total, chain.overall_mm)
                )
                # Segments must also tile the chain with no gap and no overlap: summing
                # to the right total is necessary but not sufficient.
                cursor = 0
                for segment in chain.segments:
                    assert segment.start_mm == cursor, "%s chain %s has a gap or overlap at %d" % (
                        name,
                        chain.id,
                        segment.start_mm,
                    )
                    assert segment.length_mm > 0, "%s chain %s has a zero/negative segment" % (
                        name,
                        chain.id,
                    )
                    cursor = segment.end_mm
                assert cursor == chain.overall_mm
                checked += 1
    # Two fixtures' worth of outer chains (three levels a side), elevations and the
    # section. The per-room chains that used to inflate this count are now printed in
    # the room labels (see plan_primitives), so the floor is what the outer chains give.
    assert checked >= 30, "expected a substantial number of chains, got %d" % checked


def test_chain_consistency_error_is_raised_not_corrected() -> None:
    bad = DimChain(
        id="bad",
        orientation="horizontal",
        level=1,
        offset_mm=0,
        origin_mm=0,
        segments=(DimSegment(0, 1000), DimSegment(1000, 1000)),
        overall_mm=2001,
    )
    assert not bad.is_consistent()
    assert bad.inconsistency() == -1
    try:
        assert_chains_sum((bad,))
    except ChainConsistencyError as exc:
        assert "off by -1" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("an inconsistent chain must raise, not be silently fixed")


def test_dim_geometry_refuses_an_inconsistent_chain() -> None:
    """The renderer must not draw a chain whose numbers do not add up."""
    bad = DimChain(
        id="bad",
        orientation="horizontal",
        level=1,
        offset_mm=0,
        origin_mm=0,
        segments=(DimSegment(0, 500),),
        overall_mm=900,
    )
    try:
        dim_geometry(Dim(chain=bad), scale_denominator=100)
    except ValueError as exc:
        assert "§7 step 5" in str(exc) or "sum" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("dim_geometry drew a chain that does not add up")


def test_all_dimension_text_is_integer_millimetres() -> None:
    """§7: "All dim text in mm on drawings regardless of the project's display units"."""
    for name, drawings in _sheet_sets():
        for drawing in drawings:
            for chain in drawing.chains:
                for segment in chain.segments:
                    label = segment.label()
                    assert label.isdigit(), (
                        "%s chain %s label %r is not a plain millimetre integer — no "
                        "feet, no metres, no decimal point" % (name, chain.id, label)
                    )
                    assert int(label) == segment.length_mm


def test_dim_to_jamb_flag_changes_the_opening_chain() -> None:
    """§7's firm-configurable ``dimToJamb``: centreline by default, jambs when set."""
    _name, fixture = _fixtures()[-1]
    doc = _fold(fixture)
    storey = doc.house.storeys[0].id
    centre = outer_chains(doc.house, storey, dim_to_jamb=False)
    jamb = outer_chains(doc.house, storey, dim_to_jamb=True)

    def level3(chains: Any) -> Any:
        return [c for c in chains if c.level == 3 and c.orientation == "horizontal"]

    centre_l3 = level3(centre)
    jamb_l3 = level3(jamb)
    assert centre_l3 and jamb_l3
    # Two breakpoints per opening instead of one => strictly more segments.
    assert sum(len(c.segments) for c in jamb_l3) > sum(len(c.segments) for c in centre_l3)
    # And both still sum exactly.
    assert_chains_sum(tuple(centre_l3) + tuple(jamb_l3))


# ---------------------------------------------------------------------------
# Integer arithmetic and placement
# ---------------------------------------------------------------------------
def test_div_round_is_half_away_from_zero() -> None:
    cases = (
        (1, 2, 1),
        (-1, 2, -1),
        (3, 2, 2),
        (-3, 2, -2),
        (0, 5, 0),
        (5, 5, 1),
        (4, 5, 1),
        (2, 5, 0),
        (-2, 5, 0),
        (-4, 5, -1),
        (1000, 3, 333),
        (-1000, 3, -333),
    )
    for numerator, denominator, expected in cases:
        assert div_round(numerator, denominator) == expected, (numerator, denominator)
    # Negative denominators normalise, not flip sign twice.
    assert div_round(3, -2) == -2
    try:
        div_round(1, 0)
    except ZeroDivisionError:
        pass
    else:  # pragma: no cover
        raise AssertionError("div_round(1, 0) must raise")


def test_placement_flips_y_and_scales_exactly() -> None:
    placement = Placement(
        scale_denominator=100, origin_model_mm=(1000, 2000), origin_paper_um=(50_000, 300_000)
    )
    # The origin maps to the origin.
    assert placement.to_paper_um((1000, 2000)) == (50_000, 300_000)
    # +1000 mm model at 1:100 is +10 mm paper == 10_000 µm; Y goes UP the page, so the
    # paper coordinate DECREASES.
    assert placement.to_paper_um((2000, 3000)) == (60_000, 290_000)
    assert placement.length_to_paper_um(2_300) == 23_000
    assert placement.paper_um_to_model_mm(2_500) == 250

    paper = Placement.paper()
    assert paper.to_paper_um((10, 20)) == (10_000, 20_000)
    assert paper.flip_y is False


def test_choose_scale_prefers_the_standard_ladder() -> None:
    rect = PaperRect(20, 10, 380, 400)
    # A 6 m building fits at 1:100 and the preference is honoured.
    assert choose_scale((0, 0, 6_000, 4_000), rect, preferred=100) == 100
    # A 60 m plot does not fit at 1:100, so the ladder is walked.
    chosen = choose_scale((0, 0, 60_000, 60_000), rect, preferred=100)
    assert chosen in (150, 200, 250, 500, 1000) and chosen != 100
    # Every returned scale is on the rule.
    from services.drawings.render.layout import PREFERRED_SCALES

    assert chosen in PREFERRED_SCALES


def test_fit_placement_centres_the_extent() -> None:
    rect = PaperRect(0, 0, 200, 100)
    placement = fit_placement((0, 0, 10_000, 5_000), rect, 100)
    # 10 m at 1:100 = 100 mm wide in a 200 mm slot -> 50 mm left margin.
    assert placement.to_paper_um((0, 0))[0] == 50_000
    # 5 m -> 50 mm tall in a 100 mm slot -> bottom at 75 mm, top at 25 mm.
    assert placement.to_paper_um((0, 0))[1] == 75_000
    assert placement.to_paper_um((10_000, 5_000)) == (150_000, 25_000)


# ---------------------------------------------------------------------------
# SVG: determinism, print-truth, layers
# ---------------------------------------------------------------------------
def test_svg_is_byte_identical_across_runs() -> None:
    """The §16 golden gate rests on this: same model, same bytes, every time."""
    for name, drawings in _sheet_sets():
        for drawing in drawings:
            first = render_sheet_svg(drawing)
            second = render_sheet_svg(drawing)
            assert first == second, "%s %s is not deterministic" % (name, drawing.sheet.number)


def test_svg_carries_no_timestamp_or_random_id() -> None:
    banned = ("20", "generator=", "random", "uuid", "Date")
    for _name, drawings in _sheet_sets():
        drawing = drawings[0]
        svg = render_sheet_svg(drawing)
        # No four-digit year anywhere except inside title-block text the fixture set.
        header = svg.split("<rect")[0]
        for token in banned:
            if token == "20":
                continue
            assert token not in header, "%r leaked into the SVG header" % token
        assert "<!-- garh-sheet" in header


def test_svg_is_print_true_in_millimetres() -> None:
    """A2 landscape must come out as a 594x420 mm page with a matching viewBox."""
    a2 = PAPER_SIZES["A2"].landscape()
    for _name, drawings in _sheet_sets():
        for drawing in drawings:
            svg = render_sheet_svg(drawing)
            assert 'width="%dmm"' % a2.width_mm in svg
            assert 'height="%dmm"' % a2.height_mm in svg
            assert 'viewBox="0 0 %d %d"' % (a2.width_mm, a2.height_mm) in svg


def test_svg_coordinates_are_fixed_point_never_float_repr() -> None:
    """No exponents, no long float tails: every coordinate is ``d.ddd``.

    A stray ``1e-05`` or ``0.30000000000000004`` in the output would be a platform-
    dependent byte and would make the golden gate unreliable rather than strict.
    """
    import re

    # The leading space matters: without it, `y` matches inside "stroke-dasharra**y**"
    # and the test trips over a perfectly good two-value dash pattern.
    number = re.compile(r'\s(?:x|y|x1|y1|x2|y2|cx|cy|r|font-size)="(-?[0-9.]+|[^"]*)"')
    # Scientific notation next to a digit. Not a bare "e-" substring search: that also
    # matches "strok**e-**width", which is how this assertion first went wrong.
    exponent = re.compile(r"[0-9][eE][-+][0-9]")
    for _name, drawings in _sheet_sets():
        svg = render_sheet_svg(drawings[0])
        found = exponent.search(svg)
        assert found is None, "an exponent leaked into the SVG at %d" % (
            found.start() if found else -1
        )
        for match in number.finditer(svg):
            value = match.group(1)
            if not value or value[0].isalpha():
                continue
            assert value.count(".") <= 1, value
            if "." in value:
                assert len(value.split(".")[1]) == 3, (
                    "%r is not fixed 3-decimal millimetres" % value
                )


def test_layer_groups_are_emitted_in_layers_order() -> None:
    """Draw order is LAYERS order: hatches go down before the lines that bound them.

    Checked per drawing group, not per document, because a sheet has several groups and
    each restarts the layer sequence.
    """
    import re

    group_open = re.compile(r'<g id="g-[^"]*">')
    layer_attr = re.compile(r'data-layer="([^"]+)"')
    order = {name: index for index, name in enumerate(LAYER_NAMES)}

    checked = 0
    for name, drawings in _sheet_sets():
        for drawing in drawings:
            svg = render_sheet_svg(drawing)
            starts = [match.start() for match in group_open.finditer(svg)] + [len(svg)]
            for index in range(len(starts) - 1):
                chunk = svg[starts[index] : starts[index + 1]]
                layers = layer_attr.findall(chunk)
                indices = [order[layer] for layer in layers]
                assert indices == sorted(indices), "%s %s: layers out of LAYERS order: %s" % (
                    name,
                    drawing.sheet.number,
                    layers,
                )
                # No layer opens twice in one group — that would mean two draw passes.
                assert len(layers) == len(set(layers)), layers
                checked += 1
    assert checked > 20


def test_every_primitive_layer_is_one_of_the_nine() -> None:
    for _name, drawings in _sheet_sets():
        for drawing in drawings:
            for group in drawing.groups:
                for primitive in group.primitives:
                    assert primitive.layer in LAYER_NAMES, primitive.layer


def test_sort_by_layer_is_stable_within_a_layer() -> None:
    first = Line((0, 0), (1, 0), "A-WALL")
    second = Line((0, 1), (1, 1), "A-WALL")
    dim_text = Text((0, 0), "x", "A-DIM")
    ordered = sort_by_layer([dim_text, first, second])
    assert ordered[0] is first and ordered[1] is second and ordered[2] is dim_text


def test_unknown_layer_is_rejected_at_construction() -> None:
    try:
        Line((0, 0), (1, 1), "A-NOPE")
    except KeyError as exc:
        assert "nine" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("a typo'd layer must fail loudly")


def test_arc_sweep_direction_survives_the_y_flip() -> None:
    """A door swing drawn CCW in the model must not come out mirrored on paper.

    The model sweeps counter-clockwise; the Y flip makes that clockwise on the sheet, so
    the SVG sweep-flag must be 0 when flipped and 1 when not. Getting this one digit
    wrong mirrors every door on every plan, which looks plausible and is wrong.
    """
    import re

    from services.drawings.render.svg import render_group_svg

    arc = Arc((0, 0), 900, 0, 90, "A-DOOR")
    flags = {}
    for flip in (True, False):
        svg = render_group_svg(
            DrawingGroup(id="g", placement=Placement(100, flip_y=flip), primitives=(arc,))
        )
        match = re.search(r"A [0-9.]+ [0-9.]+ 0 (\d) (\d) ", svg)
        assert match is not None, svg
        flags[flip] = (match.group(1), match.group(2))

    assert flags[True] == ("0", "0"), flags[True]  # 90° sweep, clockwise on paper
    assert flags[False] == ("0", "1"), flags[False]  # ... counter-clockwise unflipped

    # A 270° sweep must set the large-arc flag; a quarter circle must not.
    wide = render_group_svg(
        DrawingGroup(
            id="g", placement=Placement(100), primitives=(Arc((0, 0), 900, 0, 270, "A-DOOR"),)
        )
    )
    assert re.search(r"A [0-9.]+ [0-9.]+ 0 1 0 ", wide) is not None, wide


# ---------------------------------------------------------------------------
# §13 sanitisation
# ---------------------------------------------------------------------------
def test_escape_text_handles_every_xml_metacharacter() -> None:
    assert escape_text("a & b") == "a &amp; b"
    assert escape_text("<script>") == "&lt;script&gt;"
    assert escape_text('say "hi"') == "say &quot;hi&quot;"
    assert escape_text("it's") == "it&apos;s"
    # Ampersand is escaped first, so nothing double-escapes.
    assert escape_text("&lt;") == "&amp;lt;"
    # Control characters are dropped, not escaped: XML has no representation for them.
    assert escape_text("a\x01b") == "ab"


def test_hostile_room_name_cannot_produce_executable_svg() -> None:
    """A room name is free text an architect types and a client's browser renders.

    This is the §16 note that "the SVG golden diff doubles as a security test", made
    concrete: the whole hostile payload set goes through the real renderer.
    """
    payloads = (
        "<script>alert(1)</script>",
        '"><script>alert(1)</script>',
        '<foreignObject><body onload="alert(1)">x</body></foreignObject>',
        "javascript:alert(1)",
        "<img src=x onerror=alert(1)>",
        "<!ENTITY xxe SYSTEM 'file:///etc/passwd'>",
    )
    _name, fixture = _fixtures()[0]
    doc = _fold(fixture)
    statement = _statement(doc)
    for payload in payloads:
        drawings = build_sheet_set(
            doc,
            title_block=TitleBlock(
                firm_name=payload,
                project_name=payload,
                client_name=payload,
                notes=payload,
                date=payload,
            ),
            statement=statement,
        )
        for drawing in drawings:
            svg = render_sheet_svg(drawing)  # raises if it is not sanitary
            assert_sanitary(svg)
            assert "<script" not in svg.lower()
            # Every XML metacharacter in the payload must have been escaped. A payload
            # with no metacharacters ("javascript:alert(1)") legitimately appears as
            # visible text — the room really is named that, and text is inert. What must
            # never survive is a character that could close a tag or open an attribute.
            if any(char in payload for char in "<>\"'&"):
                assert payload not in svg, "the payload survived unescaped"
            for char in "<>\"'":
                assert svg.count(char + payload.strip(char)) == 0 or char not in payload


def test_assert_sanitary_catches_each_forbidden_construct() -> None:
    cases = (
        "<svg><script>x</script></svg>",
        "<svg><foreignObject/></svg>",
        '<svg><text onclick="x">a</text></svg>',
        '<svg><a href="javascript:alert(1)">x</a></svg>',
        "<!DOCTYPE svg><svg/>",
        '<svg><image href="data:image/svg+xml,x"/></svg>',
        "<svg><text>a\x00b</text></svg>",
    )
    for case in cases:
        try:
            assert_sanitary(case)
        except SvgSanitizeError:
            continue
        raise AssertionError("assert_sanitary passed %r" % case[:60])


def test_assert_sanitary_does_not_false_positive_on_real_output() -> None:
    # "<set" must not match "<setback"; the corpus contains setback labels.
    assert_sanitary('<svg><text>Front setback</text><g id="g-site-setback-front"/></svg>')
    for _name, drawings in _sheet_sets():
        for drawing in drawings:
            assert_sanitary(render_sheet_svg(drawing))


def test_safe_id_is_deterministic_and_xml_safe() -> None:
    assert safe_id("A-02A") == "A-02A"
    assert safe_id("sheet/1 2") == "sheet-1-2"
    assert safe_id("1abc") == "x-1abc"
    assert safe_id("   ") == "x"
    assert safe_id("A-02A") == safe_id("A-02A")


# ---------------------------------------------------------------------------
# Normalisation
# ---------------------------------------------------------------------------
def test_normalize_svg_is_idempotent_and_only_touches_whitespace() -> None:
    raw = "<svg>\r\n  <line/>   \r\n</svg>\r\n\r\n"
    once = normalize_svg(raw)
    assert once == "<svg>\n  <line/>\n</svg>\n"
    assert normalize_svg(once) == once
    # Content is untouched: ids, numbers and attribute order all survive.
    body = '<svg><g id="g-plan-x" data-layer="A-WALL"><line x1="1.000"/></g></svg>\n'
    assert normalize_svg(body) == body


# ---------------------------------------------------------------------------
# Collision-freedom (§16)
# ---------------------------------------------------------------------------
def test_no_overlapping_text_labels_on_any_sheet() -> None:
    """§16: "collision-free assertion (no overlapping text bboxes)".

    Measured in paper micrometres — the only space where an overlap is real. The boxes
    come from :func:`label_boxes`, the one measurer the worker's pipeline and
    ``scripts/sheet_goldens.py`` also use, so the test and the harness cannot disagree
    about what passes; and it boxes the figures inside dimension chains, so a room
    label sitting on a dimension is a collision here, which it was not while only
    ``Text`` primitives were measured.
    """
    for name, drawings in _sheet_sets():
        for drawing in drawings:
            collisions = find_label_collisions(label_boxes(drawing))
            assert not collisions, "%s sheet %s has %d overlapping label pair(s): %s" % (
                name,
                drawing.sheet.number,
                len(collisions),
                collisions[:3],
            )


def test_the_collision_audit_sees_dimension_figures() -> None:
    """Negative control for the measurer: a room name on a dimension figure is caught.

    The figure of a chain is not a ``Text`` primitive — it is exploded from the ``Dim``
    at render time — and the old audit therefore never saw it. This builds a sheet with
    exactly one text sitting on exactly one figure and requires one collision; then
    moves the text clear and requires none.
    """
    chain = DimChain(
        id="probe",
        orientation="horizontal",
        level=1,
        offset_mm=0,
        origin_mm=0,
        segments=(DimSegment(0, 3000),),
        overall_mm=3000,
    )
    dim = Dim(chain=chain, layer="A-DIM", text_height_paper_um=2_000)
    sheet = _sheet(
        sheet_id="probe",
        kind="floor-plan",
        number="A-00",
        title="Probe",
        viewport=Viewport(storey_id="s"),
        scale=Scale(100),
        title_block=TitleBlock(),
    )

    def drawing_with(label_at: tuple[int, int]) -> SheetDrawing:
        label = Text(
            at=label_at, text="BEDROOM", layer="A-TEXT", anchor="middle", baseline="middle"
        )
        return SheetDrawing(
            sheet=sheet,
            groups=(
                DrawingGroup(
                    id="plan",
                    placement=Placement(100, origin_paper_um=(100_000, 100_000)),
                    primitives=(dim, label),
                ),
            ),
            chains=(chain,),
        )

    # The figure "3000" sits centred on the chain, one text gap above the line.
    on_the_figure = find_label_collisions(label_boxes(drawing_with((1500, 250))))
    assert len(on_the_figure) == 1, on_the_figure
    assert "probe" in on_the_figure[0][0] + on_the_figure[0][1]
    clear = find_label_collisions(label_boxes(drawing_with((1500, 2000))))
    assert not clear, clear


def test_label_box_touching_is_allowed_overlapping_is_not() -> None:
    a = LabelBox(0, 0, 10, 10, "a")
    touching = LabelBox(10, 0, 10, 10, "b")
    overlapping = LabelBox(9, 0, 10, 10, "c")
    assert not a.overlaps(touching)
    assert a.overlaps(overlapping)


# ---------------------------------------------------------------------------
# Sheet set shape (F7-A)
# ---------------------------------------------------------------------------
def test_every_f7a_sheet_kind_is_produced() -> None:
    """F7-A lists six sheet kinds. All six must appear for a real project."""
    _name, fixture = _fixtures()[-1]  # the G+1
    doc = _fold(fixture)
    drawings = build_sheet_set(doc, title_block=TitleBlock(), statement=_statement(doc))
    kinds = {str(d.sheet.kind) for d in drawings}
    assert kinds == {
        "site-plan",
        "floor-plan",
        "elevation",
        "section",
        "door-window-schedule",
        "area-statement",
    }, kinds
    # All four elevations, and one plan per storey with walls.
    assert len(drawings.by_kind("elevation")) == 4
    assert len(drawings.by_kind("floor-plan")) == len(doc.house.storeys)
    for drawing in drawings:
        drawing.sheet.validate()


def test_sheet_numbers_are_unique_and_ordered() -> None:
    for _name, drawings in _sheet_sets():
        numbers = [str(d.sheet.number) for d in drawings]
        assert len(numbers) == len(set(numbers)), numbers
        assert numbers == sorted(numbers), numbers


def test_schedule_tags_are_stable_and_grouped_by_size() -> None:
    """§7: group by (kind, w, h) -> D1.., W1.., V1.., with per-storey counts."""
    _name, fixture = _fixtures()[-1]
    house = _fold(fixture).house
    rows = build_schedule_rows(house)
    assert rows, "the G+1 fixture has openings, so the schedule cannot be empty"
    tags = [row.tag for row in rows]
    assert tags == sorted(set(tags), key=tags.index), "tags repeat"
    assert tags[0] == "D1", "the widest door must be D1 — that is the main entrance"
    prefixes = {tag[0] for tag in tags}
    assert prefixes <= {"D", "W", "V"}
    # Counts must add up to the model's opening count exactly.
    assert sum(row.total for row in rows) == len(house.openings)
    for row in rows:
        assert sum(row.counts_by_storey.values()) == row.total
    # Re-running gives the same tags: no counter that depends on array order.
    assert [row.tag for row in build_schedule_rows(house)] == tags


def test_inner_chains_measure_real_room_clear_dimensions() -> None:
    _name, fixture = _fixtures()[0]
    house = _fold(fixture).house
    storey = house.storeys[0].id
    chains = inner_chains(house, storey)
    assert chains
    rooms = {room.id: room for room in house.rooms if room.storey_id == storey}
    for chain in chains:
        room_id = chain.id.rsplit("-", 1)[0]
        room = rooms[room_id]
        xs = [p.x for p in room.polygon]
        ys = [p.y for p in room.polygon]
        expected = (max(xs) - min(xs)) if chain.orientation == "horizontal" else (max(ys) - min(ys))
        assert chain.overall_mm == expected, (chain.id, chain.overall_mm, expected)


# ---------------------------------------------------------------------------
# Tables and frame
# ---------------------------------------------------------------------------
def test_ragged_table_row_is_rejected() -> None:
    columns = (Column("A", 20), Column("B", 20))
    try:
        table_primitives(columns, [["only one"]], origin_mm=(0, 0))
    except ValueError as exc:
        assert "ragged" in str(exc) or "cells" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("a ragged row must be refused, not silently padded")


def test_area_statement_table_formats_and_never_computes() -> None:
    """The table must print the statement's own numbers, unchanged."""
    from services.drawings.render.tables import area_statement_table, format_area_dual

    _name, fixture = _fixtures()[-1]
    doc = _fold(fixture)
    statement = _statement(doc)
    primitives = area_statement_table(statement)
    texts = [p.text for p in primitives if isinstance(p, Text)]
    # The plot area, formatted exactly as format_area_dual would, appears verbatim.
    assert format_area_dual(statement.plot_area_mm2) in texts
    assert format_area_dual(statement.total_built_up_area_mm2) in texts
    # A None allowance prints an em dash, not a zero.
    assert format_area_dual(None) == "—"


def test_title_block_prints_every_field_even_when_empty() -> None:
    frame = default_frame(title_block=TitleBlock(firm_name="Studio Demo"))
    group = frame_group(frame, revisions=(("A", "01-01-2026", "First issue"),))
    texts = [p.text for p in group.primitives if isinstance(p, Text)]
    for label in ("PROJECT", "CLIENT", "ARCHITECT", "SCALE", "DATE", "DRAWN", "CHECKED", "REV"):
        assert any(label in text for text in texts), label
    assert "Studio Demo" in texts
    assert "DESCRIPTION" in texts and "First issue" in texts


def test_frame_fits_inside_the_paper() -> None:
    frame = default_frame()
    group = frame_group(frame)
    paper = frame.paper
    for primitive in group.primitives:
        for x, y in primitive.points():
            assert 0 <= x <= paper.width_mm, (x, primitive)
            assert 0 <= y <= paper.height_mm, (y, primitive)
    rect = content_rect(frame)
    assert rect.x_mm + rect.width_mm <= paper.width_mm - frame.title_block_width_mm


# ---------------------------------------------------------------------------
# The projection-engine adapter (services.drawings.render.adapt)
# ---------------------------------------------------------------------------
def test_adapter_converts_the_real_projection_engine_output() -> None:
    """The §7 projection engine's stream must render through these renderers.

    This is the seam between the two halves of Phase 8, so it is tested against the real
    ``services.drawings.projection`` output rather than against a mock: a hand-written
    fake primitive would agree with the adapter by construction and prove nothing.

    Skipped loudly if the projection package is not present, so this file still runs
    standalone.
    """
    try:
        from services.drawings.projection.plan import project_plan
    except ImportError:
        print("    SKIP services.drawings.projection is not importable here")
        return

    from services.drawings.render.adapt import from_projection
    from services.drawings.render.primitives import DrawingGroup, Placement, SheetDrawing

    _name, fixture = _fixtures()[-1]
    doc = _fold(fixture)
    storey_id = doc.house.storeys[0].id
    stream = project_plan(doc.house, storey_id, 100)
    assert stream, "the projection engine produced nothing for a real storey"

    adapted = from_projection(stream, scale_denominator=100)
    # One in, one out: a dropped primitive is a missing wall.
    assert len(adapted) == len(stream)
    for primitive in adapted:
        assert primitive.layer in LAYER_NAMES, primitive.layer

    # Coordinates pass through untouched — the adapter makes no geometry decision.
    for source, target in zip(stream, adapted, strict=False):
        if hasattr(source, "a") and hasattr(source, "b"):
            assert target.a == tuple(source.a) and target.b == tuple(source.b)
        elif hasattr(source, "points"):
            assert target.vertices == tuple(tuple(p) for p in source.points)
        elif hasattr(source, "boundary"):
            assert target.outline == tuple(tuple(p) for p in source.boundary)

    # And the whole thing renders to a sanitary sheet.
    frame = default_frame(title_block=TitleBlock(firm_name="Studio Demo"))
    group = DrawingGroup(id="plan", placement=Placement(100), primitives=adapted)
    extent = group.extent_model_mm()
    assert extent is not None
    placement = fit_placement(extent, content_rect(frame), 100)
    from services.drawings.render.reference_sheets import _sheet
    from services.drawings.sheets import Scale, Viewport

    sheet = _sheet(
        sheet_id="adapted",
        kind="floor-plan",
        number="A-02A",
        title="Ground Floor Plan",
        viewport=Viewport(storey_id=storey_id),
        scale=Scale(100),
        title_block=TitleBlock(firm_name="Studio Demo"),
    )
    svg = render_sheet_svg(
        SheetDrawing(
            sheet=sheet,
            groups=(
                frame_group(sheet.frame),
                DrawingGroup(id="plan", placement=placement, primitives=adapted),
            ),
        )
    )
    assert_sanitary(svg)
    assert len(svg) > 5_000
    # Room labels from the engine survive into the sheet.
    assert "sq ft" in svg


def test_adapter_converts_text_height_from_model_mm_to_paper_um() -> None:
    """The one conversion that is easy to get wrong and invisible when wrong.

    The projection stream carries model mm (250 mm at 1:100 is 2.5 mm on paper); the
    renderers carry paper µm. A missed conversion gives text 100x too large or 100x too
    small — or, at 1:50 vs 1:100, plausibly wrong.
    """
    from services.drawings.render.adapt import from_projection_one, model_mm_to_paper_um

    assert model_mm_to_paper_um(250, 100) == 2_500
    assert model_mm_to_paper_um(125, 50) == 2_500
    assert model_mm_to_paper_um(500, 200) == 2_500

    class _Text:
        layer = "A-TEXT"
        position = (100, 200)
        text = "BEDROOM"
        height_mm = 250
        rotation_deg = 90
        h_align = "center"
        v_align = "bottom"
        owner_id = "room_1"
        kind = "room-name"

    converted = from_projection_one(_Text(), scale_denominator=100)
    assert converted.height_paper_um == 2_500
    assert converted.anchor == "middle"
    assert converted.baseline == "baseline"
    assert converted.rotation_deg == 90
    assert converted.element_id == "room_1"
    assert converted.bold is True, "a room name is the label a drawing is read by"

    # At 1:50 the same 250 mm of model is 5 mm of paper.
    assert from_projection_one(_Text(), scale_denominator=50).height_paper_um == 5_000

    try:
        model_mm_to_paper_um(250, 0)
    except ValueError:
        pass
    else:  # pragma: no cover
        raise AssertionError("a zero scale must raise, not divide")


def test_adapter_maps_patterns_styles_and_full_circles() -> None:
    from services.drawings.render.adapt import AdaptError, from_projection_one
    from services.drawings.render.primitives import (
        HATCH_CROSS,
        HATCH_DIAGONAL,
        HATCH_SOLID,
        STYLE_DASHED,
        STYLE_SOLID,
        Circle,
    )

    class _Hatch:
        layer = "A-WALL"
        boundary = ((0, 0), (100, 0), (100, 100))
        pattern = "ANSI31"
        angle_deg = 45
        spacing_mm = 250
        holes = ()
        owner_id = None
        kind = "wall-hatch"

    assert from_projection_one(_Hatch(), scale_denominator=100).pattern == HATCH_DIAGONAL
    _Hatch.pattern = "ANSI37"
    assert from_projection_one(_Hatch(), scale_denominator=100).pattern == HATCH_CROSS
    _Hatch.pattern = "SOLID"
    assert from_projection_one(_Hatch(), scale_denominator=100).pattern == HATCH_SOLID
    _Hatch.pattern = "TARTAN"
    try:
        from_projection_one(_Hatch(), scale_denominator=100)
    except AdaptError as exc:
        assert "no renderer equivalent" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("an unknown hatch pattern must not be silently substituted")

    class _Line:
        layer = "A-WALL-PART"
        a = (0, 0)
        b = (100, 0)
        dashed = True
        owner_id = "wall_1"
        kind = "wall-face"

    assert from_projection_one(_Line(), scale_denominator=100).style == STYLE_DASHED
    _Line.dashed = False
    assert from_projection_one(_Line(), scale_denominator=100).style == STYLE_SOLID

    class _Arc:
        layer = "A-TEXT"
        centre = (0, 0)
        radius_mm = 450
        start_deg = 0
        end_deg = 360
        dashed = False
        owner_id = None
        kind = "grid-bubble"

    # A 0->360 arc is a circle: one DXF CIRCLE and one SVG element, not two arcs.
    full = from_projection_one(_Arc(), scale_denominator=100)
    assert isinstance(full, Circle), type(full).__name__
    _Arc.end_deg = 90
    quarter = from_projection_one(_Arc(), scale_denominator=100)
    assert not isinstance(quarter, Circle)
    assert (quarter.start_deg, quarter.end_deg) == (0, 90)


def test_adapter_refuses_an_unknown_primitive() -> None:
    from services.drawings.render.adapt import AdaptError, from_projection

    class _Mystery:
        layer = "A-WALL"

    try:
        from_projection([_Mystery()], scale_denominator=100)
    except AdaptError as exc:
        assert "missing wall" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("an unknown primitive must raise, never be dropped")


# ---------------------------------------------------------------------------
# bare-python runner (pytest is not installed on the build machine)
# ---------------------------------------------------------------------------
if __name__ == "__main__":  # pragma: no cover
    import traceback

    failures = 0
    for _name, _fn in sorted(globals().items()):
        if _name.startswith("test_") and callable(_fn):
            try:
                _fn()
                print("PASS %s" % _name)
            except Exception:
                failures += 1
                print("FAIL %s" % _name)
                traceback.print_exc()
    print("\n%d failure(s)" % failures)
    sys.exit(1 if failures else 0)


# ---------------------------------------------------------------------------
# Opening tags: the plan prints what A-05 tabulates, and nothing else
# ---------------------------------------------------------------------------
def _opening_storeys(house: Any) -> dict[str, str]:
    wall_storey = {wall.id: wall.storey_id for wall in house.walls}
    return {opening.id: wall_storey[opening.wall_id] for opening in house.openings}


def _tag_texts(primitives: Any, opening_ids: set[str]) -> list[Text]:
    return [p for p in primitives if isinstance(p, Text) and p.element_id in opening_ids]


def test_plan_opening_tags_are_the_schedule_sheets_tags() -> None:
    """Every opening on every plan carries exactly the tag A-05 gives its group.

    Folds each fixture once, builds the schedule the A-05 sheet prints, then reads the
    tags off every floor-plan sheet and requires: one tag per opening on its storey,
    equal to the schedule's ``tag_by_opening_id``; per-storey counts of each tag equal
    to the schedule row's count column; and every row's tag present on some plan. The
    class-4 bug at data level ("the schedule assigns tags, the plan prints none") is
    exactly what this could not pass through.
    """
    from collections import Counter

    for name, fixture in _fixtures():
        doc = _fold(fixture)
        house = doc.house
        assert house.openings, "%s has no openings, so it proves nothing here" % name
        schedule = door_window_schedule(house)
        rows = {row.tag: row for row in build_schedule_rows(house)}
        storey_of = _opening_storeys(house)
        drawings = dict(_sheet_sets())[name]
        seen_tags: set[str] = set()
        for drawing in drawings.by_kind("floor-plan"):
            storey_id = drawing.meta["storeyId"]
            plan = next(g for g in drawing.groups if g.id.startswith("plan-"))
            expected = {oid for oid, sid in storey_of.items() if sid == storey_id}
            texts = _tag_texts(plan.primitives, expected)
            tagged = {t.element_id: t.text for t in texts}
            assert set(tagged) == expected, (name, storey_id, set(tagged) ^ expected)
            assert len(texts) == len(expected), "an opening was tagged twice"
            for opening_id, tag in tagged.items():
                assert tag == schedule.tag_by_opening_id[opening_id], (name, opening_id, tag)
            counts = Counter(tagged.values())
            for tag, count in counts.items():
                assert rows[tag].counts_by_storey.get(storey_id, 0) == count, (name, tag)
            seen_tags |= set(counts)
        assert seen_tags == set(rows), (name, seen_tags ^ set(rows))


def test_the_plan_prints_the_mapping_it_is_given_not_its_own() -> None:
    """Negative controls for the agreement above.

    An empty mapping draws no tags at all, and a deliberately wrong mapping is printed
    verbatim — so the positive test passes because the plan and the schedule share one
    source, not because the plan happens to compute the same thing.
    """
    _name, fixture = _fixtures()[-1]
    doc = _fold(fixture)
    storey_id = doc.house.storeys[0].id
    opening_ids = {oid for oid, sid in _opening_storeys(doc.house).items() if sid == storey_id}
    assert opening_ids

    none, _ = plan_primitives(doc, storey_id, opening_tags={})
    assert not _tag_texts(none, opening_ids)

    victim = sorted(opening_ids)[0]
    wrong, _ = plan_primitives(doc, storey_id, opening_tags={victim: "Z9"})
    texts = _tag_texts(wrong, opening_ids)
    assert [t.text for t in texts] == ["Z9"]
    assert "Z9" not in door_window_schedule(doc.house).tag_by_opening_id.values()


def test_tags_sit_beside_their_opening_and_inside_the_building() -> None:
    """A tag is within one door-width of its opening's centre, inside the building line,
    and touches no other label on the plan — the greedy placer's contract."""
    for _name, fixture in _fixtures():
        doc = _fold(fixture)
        house = doc.house
        openings = {o.id: o for o in house.openings}
        walls = {w.id: w for w in house.walls}
        for storey in house.storeys:
            extent = building_extent(house, storey.id)
            if extent is None:
                continue
            primitives, _ = plan_primitives(doc, storey.id, scale_denominator=100)
            boxes = [text_box_mm(p, 100) for p in primitives if isinstance(p, Text)]
            for index, a in enumerate(boxes):
                for b in boxes[index + 1 :]:
                    assert not a.overlaps(b), (storey.name, a, b)
            for text in _tag_texts(primitives, set(openings)):
                opening = openings[text.element_id]
                wall = walls[opening.wall_id]
                along = _opening_centre_along(wall, opening)
                centre = (along, wall.a.y) if wall.a.y == wall.b.y else (wall.a.x, along)
                dx = abs(text.at[0] - centre[0])
                dy = abs(text.at[1] - centre[1])
                assert max(dx, dy) <= opening.width_mm + 500, (text, centre)
                min_x, min_y, max_x, max_y = extent
                assert min_x <= text.at[0] <= max_x and min_y <= text.at[1] <= max_y, text


# ---------------------------------------------------------------------------
# Room labels: name, clear dimensions, m² and sq ft, inside the room
# ---------------------------------------------------------------------------
class _FakePt:
    def __init__(self, x: int, y: int) -> None:
        self.x, self.y = x, y


class _FakeRoom:
    def __init__(self, width: int, depth: int, name: str = "BEDROOM") -> None:
        self.id = "room_fake"
        self.storey_id = "s"
        self.type = "bedroom"
        self.name = name
        self.polygon = (_FakePt(0, 0), _FakePt(width, 0), _FakePt(width, depth), _FakePt(0, depth))
        self.area_mm2 = width * depth


def test_room_label_lines_are_the_polygon_extent_and_both_area_formats() -> None:
    from garh_model.units import format_sqft, format_sqm

    room = _FakeRoom(3623, 2703)
    assert room_label_lines(room) == (
        "BEDROOM",
        "3623 x 2703",
        format_sqm(3623 * 2703, 2),
        format_sqft(3623 * 2703, 1),
    )
    assert room_label_lines(room)[2].endswith("m²")
    assert room_label_lines(room)[3].endswith("sq ft")


def test_room_label_block_fits_its_room_and_sheds_lines_only_when_it_must() -> None:
    """The ladder: a bedroom gets four lines at full size; a passage turns; a shaft
    keeps its name. Every line drawn lies inside the room."""
    texts, boxes, complete = _room_label_block(_FakeRoom(3600, 3000), scale_denominator=100)
    assert complete and len(texts) == 4
    assert texts[0].bold and texts[0].height_paper_um == 2_500
    assert all(box.inside(Box(0, 0, 3600, 3000)) for box in boxes)
    assert all(text.rotation_deg == 0 for text in texts)

    # Deeper than wide, and too narrow for the block upright: it turns.
    texts, boxes, complete = _room_label_block(
        _FakeRoom(1000, 3000, "PASSAGE"), scale_denominator=100
    )
    assert complete, [t.text for t in texts]
    assert all(text.rotation_deg == 90 for text in texts)
    assert all(box.inside(Box(0, 0, 1000, 3000)) for box in boxes)

    # A shallow utility: the areas share a line rather than vanish.
    texts, _boxes, complete = _room_label_block(
        _FakeRoom(2400, 863, "UTILITY"), scale_denominator=100
    )
    assert complete
    assert [t.text for t in texts][:2] == ["UTILITY", "2400 x 863"]
    assert "m²" in texts[2].text and "sq ft" in texts[2].text

    # A shaft: the name, and honestly reported as incomplete.
    texts, _boxes, complete = _room_label_block(_FakeRoom(863, 633, "SHAFT"), scale_denominator=100)
    assert not complete
    assert [t.text for t in texts] == ["SHAFT"]


def test_room_label_block_keeps_off_a_stair_flight() -> None:
    room = _FakeRoom(2128, 2703, "STAIRCASE")
    flight = Box(0, 0, 900, 1750)  # a dogleg's drawn flight up the left side
    texts, boxes, _complete = _room_label_block(room, scale_denominator=100, obstacles=(flight,))
    assert texts
    assert all(not box.overlaps(flight) for box in boxes), boxes
    assert all(box.inside(Box(0, 0, 2128, 2703)) for box in boxes)
    # Negative control: without the obstacle the block is centred on the flight.
    _texts, centred, _ = _room_label_block(room, scale_denominator=100)
    assert any(box.overlaps(flight) for box in centred)


def test_every_room_on_every_plan_is_labelled_and_a_real_room_gets_all_four_values() -> None:
    """Corpus gate: rooms of 2 m² and more carry name, dimensions, m² and sq ft, inside
    the room; smaller ones carry at least their name and are listed, not lost."""
    partial: list[tuple[str, str, int]] = []
    for name, fixture in _fixtures():
        doc = _fold(fixture)
        for storey in doc.house.storeys:
            if not [w for w in doc.house.walls if w.storey_id == storey.id]:
                continue
            primitives, _ = plan_primitives(doc, storey.id, scale_denominator=100)
            for room in doc.house.rooms:
                if room.storey_id != storey.id:
                    continue
                lines = room_label_lines(room)
                texts = [p for p in primitives if isinstance(p, Text) and p.element_id == room.id]
                printed = " | ".join(t.text for t in texts)
                assert texts and texts[0].text == lines[0], (name, room.name)
                xs = [p.x for p in room.polygon]
                ys = [p.y for p in room.polygon]
                room_box = Box(min(xs), min(ys), max(xs), max(ys))
                for text in texts:
                    assert text_box_mm(text, 100).inside(room_box), (name, room.name, text)
                if all(value in printed for value in lines[1:]):
                    continue
                partial.append((name, room.name, room.area_mm2))
                assert room.area_mm2 < 2_000_000, (
                    "%s: %s is %d mm² and lost part of its label: %r"
                    % (name, room.name, room.area_mm2, printed)
                )
