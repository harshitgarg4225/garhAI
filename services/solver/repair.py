"""§5.3 final step — run the model invariants, repair the trivial, discard the rest.

    "Run model invariants; auto-repair trivial violations (nudge by one module)
     else discard candidate" — engineering playbook §5.3.

WHAT COUNTS AS TRIVIAL (closed list — anything not on it is a discard):

* ``OPENING_OUT_OF_WALL`` where moving the opening's centre by **at most one
  115mm module** brings it inside the host wall's end margins. The nudge is the
  smallest move that satisfies the invariant, each opening is nudged at most
  once, and the nudged model is re-validated from scratch — a repair that
  merely relocates the problem still ends in a discard.
* **Sliver walls**: a wall shorter than one 115mm module hosting no openings is
  dropped in a pre-pass. Rooms in the §5.3 fragment carry their own persisted
  clear polygons, so removing a sliver cannot un-close a room; a sliver that
  *does* host an opening is not trivial (the opening cannot legally fit a
  <115mm wall) and the candidate is discarded instead.

Everything else — stair rise mismatches, duplicate walls, sill/height overruns,
rooms that fail to close — means stage A/B produced geometry that cannot be
saved by a one-module shuffle, and §5.3 says the candidate dies. The death is
**typed** (:class:`DiscardReason` with a stable ``code``), because §15's honest
generation theater shows real discard reasons, and the worker logs them.

The invariants themselves are ``garh_model.validate.validate_model`` — the SAME
function ``fold()`` runs on every user edit. The solver does not get a private,
softer notion of validity: if this module passes a model, ``solver.apply_option``
will fold it.

ortools-free by design; ``garh_model`` is pure stdlib and imported lazily so this
module imports cleanly even where ``apps/api`` is not on ``sys.path`` yet.
"""

from __future__ import annotations

import math
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from services.solver.geometry import round_half_away
from services.solver.types import FINE_MODULE_MM, SolveParams

#: One repair step is exactly one brick module — §5.3's "nudging one 115mm module".
NUDGE_MM = FINE_MODULE_MM

#: Mirror of ``garh_model.validate.WALL_END_MARGIN_MM`` (asserted equal in tests).
WALL_END_MARGIN_MM = 115

#: The one validation code a nudge may fix. A closed allowlist, not a heuristic:
#: growing it is a reviewed decision, never a side effect.
TRIVIALLY_REPAIRABLE_CODES: Tuple[str, ...] = ("OPENING_OUT_OF_WALL",)


