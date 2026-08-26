"""The ``solver.generate`` / ``solver.resolve`` job handler (playbook §5).

Turns a queue envelope into :class:`~services.solver.types.SolveParams`, runs the §5
pipeline, and returns the options. Two properties are worth stating explicitly:

* **CP-SAT runs off the event loop.** The solve is seconds of pure CPU, so it goes
  through ``asyncio.to_thread`` — otherwise this worker's heartbeat would stall behind
  it and the queue would redeliver a job that is still running.
* **Progress crosses that boundary safely.** The pipeline is async and the solve is
  not, so stage callbacks are marshalled back onto the loop with
  ``run_coroutine_threadsafe``. That keeps §15's honest progress events flowing during
  the part of the job that actually takes time.

Payload contract (the API's enqueue helper must match):

===================  =========================================================
field                meaning
===================  =========================================================
``plot``             ``{polygon: [{x,y}...], edges: [...], northDeg}``
``profile``          ``{cityPack, coveragePercent, farX100, maxHeightMm, maxFloors}``
``brief``            ``{storeys, vastuMode, rooms: [...], targetBuiltUpMm2}``
``seed``             integer; the same seed must reproduce the same options
``lockedRoomIds``    §5.7 partial re-solve
``optionCount``      how many options to aim for (default 3)
===================  =========================================================
"""

from __future__ import annotations

import asyncio
from typing import Any, Mapping, Sequence

from services.common.errors import InvalidJobError
from services.common.jobstore import JobResult
from services.common.logging import get_logger
from services.common.runtime import BaseJobHandler, JobContext
from services.solver.envelope import EnvelopeError
from services.solver.geometry import as_polygon
from services.solver.pipeline import SolveContext, run_solver
from services.solver.types import PlotEdge, RegProfile, RoomRequest, SolveParams

log = get_logger("solver.handler")

#: §14: "Solver 3 options <=60s (fixtures); CI uses 2 workers: <=120s". The handler's
#: wall-clock budget is generous compared to the per-candidate budget so a job dies
#: from its own timeout rather than from the queue's visibility timeout.
DEFAULT_TIMEOUT_SECONDS = 300

_VALID_EDGE_ROLES = ("front", "rear", "side", "side-left", "side-right")


class SolverJobHandler(BaseJobHandler):
    """Generates plan options for a project."""

    kinds = ("solver.generate", "solver.resolve")
    timeout_seconds: int | None = DEFAULT_TIMEOUT_SECONDS

    async def handle(self, ctx: JobContext) -> JobResult:
        params = _parse_params(ctx.payload, kind=ctx.envelope.kind)
        loop = asyncio.get_running_loop()

        async def progress(stage: str, message: str, **data: Any) -> None:
            percent = data.pop("percent", None)
            artifact = data.pop("artifactName", None)
            if artifact:
                await ctx.progress.artifact(artifact, **data)
            else:
                await ctx.progress.stage(stage, message, percent=percent, **data)

        def progress_from_thread(stage: str, message: str, **data: Any) -> None:
            """Bridge the sync solve back onto the loop. Never blocks the solver."""
            asyncio.run_coroutine_threadsafe(progress(stage, message, **data), loop)

        context = SolveContext(
            params=params,
            progress=progress,
            check_cancelled=ctx.raise_if_cancelled,
            num_search_workers=ctx.settings.solver_num_search_workers,
            progress_from_thread=progress_from_thread,
        )
        # The pipeline awaits its own progress calls, so it runs on the loop; the CPU
        # work inside stage A is what `stage_a_topology` must push to a thread.
        try:
            result = await run_solver(context)
        except EnvelopeError as exc:
            raise InvalidJobError(
                exc.message, action=exc.action, detail=exc.detail
            ) from exc

        data: dict[str, Any] = result.to_json()
        log.info(
            "solver.job.done",
            option_count=len(result.options),
            considered=result.considered,
            rejected=result.rejected_by_gates,
        )
        message = (
            result.banner
            if result.banner
            else "Generated %d plan options." % len(result.options)
        )
        return JobResult(data=data, message=message)


