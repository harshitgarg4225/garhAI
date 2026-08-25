"""§5.3 wall synthesis — golden micro-cases + properties, all pure integers.

Everything here runs on a bare Python 3.9 with no third-party packages: the
point of keeping stage B ortools-free is that THIS file can prove it on the
build machine. Golden values were hand-derived from the documented coordinate
conventions in ``walls.py`` (115 = 57+58 internal split, external outer face on
the footprint boundary, centreline 115 inward, mitred corners) and then pinned.

Runnable by pytest in CI and by ``python3 services/solver/tests/test_walls.py``
where pytest is absent (same convention as test_pipeline.py).
"""

from __future__ import annotations

import sys
import types
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]
for _path in (str(_REPO_ROOT), str(_REPO_ROOT / "apps" / "api")):
    if _path not in sys.path:
        sys.path.insert(0, _path)


def _install_worker_dep_stubs() -> None:
    """Import-time stand-ins for structlog/pydantic where they are absent.

    ``services.common`` (pulled in by ``services.solver.types``) imports
    structlog and pydantic at module scope; the build machine has neither
    installed (DECISIONS.md toolchain-gap row). The stubs cover exactly the
    surface exercised on the import path, and a REAL package always wins — a
    stub is only installed when the import fails.
    """
    try:
        import structlog  # noqa: F401
    except ImportError:
        stub = types.ModuleType("structlog")

        class _Logger:
            def _noop(self, *args, **kwargs):
                return None

            info = warning = debug = error = exception = critical = _noop

            def bind(self, **kwargs):
                return self

        stub.get_logger = lambda *a, **k: _Logger()
        contextvars_mod = types.ModuleType("structlog.contextvars")
        contextvars_mod.bind_contextvars = lambda **k: None
        contextvars_mod.clear_contextvars = lambda: None
        stub.contextvars = contextvars_mod
        sys.modules["structlog"] = stub
        sys.modules["structlog.contextvars"] = contextvars_mod
    try:
        import pydantic  # noqa: F401
    except ImportError:
        pyd = types.ModuleType("pydantic")
        pyd.Field = lambda default=None, **kw: default

        def _field_validator(*args, **kwargs):
            def deco(fn):
                return fn

            return deco

        pyd.field_validator = _field_validator
        sys.modules["pydantic"] = pyd
        pyds = types.ModuleType("pydantic_settings")

        class _BaseSettings:
            def __init__(self, **kwargs):
                for key, value in kwargs.items():
                    setattr(self, key, value)

        pyds.BaseSettings = _BaseSettings
        pyds.SettingsConfigDict = dict
        sys.modules["pydantic_settings"] = pyds


_install_worker_dep_stubs()

import random  # noqa: E402

from services.solver.types import RoomPlacement  # noqa: E402
from services.solver.walls import (  # noqa: E402
    INSET_EXTERNAL,
    INSET_INTERNAL_HIGH,
    INSET_INTERNAL_LOW,
    CellLayout,
    WallSynthesisError,
    build_wall_network,
    clear_polygon,
    snap_mm,
)

M = 115  # the brick module


def _p(key: str, room_type: str, x: int, y: int, w: int, d: int, storey: int = 0, room_id=None):
    return RoomPlacement(key, room_type, storey, x, y, w, d, room_id=room_id)


def _span(wall):
    if wall.axis == "v":
        lo, hi = wall.a[1], wall.b[1]
    else:
        lo, hi = wall.a[0], wall.b[0]
    return min(lo, hi), max(lo, hi)


def _walls_overlap(w1, w2) -> bool:
    """Collinear overlap of non-zero length — the WALL_DUPLICATE invariant."""
    if w1.axis != w2.axis:
        return False
    line1 = w1.a[0] if w1.axis == "v" else w1.a[1]
    line2 = w2.a[0] if w2.axis == "v" else w2.a[1]
    if line1 != line2:
        return False
    a1, b1 = _span(w1)
    a2, b2 = _span(w2)
    return min(b1, b2) - max(a1, a2) > 0


# ---------------------------------------------------------------------------
# snapping
# ---------------------------------------------------------------------------


