"""D-6: the area statement in the form a municipal office expects.

The numbers were already right — ``test_schedules.py`` proves that, rule row by rule row,
against an independent ``garh_rules.evaluate`` run. This file is about the other half of
the problem, the one that gets a set rejected before anyone reads a figure: **the form**.

What it proves:

* **The proforma's shape.** Five columns, serial numbers, sections, sub-serials that a
  query sheet can cite, and PERMISSIBLE ahead of PROPOSED — the order every Indian
  sanction proforma is read in, and the one the sheet used to have backwards.
* **Every item §7 asks for is on it**: plot area, coverage achieved and permissible, FAR
  achieved and permissible, built-up per floor and total, every setback provided and
  required, parking, height, floors. Asserted against the engine's own row keys, so a
  future engine row that this form has no section for shows up as a warning rather than
  disappearing.
* **One source, still.** Every printed figure is compared against an independent
  ``garh_rules.evaluate`` of the same context — and then the statement is *doctored* and
  the form re-rendered, which is the test that fails the moment anyone recomputes a FAR
  inside the drawings package.
* **The setback verdict is not a second opinion.** ``OK`` / ``SHORT BY n mm`` is a
  comparison of two engine integers; it is pinned here against
  ``garh_rules.areas.SetbackRow.status`` so it cannot drift into a rule of its own.
* **It reaches the paper.** The A-06 sheet's rendered SVG carries the proforma headers and
  the doctored figure, not just the row objects.

Two negative controls, both of which must go red when the thing they guard is broken:

``test_negative_control_a_doctored_statement_changes_the_form``
    proves the "one source" test is not passing on a coincidence.
``test_negative_control_a_statement_without_ratios_warns_instead_of_computing``
    strips the engine's ratio properties. A form that quietly divided the areas itself
    would print a FAR anyway and this would never fire.

Runnable two ways::

    pytest -q services/drawings/tests/test_area_statement.py
    python3 services/drawings/tests/test_area_statement.py
"""

from __future__ import annotations

import json
import sys
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

from garh_rules import evaluate  # noqa: E402
from garh_rules.formatting import format_ratio  # noqa: E402

from services.drawings.layers import LAYER_NAMES  # noqa: E402
from services.drawings.render.primitives import Text  # noqa: E402
from services.drawings.render.svg import render_sheet_svg  # noqa: E402
from services.drawings.render.tables import (  # noqa: E402
    area_statement_height_mm,
    area_statement_table,
    format_area_dual,
)
from services.drawings.schedules import build_area_statement_sheet  # noqa: E402
from services.drawings.schedules.municipal import (  # noqa: E402
    CERTIFICATION_NOTES,
    MISSING,
    MUNICIPAL_COLUMNS,
    municipal_form,
)
from services.drawings.sheets import default_frame  # noqa: E402

RULEPACK_ROOT = str(_REPO_ROOT / "rulepacks")
RULES_FIXTURE = _REPO_ROOT / "fixtures" / "rules" / "blr" / "blr.far.road.9-18m.pass.json"


def rules_context() -> dict[str, Any]:
    """The committed BLR fixture's evaluation context — a real plot where FAR, coverage,
    setback, floors, height and parking rules all fire."""
    with open(RULES_FIXTURE, encoding="utf-8") as handle:
        return json.load(handle)["context"]


def sheet(context: Any = None) -> Any:
    return build_area_statement_sheet(
        context if context is not None else rules_context(), rulepack_root=RULEPACK_ROOT
    )


def form(context: Any = None) -> Any:
    return sheet(context).municipal_form()


def _row(rows: Any, needle: str, *, band: bool = False) -> Any:
    """The one figure row (or section band) whose description contains ``needle``.

    Bands are skipped by default: "FLOOR AREA RATIO (FAR)" is both a section heading and a
    figure row, and a test that matched the heading would compare a blank cell and pass.
    """
    matches = [
        row for row in rows if needle.lower() in row.description.lower() and row.band == band
    ]
    if len(matches) != 1:
        raise AssertionError(
            "expected exactly one %s row matching %r, got %d, in:\n%s"
            % (
                "band" if band else "figure",
                needle,
                len(matches),
                "\n".join("%-6s %s" % (r.number, r.description) for r in rows),
            )
        )
    return matches[0]


