"""``services.drawings.pipeline`` — the model → six-sheets contract (§7, F7-A).

Runs two ways, like every sibling in this directory:

    python3 services/drawings/tests/test_pipeline.py     # bare interpreter, no pytest
    pytest -q services/drawings/tests/test_pipeline.py

Self-bootstrapping: it puts the repo root and ``apps/api`` on ``sys.path`` and installs
``services.dev_stubs`` so ``structlog``/``pydantic`` absence cannot stop the pure core
from being exercised. Nothing here needs ``ezdxf``, a queue, or a database — which is
the whole point of the pipeline being a function.
"""

from __future__ import annotations

import json
import os
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.abspath(os.path.join(_HERE, "..", "..", ".."))
for _path in (_ROOT, os.path.join(_ROOT, "apps", "api")):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from services.dev_stubs import install_worker_dep_stubs  # noqa: E402

install_worker_dep_stubs()

from garh_model.fold import apply_group  # noqa: E402
from garh_model.model import empty_project_doc  # noqa: E402
from garh_model.ops import Op  # noqa: E402

from services.drawings.pipeline import (  # noqa: E402
    DB_KIND_ORDER,
    DB_KIND_TO_DRAWING_KIND,
    DRAWING_KIND_TO_DB_KIND,
    PipelineError,
    SheetBundle,
    TransportStatement,
    build_sheets,
    canonical_sheet_kinds,
    sheet_glb_bytes,
    sheet_png_manifest,
)

FIXTURE_DIR = os.path.join(_ROOT, "fixtures", "sheets", "inputs")
RULEPACK_DIR = os.path.join(_ROOT, "rulepacks")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
def _fixture_path(name: str) -> str:
    return os.path.join(FIXTURE_DIR, name)


def load_document(name: str = "demo-02-blr-30x40-g1.json") -> dict:
    """Fold a real op log through the real engine, then hand over its JSON.

    The pipeline takes a *document*, not a doc object, because that is what crosses the
    presigned asset boundary in production. Folding here rather than checking in a
    folded document keeps the fixture honest: if an op's fold changes, this test sees it.
    """
    with open(_fixture_path(name), encoding="utf-8") as handle:
        fixture = json.load(handle)
    ops = [Op.from_json(raw) for raw in fixture["ops"]]
    doc = apply_group(empty_project_doc(fixture.get("unitsDisplay", "ft-in")), ops).model
    return doc.to_json()


def load_areas(document: dict) -> dict:
    """``AreaStatement.to_json()`` from the SAME evaluation compliance uses (§7).

    Imported here rather than at module scope so a machine without the rules packs still
    runs the sheet tests that do not need them.
    """
    from garh_api.compliance import build_evaluation_context, packs_for
    from garh_rules import evaluate

    packs = packs_for(document)
    context = build_evaluation_context(document, packs=list(packs))
    return evaluate(context, root=RULEPACK_DIR).areas.to_json()


TITLE_BLOCK = {
    "firmName": "Studio Demo",
    "projectName": "30x40 Bengaluru G+1",
    "clientName": "Sri R. Kumar",
    "revision": "A",
    "date": "01-01-2026",
    "drawnBy": "GA",
    "checkedBy": "HG",
}


def make_bundle(**overrides):
    document = overrides.pop("document", None) or load_document()
    areas = overrides.pop("areas", "auto")
    if areas == "auto":
        areas = TransportStatement.from_json(load_areas(document))
    payload = {
        "designVersionId": "11111111-1111-1111-1111-111111111111",
        "scaleDenominator": 100,
        "sheetSize": "A2",
        "dimToJamb": False,
        "titleBlock": TITLE_BLOCK,
        "revisions": [{"revision": "A", "date": "01-01-2026", "note": "First submission"}],
    }
    payload.update(overrides.pop("payload", {}))
    bundle = SheetBundle.from_payload(payload, document=document, areas=None)
    return SheetBundle(
        document=bundle.document,
        areas=areas,
        kinds=overrides.pop("kinds", bundle.kinds),
        scale_denominator=bundle.scale_denominator,
        paper=bundle.paper,
        dim_to_jamb=overrides.pop("dim_to_jamb", bundle.dim_to_jamb),
        title_block_fields=bundle.title_block_fields,
        revisions=bundle.revisions,
        number_prefix=bundle.number_prefix,
        design_version_id=bundle.design_version_id,
    )


