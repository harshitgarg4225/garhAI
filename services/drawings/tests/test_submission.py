"""Per-authority submission templates (D-4).

A rule pack answers *is this design legal*. A template answers *is this SET
submittable* — which is a different question, and the one that actually sends an
architect back across the counter. A perfectly compliant design still gets returned
when the khata number is missing from the title block, and no compliance engine can
see that.

Three defects here would each pass a green suite and be wrong on paper, so each has a
negative control:

* a template that requires a sheet kind, a rule pack or a title-block label the rest of
  the system does not have — it loads, it reads correctly, and the requirement simply
  never fires (this repository shipped that once already: 83 rules went quiet because a
  default sat outside the packs' own enum);
* a readiness check that reports green over statutory boxes the frame never prints;
* a Bengaluru UI that assumes one authority per city, and hands half of Bengaluru the
  wrong checklist.
"""

from __future__ import annotations

import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.abspath(os.path.join(_HERE, "..", "..", ".."))
for _path in (_ROOT, os.path.join(_ROOT, "apps", "api")):
    if _path not in sys.path:
        sys.path.insert(0, _path)

import pytest  # noqa: E402

from services.dev_stubs import install_worker_dep_stubs  # noqa: E402

install_worker_dep_stubs()

from garh_model.testing import make_two_room_plan_with_openings  # noqa: E402

from services.drawings import submission  # noqa: E402
from services.drawings.pipeline import (  # noqa: E402
    DB_KIND_ORDER,
    SUBMISSION_DB_KINDS,
    SheetBundle,
    build_sheets,
)
from services.drawings.projection.primitives import Text  # noqa: E402
from services.drawings.sheets.frame import (  # noqa: E402
    LABEL_MAX_CHARS,
    frame_primitives,
)
from services.drawings.submission import (  # noqa: E402
    SubmissionTemplateError,
    check_submission,
    load_templates,
    statutory_pairs,
    template_for,
    templates_for_city_pack,
)

BBMP_FIELDS = {
    "khataNumber": "A-1234/56",
    "wardNumber": "150",
    "architectRegistrationNo": "CA/2011/51234",
    "ownerName": "R Rao",
}


def _write_template(tmp_path, **overrides):
    """Write one template to a temp dir and load it. Used to break gates on purpose."""
    payload = {
        "authority": "test",
        "cityPack": "blr",
        "title": "Test authority (seed)",
        "shortTitle": "TEST",
        "citation": "nothing at all",
        "confidence": "seed",
        "review": "unreviewed",
        "paper": "A2",
        "scaleDenominator": 100,
        "sheets": [{"kind": "floor", "required": True, "note": ""}],
        "statutoryFields": [{"key": "plotNumber", "label": "PLOT NO.", "required": True}],
        "declarations": [],
    }
    payload.update(overrides)
    index = {
        "schemaVersion": 1,
        "templates": [{"authority": payload["authority"], "file": "test.json"}],
    }
    (tmp_path / "test.json").write_text(json.dumps(payload), encoding="utf-8")
    (tmp_path / "index.json").write_text(json.dumps(index), encoding="utf-8")
    os.environ["GARH_SUBMISSION_TEMPLATE_DIR"] = str(tmp_path)
    submission.reset_cache()
    try:
        return load_templates()
    finally:
        os.environ.pop("GARH_SUBMISSION_TEMPLATE_DIR", None)
        submission.reset_cache()


# ===========================================================================
# The shipped templates
# ===========================================================================
def test_every_shipped_template_loads() -> None:
    templates = load_templates()
    assert set(templates) == {"bbmp", "bda", "ncr", "ghmc"}


def test_every_shipped_template_is_marked_seed_and_unreviewed() -> None:
    """Nobody gets to quietly promote one of these to fact.

    Not one of them has been checked against an authority's published checklist. The
    status travels outward on every readiness result so a screen cannot render a green
    tick without also rendering what the tick is worth.
    """
    for template in load_templates().values():
        assert template.confidence == "seed", template.authority
        assert template.review == "unreviewed", template.authority
        assert template.verify, "%s must say what a reviewer has to confirm" % template.authority
        assert template.citation, template.authority


