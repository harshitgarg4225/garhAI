"""Everything a ``solver.generate`` envelope must carry (§5) — assembled server-side.

``services/solver/handler.py`` states the payload contract the worker parses:
``plot`` (polygon + one edge per side with a role, its setback and its road),
``profile`` (the regulatory numbers) and ``brief`` (the room requests). Workers
hold no database connection, so the API must put all three on the envelope at
enqueue time — this module is that assembly, the solve-path sibling of
``routers/sheets.build_sheets_job``.

Where each number comes from:

* **plot** — the folded document's ``plot`` (the op log is the truth; the
  ``plots`` table is a projection of it), via the same ``load_project_state``
  path ``GET /compliance`` uses.
* **profile** — the rules engine's area statement (``evaluate_document``): FAR,
  coverage, height, floors and per-edge setbacks are read out of the applicable
  rules' own limits, the same evaluation the compliance tab and the sheet quote.
  The solver cannot disagree with the compliance panel about what is allowed,
  because it is handed the panel's own numbers (one source, §7).
* **brief** — the folded document's brief ``data.rooms``, expanded per ``count``
  with exactly the keys ``services.solver.program.build_program_from_brief``
  mints, so a room key — and with it §5.7's ``lockedRoomIds`` — names the same
  room on every run.

Missing inputs are refused here with an actionable 4xx (the ``no_design_version``
idiom) rather than enqueued into a job the worker can only kill.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any, Mapping, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from garh_api.logging import get_logger
from garh_api.repositories import ProjectRepository, TenantCtx
from garh_api.routers import ApiError

_log = get_logger(__name__)

#: An allowance no loaded pack regulates is passed as the parse ceiling from
#: ``services.solver.handler._parse_params`` — an honest "unbounded", never an
#: invented bye-law number. The area statement renders the same absence as
#: "not regulated by the loaded packs"; this is that sentence in integers.
UNREGULATED_COVERAGE_PERCENT = 100
UNREGULATED_FAR_X100 = 10_000
UNREGULATED_HEIGHT_MM = 1_000_000
UNREGULATED_FLOORS = 100

#: ``brief.storeys`` bound in ``_parse_params``.
MAX_SOLVER_STOREYS = 6

#: Rules-engine edge roles → solver edge roles. Only ``front`` is behaviourally
#: special in the solver (entrance rules, stair anchor ranking); ``side-a`` /
#: ``side-b`` / ``other`` map to plain ``side`` rather than claiming a
#: handedness the §6 projection never derived.
_EDGE_ROLES: Mapping[str, str] = {"front": "front", "rear": "rear"}


@dataclass(frozen=True)
class SolveInputs:
    """The three payload objects the worker contract requires, plus the resolved
    storey count (which the contract reads from ``brief.storeys``)."""

    plot: dict[str, Any]
    profile: dict[str, Any]
    brief: dict[str, Any]
    storeys: int


async def build_solve_inputs(
    session: AsyncSession,
    ctx: TenantCtx,
    project_id: uuid.UUID,
    branch: uuid.UUID,
    *,
    requested_storeys: Optional[int] = None,
) -> SolveInputs:
    """Load, derive and shape one solve request's inputs. Raises 4xx on gaps."""
    from garh_api.compliance import ComplianceUnavailable, evaluate_document
    from garh_api.routers.ops import load_project_state

    document = (await load_project_state(session, ctx, project_id, branch)).document

    plot_doc = dict(document.get("plot") or {})
    boundary = list(plot_doc.get("boundary") or [])
    if len(boundary) < 3:
        raise ApiError(
            "This project doesn't have a plot boundary yet.",
            status=409,
            code="no_plot_boundary",
            action="Draw the plot on the Plot tab, then generate again.",
        )

    brief_doc = dict(document.get("brief") or {})
    brief_data = dict(brief_doc.get("data") or {})
    rooms = _room_requests(brief_data)
    if not rooms:
        raise ApiError(
            "This brief doesn't list any rooms yet.",
            status=409,
            code="no_brief_rooms",
            action="Add the rooms the client needs on the Brief tab.",
        )

    project = await ProjectRepository(session, ctx).require(project_id)
    try:
        report, _pack_versions = evaluate_document(document, city_pack=project.city_pack)
    except ComplianceUnavailable as exc:
        # The boundary precondition is already handled above, so this is the
        # engine or the packs failing — a server problem, not the architect's.
        raise ApiError(
            "We couldn't work out the regulatory limits for this plot.",
            status=503,
            code="compliance_unavailable",
            action="Try again in a moment.",
        ) from exc
    areas_raw = report.get("areas")
    areas: dict[str, Any] = areas_raw if isinstance(areas_raw, dict) else {}
    if not areas:
        _log.warning(
            "solve.area_statement_missing",
            project_id=str(project_id),
            consequence="profile limits fall back to the unregulated ceilings",
        )

    plot_area_mm2 = areas.get("plotAreaMm2")
    plot_area_mm2 = plot_area_mm2 if isinstance(plot_area_mm2, int) else 0

    reg = dict(plot_doc.get("regProfile") or {})
    profile: dict[str, Any] = {
        "cityPack": str(reg.get("cityPack") or project.city_pack or "nbc-core"),
        "coveragePercent": _ratio_x100(
            areas.get("coverageAllowedMm2"), plot_area_mm2, cap=UNREGULATED_COVERAGE_PERCENT
        ),
        "farX100": _ratio_x100(
            areas.get("farAllowedMm2"), plot_area_mm2, cap=UNREGULATED_FAR_X100
        ),
        "maxHeightMm": _limit(areas.get("heightAllowedMm"), cap=UNREGULATED_HEIGHT_MM),
        "maxFloors": _limit(areas.get("floorsAllowed"), cap=UNREGULATED_FLOORS),
    }
    overrides = reg.get("overrides")
    if isinstance(overrides, dict) and overrides:
        profile["overrides"] = dict(overrides)

    storeys = _resolve_storeys(requested_storeys, document, brief_data)
    brief: dict[str, Any] = {
        "storeys": storeys,
        "vastuMode": str(brief_doc.get("vastuMode") or "advisory"),
        "rooms": rooms,
    }

    return SolveInputs(
        plot=_plot_payload(boundary, plot_doc, areas),
        profile=profile,
        brief=brief,
        storeys=storeys,
    )