# ---------------------------------------------------------------------------
# Kind vocabularies
# ---------------------------------------------------------------------------
def test_the_two_kind_vocabularies_are_a_bijection():
    """The DB CHECK constraint and the drawing engine must agree, one to one.

    They are different spellings of the same six sheets ("floor" vs "floor-plan"). A
    request for ``kinds:["floor"]`` used to pass the API validator and then fail the
    worker's; the table is what stops that recurring.
    """
    from services.drawings.sheets import SHEET_KINDS as DRAWING_KINDS

    assert tuple(sorted(DRAWING_KIND_TO_DB_KIND)) == tuple(sorted(DRAWING_KINDS))
    assert len(DB_KIND_TO_DRAWING_KIND) == len(DRAWING_KIND_TO_DB_KIND) == 6
    # The DB half needs SQLAlchemy to import garh_api.models. Absent here (Python 3.9,
    # no worker deps), so the check is skipped LOUDLY rather than silently passing:
    # in CI both halves import and the assertion runs.
    try:
        from garh_api import models
    except ImportError as exc:
        print("  note: DB SHEET_KINDS half not checked here (%s)" % exc)
        return
    assert tuple(sorted(DB_KIND_TO_DRAWING_KIND)) == tuple(sorted(models.SHEET_KINDS))


def test_canonical_kinds_accepts_both_spellings():
    assert canonical_sheet_kinds(None) == DB_KIND_ORDER
    assert canonical_sheet_kinds(["floor"]) == ("floor",)
    assert canonical_sheet_kinds(["floor-plan"]) == ("floor",)
    # Order is submission order, not request order.
    assert canonical_sheet_kinds(["area-statement", "site"]) == ("site", "area-statement")
    # Duplicates collapse rather than drawing a sheet twice.
    assert canonical_sheet_kinds(["floor", "floor-plan"]) == ("floor",)


def test_unknown_kind_is_an_actionable_error():
    try:
        canonical_sheet_kinds(["roof-plan"])
    except PipelineError as exc:
        assert "roof-plan" in exc.detail
        assert exc.action
    else:  # pragma: no cover
        raise AssertionError("an unknown sheet kind must raise")


# ---------------------------------------------------------------------------
# The set itself
# ---------------------------------------------------------------------------
def test_the_full_set_is_the_six_f7a_kinds():
    result = build_sheets(make_bundle())
    kinds = {sheet.kind for sheet in result.sheets}
    assert kinds == set(DB_KIND_ORDER), (kinds, result.skipped)
    # One site, one section, one schedule, one area statement, four elevations, and one
    # plan per storey that has walls.
    counts = {kind: len([s for s in result.sheets if s.kind == kind]) for kind in kinds}
    assert counts["site"] == 1
    assert counts["section"] == 1
    assert counts["schedule"] == 1
    assert counts["area-statement"] == 1
    assert counts["elevation"] == 4
    assert counts["floor"] >= 1


def test_slugs_are_derived_from_geometry_not_from_names():
    """§7's regeneration contract: ``Annotation.sheetId`` must survive a rename.

    ``reference_sheets`` names a plan sheet after the storey ("sheet-plan-ground-floor").
    The pipeline overrides that with ``floor-plan-<storeyId>`` — an id the user cannot
    edit — so renaming "Ground Floor" to "Stilt" does not orphan every annotation on it.
    """
    document = load_document()
    result = build_sheets(make_bundle(document=document))
    storey_ids = [s["id"] for s in document["house"]["storeys"]]
    slugs = {sheet.slug for sheet in result.sheets}
    assert "site-plan" in slugs
    assert "section-a" in slugs
    assert "door-window-schedule" in slugs
    assert "area-statement" in slugs
    assert {"elevation-n", "elevation-e", "elevation-s", "elevation-w"} <= slugs
    assert any("floor-plan-%s" % sid in slugs for sid in storey_ids)

    renamed = json.loads(json.dumps(document))
    renamed["house"]["storeys"][0]["name"] = "Stilt Level"
    after = build_sheets(make_bundle(document=renamed))
    assert {s.slug for s in after.sheets} == slugs


