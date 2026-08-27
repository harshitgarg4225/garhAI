"""Run the plan projector on a demo house and print what came out. **Executable.**

    python "services/drawings/projection/smoke.py"

WHY A SCRIPT-SHAPED MODULE
--------------------------
Run it as a **file**, not with ``-m``. As a file, Python executes it without importing
``services.drawings``, whose ``__init__`` eagerly imports the DXF helper and therefore
``services.common`` → ``structlog``. Those are real dependencies of the worker and they
are absent on a bare developer machine (see the toolchain-gap row in ``DECISIONS.md``),
so the bootstrap below installs the established stand-ins *before* the first repo import.
In Docker and in CI, where the dependencies are real, ``-m`` works too and the stubs are
inert.

This exists because the projection engine is pure integer arithmetic over the model and
therefore needs no dependency at all to be *exercised* — the ezdxf boundary is one module
away in ``services/drawings/dxf.py``. Being able to project a real house and count the
result is the difference between "the code is written" and "the code runs".

The demo house is built by folding real ops through ``garh_model``, not by hand-writing a
document: if an op payload or the fold's derived rooms change, this smoke breaks, which is
the point.
"""

from __future__ import annotations

import os
import sys
from typing import Any

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.abspath(os.path.join(_HERE, "..", "..", ".."))
_APPS_API = os.path.join(_REPO_ROOT, "apps", "api")

#: The demo storey height, and a stair that fits it: 18 × 167 = 3006mm, inside the
#: model's ±10mm rise tolerance.
STOREY_HEIGHT_MM = 3000
RISER_MM = 167
RISERS = 18
TREAD_MM = 275


def bootstrap() -> tuple[str, ...]:
    """Put the repo on the path and stub the absent worker deps. Returns what was faked."""
    for path in (_REPO_ROOT, _APPS_API):
        if path not in sys.path:
            sys.path.insert(0, path)
    from services.dev_stubs import install_worker_dep_stubs

    return install_worker_dep_stubs()


def demo_ops() -> list[Any]:
    """Ops for a G+0 demo: 8.0 × 5.4m envelope, spine wall, four openings, stair,
    six columns on a 3 × 2 grid, and a north balcony.

    Deliberately richer than ``garh_model.testing.make_two_room_plan_with_openings``
    (which the tests use as the shared 2-room fixture): this one has to reach every
    branch of the plan projector, including the ones the small fixture has no geometry
    for — treads, a ventilator, the column grid, a railing.
    """
    from garh_model.model import DEFAULTS
    from garh_model.ops import op
    from garh_model.testing import DEMO_PLOT_POLYGON, fixed_id

    storey = fixed_id("storey", "GF")

    def pt(x: int, y: int) -> dict[str, int]:
        return {"x": x, "y": y}

    walls = (
        ("WS", (0, 0), (8000, 0), 230, "external"),
        ("WE", (8000, 0), (8000, 5400), 230, "external"),
        ("WN", (8000, 5400), (0, 5400), 230, "external"),
        ("WW", (0, 5400), (0, 0), 230, "external"),
        ("WSP", (4000, 0), (4000, 5400), 115, "internal"),
    )
    ops: list[Any] = [
        op("plot.set_boundary", polygon=list(DEMO_PLOT_POLYGON), source="seed"),
        # 20° so the north dart is visibly rotated and nothing accidentally passes
        # because a sin/cos mix-up is invisible at 0°.
        op("plot.set_north", deg=20),
        op("plot.set_road", edgeIndex=0, widthMm=9000, name="9m Road"),
        op("storey.add", id=storey, index=0, name="Ground Floor", heightMm=STOREY_HEIGHT_MM),
    ]
    for tag, a, b, thickness, kind in walls:
        ops.append(
            op(
                "wall.add",
                id=fixed_id("wall", tag),
                storeyId=storey,
                a=pt(*a),
                b=pt(*b),
                thicknessMm=thickness,
                kind=kind,
            )
        )

    ops.extend(
        [
            op(
                "opening.add",
                id=fixed_id("opening", "D1"),
                wallId=fixed_id("wall", "WS"),
                kind="door",
                widthMm=DEFAULTS.door_width_mm,
                heightMm=DEFAULTS.door_height_mm,
                sillMm=0,
                offsetMm=2000,
                swing="in-left",
                tag="D1",
            ),
            op(
                "opening.add",
                id=fixed_id("opening", "D2"),
                wallId=fixed_id("wall", "WSP"),
                kind="door",
                widthMm=DEFAULTS.bath_door_width_mm,
                heightMm=DEFAULTS.door_height_mm,
                sillMm=0,
                offsetMm=1200,
                swing="out-right",
                tag="D2",
            ),
            op(
                "opening.add",
                id=fixed_id("opening", "W1"),
                wallId=fixed_id("wall", "WW"),
                kind="window",
                widthMm=DEFAULTS.window_width_mm,
                heightMm=DEFAULTS.window_height_mm,
                sillMm=DEFAULTS.sill_default_mm,
                offsetMm=2700,
                swing="in-left",
                tag="W1",
            ),
            op(
                "opening.add",
                id=fixed_id("opening", "V1"),
                wallId=fixed_id("wall", "WE"),
                kind="ventilator",
                widthMm=DEFAULTS.ventilator_width_mm,
                heightMm=DEFAULTS.ventilator_height_mm,
                sillMm=DEFAULTS.ventilator_sill_mm,
                offsetMm=4000,
                swing="in-left",
                tag="V1",
            ),
            op(
                "stair.add",
                id=fixed_id("stair", "ST1"),
                storeyId=storey,
                kind="straight",
                origin=pt(4400, 400),
                direction="N",
                riserMm=RISER_MM,
                treadMm=TREAD_MM,
                widthMm=DEFAULTS.stair_width_mm,
                risersCount=RISERS,
                landing=None,
            ),
            op(
                "balcony.set",
                action="add",
                id=fixed_id("balcony", "B1"),
                storeyId=storey,
                polygon=[pt(1000, 5400), pt(3000, 5400), pt(3000, 6300), pt(1000, 6300)],
                railingKind="ms",
                railingHeightMm=DEFAULTS.railing_height_mm,
                projectionMm=900,
                slabThicknessMm=125,
            ),
        ]
    )

    for index, (x, y) in enumerate(
        ((200, 200), (4000, 200), (7800, 200), (200, 5200), (4000, 5200), (7800, 5200))
    ):
        ops.append(
            op(
                "column.set",
                action="add",
                id=fixed_id("column", "C%d" % (index + 1)),
                storeyId=storey,
                pt=pt(x, y),
                sizeMm={"xMm": 230, "yMm": 230},
            )
        )
    return ops