# ---------------------------------------------------------------------------
# the form
# ---------------------------------------------------------------------------
def test_the_columns_are_the_proforma_order_permissible_before_proposed() -> None:
    """The single most consequential difference from the generic table this replaces.

    Every sanction proforma reads "rule, then what you did". Printing the proposal first
    is the kind of wrongness a scrutiny clerk bounces without comment.
    """
    headers = [header for _key, header, _align in MUNICIPAL_COLUMNS]
    assert headers == [
        "SL. NO.",
        "DESCRIPTION",
        "PERMISSIBLE / REQUIRED",
        "PROPOSED / PROVIDED",
        "REMARKS",
    ]
    assert headers.index("PERMISSIBLE / REQUIRED") < headers.index("PROPOSED / PROVIDED")
    # …and the cells are in the same order as the headers.
    row = form().rows[0]
    assert row.cells() == (
        row.number,
        row.description,
        row.limit,
        row.value,
        row.remarks,
    )


def test_every_item_the_spec_names_is_on_the_statement() -> None:
    """§7's list, item by item: plot area, per-storey built-up, total, FAR, coverage,
    setbacks — plus the height, floors and parking a sanction proforma also carries."""
    rows = form().rows
    descriptions = " | ".join(row.description.lower() for row in rows)
    for needle in (
        "plot area",
        "ground coverage",
        "floor area ratio",
        "built-up",
        "total built-up",
        "setback",
        "height of building",
        "number of floors",
        "car parking",
    ):
        assert needle in descriptions, needle

    # one built-up line per storey the engine reported, plus the total
    statement = sheet().statement
    per_storey = [r for r in rows if r.number.startswith("4.")]
    assert len(per_storey) == len(statement.per_storey) + 1

    # one setback line per plot edge the engine reported
    setbacks = [r for r in rows if "setback" in r.description.lower() and not r.band]
    assert len(setbacks) == len(statement.setbacks)
    assert setbacks, "the fixture must exercise setbacks for this test to mean anything"


def test_the_serials_are_a_citable_numbering() -> None:
    """ "Clarify item 6.2" has to resolve to exactly one row."""
    rows = form().rows
    numbers = [row.number for row in rows]
    assert len(numbers) == len(set(numbers)), numbers

    sections = [n for n in numbers if "." not in n]
    assert sections == [str(i) for i in range(1, len(sections) + 1)], sections

    for number in numbers:
        if "." not in number:
            continue
        parent, child = number.split(".")
        assert parent in sections, number
        assert child.isdigit() and int(child) >= 1, number

    # sub-serials run 1..n inside their section, in printed order
    for section in sections:
        children = [int(n.split(".")[1]) for n in numbers if n.startswith(section + ".")]
        assert children == list(range(1, len(children) + 1)), (section, children)


def test_section_bands_carry_no_figures_and_are_emphasised() -> None:
    bands = [row for row in form().rows if row.band]
    assert bands, "a proforma without sections is the flat list this replaces"
    for band in bands:
        assert band.limit == "" and band.value == ""
        assert "." not in band.number
    built = form()
    assert {index for index, r in enumerate(built.rows) if r.band} <= set(built.emphasis_indices())


def test_the_certification_and_seed_confidence_notes_are_printed() -> None:
    """Golden rule 4: assumptions and citations visible. The packs are graded ``seed``
    and the sheet has to say so — printing a permissible figure as settled law is the
    liability this whole product is careful about."""
    notes = form().notes()
    for note in CERTIFICATION_NOTES:
        assert note in notes
    joined = " ".join(notes).lower()
    assert "confidence grade" in joined
    assert "does not recompute" in joined
    assert "signature" in joined


# ---------------------------------------------------------------------------
# one source
# ---------------------------------------------------------------------------
def test_every_printed_figure_is_the_rules_engines_own_number() -> None:
    """The form is built one way; the engine is run again, independently; the printed
    cells are matched against the rule rows that produced them."""
    context = rules_context()
    built = sheet(context)
    rows = built.municipal_form().rows
    report = evaluate(context, root=RULEPACK_ROOT)
    results = {r.check_type: r for r in report.results if r.applicable}

    coverage = results["coverage_max"]
    coverage_row = _row(rows, "covered area on ground floor")
    assert coverage_row.value == format_area_dual(coverage.actual)
    assert coverage_row.limit == format_area_dual(coverage.limit)

    far = results["far_max"]
    far_area = _row(rows, "FAR-countable built-up area")
    assert far_area.value == format_area_dual(far.actual)
    assert far_area.limit == format_area_dual(far.limit)

    far_ratio = _row(rows, "Floor area ratio")
    assert far_ratio.value == format_ratio(built.far_achieved)
    assert far_ratio.limit == format_ratio(built.far_allowed)

    height = results["height_max"]
    assert _row(rows, "height of building").value == "%d mm" % height.actual
    assert _row(rows, "height of building").limit == "%d mm" % height.limit

    floors = results["floors_max"]
    assert _row(rows, "number of floors").value == "%d" % floors.actual
    assert _row(rows, "number of floors").limit == "%d" % floors.limit

    parking = results["parking_min"]
    assert _row(rows, "car parking").value == "%d" % parking.actual
    assert _row(rows, "car parking").limit == "%d" % parking.limit

    for line in built.setbacks:
        printed = _row(rows, "%s setback" % line.role.replace("-", " "))
        assert printed.value == "%d mm" % line.provided_mm
        assert printed.limit == ("%d mm" % line.required_mm if line.required_mm else MISSING)
        for rule_id in line.rule_ids:
            assert rule_id in printed.remarks