def test_snap_mm_rounds_half_away_on_the_anchored_grid() -> None:
    assert snap_mm(0) == 0
    assert snap_mm(57) == 0  # 57/115 < .5
    assert snap_mm(58) == 115  # 57.5 rounds away from zero → up
    assert snap_mm(115) == 115
    assert snap_mm(-58) == -115
    # The grid is anchored at origin, not at absolute zero.
    assert snap_mm(1057, origin=1000) == 1000
    assert snap_mm(1058, origin=1000) == 1115


def test_snap_mm_rejects_nonpositive_module() -> None:
    try:
        snap_mm(100, module_mm=0)
    except ValueError:
        return
    raise AssertionError("module_mm=0 must raise")


# ---------------------------------------------------------------------------
# the 2-room golden micro-case (byte-exact wall set)
# ---------------------------------------------------------------------------

TWO_ROOMS = (
    _p("living", "living", 0, 0, 3450, 3450),
    _p("kitchen", "kitchen", 3450, 0, 3450, 3450),
)


def test_two_room_golden_wall_set() -> None:
    layout = CellLayout.from_placements(TWO_ROOMS)
    net = build_wall_network(layout)
    assert net.outline == ((0, 0), (6900, 0), (6900, 3450), (0, 3450))
    got = [
        (w.axis, w.kind, w.a, w.b, w.thickness_mm, w.line_mm) for w in net.walls
    ]
    assert got == [
        ("h", "external", (115, 115), (6785, 115), 230, 0),
        ("v", "external", (6785, 115), (6785, 3335), 230, 6900),
        ("h", "external", (6785, 3335), (115, 3335), 230, 3450),
        ("v", "external", (115, 3335), (115, 115), 230, 0),
        ("v", "internal", (3450, 115), (3450, 3335), 115, 3450),
    ]


def test_two_rooms_share_exactly_one_wall() -> None:
    net = build_wall_network(CellLayout.from_placements(TWO_ROOMS))
    internal = [w for w in net.walls if w.kind == "internal"]
    assert len(internal) == 1
    assert internal[0].thickness_mm == 115
    assert [
        (s.low_room, s.high_room, s.wall_index, s.lo, s.hi) for s in net.adjacencies
    ] == [("living", "kitchen", 4, 115, 3335)]


def test_two_room_external_spans_with_compass() -> None:
    net = build_wall_network(CellLayout.from_placements(TWO_ROOMS))
    got = {(s.room_key, s.outward, s.lo, s.hi) for s in net.external_spans}
    assert got == {
        ("living", "S", 0, 3450),
        ("kitchen", "S", 3450, 6900),
        ("kitchen", "E", 0, 3450),
        ("living", "N", 0, 3450),
        ("kitchen", "N", 3450, 6900),
        ("living", "W", 0, 3450),
    }


def test_two_room_clear_polygons_exact() -> None:
    layout = CellLayout.from_placements(TWO_ROOMS)
    net = build_wall_network(layout)
    living = clear_polygon(layout, net, "living")
    kitchen = clear_polygon(layout, net, "kitchen")
    # living: external W (230 in), internal E (LOW side → 57 in), external N/S.
    assert set(living) == {(230, 230), (3393, 230), (3393, 3220), (230, 3220)}
    # kitchen: internal W (HIGH side → 58 in), external E.
    assert set(kitchen) == {(3508, 230), (6670, 230), (6670, 3220), (3508, 3220)}


def test_dimension_chains_sum_exactly() -> None:
    """§7's non-negotiable: clear spans + wall bands == overall extent, no drift."""
    layout = CellLayout.from_placements(TWO_ROOMS)
    net = build_wall_network(layout)
    living = clear_polygon(layout, net, "living")
    kitchen = clear_polygon(layout, net, "kitchen")
    lx = sorted({p[0] for p in living})
    kx = sorted({p[0] for p in kitchen})
    # ext 230 | living clear | int 115 | kitchen clear | ext 230 == 6900
    assert 230 + (lx[1] - lx[0]) + 115 + (kx[1] - kx[0]) + 230 == 6900
    ly = sorted({p[1] for p in living})
    assert 230 + (ly[1] - ly[0]) + 230 == 3450
    # And the asymmetric 115 split is exactly 57 + 58 (module docstring contract).
    assert INSET_INTERNAL_LOW + INSET_INTERNAL_HIGH == 115
    assert INSET_EXTERNAL == 230