def _plot_payload(
    boundary: list[Any], plot_doc: Mapping[str, Any], areas: Mapping[str, Any]
) -> dict[str, Any]:
    """``payload["plot"]`` per the worker contract: polygon, edges, north.

    One edge per boundary vertex, matching the §6 projection's edge indexing.
    ``requiredMm: null`` (not regulated by the loaded packs) becomes ``0`` — an
    unregulated edge honestly permits building to the line.
    """
    road_widths: dict[int, int] = {}
    for road in plot_doc.get("roads") or ():
        if not isinstance(road, Mapping):
            continue
        try:
            road_widths[int(road["edgeIndex"])] = int(road.get("widthMm") or 0)
        except (KeyError, TypeError, ValueError):
            continue

    setback_rows: dict[int, Mapping[str, Any]] = {}
    for row in areas.get("setbacks") or ():
        if isinstance(row, Mapping) and isinstance(row.get("edgeIndex"), int):
            setback_rows[int(row["edgeIndex"])] = row

    edges: list[dict[str, Any]] = []
    for index in range(len(boundary)):
        row = setback_rows.get(index) or {}
        required = row.get("requiredMm")
        edges.append(
            {
                "index": index,
                "role": _EDGE_ROLES.get(str(row.get("role") or ""), "side"),
                "setbackMm": required if isinstance(required, int) else 0,
                "roadWidthMm": road_widths.get(index, 0),
            }
        )

    return {
        "polygon": [{"x": int(p["x"]), "y": int(p["y"])} for p in boundary],
        "edges": edges,
        "northDeg": int(plot_doc.get("northDeg") or 0) % 360,
    }


def _room_requests(brief_data: Mapping[str, Any]) -> list[dict[str, Any]]:
    """BriefDoc ``data.rooms`` → the worker's ``brief.rooms`` entries.

    Key expansion mirrors ``services.solver.program.build_program_from_brief``
    (``bedroom``, ``bedroom2``, …). NBC floors, aspect bounds and wetness are
    the worker's program layer's job — the brief's own numbers pass through
    untouched. Values are integers by op 5's own validation (no floats cross
    the model boundary), so ``int()`` here can only ever see ints.
    """
    requests: list[dict[str, Any]] = []
    for raw in brief_data.get("rooms") or ():
        if not isinstance(raw, Mapping):
            continue
        room_type = str(raw.get("type") or "other")
        count = int(raw.get("count") or 1)
        for occurrence in range(max(1, count)):
            entry: dict[str, Any] = {
                "key": room_type if occurrence == 0 else "%s%d" % (room_type, occurrence + 1),
                "type": room_type,
                "minAreaMm2": int(raw.get("minAreaMm2") or 0),
                "targetAreaMm2": int(raw.get("targetAreaMm2") or 0),
                "minWidthMm": int(raw.get("minWidthMm") or 0),
            }
            if raw.get("storey") is not None:
                entry["storeyIndex"] = int(raw["storey"])
            requests.append(entry)
    return requests


def _resolve_storeys(
    requested: Optional[int], document: Mapping[str, Any], brief_data: Mapping[str, Any]
) -> int:
    """The request's storeys, else the document's, else the brief's G+n, else 1."""
    if isinstance(requested, int) and requested >= 1:
        return min(MAX_SOLVER_STOREYS, requested)
    modelled = len(list((document.get("house") or {}).get("storeys") or ()))
    if modelled >= 1:
        return min(MAX_SOLVER_STOREYS, modelled)
    floors = brief_data.get("floorsAboveGround")
    if isinstance(floors, int) and not isinstance(floors, bool) and floors >= 0:
        return min(MAX_SOLVER_STOREYS, floors + 1)
    return 1


def _ratio_x100(allowed_mm2: Any, plot_area_mm2: int, *, cap: int) -> int:
    """Invert ``ratio.floor_of``: a mm² allowance → the pack's ratio ×100.

    ``far_max`` / ``coverage_max`` floor ``ratio × plotArea`` to mm² (§6);
    ceiling division recovers the pack's ratio exactly, because plot areas
    (~10⁸ mm²) dwarf the ×100 quantisation. ``cap`` doubles as the honest
    "not regulated" value — see :data:`UNREGULATED_FAR_X100`.
    """
    if (
        not isinstance(allowed_mm2, int)
        or isinstance(allowed_mm2, bool)
        or plot_area_mm2 <= 0
    ):
        return cap
    return max(1, min(cap, -(-allowed_mm2 * 100 // plot_area_mm2)))


def _limit(value: Any, *, cap: int) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        return cap
    return max(1, min(cap, value))


__all__ = [
    "MAX_SOLVER_STOREYS",
    "UNREGULATED_COVERAGE_PERCENT",
    "UNREGULATED_FAR_X100",
    "UNREGULATED_FLOORS",
    "UNREGULATED_HEIGHT_MM",
    "SolveInputs",
    "build_solve_inputs",
]
