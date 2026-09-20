"""An accepted deviation is printed on the sheet that goes over the counter.

``AreaStatement.overridden_rule_ids`` has carried the architect's accepted rules since
Phase 2, and its own docstring said "the annexure marks each such rule as overridden so
a reviewer at the counter sees the acknowledgement, not a silent pass." The A-06 sheet
did not print them.

What that meant on paper: a submission set could show a FAR or a setback that FAILS —
in the architect's own numbers, correctly measured — with nothing anywhere on the
drawing saying the deviation was a decision taken deliberately and recorded with a
reason. To the person reading it across a counter, that is not a deviation, it is a
mistake. The product sells citable compliance; this is the citation.

Three cases, and the middle one is the control: a statement with no overrides must not
grow the line, or the sheet would cry deviation on every clean set and nobody would
read the footnotes again.
"""

from __future__ import annotations

from typing import Any

from services.drawings.schedules.area_statement import AreaStatementSheet

RULE = "blr.setback.front.road.9-18m"  # a rule the committed fixture actually fires
OTHER = "blr.far.road.9-18m"


class _Statement:
    """The smallest thing that quacks like a `garh_rules.AreaStatement` here.

    The sheet is asserted against the real statement all over
    `test_area_statement.py`; what this file is about is one field reaching one
    footnote, so the rest of the numbers are not the subject and a stub keeps the
    failure message about the thing that broke.
    """

    def __init__(self, overridden: tuple[str, ...]) -> None:
        self.overridden_rule_ids = overridden

    def to_json(self) -> dict[str, Any]:
        return {"overriddenRuleIds": list(self.overridden_rule_ids)}


def _sheet(overridden: tuple[str, ...] = (), **kwargs: Any) -> AreaStatementSheet:
    return AreaStatementSheet(
        statement=_Statement(overridden),
        storeys=(),
        setbacks=(),
        **kwargs,
    )


def test_the_accepted_rules_are_named_on_the_sheet() -> None:
    notes = _sheet((RULE, OTHER)).footnotes()
    assert notes, "the sheet prints no footnotes at all"
    first = notes[0]
    assert "ACCEPTED DEVIATIONS" in first, (
        "the acknowledgement is not the first footnote. A reviewer scanning the sheet "
        "must meet it before the unit conventions: %s" % (notes,)
    )
    assert RULE in first and OTHER in first, first
    # It must say the rules are NOT met. "Overridden" on its own reads, to someone
    # who does not know this product, like the rule did not apply.
    assert "not" in first.lower() and "met" in first.lower(), first


def test_CONTROL_a_clean_set_says_nothing_about_deviations() -> None:
    notes = _sheet(()).footnotes()
    assert not any("ACCEPTED DEVIATIONS" in note for note in notes), (
        "a set with no accepted deviations is announcing deviations, which trains "
        "every reader to skip the footnotes: %s" % (notes,)
    )
    # ...and the sheet still prints the notes it always did.
    assert any("Areas: m2" in note for note in notes), notes


def test_a_statement_from_before_the_field_existed_still_renders() -> None:
    """A frozen report predating `overridden_rule_ids` must not crash the sheet.

    Reports are stored, and one written months ago is re-rendered when an architect
    reprints an old version. An AttributeError here would be a 500 on a drawing set.
    """

    class _Old:
        pass

    sheet = AreaStatementSheet(statement=_Old(), storeys=(), setbacks=())
    assert sheet.overridden_rule_ids() == ()
    assert sheet.footnotes()


def test_the_json_carries_them_too_on_a_REAL_statement() -> None:
    """The printed sheet is not the only consumer; the JSON is the machine-readable one.

    Built from the committed BLR fixture rather than the stub above, because
    ``to_json`` walks the whole statement: a stub would only prove the stub, and this
    is the one assertion where the rest of the object has to be real.
    """
    import json
    import os

    from services.drawings.schedules import build_area_statement_sheet

    repo = os.path.dirname(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    )
    fixture = os.path.join(repo, "fixtures", "rules", "blr", "blr.far.road.9-18m.pass.json")
    with open(fixture, encoding="utf-8") as handle:
        context = json.load(handle)["context"]

    clean = build_area_statement_sheet(
        context, rulepack_root=os.path.join(repo, "rulepacks")
    ).to_json()
    assert clean["overriddenRuleIds"] == [], (
        "a fixture with no accepted deviations reports some: %s" % clean["overriddenRuleIds"]
    )

    overridden = json.loads(json.dumps(context))
    overridden["profile"].setdefault("overrides", {})[RULE] = {
        "reason": "Corner plot; BBMP approved the deviation.",
        "at": "2026-09-20T00:00:00Z",
    }
    marked = build_area_statement_sheet(
        overridden, rulepack_root=os.path.join(repo, "rulepacks")
    ).to_json()
    assert marked["overriddenRuleIds"] == [RULE], (
        "an override written through the profile — the shape the route stores — never "
        "reached the sheet: %s" % marked["overriddenRuleIds"]
    )