# ---------------------------------------------------------------------------
# payload parsing — every failure names the field (golden rule 9)
# ---------------------------------------------------------------------------
def _parse_params(payload: Mapping[str, Any], *, kind: str) -> SolveParams:
    plot = _require_mapping(payload.get("plot"), "plot")
    profile_raw = _require_mapping(payload.get("profile"), "profile")
    brief = _require_mapping(payload.get("brief"), "brief")

    polygon = _parse_polygon(plot.get("polygon"))
    edges = _parse_edges(plot.get("edges"), edge_count=len(polygon))
    profile = RegProfile(
        city_pack=str(profile_raw.get("cityPack") or "nbc-core"),
        coverage_percent=_int(profile_raw.get("coveragePercent"), "profile.coveragePercent", 1, 100),
        far_x100=_int(profile_raw.get("farX100"), "profile.farX100", 1, 10_000),
        max_height_mm=_int(profile_raw.get("maxHeightMm"), "profile.maxHeightMm", 1, 1_000_000),
        max_floors=_int(profile_raw.get("maxFloors"), "profile.maxFloors", 1, 100),
        overrides=profile_raw.get("overrides") if isinstance(profile_raw.get("overrides"), dict) else {},
    )

    rooms = tuple(_parse_room(item, index) for index, item in enumerate(brief.get("rooms") or []))
    if not rooms:
        raise InvalidJobError(
            "This brief doesn't list any rooms yet.",
            action="Add the rooms the client needs on the Brief tab.",
            detail="brief.rooms is empty",
        )

    vastu = str(brief.get("vastuMode") or "advisory")
    if vastu not in ("off", "advisory", "strict"):
        raise InvalidJobError(
            "We don't recognise that Vastu setting.",
            detail="brief.vastuMode=%r" % vastu,
        )

    return SolveParams(
        brief_data=_parse_brief_data(brief.get("data")),
        plot_polygon=polygon,
        edges=edges,
        profile=profile,
        rooms=rooms,
        storeys=_int(brief.get("storeys", 1), "brief.storeys", 1, 6),
        north_deg=_int(plot.get("northDeg", 0), "plot.northDeg", 0, 359),
        vastu_mode=vastu,  # type: ignore[arg-type]  # checked above
        locked_room_ids=tuple(
            str(item) for item in payload.get("lockedRoomIds") or [] if isinstance(item, str)
        ),
        seed=_int(payload.get("seed", 0), "seed", 0, 2**31 - 1),
        target_option_count=_int(payload.get("optionCount", 3), "optionCount", 1, 5),
    )


#: The only ``brief.data`` keys a solve payload may carry: the declarations the
#: §5.4 rules pass reads (``garh_api.compliance.build_evaluation_context``).
#: An allowlist by design — brief free text stays out of worker payloads (§13).
_BRIEF_DATA_KEYS = ("carParking", "dwellingUnits", "rainwaterHarvesting")


def _parse_brief_data(raw: Any) -> Mapping[str, Any]:
    if not isinstance(raw, Mapping):
        return {}
    out: dict[str, Any] = {}
    for key in _BRIEF_DATA_KEYS:
        if key not in raw:
            continue
        value = raw[key]
        if key == "rainwaterHarvesting":
            if isinstance(value, bool):
                out[key] = value
        elif isinstance(value, int) and not isinstance(value, bool) and value >= 0:
            out[key] = value
    return out


def _parse_polygon(raw: Any) -> Any:
    if not isinstance(raw, list) or len(raw) < 3:
        raise InvalidJobError(
            "This project doesn't have a plot boundary yet.",
            action="Draw the plot on the Plot tab, then generate again.",
            detail="plot.polygon needs at least 3 points, got %r" % type(raw).__name__,
        )
    points: list[tuple[int, int]] = []
    for index, item in enumerate(raw):
        if isinstance(item, Mapping):
            x, y = item.get("x"), item.get("y")
        elif isinstance(item, (list, tuple)) and len(item) == 2:
            x, y = item[0], item[1]
        else:
            raise InvalidJobError(
                "The plot boundary could not be read.",
                detail="plot.polygon[%d] is %r" % (index, item),
            )
        points.append((_int(x, "plot.polygon[%d].x" % index), _int(y, "plot.polygon[%d].y" % index)))
    try:
        return as_polygon(points)
    except ValueError as exc:
        raise InvalidJobError(
            "The plot boundary could not be read.", detail=str(exc)
        ) from exc