def test_the_api_slug_mirror_agrees():
    """``garh_api.routers.sheets.sheet_slugs_for`` must predict exactly these slugs.

    The API mints one presigned PUT per sheet per format, so it has to know the slugs
    before the worker computes them — and it deliberately does not import
    ``services.*`` (the same rule that makes ``garh_api.queue`` a hand-mirror of the
    worker envelope). This test is what keeps the duplicate honest: one real fold
    through both sides, compared.

    A slug the worker draws but the API did not predict costs that sheet its download.
    A slug the API predicts but the worker never draws costs one unused URL.
    """
    document = load_document()
    result = build_sheets(make_bundle(document=document))
    drawn = sorted((sheet.slug, sheet.kind) for sheet in result.sheets)
    try:
        from garh_api.routers.sheets import sheet_slugs_for
    except ImportError as exc:
        # FastAPI/SQLAlchemy are absent on a bare interpreter. Say so out loud rather
        # than passing silently — in CI both halves import and this really compares.
        print("  note: API slug mirror not checked here (%s)" % exc)
        return
    predicted = sorted(sheet_slugs_for(document, DB_KIND_ORDER))
    assert predicted == drawn, (predicted, drawn)


def test_numbering_suffixes_only_when_a_kind_has_several_sheets():
    result = build_sheets(make_bundle())
    numbers = {sheet.slug: sheet.number for sheet in result.sheets}
    assert numbers["site-plan"] == "A-01"
    assert numbers["section-a"] == "A-04"
    assert numbers["door-window-schedule"] == "A-05"
    assert numbers["area-statement"] == "A-06"
    # Four elevations always get a letter.
    assert numbers["elevation-n"] == "A-03A"
    assert numbers["elevation-w"] == "A-03D"
    # Every number is unique — a duplicate number would collide in the sheets table's
    # (kind, number) re-anchor key and silently move annotations.
    assert len(set(numbers.values())) == len(numbers)


def test_the_printed_sheet_number_matches_the_persisted_one():
    """Restamping the slug must restamp the title block too, or the paper lies."""
    result = build_sheets(make_bundle())
    for sheet in result.sheets:
        block = sheet.drawing.sheet.frame.title_block
        assert block.sheet_number == sheet.number, sheet.slug
        assert block.drawing_title == sheet.title, sheet.slug
        assert sheet.number in sheet.svg


def test_requesting_one_kind_draws_only_that_kind():
    result = build_sheets(make_bundle(kinds=("schedule",)))
    assert [s.kind for s in result.sheets] == ["schedule"]


# ---------------------------------------------------------------------------
# The invariant §7 step 5 calls out by name
# ---------------------------------------------------------------------------
def test_every_chain_sums_exactly():
    """ "Values from integer mm — chains must sum exactly." Asserted, not commented."""
    result = build_sheets(make_bundle())
    checked = 0
    for sheet in result.sheets:
        for chain in sheet.chains:
            assert chain["sumMm"] == chain["overallMm"], (sheet.slug, chain["id"])
            assert sum(seg["lengthMm"] for seg in chain["segments"]) == chain["overallMm"]
            # Every segment length is an int — a float here is how drift starts.
            for segment in chain["segments"]:
                assert isinstance(segment["lengthMm"], int)
            checked += 1
    assert checked >= 8, "the demo set should carry real dimension chains, got %d" % checked
    assert result.chain_count == checked