def test_input_order_never_leaks_into_output() -> None:
    net_a = build_wall_network(CellLayout.from_placements(TWO_ROOMS))
    net_b = build_wall_network(CellLayout.from_placements(tuple(reversed(TWO_ROOMS))))
    assert net_a.walls == net_b.walls
    assert net_a.adjacencies == net_b.adjacencies
    assert net_a.external_spans == net_b.external_spans


# ---------------------------------------------------------------------------
# L-shaped footprint (mitred reflex corner, trims at the ring)
# ---------------------------------------------------------------------------

L_ROOMS = (
    _p("a", "living", 0, 0, 4600, 3450),
    _p("b", "kitchen", 4600, 0, 4600, 3450),
    _p("c", "bedroom", 0, 3450, 4600, 3450),
)


def test_l_shape_golden() -> None:
    layout = CellLayout.from_placements(L_ROOMS)
    net = build_wall_network(layout)
    assert net.outline == (
        (0, 0), (9200, 0), (9200, 3450), (4600, 3450), (4600, 6900), (0, 6900)
    )
    got = [(w.axis, w.kind, w.a, w.b, w.thickness_mm) for w in net.walls]
    assert got == [
        ("h", "external", (115, 115), (9085, 115), 230),
        ("v", "external", (9085, 115), (9085, 3335), 230),
        ("h", "external", (9085, 3335), (4485, 3335), 230),
        ("v", "external", (4485, 3335), (4485, 6785), 230),
        ("h", "external", (4485, 6785), (115, 6785), 230),
        ("v", "external", (115, 6785), (115, 115), 230),
        ("h", "internal", (115, 3450), (4485, 3450), 115),
        ("v", "internal", (4600, 115), (4600, 3335), 115),
    ]
    assert [
        (s.low_room, s.high_room, s.lo, s.hi) for s in net.adjacencies
    ] == [("a", "c", 115, 4485), ("a", "b", 115, 3335)]


def test_no_two_walls_overlap_on_the_l_shape() -> None:
    net = build_wall_network(CellLayout.from_placements(L_ROOMS))
    walls = net.walls
    for i in range(len(walls)):
        for j in range(i + 1, len(walls)):
            assert not _walls_overlap(walls[i], walls[j]), (i, j)


# ---------------------------------------------------------------------------
# typed failures — §15 honest discard reasons
# ---------------------------------------------------------------------------


def _expect_code(code: str, fn) -> None:
    try:
        fn()
    except WallSynthesisError as exc:
        assert exc.code == code, "expected %s, got %s" % (code, exc.code)
        return
    raise AssertionError("expected WallSynthesisError %s" % code)


def test_overlapping_rooms_are_a_typed_error() -> None:
    _expect_code(
        "ROOM_OVERLAP",
        lambda: build_wall_network(
            CellLayout.from_placements(
                (_p("a", "living", 0, 0, 2300, 2300), _p("b", "bath", 1150, 0, 2300, 2300))
            )
        ),
    )


def test_disjoint_rooms_are_a_typed_error() -> None:
    _expect_code(
        "FOOTPRINT_SPLIT",
        lambda: build_wall_network(
            CellLayout.from_placements(
                (_p("a", "living", 0, 0, 2300, 2300), _p("b", "bath", 4600, 4600, 2300, 2300))
            )
        ),
    )


def test_point_touching_rooms_are_a_typed_error() -> None:
    _expect_code(
        "FOOTPRINT_PINCH",
        lambda: build_wall_network(
            CellLayout.from_placements(
                (_p("a", "living", 0, 0, 2300, 2300), _p("b", "bath", 2300, 2300, 2300, 2300))
            )
        ),
    )


def test_locked_room_off_module_refuses_to_move() -> None:
    locked = _p("a", "living", 100, 0, 2300, 2300, room_id="room_00000000000000000000000000")
    try:
        CellLayout.from_placements((locked,))
    except WallSynthesisError as exc:
        assert exc.code == "LOCKED_ROOM_MOVED"
        return
    raise AssertionError("expected LOCKED_ROOM_MOVED")


