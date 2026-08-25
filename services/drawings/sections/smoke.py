"""Choose and project the §7 section on a real G+1 house, and print it. **Executable.**

    python "services/drawings/sections/smoke.py"

Run it as a **file**, not with ``-m`` — same reason as
``services/drawings/elevations/smoke.py``: the stand-ins for the absent worker
dependencies have to be installed before the first ``services.drawings`` import.

What it prints, and why each part is worth printing:

* the **scoring table** — every candidate cut, so the choice is visible arithmetic rather
  than an assertion. §7 says the line is auto-chosen; this is the "why";
* the **levels found** and the **storey-height chain**, with Σ segments next to the overall
  so the §7 step 5 invariant is readable, not just tested;
* the **notes**, which is where the section says what the model cannot describe — the
  return flight of a dogleg, the assumed terrace slab, the derived mumty.

Exit 0 = every check held.
"""

from __future__ import annotations

import os
import sys
from typing import List, Tuple

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.abspath(os.path.join(_HERE, "..", "..", ".."))
_APPS_API = os.path.join(_REPO_ROOT, "apps", "api")

PASS = "  ok  "
FAIL = "  FAIL"

_failures: List[str] = []


def bootstrap() -> Tuple[str, ...]:
    for path in (_REPO_ROOT, _APPS_API):
        if path not in sys.path:
            sys.path.insert(0, path)
    from services.dev_stubs import install_worker_dep_stubs

    return install_worker_dep_stubs()


def check(label: str, condition: bool, detail: str = "") -> None:
    print("%s  %s%s" % (PASS if condition else FAIL, label, (" — " + detail) if detail else ""))
    if not condition:
        _failures.append(label)


def main() -> int:
    stubbed = bootstrap()
    print("Garh AI — §7 section smoke")
    print("stubbed dependencies: %s" % (", ".join(stubbed) or "none (real packages present)"))

    from services.drawings.dimensions import assert_chains_sum, find_label_collisions
    from services.drawings.elevations.demo_house import demo_house, demo_material_names
    from services.drawings.elevations.smoke import report
    from services.drawings.projection.primitives import (
        Line,
        Text,
        by_kind,
        find_unsafe_text,
        primitives_digest,
        validate_primitives,
    )
    from services.drawings.sections.choose import choose_section_line
    from services.drawings.sections.project import (
        FOUNDATION_DEPTH_BELOW_PLINTH_MM,
        FOUNDATION_LABEL,
        SectionOptions,
        build_section,
    )
    from services.drawings.elevations.vertical import K_FOUNDATION_LABEL, K_FOUNDATION_LINE

    house = demo_house()
    choice = choose_section_line(house)
    print("\nsection-line scoring (%d candidates)" % len(choice.candidates))
    print("   axis    at        score  reasons")
    seen = set()
    for candidate in choice.candidates:
        key = (candidate.line.axis, candidate.line.position_mm)
        if key in seen:
            continue
        seen.add(key)
        print(
            "   %-4s  %7d  %7d  %s"
            % (
                candidate.line.axis,
                candidate.line.position_mm,
                candidate.score,
                "; ".join("%s %+d" % (reason, points) for reason, points in candidate.breakdown),
            )
        )
    assert choice.best is not None
    print(
        "\nchosen: %s at %d, viewed from the %s (looking %s) — through stair %s"
        % (
            choice.best.line.axis,
            choice.best.line.position_mm,
            choice.best.line.view_direction,
            choice.best.line.looking,
            choice.best.stair_id,
        )
    )

    result = build_section(house, options=SectionOptions(material_names=demo_material_names()))
    drawing = result.drawing
    report(drawing)
    print("  cut line   : %s" % (result.line.to_json() if result.line else None))
    print(
        "  stairs cut : %s"
        % ", ".join(
            "%s (%s, %d/%d risers drawn)"
            % (g.stair_id, g.kind, g.drawn_risers, g.risers_count)
            for g in result.stairs
        )
    )

    print("\nchecks")
    validate_primitives(drawing.primitives)
    assert_chains_sum(drawing.chains)
    check("a cut line was chosen", result.line is not None)
    check("cut runs along the stair flight", bool(choice.best.along_flight))
    check("cut passes through the flight itself", bool(choice.best.through_flight))
    check(
        "cut reaches a wet area (§7's 'if possible')",
        bool(choice.best.wet_room_ids),
        "%d wet room(s)" % len(choice.best.wet_room_ids),
    )
    stair_ids = {g.stair_id for g in result.stairs}
    check(
        "the chosen line intersects the stair it was scored against",
        choice.best.stair_id in stair_ids,
        ", ".join(sorted(stair_ids)),
    )
    check(
        "storey-height chain sums exactly",
        all(c.is_consistent() for c in drawing.chains),
        "Σ %d == %d" % (drawing.chains[0].sum_of_segments(), drawing.chains[0].overall_mm),
    )
    lines = [p for p in by_kind(drawing.primitives, K_FOUNDATION_LINE) if isinstance(p, Line)]
    labels = [p for p in by_kind(drawing.primitives, K_FOUNDATION_LABEL) if isinstance(p, Text)]
    expected_z = drawing.levels.plinth_mm - FOUNDATION_DEPTH_BELOW_PLINTH_MM
    check("one indicative foundation line", len(lines) == 1)
    check("foundation line is dashed", bool(lines) and lines[0].dashed)
    check(
        "foundation line is 900mm below plinth",
        bool(lines) and lines[0].a[1] == expected_z and lines[0].b[1] == expected_z,
        "z = %d" % (lines[0].a[1] if lines else 0),
    )
    check(
        "foundation label is exactly the §7 text",
        bool(labels) and labels[0].text == FOUNDATION_LABEL,
        labels[0].text if labels else "(missing)",
    )
    check("mumty shown", bool(by_kind(drawing.primitives, "mumty")))
    check("no overlapping labels", not find_label_collisions(drawing.label_boxes()))
    check("no markup in any text (§13)", not find_unsafe_text(drawing.primitives))
    again = build_section(house, options=SectionOptions(material_names=demo_material_names()))
    check(
        "deterministic: same model, same bytes",
        primitives_digest(drawing.primitives) == primitives_digest(again.drawing.primitives),
        primitives_digest(drawing.primitives)[:16],
    )

    print("\n%d check(s) failed" % len(_failures) if _failures else "\nall checks passed")
    return 1 if _failures else 0


if __name__ == "__main__":
    sys.exit(main())