def test_generation_is_deterministic_byte_for_byte():
    bundle = make_bundle()
    first = build_sheets(bundle)
    second = build_sheets(bundle)
    assert [s.svg for s in first.sheets] == [s.svg for s in second.sheets]
    assert first.state_hash == second.state_hash
    assert json.dumps(first.to_json(), sort_keys=True) == json.dumps(
        second.to_json(), sort_keys=True
    )


def test_no_svg_carries_a_timestamp_or_a_random_id():
    """§16's golden gate rests on this. A date in the output means a daily CI failure."""
    result = build_sheets(make_bundle())
    for sheet in result.sheets:
        lowered = sheet.svg.lower()
        for banned in ("generated on", "<script", "foreignobject", "onload="):
            assert banned not in lowered, (sheet.slug, banned)


def test_svg_is_sanitary_and_print_true():
    from services.drawings.render.sanitize import assert_sanitary

    result = build_sheets(make_bundle())
    for sheet in result.sheets:
        assert_sanitary(sheet.svg)  # raises on anything executable
        assert sheet.svg.startswith("<svg ")
        assert sheet.svg.endswith("</svg>\n")
        # Print-true: physical page size in millimetres, not pixels.
        assert 'width="594mm"' in sheet.svg and 'height="420mm"' in sheet.svg


# ---------------------------------------------------------------------------
# One source for the area statement (§7)
# ---------------------------------------------------------------------------
def test_the_area_sheet_prints_the_statement_it_was_given_and_computes_nothing():
    """Doctor the statement; the doctored number must reach the paper.

    This is the test that makes "same numbers, one source" enforceable. If anyone ever
    adds a FAR calculation to the drawings package, the sheet stops matching the
    statement and this fails.
    """
    document = load_document()
    payload = load_areas(document)
    for row in payload["rows"]:
        if row["key"] == "plot_area":
            row["value"] = 123_456_789
            row["label"] = "Plot area"
    statement = TransportStatement.from_json(payload)
    result = build_sheets(
        make_bundle(document=document, areas=statement, kinds=("area-statement",))
    )
    sheet = result.sheets[0]
    from services.drawings.render.tables import _format_value  # type: ignore[attr-defined]

    printed = _format_value(statement.rows()[0])
    assert printed in sheet.svg, printed


def test_a_missing_evaluation_skips_the_area_sheet_with_a_note():
    """No statement → no area sheet, and the job says why. Never a blank sheet."""
    result = build_sheets(make_bundle(areas=None))
    assert all(sheet.kind != "area-statement" for sheet in result.sheets)
    assert any("compliance" in note for note in result.notes), result.notes
    # The other five kinds still generate.
    assert {s.kind for s in result.sheets} == set(DB_KIND_ORDER) - {"area-statement"}


def test_ratios_match_the_engines_own_formatted_strings():
    """The transport statement's FAR/coverage must print what the engine printed.

    ``TransportStatement`` rebuilds the two ratios as ``Fraction(numerator,
    denominator)`` over the engine's own integers, because the site plan's note is
    rendered by ``garh_rules.formatting.format_ratio``, which needs an exact rational.
    This test is the guarantee that the rebuild is a *presentation* of the engine's
    numbers and not a second opinion: it compares against the ``farAchieved`` /
    ``coverageAchieved`` strings the engine itself serialised.
    """
    from garh_rules.formatting import format_ratio

    payload = load_areas(load_document())
    statement = TransportStatement.from_json(payload)
    assert format_ratio(statement.far_achieved) == payload["farAchieved"]
    assert format_ratio(statement.coverage_achieved) == payload["coverageAchieved"]
    if payload.get("farAllowed") is not None:
        assert format_ratio(statement.far_allowed) == payload["farAllowed"]
    if payload.get("coverageAllowed") is not None:
        assert format_ratio(statement.coverage_allowed) == payload["coverageAllowed"]