def test_bengaluru_has_two_authorities_and_they_are_different_sets() -> None:
    """The reason a template is keyed on the authority and not on the city pack.

    BBMP and BDA both sanction under the ``blr`` rule pack. A UI that assumed one
    template per city would silently take whichever came first, and half of Bengaluru
    would be working to the wrong checklist.
    """
    blr = templates_for_city_pack("blr")
    assert {t.authority for t in blr} == {"bbmp", "bda"}
    fields = {t.authority: set(t.required_field_keys()) for t in blr}
    assert fields["bbmp"] != fields["bda"], "two authorities that want the same thing"


def test_every_template_points_at_a_rule_pack_that_exists() -> None:
    with open(os.path.join(_ROOT, "rulepacks", "index.json"), encoding="utf-8") as handle:
        packs = {p["pack"] for p in json.load(handle)["packs"]}
    for template in load_templates().values():
        assert template.city_pack in packs, template.authority


def test_template_sheet_order_matches_the_pipelines_own_order() -> None:
    """Names a limit rather than faking a feature.

    ``canonical_sheet_kinds`` re-sorts every requested set into ``DB_KIND_ORDER``, so a
    template that wanted its sheets in a different order could not get them today. No
    shipped template does. If one ever does, this fails and says so — which is the
    honest outcome, because the alternative is a template silently ignored.
    """
    for template in load_templates().values():
        order = template.sheet_order()
        positions = [DB_KIND_ORDER.index(kind) for kind in order]
        assert positions == sorted(positions), (
            "%s wants its sheets in an order the pipeline cannot produce; "
            "canonical_sheet_kinds would re-sort them" % template.authority
        )


def test_template_for_names_what_exists_when_asked_for_something_that_does_not() -> None:
    with pytest.raises(KeyError) as excinfo:
        template_for("mcgm")
    assert "bbmp" in str(excinfo.value)


# ===========================================================================
# The load gates — each one broken on purpose
# ===========================================================================
def test_a_sheet_kind_outside_the_vocabulary_refuses_to_load(tmp_path) -> None:
    """The inert-requirement gate.

    A template asking for a "structural-calculations" sheet would load happily, look
    entirely correct, and check green forever, because nothing would ever be missing.
    """
    with pytest.raises(SubmissionTemplateError, match="does not draw"):
        _write_template(tmp_path, sheets=[{"kind": "structural-calculations", "required": True}])


def test_negative_control_the_same_template_with_a_real_kind_loads(tmp_path) -> None:
    """Prove the gate above discriminates rather than rejecting everything."""
    assert _write_template(tmp_path, sheets=[{"kind": "floor", "required": True}])


def test_a_working_drawing_cannot_be_demanded_at_submission(tmp_path) -> None:
    """A setting-out plan is a GFC deliverable. An authority does not ask for one, and a
    template that did would put a working drawing into a municipal set."""
    assert "setting-out" not in SUBMISSION_DB_KINDS
    with pytest.raises(SubmissionTemplateError, match="does not draw"):
        _write_template(tmp_path, sheets=[{"kind": "setting-out", "required": True}])


def test_a_rule_pack_that_does_not_exist_refuses_to_load(tmp_path) -> None:
    """Otherwise the template is unofferable: no project would ever match it."""
    with pytest.raises(SubmissionTemplateError, match="not in rulepacks/index.json"):
        _write_template(tmp_path, cityPack="mumbai")


def test_a_statutory_label_the_title_block_would_truncate_refuses_to_load(tmp_path) -> None:
    """The silent-truncation gate.

    ``ARCHITECT REG. NO.`` is 18 characters; the block prints 16 and appends an
    ellipsis. An abbreviated statutory label on a sanction drawing is a defect nobody
    sees until it is on a counter, so it fails where the template is authored.
    """
    long_label = "X" * (LABEL_MAX_CHARS + 1)
    with pytest.raises(SubmissionTemplateError, match="silently truncate"):
        _write_template(
            tmp_path, statutoryFields=[{"key": "k", "label": long_label, "required": True}]
        )


def test_negative_control_a_label_exactly_at_the_limit_loads(tmp_path) -> None:
    """Prove the length gate is a boundary and not a blanket refusal."""
    label = "X" * LABEL_MAX_CHARS
    assert _write_template(
        tmp_path, statutoryFields=[{"key": "k", "label": label, "required": True}]
    )