def _parse_edges(raw: Any, *, edge_count: int) -> tuple[PlotEdge, ...]:
    if not isinstance(raw, list) or not raw:
        raise InvalidJobError(
            "This plot doesn't have setbacks set yet.",
            action="Pick a city preset on the Plot tab.",
            detail="plot.edges is missing or empty",
        )
    edges: list[PlotEdge] = []
    for index, item in enumerate(raw):
        entry = _require_mapping(item, "plot.edges[%d]" % index)
        role = str(entry.get("role") or "side")
        if role not in _VALID_EDGE_ROLES:
            raise InvalidJobError(
                "We don't recognise that plot edge type.",
                detail="plot.edges[%d].role=%r, expected one of %s"
                % (index, role, ", ".join(_VALID_EDGE_ROLES)),
            )
        edges.append(
            PlotEdge(
                index=index,
                role=role,  # type: ignore[arg-type]  # checked above
                setback_mm=_int(entry.get("setbackMm", 0), "plot.edges[%d].setbackMm" % index, 0),
                road_width_mm=_int(
                    entry.get("roadWidthMm", 0), "plot.edges[%d].roadWidthMm" % index, 0
                ),
            )
        )
    if len(edges) != edge_count:
        raise InvalidJobError(
            "The plot's setbacks don't match its shape.",
            action="Re-check the edges on the Plot tab.",
            detail="%d edges for a %d-sided boundary" % (len(edges), edge_count),
        )
    return tuple(edges)


def _parse_room(raw: Any, index: int) -> RoomRequest:
    entry = _require_mapping(raw, "brief.rooms[%d]" % index)
    key = str(entry.get("key") or entry.get("type") or "room%d" % index)
    return RoomRequest(
        key=key,
        room_type=str(entry.get("type") or "unassigned"),
        min_area_mm2=_int(entry.get("minAreaMm2", 0), "brief.rooms[%d].minAreaMm2" % index, 0),
        target_area_mm2=_int(
            entry.get("targetAreaMm2", 0), "brief.rooms[%d].targetAreaMm2" % index, 0
        ),
        min_width_mm=_int(entry.get("minWidthMm", 0), "brief.rooms[%d].minWidthMm" % index, 0),
        max_aspect_x100=_int(
            entry.get("maxAspectX100", 220), "brief.rooms[%d].maxAspectX100" % index, 100
        ),
        storey_index=(
            _int(entry["storeyIndex"], "brief.rooms[%d].storeyIndex" % index, 0)
            if entry.get("storeyIndex") is not None
            else None
        ),
        needs_external_wall=bool(entry.get("needsExternalWall", True)),
        is_wet=bool(entry.get("isWet", False)),
        locked=bool(entry.get("locked", False)),
    )


def _require_mapping(value: Any, where: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise InvalidJobError(
            "This solve request is missing some of its details.",
            action="Start it again from the app.",
            detail="%s must be an object, got %s" % (where, type(value).__name__),
        )
    return value


def _int(value: Any, where: str, minimum: int | None = None, maximum: int | None = None) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise InvalidJobError(
            "This solve request could not be read.",
            detail="%s must be an integer (geometry is integer millimetres), got %r"
            % (where, value),
        )
    if minimum is not None and value < minimum:
        raise InvalidJobError(
            "This solve request could not be read.",
            detail="%s must be >= %d, got %d" % (where, minimum, value),
        )
    if maximum is not None and value > maximum:
        raise InvalidJobError(
            "This solve request could not be read.",
            detail="%s must be <= %d, got %d" % (where, maximum, value),
        )
    return value


__all__ = ["DEFAULT_TIMEOUT_SECONDS", "SolverJobHandler"]