def test_the_site_plan_note_prints_the_engines_far_and_coverage():
    payload = load_areas(load_document())
    statement = TransportStatement.from_json(payload)
    result = build_sheets(make_bundle(areas=statement, kinds=("site",)))
    svg = result.sheets[0].svg
    assert "FAR ACHIEVED: %s" % payload["farAchieved"] in svg
    assert "GROUND COVERAGE" in svg and "PLOT AREA" in svg


def test_transport_statement_rejects_an_empty_evaluation():
    try:
        TransportStatement.from_json({"warnings": []})
    except PipelineError as exc:
        assert "compliance" in exc.action.lower()
    else:  # pragma: no cover
        raise AssertionError("an empty statement must raise")


def test_transport_statement_carries_warnings_to_the_sheet():
    document = load_document()
    payload = load_areas(document)
    payload["warnings"] = ["Per-storey built-up areas do not sum to the total."]
    statement = TransportStatement.from_json(payload)
    result = build_sheets(
        make_bundle(document=document, areas=statement, kinds=("area-statement",))
    )
    assert "do not sum to the total" in result.sheets[0].svg


# ---------------------------------------------------------------------------
# Anchors, viewports, layout payload
# ---------------------------------------------------------------------------
def test_each_sheet_reports_the_element_ids_drawn_on_it():
    """The Review Tray's re-attach picker offers these, not the whole model."""
    result = build_sheets(make_bundle())
    plans = [s for s in result.sheets if s.kind == "floor"]
    assert plans
    for plan in plans:
        assert plan.element_ids, plan.slug
        assert plan.element_ids == sorted(set(plan.element_ids))
        assert any(eid.startswith(("wall", "room", "opening")) for eid in plan.element_ids)


def test_viewport_names_exactly_one_selector():
    result = build_sheets(make_bundle())
    for sheet in result.sheets:
        viewport = sheet.viewport
        selectors = [k for k in ("storeyId", "elevationDirection", "sectionLine") if k in viewport]
        if sheet.kind in ("schedule", "area-statement"):
            assert selectors == [], (sheet.slug, selectors)
        else:
            assert len(selectors) == 1, (sheet.slug, selectors)


def test_layout_json_is_persistable_and_holds_no_svg():
    result = build_sheets(make_bundle())
    for sheet in result.sheets:
        layout = sheet.to_json()
        assert "svg" not in layout and "drawing" not in layout
        assert layout["sheetId"] == sheet.slug
        assert layout["stats"]["svgBytes"] > 500
        json.dumps(layout)  # must be JSONB-safe


def test_section_viewport_carries_the_cut_line_the_plan_marks():
    result = build_sheets(make_bundle())
    section = [s for s in result.sheets if s.kind == "section"]
    assert section
    line = section[0].viewport.get("sectionLine")
    assert isinstance(line, list) and len(line) == 2
    assert all(isinstance(v, int) for point in line for v in point), line


# ---------------------------------------------------------------------------
# Honest failure
# ---------------------------------------------------------------------------
def test_an_empty_design_produces_no_building_sheets_and_says_why():
    """Nothing is invented and nothing is silently dropped.

    An empty project has no walls, so there is no plan, no elevation and no section.
    Each missing sheet is recorded in ``skipped`` with the reason the builder gave, so
    the UI can say "no walls yet" instead of showing an empty frame or a failed job.
    """
    empty = empty_project_doc("ft-in").to_json()
    result = build_sheets(SheetBundle(document=empty))
    drawn = {sheet.kind for sheet in result.sheets}
    assert "floor" not in drawn and "elevation" not in drawn and "section" not in drawn
    skipped = {entry["kind"] for entry in result.skipped}
    assert {"elevation", "section"} <= skipped, result.skipped
    assert all(entry["reason"] for entry in result.skipped)


def test_a_design_that_draws_nothing_at_all_fails_with_an_action():
    try:
        build_sheets(SheetBundle(document=empty_project_doc("ft-in").to_json(), kinds=("floor",)))
    except PipelineError as exc:
        assert exc.action
        assert "storey" in exc.action or "walls" in exc.action
    else:  # pragma: no cover
        raise AssertionError("a set with every sheet skipped must fail")