def test_degenerate_room_is_a_typed_error() -> None:
    try:
        CellLayout.from_placements((_p("a", "living", 0, 0, 50, 2300),))
    except WallSynthesisError as exc:
        assert exc.code == "DEGENERATE_ROOM"
        return
    raise AssertionError("expected DEGENERATE_ROOM")


def test_unlocked_placements_snap_to_the_module() -> None:
    layout = CellLayout.from_placements((_p("a", "living", 3, 112, 2302, 2295),))
    room = layout.room("a")
    assert (room.x1, room.y1, room.x2, room.y2) == (0, 115, 2300, 2415)


# ---------------------------------------------------------------------------
# property test: random guillotine tilings (seeded — deterministic in CI)
# ---------------------------------------------------------------------------


def _guillotine(rng: random.Random, x1: int, y1: int, x2: int, y2: int, depth: int):
    w, d = x2 - x1, y2 - y1
    min_side = 8 * M  # 920mm — no degenerate strips
    if depth == 0 or (w < 2 * min_side and d < 2 * min_side):
        return [(x1, y1, x2, y2)]
    if w >= d:
        cut = x1 + M * rng.randint(min_side // M, (w - min_side) // M)
        return _guillotine(rng, x1, y1, cut, y2, depth - 1) + _guillotine(
            rng, cut, y1, x2, y2, depth - 1
        )
    cut = y1 + M * rng.randint(min_side // M, (d - min_side) // M)
    return _guillotine(rng, x1, y1, x2, cut, depth - 1) + _guillotine(
        rng, x1, cut, x2, y2, depth - 1
    )


def test_random_tilings_always_build_clean_networks() -> None:
    for seed in range(12):
        rng = random.Random(seed)
        rects = _guillotine(rng, 0, 0, 9200, 6900, depth=3)
        placements = tuple(
            _p("r%02d" % i, "unassigned", x1, y1, x2 - x1, y2 - y1)
            for i, (x1, y1, x2, y2) in enumerate(rects)
        )
        layout = CellLayout.from_placements(placements)
        net = build_wall_network(layout)
        # tiles a rectangle → 4-vertex outline covering everything
        assert net.outline == ((0, 0), (9200, 0), (9200, 6900), (0, 6900)), seed
        # every wall on the 115 grid, non-zero, at least one module long
        for wall in net.walls:
            for coord in (*wall.a, *wall.b):
                assert coord % M == 0, (seed, wall)
            assert wall.length_mm >= M, (seed, wall)
        # no two walls collinear-overlap (the WALL_DUPLICATE invariant)
        for i in range(len(net.walls)):
            for j in range(i + 1, len(net.walls)):
                assert not _walls_overlap(net.walls[i], net.walls[j]), (seed, i, j)
        # dedupe: at most one wall per (axis, line) among internals
        internal_lines = [(w.axis, w.line_mm, _span(w)) for w in net.walls if w.kind == "internal"]
        for i in range(len(internal_lines)):
            for j in range(i + 1, len(internal_lines)):
                a, b = internal_lines[i], internal_lines[j]
                if a[0] == b[0] and a[1] == b[1]:
                    assert min(a[2][1], b[2][1]) <= max(a[2][0], b[2][0]), (seed, a, b)
        # every room yields a positive clear polygon and order never leaks
        for room in layout.rooms:
            poly = clear_polygon(layout, net, room.key)
            assert len(poly) >= 4, (seed, room.key)
        shuffled = list(placements)
        rng.shuffle(shuffled)
        assert build_wall_network(CellLayout.from_placements(tuple(shuffled))).walls == net.walls


# ---------------------------------------------------------------------------
# bare-python runner (pytest is not installed on the build machine)
# ---------------------------------------------------------------------------

if __name__ == "__main__":  # pragma: no cover
    import traceback

    failures = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print("PASS %s" % name)
            except Exception:  # noqa: BLE001
                failures += 1
                print("FAIL %s" % name)
                traceback.print_exc()
    sys.exit(1 if failures else 0)