def test_setback_verdicts_agree_with_the_rules_engine() -> None:
    """``OK`` / ``SHORT BY n`` is a comparison of two engine integers, never a rule of
    its own. If it ever became one, it would disagree with the engine's status here."""
    context = json.loads(json.dumps(rules_context()))
    context["plot"]["edges"][0]["setbackProvidedMm"] = 1200  # front setback well short
    built = sheet(context)
    rows = built.municipal_form().rows
    checked = 0
    for line in built.statement.setbacks:
        printed = _row(rows, "%s setback" % line.role.replace("-", " "))
        if line.status == "short":
            assert "SHORT BY %d mm" % line.shortfall_mm in printed.remarks
            assert printed.emphasis, "a shortfall must be the row a reviewer's eye lands on"
        elif line.required_mm is None:
            assert "not regulated" in printed.remarks
        else:
            assert printed.remarks.startswith("OK")
        checked += 1
    assert checked, "no setbacks in the fixture — this test would be vacuous"
    assert any(line.status == "short" for line in built.statement.setbacks)


def test_negative_control_a_doctored_statement_changes_the_form() -> None:
    """The one-source test above must be capable of failing.

    Doctor the engine's own object and the doctored number has to reach the paper
    verbatim. A form that recomputed the FAR allowance from the model would ignore this
    and print the honest number — and the test above would pass while the sheet lied.
    """
    context = rules_context()
    honest = sheet(context)
    doctored_mm2 = honest.statement.far_allowed_mm2 + 12_345_678
    doctored = build_area_statement_sheet(
        context,
        statement=replace(honest.statement, far_allowed_mm2=doctored_mm2),
        rulepack_root=RULEPACK_ROOT,
    )

    honest_cell = _row(honest.municipal_form().rows, "FAR-countable built-up area").limit
    doctored_cell = _row(doctored.municipal_form().rows, "FAR-countable built-up area").limit
    assert doctored_cell != honest_cell, "the doctored number never reached the form"
    assert doctored_cell == format_area_dual(doctored_mm2)

    # …and the doctored FAR *ratio* follows it, from the engine's own property.
    assert doctored.far_allowed == Fraction(doctored_mm2, honest.plot_area_mm2)
    ratio_cell = _row(doctored.municipal_form().rows, "Floor area ratio").limit
    assert ratio_cell == format_ratio(doctored.far_allowed)


class _RatiolessStatement:
    """The engine's rows with its ratio properties removed — see the test below."""

    def __init__(self, statement: Any) -> None:
        self._statement = statement
        self.warnings = tuple(statement.warnings)

    def rows(self) -> Any:
        return self._statement.rows()


def test_negative_control_a_statement_without_ratios_warns_instead_of_computing() -> None:
    """Strip the engine's FAR/coverage ratios and the form must go quiet, not clever.

    This is the gate on "no second source". A form that divided the countable area by the
    plot area itself would print a perfectly plausible FAR here and nothing would ever
    fire — which is exactly how this repository once shipped 83 inert rules behind a green
    report.
    """
    stripped = municipal_form(_RatiolessStatement(sheet().statement))
    figures = [row.description.lower() for row in stripped.rows if not row.band]
    # The FAR *section* stays — its countable-area row is a plain engine figure. What
    # must be gone is the derived ratio line, in both sections.
    assert "floor area ratio (far)" not in figures
    assert "ground coverage (%)" not in figures
    joined = " ".join(stripped.warnings)
    assert "does not carry FAR ratios" in joined
    assert "does not carry coverage ratios" in joined
    # The area rows are still there — only the derived ratio is withheld.
    assert _row(stripped.rows, "FAR-countable built-up area").value