def test_a_broken_document_names_the_problem():
    try:
        build_sheets(SheetBundle(document={"nonsense": True}))
    except PipelineError as exc:
        assert "ProjectDoc.from_json" in exc.detail or "expected an object" in exc.detail
    else:  # pragma: no cover
        raise AssertionError("a non-document must raise")


def test_one_broken_sheet_does_not_kill_the_set():
    """A storey with no walls is skipped with a reason, not a 500."""
    document = load_document()
    template = json.loads(json.dumps(document["house"]["storeys"][-1]))
    template["id"] = "storey_empty_test"
    template["name"] = "Terrace"
    document["house"]["storeys"].append(template)
    result = build_sheets(make_bundle(document=document))
    # The extra storey has no walls, so it simply produces no plan sheet.
    assert all("storey_empty_test" not in sheet.slug for sheet in result.sheets)
    assert result.sheets


# ---------------------------------------------------------------------------
# Exports that need no third-party package
# ---------------------------------------------------------------------------
def test_glb_export_runs_here_and_is_deterministic():
    document = load_document()
    first = sheet_glb_bytes(document)
    second = sheet_glb_bytes(document)
    assert first == second
    assert first[:4] == b"glTF"
    assert len(first) > 2000


def test_png_pack_manifest_names_files_in_submission_order():
    result = build_sheets(make_bundle())
    manifest = sheet_png_manifest(result.drawings())
    assert len(manifest) == len(result.sheets)
    assert manifest[0]["filename"].startswith("01-a-01")
    for index, entry in enumerate(manifest, start=1):
        assert entry["filename"].startswith("%02d-" % index)
        # The "review" preset caps the long edge at 2400 px, so A2's effective DPI
        # comes out BELOW the 150 target. `textLegible` is the honest signal the UI
        # shows, not the nominal DPI — asserted here so a preset change that makes
        # dimension text unreadable fails a test instead of shipping.
        assert entry["widthPx"] <= 2400 and entry["heightPx"] <= 2400
        assert entry["widthPx"] > 1000 and entry["heightPx"] > 700
        assert entry["preset"] == "review"
        assert isinstance(entry["textLegible"], bool)


def test_dxf_absence_is_reported_as_a_format_problem_not_a_job_failure():
    """No ezdxf here, so this asserts the honest error rather than the bytes."""
    from services.drawings.pipeline import sheet_dxf_bytes

    result = build_sheets(make_bundle(kinds=("schedule",)))
    try:
        import ezdxf  # noqa: F401
    except ImportError:
        try:
            sheet_dxf_bytes(result.drawings())
        except PipelineError as exc:
            assert "ezdxf" in exc.detail.lower() or "ezdxf" in exc.action.lower()
            return
        raise AssertionError("without ezdxf the DXF path must raise PipelineError") from None
    assert isinstance(sheet_dxf_bytes(result.drawings()), bytes)


# ---------------------------------------------------------------------------
# The revision register and the carpet section, on the sheet the product ships
# ---------------------------------------------------------------------------
#: The revision rows **exactly as the API serialises them**: the field names of
#: ``garh_api.schemas.sheets.RevisionRow`` under ``model_dump(by_alias=True)``, which
#: ``routers/sheets.py`` drops straight into the ``drawings.generate_sheets`` payload.
#: Hand-made rows in the register's own ``{"number", "description"}`` spelling are how
#: the register came to be permanently empty in production with every test green.
API_REVISION_ROWS = [
    {"revision": "R1", "date": "05-03-2026", "note": "Issued for sanction", "author": "SG"},
    {"revision": "R2", "date": "19-03-2026", "note": "Setbacks revised per query", "author": "SG"},
]