def demo_doc() -> Any:
    """Fold the demo ops, then name the two detected rooms west-to-east."""
    from garh_model.fold import apply_group
    from garh_model.geometry import polygon_centroid
    from garh_model.ops import op
    from garh_model.testing import make_empty_doc

    doc = apply_group(make_empty_doc(), demo_ops()).model
    rooms = sorted(doc.house.rooms, key=lambda room: polygon_centroid(room.polygon).x)
    naming = [("living_dining", "Living / Dining"), ("bedroom_master", "Master Bedroom")]
    assigns = [
        op("room.assign", roomId=room.id, type=room_type, name=name)
        for room, (room_type, name) in zip(rooms, naming, strict=False)
    ]
    return apply_group(doc, assigns).model if assigns else doc


def main() -> int:
    stubbed = bootstrap()
    from services.drawings.projection import (
        PlanOptions,
        SectionMarker,
        clipped_gap_total,
        count_by_kind,
        count_by_layer,
        find_unsafe_text,
        opening_dim_stations,
        primitives_digest,
        project_plan_detail,
        split_span,
        validate_primitives,
    )

    print("=" * 78)
    print("§7 plan projection smoke — services/drawings/projection")
    print("stubbed worker deps: %s" % (", ".join(stubbed) or "none (real packages present)"))

    doc = demo_doc()
    house = doc.house
    storey = house.storeys[0]
    print(
        "model: %d walls, %d openings, %d rooms, %d stairs, %d columns, %d balconies "
        "on %r (FFL %+dmm)"
        % (
            len(house.walls),
            len(house.openings),
            len(house.rooms),
            len(house.stairs),
            len(house.columns),
            len(house.balconies),
            storey.name,
            storey.level.ffl_mm,
        )
    )

    for denominator in (100, 50):
        options = PlanOptions(
            north_deg=doc.plot.north_deg,
            section_markers=(SectionMarker(a=(4850, -1200), b=(4850, 6600), label="A"),),
        )
        projection = project_plan_detail(house, storey.id, denominator, options=options)
        validate_primitives(projection.primitives)
        unsafe = find_unsafe_text(projection.primitives)

        print("")
        print("-" * 78)
        print("scale 1:%d — %d primitives" % (denominator, len(projection.primitives)))
        print("extent (model mm): %s" % (projection.extent,))
        print(
            "text height: room name %dmm, label %dmm; hatch spacing %dmm"
            % (
                projection.style.room_name_height_mm,
                projection.style.label_height_mm,
                projection.style.hatch_spacing_mm,
            )
        )
        print("digest: %s" % primitives_digest(projection.primitives))
        print("unsafe text (§13): %s" % (unsafe if unsafe else "NONE"))
        print("")
        print("primitives by layer:")
        for layer, count in count_by_layer(projection.primitives).items():
            print("  %-12s %4d" % (layer, count))
        print("primitives by kind:")
        for kind, count in count_by_kind(projection.primitives).items():
            print("  %-20s %4d" % (kind or "(none)", count))

        if denominator == 100:
            print("")
            print("wall face split invariant (Σ runs + Σ gaps == face extent):")
            for band in projection.bands:
                for name, start, end in (
                    ("left ", band.extents.left_start_mm, band.extents.left_end_mm),
                    ("right", band.extents.right_start_mm, band.extents.right_end_mm),
                ):
                    runs = split_span(start, end, band.gaps)
                    total = sum(run.length_mm for run in runs)
                    gaps = clipped_gap_total(start, end, band.gaps)
                    ok = total + gaps == end - start
                    print(
                        "  %s %s  extent %5d..%-5d  runs %d (%5dmm) + gaps %4dmm = %5dmm  %s"
                        % (
                            band.wall.id.split("_")[-1][-4:],
                            name,
                            start,
                            end,
                            len(runs),
                            total,
                            gaps,
                            end - start,
                            "OK" if ok else "MISMATCH",
                        )
                    )
                    if not ok:
                        return 1
            print("")
            print("level-3 dim stations handed to the dimension engine:")
            for band in projection.bands:
                for mode in (False, True):
                    stations = opening_dim_stations(band, house.openings, dim_to_jamb=mode)
                    if stations:
                        print(
                            "  %s dimToJamb=%-5s %s"
                            % (
                                band.wall.id.split("_")[-1][-4:],
                                mode,
                                ", ".join(
                                    "%s@%d" % (oid.split("_")[-1][-2:], mm) for oid, mm in stations
                                ),
                            )
                        )
    print("")
    print("OK — projection ran, validated and hashed with no ezdxf and no I/O.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
