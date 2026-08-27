"""The two registered ``custom`` functions. The enum is closed on purpose.

``check.fn`` is an enum in the schema, not a free string, so a pack can never
name code that does not exist — registering a third function means bumping
``schemaVersion``. Both are declared ``project`` scope in
``x-garh-check-meta.customFns`` and the loader enforces that.

``rwh_required`` is a **declaration** check, not a geometric one. The MVP model
has no rainwater-harvesting element, so the honest thing is to check that the
architect ticked the box, say so in the rule text, and keep the severity at
``warn``. Pretending to verify sump volume from a model that contains no sump
would be the worst kind of green tick.

``brahmasthan_open`` is the one check that needs real geometry: the overlap
between each enclosed room and the centre cell of the 3x3 grid. It is computed
with exact rational arithmetic (:func:`garh_rules.geometry.clip_area_against_rect`)
because the verdict is ``floor(10000 * overlap / cellArea)`` compared against a
ten-thousandths limit — a float overlap could move that integer by one at exactly
the boundary the fixtures sit on.
"""

from __future__ import annotations

from collections.abc import Mapping
from fractions import Fraction
from typing import Any

from .errors import PackLoadError
from .geometry import clip_area_against_rect
from .packs import Check
from .ratio import Ratio
from .scope import CheckEnv, Instance, Outcome

__all__ = ["CUSTOM_FNS", "run_custom", "custom_result_unit"]

#: ``fn`` -> the unit of ``actual``/``limit`` in the result row.
_UNITS: Mapping[str, str] = {"rwh_required": "boolean", "brahmasthan_open": "bp10000"}


def custom_result_unit(fn: str) -> str:
    unit = _UNITS.get(fn)
    if unit is None:  # pragma: no cover - rejected at pack load
        raise PackLoadError("no result unit registered for custom fn %r" % fn)
    return unit


def rwh_required(check: Check, instance: Instance, env: CheckEnv) -> Outcome:
    """``actual = profile[args.flag]``, ``limit = True``.

    A missing flag would mean "we could not tell", which is not a pass — the
    loader already restricts ``args.flag`` to a boolean field the profile actually
    exposes, so reaching here with ``None`` is impossible by construction.
    """
    flag_name = str(check.mapping_param("args").get("flag"))
    declared = env.context.profile.flag(flag_name)
    satisfied = declared is True
    return Outcome(
        satisfied=satisfied,
        actual=bool(declared),
        limit=True,
        order_key=Fraction(0) if satisfied else Fraction(-1),
        satisfaction=Fraction(1) if satisfied else Fraction(0),
        note=(
            None
            if satisfied
            else "Declaration check only: the model carries no rainwater-harvesting element, so "
            "this confirms the declaration, not the structure."
        ),
    )


def brahmasthan_open(check: Check, instance: Instance, env: CheckEnv) -> Outcome:
    """How much of the centre cell the most-enclosing room covers, in 1/10000ths.

    ``actual = max`` over non-open rooms of
    ``floor(10000 * area(room ∩ centreCell) / area(centreCell))``;
    ``limit = floor(10000 * maxEnclosedRatio)``. Fails when ``actual > limit``.

    "Non-open" comes from the pack: ``args.openRoomTypes`` if given, else the
    merged ``vocabulary.openRoomTypes``. A courtyard, a living room or a corridor
    across the centre is what the rule *wants*; a bedroom is what it objects to.

    All storeys count. A first-floor bedroom sitting over the centre encloses the
    brahmasthan just as a ground-floor one does, and the context gives no reason
    to privilege the ground floor.
    """
    args = check.mapping_param("args")
    ratio = Ratio.from_json(args["maxEnclosedRatio"], "args.maxEnclosedRatio")
    limit_bp = ratio.floor_of(10_000)

    declared_open = args.get("openRoomTypes")
    open_types = (
        frozenset(str(t) for t in declared_open)
        if declared_open is not None
        else env.vocabulary.open_room_types
    )

    grid = env.grid
    x0, y0, x1, y1 = grid.centre_cell_rect()
    cell_area = (x1 - x0) * (y1 - y0)
    if cell_area <= 0:
        # A degenerate plot cannot be scored on its centre. Reported as satisfied
        # with a note rather than silently dividing by zero.
        return Outcome(
            satisfied=True,
            actual=0,
            limit=limit_bp,
            order_key=Fraction(limit_bp),
            satisfaction=Fraction(1),
            note="The plot has no measurable centre cell, so the brahmasthan was not assessed.",
        )

    worst = 0
    offenders: list[str] = []
    for room in env.context.model.rooms:
        if room.type in open_types:
            continue
        overlap = clip_area_against_rect(grid.rotate_ring(room.polygon_mm), x0, y0, x1, y1)
        if overlap <= 0:
            continue
        # floor(10000 * overlap / cell_area) on an exact rational
        covered_bp = int(Fraction(10_000) * overlap / cell_area)
        if covered_bp > limit_bp:
            offenders.append(room.id)
        worst = max(worst, covered_bp)

    satisfied = worst <= limit_bp
    return Outcome(
        satisfied=satisfied,
        actual=worst,
        limit=limit_bp,
        order_key=Fraction(limit_bp - worst),
        satisfaction=Fraction(1) if satisfied else Fraction(0),
        elements=tuple(offenders),
    )


#: The closed registry. ``checks.py`` dispatches through this and nothing else.
CUSTOM_FNS: Mapping[str, Any] = {
    "rwh_required": rwh_required,
    "brahmasthan_open": brahmasthan_open,
}


def run_custom(check: Check, instance: Instance, env: CheckEnv) -> Outcome:
    fn_name = check.str_param("fn")
    fn = CUSTOM_FNS.get(fn_name)
    if fn is None:  # pragma: no cover - rejected at pack load
        raise PackLoadError("custom fn %r has no implementation" % fn_name)
    return fn(check, instance, env)  # type: ignore[no-any-return]


def registered_fns() -> tuple[str, ...]:
    return tuple(sorted(CUSTOM_FNS))