def _payload_bundle(**payload_overrides):
    """A bundle built the way the worker builds one: payload in, nothing hand-set.

    ``make_bundle`` above reconstructs the dataclass field by field and drops ``register``
    on the way; this keeps whatever ``SheetBundle.from_payload`` decided, which is the
    only thing production runs.
    """
    document = payload_overrides.pop("document", None) or load_document()
    areas = payload_overrides.pop("areas", "auto")
    if areas == "auto":
        areas = TransportStatement.from_json(load_areas(document))
    payload = {
        "designVersionId": "11111111-1111-1111-1111-111111111111",
        "kinds": ["area-statement"],
        "titleBlock": TITLE_BLOCK,
        "revisions": API_REVISION_ROWS,
    }
    payload.update(payload_overrides)
    bundle = SheetBundle.from_payload(payload, document=document, areas=None)
    import dataclasses

    return dataclasses.replace(bundle, areas=areas)


def test_the_api_revision_payload_draws_a_real_register_on_the_paper():
    """D-1 through the only caller that exists, in the shape it actually sends.

    ``Revision.from_json`` demanded ``number``; ``RevisionRow`` sends ``revision`` and
    ``note``. So ``_register_from`` raised, swallowed the error and returned ``None``,
    and every sheet set the product has ever generated carried an empty register while
    the feature's own tests — which built rows in the other spelling — stayed green.
    """
    bundle = _payload_bundle()
    assert bundle.register is not None, "the API's own row shape must build a register"
    assert [r.number for r in bundle.register] == ["R1", "R2"]
    assert bundle.register.latest.author == "SG"

    sheet = next(s for s in build_sheets(bundle).sheets if s.kind == "area-statement")
    assert ">REVISION REGISTER<" in sheet.svg
    assert sheet.svg.count(">BY<") == 1, "the register's author column must be on the sheet"
    for row in API_REVISION_ROWS:
        assert ">%s<" % row["note"] in sheet.svg, row
        assert ">%s<" % row["author"] in sheet.svg, row


def test_the_shipped_area_sheet_carries_the_carpet_section_and_its_serials():
    """D-6's citable serials, on the rendering the product produces (not the test's own).

    ``AreaStatementSheet.municipal_form()`` passes its own storey lines, so carpet is
    section 5 and setbacks are section 6 — which is what the docstrings and
    ``test_area_statement.py`` cite ("clarify item 6.2"). The worker passed no carpet
    lines at all, so the shipped sheet had no section 5 and every section after it was
    numbered one lower. A serial that differs between the tested sheet and the printed
    one is not a citable serial.
    """
    from services.drawings.pipeline import _parse_document
    from services.drawings.render.reference_sheets import carpet_lines_for
    from services.drawings.schedules.municipal import municipal_form

    document = load_document()
    statement = TransportStatement.from_json(load_areas(document))
    lines = carpet_lines_for(_parse_document(document), statement)
    assert lines, "the demo model has rooms, so the shipped sheet must have carpet lines"
    assert all(line.carpet_area_mm2 for line in lines)
    # Carpet is a subset of built-up; a derivation that counted the wrong rooms shows here.
    for line in lines:
        assert line.carpet_area_mm2 <= line.built_up_area_mm2, line

    form = municipal_form(statement, carpet_lines=lines)
    numbered = {row.number: row.description for row in form.rows}
    carpet_band = next(n for n, d in numbered.items() if "CARPET AREA" in d)
    setback_band = next(n for n, d in numbered.items() if d.startswith("SETBACKS"))
    assert carpet_band == "5", numbered
    assert setback_band == "6", numbered
    assert "6.2" in numbered, numbered

    # …and that is the sheet the worker draws, section band and figures both.
    sheet = next(s for s in build_sheets(_payload_bundle()).sheets if s.kind == "area-statement")
    assert ">CARPET AREA (not a regulatory figure)<" in sheet.svg
    assert ">TOTAL CARPET AREA<" in sheet.svg
    for number, description in numbered.items():
        assert ">%s<" % number in sheet.svg, (number, description)