def test_no_shipped_label_is_truncated_on_paper() -> None:
    """The gate is only worth having if the shipped templates actually satisfy it."""
    for template in load_templates().values():
        for statutory in template.statutory_fields:
            assert len(statutory.label) <= LABEL_MAX_CHARS, (template.authority, statutory.label)


def test_a_template_that_disagrees_with_the_manifest_refuses_to_load(tmp_path) -> None:
    """The two files are edited separately; a disagreement means one describes a
    template nobody serves."""
    payload = {
        "authority": "elsewhere",
        "cityPack": "blr",
        "title": "t",
        "citation": "c",
        "paper": "A2",
        "scaleDenominator": 100,
        "sheets": [{"kind": "floor"}],
    }
    (tmp_path / "test.json").write_text(json.dumps(payload), encoding="utf-8")
    (tmp_path / "index.json").write_text(
        json.dumps({"templates": [{"authority": "test", "file": "test.json"}]}), encoding="utf-8"
    )
    os.environ["GARH_SUBMISSION_TEMPLATE_DIR"] = str(tmp_path)
    submission.reset_cache()
    try:
        with pytest.raises(SubmissionTemplateError, match="but the file says"):
            load_templates()
    finally:
        os.environ.pop("GARH_SUBMISSION_TEMPLATE_DIR", None)
        submission.reset_cache()


def test_a_template_with_no_sheets_refuses_to_load(tmp_path) -> None:
    with pytest.raises(SubmissionTemplateError, match="no sheets"):
        _write_template(tmp_path, sheets=[])


# ===========================================================================
# Readiness
# ===========================================================================
def test_a_complete_set_is_ready() -> None:
    template = template_for("bbmp")
    readiness = check_submission(
        template,
        kinds=template.required_kinds(),
        title_block_fields=BBMP_FIELDS,
        paper=template.paper,
    )
    assert readiness.ready, readiness.shortfalls
    assert readiness.satisfied == readiness.total


def test_ready_never_means_approved() -> None:
    """The status rides along with the tick, always.

    A product selling citable compliance must not let a screen show a green check for a
    checklist nobody has reviewed.
    """
    template = template_for("bbmp")
    readiness = check_submission(
        template, kinds=template.required_kinds(), title_block_fields=BBMP_FIELDS
    )
    assert readiness.ready
    assert readiness.confidence == "seed"
    assert readiness.review == "unreviewed"
    assert readiness.verify


def test_a_missing_sheet_is_named_not_counted() -> None:
    template = template_for("bbmp")
    without_section = [k for k in template.required_kinds() if k != "section"]
    readiness = check_submission(template, kinds=without_section, title_block_fields=BBMP_FIELDS)
    assert not readiness.ready
    assert [s.what for s in readiness.shortfalls] == ["section"]
    assert "section" in readiness.shortfalls[0].detail


def test_a_missing_statutory_field_blocks_the_set() -> None:
    """The whole point of D-4: a compliant design with no khata number comes back."""
    template = template_for("bbmp")
    fields = dict(BBMP_FIELDS)
    del fields["khataNumber"]
    readiness = check_submission(
        template, kinds=template.required_kinds(), title_block_fields=fields
    )
    assert not readiness.ready
    assert [s.what for s in readiness.shortfalls] == ["khataNumber"]
    assert "KHATA NO." in readiness.shortfalls[0].detail


def test_a_blank_statutory_field_counts_as_missing() -> None:
    """A whitespace value is not a khata number."""
    template = template_for("bbmp")
    readiness = check_submission(
        template,
        kinds=template.required_kinds(),
        title_block_fields={**BBMP_FIELDS, "khataNumber": "   "},
    )
    assert not readiness.ready
    assert [s.what for s in readiness.shortfalls] == ["khataNumber"]


def test_an_optional_sheet_is_an_advisory_not_a_shortfall() -> None:
    """A checklist that cries wolf over a door schedule is a checklist nobody reads."""
    template = template_for("bbmp")
    readiness = check_submission(
        template, kinds=template.required_kinds(), title_block_fields=BBMP_FIELDS
    )
    assert "schedule" not in template.required_kinds()
    assert readiness.ready
    assert any("schedule" in note for note in readiness.advisories)