@dataclass(frozen=True)
class DiscardReason:
    """Why a candidate died. ``code`` is stable and machine-readable."""

    code: str
    message: str
    detail: Optional[str] = None

    def to_json(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {"code": self.code, "message": self.message}
        if self.detail is not None:
            out["detail"] = self.detail
        return out


@dataclass(frozen=True)
class RepairAction:
    """One trivial repair that was actually applied."""

    kind: str  # 'opening-nudge' | 'sliver-wall-dropped'
    element_id: str
    delta_mm: int
    description: str

    def to_json(self) -> Dict[str, Any]:
        return {
            "kind": self.kind,
            "elementId": self.element_id,
            "deltaMm": self.delta_mm,
            "description": self.description,
        }


@dataclass(frozen=True)
class RepairOutcome:
    """Either a valid (possibly repaired) house fragment, or a typed discard.

    Exactly one of ``house`` / ``discard`` is set. ``actions`` lists what was
    repaired — empty on the happy path, never fabricated.
    """

    house: Optional[Dict[str, Any]]
    actions: Tuple[RepairAction, ...] = ()
    discard: Optional[DiscardReason] = None

    @property
    def ok(self) -> bool:
        return self.house is not None


def ensure_model_importable() -> None:
    """Make ``garh_model`` importable when running from the repo checkout.

    The worker image sets ``PYTHONPATH=/app:/app/apps/api``; locally the repo
    layout is discovered relative to this file. Mirrors the helper in
    :mod:`services.solver.openings` so either module works standalone.
    """
    try:
        import garh_model  # noqa: F401

        return
    except ImportError:
        pass
    root = Path(__file__).resolve().parents[2]
    candidate = root / "apps" / "api"
    if candidate.is_dir() and str(candidate) not in sys.path:
        sys.path.append(str(candidate))


# ---------------------------------------------------------------------------
# document wrapping + validation
# ---------------------------------------------------------------------------


def wrap_project_doc(house: Mapping[str, Any], params: SolveParams) -> Dict[str, Any]:
    """Wrap a HouseModel fragment in the ProjectDoc JSON ``validate_model`` takes.

    The plot section is real (boundary, north, roads from the solve params) so
    the plot invariants run too; the brief carries only what validation reads.
    """
    roads = [
        {"edgeIndex": edge.index, "widthMm": edge.road_width_mm, "name": None}
        for edge in params.edges
        if edge.road_width_mm > 0
    ]
    return {
        "schemaVersion": int(house.get("schemaVersion", 1)),
        "plot": {
            "boundary": [{"x": x, "y": y} for x, y in params.plot_polygon],
            "northDeg": params.north_deg % 360,
            "roads": roads,
            "regProfile": {"cityPack": params.profile.city_pack, "overrides": {}},
            "source": "solver",
        },
        "brief": {
            # The allowlisted declarations the rules read (carParking, RWH,
            # dwellingUnits) — so the §5.4 pass sees the same brief facts the
            # compliance panel does instead of defaulting them all to zero.
            "data": dict(params.brief_data),
            "vastuMode": params.vastu_mode,
            "completeness": 0,
        },
        "house": {k: v for k, v in house.items() if not k.startswith("solver")},
        "annotations": [],
    }


def validate_house(house: Mapping[str, Any], params: SolveParams) -> Tuple[Any, ...]:
    """Run the section-3 fold invariants over the fragment. Errors only.

    Returns ``garh_model.validate.ValidationIssue`` rows — the same objects the
    fold rejects ops with, so a repair decision here reads the same evidence the
    editor would show the architect.
    """
    ensure_model_importable()
    from garh_model.model import ProjectDoc
    from garh_model.validate import validate_model

    doc = ProjectDoc.from_json(wrap_project_doc(house, params))
    return tuple(validate_model(doc, include_warnings=False))


# ---------------------------------------------------------------------------
# the repairs
# ---------------------------------------------------------------------------


def _wall_length_mm(wall: Mapping[str, Any]) -> int:
    a, b = wall["a"], wall["b"]
    return round_half_away(math.hypot(b["x"] - a["x"], b["y"] - a["y"]))


def _drop_sliver_walls(
    house: Dict[str, Any],
) -> Tuple[Dict[str, Any], List[RepairAction], Optional[DiscardReason]]:
    """Pre-pass: drop <115mm walls with no openings; a hosted sliver is fatal."""
    walls = list(house.get("walls") or [])
    openings = list(house.get("openings") or [])
    hosted = {str(o.get("wallId")) for o in openings}
    kept: List[Mapping[str, Any]] = []
    actions: List[RepairAction] = []
    for wall in walls:
        length = _wall_length_mm(wall)
        if length >= FINE_MODULE_MM:
            kept.append(wall)
            continue
        wall_id = str(wall.get("id"))
        if wall_id in hosted:
            return (
                house,
                actions,
                DiscardReason(
                    code="SLIVER_WALL_HOSTS_OPENING",
                    message="A wall shorter than one 115mm module hosts an opening; "
                    "no one-module nudge can make that buildable.",
                    detail="wall %s is %dmm long" % (wall_id, length),
                ),
            )
        actions.append(
            RepairAction(
                kind="sliver-wall-dropped",
                element_id=wall_id,
                delta_mm=length,
                description="Dropped a %dmm sliver wall (shorter than one 115mm module)."
                % length,
            )
        )
    if len(kept) != len(walls):
        house = dict(house)
        house["walls"] = kept
    return house, actions, None


def _nudged_offset(
    offset_mm: int, width_mm: int, wall_length_mm: int
) -> Optional[int]:
    """The nearest legal centre offset, or None when >1 module away / impossible.

    Mirrors ``garh_model.validate._opening_fit_issue`` arithmetic exactly:
    ``floor(w/2)`` before the centre, ``ceil(w/2)`` after it.
    """
    usable = wall_length_mm - 2 * WALL_END_MARGIN_MM
    if width_mm > usable:
        return None  # no offset can fit this opening on this wall
    half_lo = width_mm // 2
    half_hi = -((-width_mm) // 2)
    min_offset = WALL_END_MARGIN_MM + half_lo
    max_offset = wall_length_mm - WALL_END_MARGIN_MM - half_hi
    clamped = min(max(offset_mm, min_offset), max_offset)
    if clamped == offset_mm:
        return None  # nothing to fix — the issue is not offset-shaped
    if abs(clamped - offset_mm) > NUDGE_MM:
        return None  # §5.3: one module is the repair budget, more is a discard
    return clamped


def _try_nudge_opening(
    house: Dict[str, Any], opening_id: str, nudged: set
) -> Tuple[Dict[str, Any], Optional[RepairAction]]:
    if opening_id in nudged:
        return house, None  # one nudge per opening, ever
    walls_by_id = {str(w.get("id")): w for w in house.get("walls") or []}
    openings = list(house.get("openings") or [])
    for index, opening in enumerate(openings):
        if str(opening.get("id")) != opening_id:
            continue
        wall = walls_by_id.get(str(opening.get("wallId")))
        if wall is None:
            return house, None
        new_offset = _nudged_offset(
            int(opening["offsetMm"]), int(opening["widthMm"]), _wall_length_mm(wall)
        )
        if new_offset is None:
            return house, None
        delta = new_offset - int(opening["offsetMm"])
        patched = dict(opening)
        patched["offsetMm"] = new_offset
        openings[index] = patched
        out = dict(house)
        out["openings"] = openings
        nudged.add(opening_id)
        return out, RepairAction(
            kind="opening-nudge",
            element_id=opening_id,
            delta_mm=delta,
            description="Nudged the opening %+dmm along its wall to clear the "
            "%dmm end margin." % (delta, WALL_END_MARGIN_MM),
        )
    return house, None


def repair_house(house: Mapping[str, Any], params: SolveParams) -> RepairOutcome:
    """§5.3's repair-or-discard, over one assembled HouseModel fragment.

    Deterministic: issues are handled in the order ``validate_model`` reports
    them, every nudge is the minimum legal move, and the input mapping is never
    mutated — the outcome carries a fresh dict.
    """
    working: Dict[str, Any] = dict(house)
    working, actions, fatal = _drop_sliver_walls(working)
    if fatal is not None:
        return RepairOutcome(house=None, actions=tuple(actions), discard=fatal)

    nudged: set = set()
    for _round in range(2):  # pass 1 repairs, pass 2 must come back clean
        issues = validate_house(working, params)
        if not issues:
            return RepairOutcome(house=working, actions=tuple(actions))
        progressed = False
        unfixable: List[Any] = []
        for issue in issues:
            if issue.code in TRIVIALLY_REPAIRABLE_CODES and issue.element_ids:
                working, action = _try_nudge_opening(
                    working, issue.element_ids[0], nudged
                )
                if action is not None:
                    actions.append(action)
                    progressed = True
                    continue
            unfixable.append(issue)
        if unfixable or not progressed:
            first = unfixable[0] if unfixable else issues[0]
            return RepairOutcome(
                house=None,
                actions=tuple(actions),
                discard=DiscardReason(
                    code=str(first.code),
                    message=str(first.message),
                    detail="%d invariant violation(s); trivially repairable: none left"
                    % len(unfixable or issues),
                ),
            )
    # Two passes were not enough: the nudges did not converge — honest discard.
    issues = validate_house(working, params)
    if not issues:
        return RepairOutcome(house=working, actions=tuple(actions))
    return RepairOutcome(
        house=None,
        actions=tuple(actions),
        discard=DiscardReason(
            code="REPAIR_DID_NOT_CONVERGE",
            message="One-module nudges did not clear the model invariants.",
            detail=", ".join(sorted({str(i.code) for i in issues})),
        ),
    )


__all__ = [
    "NUDGE_MM",
    "TRIVIALLY_REPAIRABLE_CODES",
    "WALL_END_MARGIN_MM",
    "DiscardReason",
    "RepairAction",
    "RepairOutcome",
    "ensure_model_importable",
    "repair_house",
    "validate_house",
    "wrap_project_doc",
]