def test_a_row_the_form_has_no_section_for_is_reported_not_dropped() -> None:
    """The engine will grow rows. A statement figure that silently vanished off a
    municipal sheet is the failure this warning exists to prevent."""

    class _ExtraRow:
        key = "solar.water_heater"
        label = "Solar water heater"
        value = 1
        unit = "count"
        allowed = 1
        kind = "requirement"
        note = None
        rule_ids = ("blr.solar.mandatory",)

    statement = sheet().statement

    class _Grown:
        warnings = ()
        far_achieved = statement.far_achieved
        far_allowed = statement.far_allowed
        coverage_achieved = statement.coverage_achieved
        coverage_allowed = statement.coverage_allowed

        def rows(self) -> Any:
            return (*statement.rows(), _ExtraRow())

    grown = municipal_form(_Grown())
    assert any("solar.water_heater" in warning for warning in grown.warnings)
    assert any("no section for" in warning for warning in grown.warnings)


# ---------------------------------------------------------------------------
# the sheet
# ---------------------------------------------------------------------------
def test_the_table_fits_the_default_a2_frame() -> None:
    frame = default_frame()
    table = sheet().municipal_table()
    assert table.fits_within(frame.drawable_width_mm(), frame.drawable_height_mm()), (
        table.width_mm(),
        table.height_mm(),
    )


def test_the_sheet_primitives_use_only_the_nine_layers_and_integer_paper_mm() -> None:
    primitives = area_statement_table(sheet().statement)
    assert primitives
    for primitive in primitives:
        assert primitive.layer in LAYER_NAMES, primitive
        for value in getattr(primitive, "__dict__", {}).values():
            assert not isinstance(value, float), primitive


def test_the_stated_height_covers_every_primitive() -> None:
    """``area_statement_height_mm`` is what the revision register is placed below. If it
    under-reports, the register overprints the notes — the class of overlap §16's
    collision assertion exists to catch."""
    statement = sheet().statement
    origin = (25, 25)
    primitives = area_statement_table(statement, origin_mm=origin)
    bottom = max(y for p in primitives for _x, y in p.points())
    stated = area_statement_height_mm(statement)
    assert bottom <= origin[1] + stated, (bottom, origin[1] + stated)
    # …and is not wildly generous either: within one row height of the real extent.
    assert origin[1] + stated - bottom <= 7


def test_the_proforma_reaches_the_rendered_a06_sheet() -> None:
    """Not the row objects — the SVG bytes the exporter writes."""
    from services.drawings.render.reference_sheets import area_statement_sheet
    from services.drawings.sheets import TitleBlock

    context = rules_context()
    honest = sheet(context)
    doctored_mm2 = honest.statement.far_allowed_mm2 + 12_345_678
    statement = replace(honest.statement, far_allowed_mm2=doctored_mm2)
    drawing = area_statement_sheet(
        None, statement, number="A-06", title_block=TitleBlock(project_name="Proforma test")
    )
    texts = {p.text for group in drawing.groups for p in group.primitives if isinstance(p, Text)}
    assert "SL. NO." in texts
    assert "PERMISSIBLE / REQUIRED" in texts
    assert "PROPOSED / PROVIDED" in texts
    assert format_area_dual(doctored_mm2) in texts

    svg = render_sheet_svg(drawing)
    assert "SL. NO." in svg
    assert format_area_dual(doctored_mm2) in svg
    assert "SIGNATURE" in svg


def test_the_workers_transport_statement_renders_the_same_form() -> None:
    """The worker never sees a ``garh_rules`` object — it decodes JSON into
    ``TransportStatement``. Both must lay out identically, or the sheet a user downloads
    differs from the sheet the tests draw."""
    from services.drawings.pipeline import TransportStatement

    statement = sheet().statement
    transported = TransportStatement.from_json(statement.to_json())
    direct = municipal_form(statement)
    codec = municipal_form(transported)
    assert [row.cells() for row in codec.rows] == [row.cells() for row in direct.rows]
    assert codec.notes() == direct.notes()


def test_carpet_area_is_its_own_section_and_labelled_non_regulatory() -> None:
    """Carpet is the one number on the statement no pack bands anything on. Mixing it into
    the built-up figures a reviewer checks against FAR would invite exactly the wrong
    comparison."""
    built = sheet()
    rows = built.municipal_form().rows
    band = _row(rows, "carpet area", band=True)
    assert "not a regulatory figure" in band.description.lower()
    carpet_rows = [r for r in rows if r.number.startswith(band.number + ".")]
    assert carpet_rows
    known = [line for line in built.storeys if line.carpet_area_mm2 is not None]
    assert len(carpet_rows) >= len(known)

    # …and with no carpet lines the section is absent entirely, not empty.
    without = municipal_form(built.statement)
    assert all("carpet" not in row.description.lower() for row in without.rows)


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