def test_the_wrong_paper_is_a_shortfall_with_both_sizes_in_it() -> None:
    template = template_for("bbmp")
    readiness = check_submission(
        template, kinds=template.required_kinds(), title_block_fields=BBMP_FIELDS, paper="A4"
    )
    assert not readiness.ready
    detail = readiness.shortfalls[0].detail
    assert "A2" in detail and "A4" in detail


def test_negative_control_readiness_is_measured_against_the_set_not_the_template() -> None:
    """The shape of a test that cannot fail is one that checks a template against
    itself. An empty set must come back with everything outstanding."""
    template = template_for("bbmp")
    empty = check_submission(template, kinds=(), title_block_fields={})
    assert not empty.ready
    assert empty.satisfied == 0
    assert len(empty.shortfalls) == len(template.required_kinds()) + len(
        template.required_field_keys()
    )


def test_each_authority_asks_for_something_the_others_do_not() -> None:
    """If every template demanded the same fields, D-4 would be one template with four
    names and the readiness check would be measuring nothing local."""
    by_authority = {t.authority: set(t.required_field_keys()) for t in load_templates().values()}
    for authority, keys in by_authority.items():
        others: set[str] = set()
        for other, other_keys in by_authority.items():
            if other != authority:
                others |= other_keys
        assert keys - others, "%s asks for nothing the others do not" % authority


# ===========================================================================
# The statutory boxes must reach paper
# ===========================================================================
def _sheet_texts(authority: str | None, fields: dict[str, str]) -> set[str]:
    """Build a real set through the real pipeline and read the text off sheet one."""
    doc = make_two_room_plan_with_openings()
    bundle = SheetBundle(
        document=doc.to_json(),
        kinds=("floor",),
        title_block_fields={"firmName": "Studio", **fields},
        **({"authority": authority} if authority else {}),
    )
    result = build_sheets(bundle)
    assert result.sheets, result.skipped
    drawing = result.sheets[0].drawing
    # Everything a reader sees: the drawing itself AND the frame around it. The title
    # block lives on the sheet's frame rather than in the primitive groups, and reading
    # only the groups is how this test first passed while asserting nothing.
    texts = {p.text for group in drawing.groups for p in group.primitives if isinstance(p, Text)}
    texts |= {p.text for p in frame_primitives(drawing.sheet.frame) if isinstance(p, Text)}
    return texts


def test_the_statutory_boxes_are_actually_PRINTED_on_the_sheet() -> None:
    """The gate that stops the field check from guarding nothing.

    ``check_submission`` reads the title-block payload. If the frame does not print
    those fields, a set can be reported ready while the drawing carries no khata number
    at all — a green check over a missing box, which is the exact shape of bug class 1
    in ``CLAUDE.md``.
    """
    texts = _sheet_texts("bbmp", BBMP_FIELDS)
    for label, value in statutory_pairs(template_for("bbmp"), BBMP_FIELDS):
        assert label in texts, "label %r never reached paper" % label
        assert value in texts, "value %r never reached paper" % value


def test_negative_control_a_set_with_no_authority_prints_no_statutory_boxes() -> None:
    """Prove the test above discriminates: without an authority those labels are absent,
    so their presence is caused by the template and not by the frame drawing them
    unconditionally."""
    texts = _sheet_texts(None, BBMP_FIELDS)
    assert "KHATA NO." not in texts
    assert "WARD NO." not in texts


def test_an_unfilled_statutory_box_still_prints_its_label() -> None:
    """A box an architect can see is empty gets filled. A box that vanishes when
    unfilled gets noticed at the counter instead."""
    texts = _sheet_texts("bbmp", {"khataNumber": "A-1234/56"})
    assert "KHATA NO." in texts
    assert "WARD NO." in texts, "an empty statutory box must keep its label"


def test_an_unknown_authority_does_not_kill_the_set() -> None:
    """A project row naming a template this build does not ship must cost the architect
    some boxes, never their whole set."""
    texts = _sheet_texts("mcgm", BBMP_FIELDS)
    assert texts, "the sheet still drew"
    assert "KHATA NO." not in texts
