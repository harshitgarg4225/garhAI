"""Project all four elevations of a real G+1 house and print what came out. **Executable.**

    python "services/drawings/elevations/smoke.py"

Run it as a **file**, not with ``-m``. As a file, Python executes it without first
importing ``services.drawings``, whose ``__init__`` pulls in the DXF helper and therefore
``services.common`` → ``structlog``; those are real worker dependencies and are absent on a
bare machine (see the toolchain-gap row in ``DECISIONS.md``), so :func:`bootstrap` installs
the established stand-ins *before* the first repo import. In Docker and CI, where the
dependencies are real, the stubs are inert and ``-m`` works too. Same shape as
``services/drawings/projection/smoke.py``.

This exists because the elevation projector is pure integer arithmetic over the model and
needs no dependency to be *exercised* — the ezdxf boundary is one module away. Printing the
levels it found and the primitives it emitted is the difference between "the code is
written" and "the code runs".

Exit 0 = every check held. The checks are the invariants §7 and §16 actually name: chains
sum exactly, no two labels overlap, every primitive is valid and on one of the nine layers,
far-face openings are absent, and two runs of the same model produce the same bytes.
"""

from __future__ import annotations

import os
import sys
from typing import Any, Dict, List, Tuple

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.abspath(os.path.join(_HERE, "..", "..", ".."))
_APPS_API = os.path.join(_REPO_ROOT, "apps", "api")

PASS = "  ok  "
FAIL = "  FAIL"

_failures: List[str] = []


def bootstrap() -> Tuple[str, ...]:
    """Put the repo on the path and stub the absent worker deps. Returns what was faked."""
    for path in (_REPO_ROOT, _APPS_API):
        if path not in sys.path:
            sys.path.insert(0, path)
    from services.dev_stubs import install_worker_dep_stubs

    return install_worker_dep_stubs()


def check(label: str, condition: bool, detail: str = "") -> None:
    print("%s  %s%s" % (PASS if condition else FAIL, label, (" — " + detail) if detail else ""))
    if not condition:
        _failures.append(label)


def report(drawing: Any) -> None:
    """Print one drawing's counts, levels, chain and notes."""
    from services.drawings.dimensions import find_label_collisions

    extent = drawing.extent_mm()
    print("\n%s   (%s, 1:%d)" % (drawing.name, drawing.kind, drawing.scale_denominator))
    print("  primitives : %d" % len(drawing.primitives))
    print("  by layer   : %s" % _fmt(drawing.by_layer()))
    print("  by kind    : %s" % _fmt(drawing.by_kind()))
    print(
        "  extent     : u %d..%d mm, z %d..%d mm"
        % (extent[0], extent[2], extent[1], extent[3])
        if extent
        else "  extent     : (empty)"
    )
    print("  levels     :")
    for marker in drawing.level_markers:
        print("      %7d  %s" % (marker.level_mm, " / ".join(marker.labels)))
    for chain in drawing.chains:
        print(
            "  chain %s: %s = %d  (Σ segments %d)"
            % (
                chain.id,
                " + ".join(str(s.length_mm) for s in chain.segments),
                chain.overall_mm,
                chain.sum_of_segments(),
            )
        )
    print("  labels     : %d, collisions %d" % (
        len(drawing.label_boxes()),
        len(find_label_collisions(drawing.label_boxes())),
    ))
    for note in drawing.notes:
        print("  note       : %s" % note)


def _fmt(counts: Dict[str, int]) -> str:
    return ", ".join("%s=%d" % (key, value) for key, value in counts.items()) or "(none)"


def main() -> int:
    stubbed = bootstrap()
    print("Garh AI — §7 elevations smoke")
    print("stubbed dependencies: %s" % (", ".join(stubbed) or "none (real packages present)"))

    from services.drawings.dimensions import assert_chains_sum, find_label_collisions
    from services.drawings.elevations.demo_house import (
        DEMO_IDS,
        demo_house,
        demo_material_names,
    )
    from services.drawings.elevations.project import ElevationOptions, build_all_elevations
    from services.drawings.projection.primitives import (
        by_owner,
        find_unsafe_text,
        primitives_digest,
        validate_primitives,
    )

    house = demo_house()
    print(
        "\ndemo house: %d storeys, %d walls, %d openings, %d stairs, %d balconies, "
        "%d facade components"
        % (
            len(house.storeys),
            len(house.walls),
            len(house.openings),
            len(house.stairs),
            len(house.balconies),
            len(house.facade.components),
        )
    )

    options = ElevationOptions(material_names=demo_material_names())
    elevations = build_all_elevations(house, options)
    for drawing in elevations.values():
        report(drawing)

    print("\nchecks")
    check("four elevations projected", len(elevations) == 4, ", ".join(elevations))
    for direction, drawing in elevations.items():
        validate_primitives(drawing.primitives)
        assert_chains_sum(drawing.chains)
        check(
            "%s: primitives emitted" % direction,
            len(drawing.primitives) > 20,
            "%d" % len(drawing.primitives),
        )
        check(
            "%s: height chain sums exactly" % direction,
            all(c.is_consistent() for c in drawing.chains),
        )
        check(
            "%s: no overlapping labels" % direction,
            not find_label_collisions(drawing.label_boxes()),
        )
        check("%s: no markup in any text (§13)" % direction, not find_unsafe_text(drawing.primitives))

    # Level markers must be the model's own numbers.
    levels = house.levels
    ffls = list(levels.ffl_per_storey_mm)
    expected = {0, levels.plinth_mm}
    for ffl in ffls:
        expected.update({ffl, ffl + levels.sill_default_mm, ffl + levels.lintel_default_mm})
    terrace = ffls[-1] + house.storeys[-1].height_mm
    expected.update({terrace, terrace + levels.parapet_mm})
    found = {m.level_mm for m in elevations["N"].level_markers}
    check(
        "level markers are exactly the model's levels",
        found == expected,
        "found %s" % sorted(found),
    )

    # The hidden-line rule, checked on a real pair: the north window is on the north
    # elevation and absent from the south one, and vice versa for the entrance door.
    north_window = DEMO_IDS["gf_win_n"]
    south_door = DEMO_IDS["gf_door"]
    check(
        "north window drawn on the north elevation",
        bool(by_owner(elevations["N"].primitives, north_window)),
    )
    check(
        "north window absent from the south elevation (far face)",
        not by_owner(elevations["S"].primitives, north_window),
    )
    check(
        "entrance door drawn on the south elevation",
        bool(by_owner(elevations["S"].primitives, south_door)),
    )
    check(
        "entrance door absent from the north elevation (far face)",
        not by_owner(elevations["N"].primitives, south_door),
    )

    again = build_all_elevations(house, options)
    check(
        "deterministic: same model, same bytes",
        all(
            primitives_digest(elevations[d].primitives) == primitives_digest(again[d].primitives)
            for d in elevations
        ),
        primitives_digest(elevations["N"].primitives)[:16],
    )

    print("\n%d check(s) failed" % len(_failures) if _failures else "\nall checks passed")
    return 1 if _failures else 0


if __name__ == "__main__":
    sys.exit(main())