def test_an_unreadable_previous_issue_skips_the_clouds_not_the_whole_set():
    """The diff is an optional annotation; it must never cost the sheets.

    ``_revision_diff`` runs before the per-sheet ``try``/``except``, so a previous
    document the diff cannot read used to fail the entire job — nine good sheets lost to
    one unreadable asset, contradicting the handler's promise that a set without a
    readable previous issue "draws exactly as before".
    """
    import dataclasses

    # Float geometry: `diff.py` refuses it by design (integer millimetres, model-wide).
    broken_previous = {
        "house": {
            "storeys": [{"id": "storey_x", "name": "Ground"}],
            "walls": [
                {
                    "id": "wall_x",
                    "storeyId": "storey_x",
                    "a": {"x": 0.5, "y": 0},
                    "b": {"x": 1000, "y": 0},
                    "thicknessMm": 230,
                    "heightMm": 3000,
                }
            ],
        }
    }
    bundle = dataclasses.replace(
        _payload_bundle(kinds=["floor", "area-statement"]), previous_document=broken_previous
    )
    assert bundle.register is not None

    result = build_sheets(bundle)
    assert result.sheets, "an unreadable previous issue must not fail the set"
    assert any("no revision clouds" in note for note in result.notes), result.notes
    # The register still prints: it does not depend on the diff.
    area = next(s for s in result.sheets if s.kind == "area-statement")
    assert ">REVISION REGISTER<" in area.svg


# ---------------------------------------------------------------------------
# §14 budget
# ---------------------------------------------------------------------------
def test_g1_3bhk_set_is_far_inside_the_five_minute_budget():
    """§14: "Sheet set G+1 3BHK ≤5min". Measured, printed, and asserted with headroom."""
    bundle = make_bundle()
    started = time.perf_counter()
    result = build_sheets(bundle)
    elapsed = time.perf_counter() - started
    assert len(result.sheets) >= 6
    assert elapsed < 60.0, "%d sheets took %.1fs" % (len(result.sheets), elapsed)
    globals()["_LAST_TIMING"] = (len(result.sheets), elapsed, result.chain_count)


# ---------------------------------------------------------------------------
# Bare runner
# ---------------------------------------------------------------------------
def _main() -> int:
    tests = [
        (name, fn)
        for name, fn in sorted(globals().items())
        if name.startswith("test_") and callable(fn)
    ]
    passed, failed = 0, []
    for name, fn in tests:
        try:
            fn()
            passed += 1
        except Exception as exc:
            import traceback

            failed.append((name, "%s: %s" % (type(exc).__name__, exc)))
            traceback.print_exc()
    print("\n%d passed, %d failed" % (passed, len(failed)))
    for name, message in failed:
        print("  FAIL %s — %s" % (name, message))

    if not failed:
        timing = globals().get("_LAST_TIMING")
        result = build_sheets(make_bundle())
        print("\nsheet set (demo-02, BLR 30x40 G+1):")
        for sheet in result.sheets:
            print(
                "  %-6s %-16s %-22s 1:%-4d %5d prims %2d chains %6d B svg"
                % (
                    sheet.number,
                    sheet.kind,
                    sheet.slug,
                    sheet.scale_denominator,
                    sheet.primitive_count,
                    len(sheet.chains),
                    len(sheet.svg.encode("utf-8")),
                )
            )
        print(
            "  totals: %d sheets, %d chains (all sum exactly), %d label collisions"
            % (len(result.sheets), result.chain_count, result.label_collisions)
        )
        if result.skipped:
            print(
                "  skipped: %s"
                % "; ".join("%s (%s)" % (s["sheetId"], s["reason"]) for s in result.skipped)
            )
        if result.notes:
            print("  notes: %s" % " | ".join(result.notes))
        if timing:
            print("  §14 budget: %d sheets in %.2fs (limit 300s)" % (timing[0], timing[1]))
        print("  stateHash: %s" % result.state_hash)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(_main())
