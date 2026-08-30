"""§5 pipeline orchestration — the typed, stage-named driver for the layout solver.

Flow (one function per stage, §5.1–§5.6 in order)::

    envelope → program → stair candidates → stage A per candidate (parallel, budgeted)
             → stage B → critic (hard rules) → critic (soft scores incl. Vastu)
             → diversity → gates → 3–5 PlanOptions with scores + rationale seeds

**The progress events are the product.** §15 forbids fake progress bars and specifies
the "generation theater" copy — *"Placing staircase… Packing rooms… Checking BBMP
setbacks… Scoring Vastu"* — as **driven by real worker events**. Every stage below
emits its message when that stage actually begins; the percentages are stage
boundaries, never a timer. :data:`STAGES` is the single place the copy lives, so the
UI and the worker cannot drift apart.

**Determinism is configuration, not luck.** CP-SAT with ``num_search_workers=8`` is a
portfolio race between eight strategies — fast, and the production choice (§5.2) —
but which strategy wins first can differ run to run, and a wall-clock ``max_time``
makes the search depth a property of the machine. Golden files (§16) compare plan
JSON with tolerance 0, so tests must instead run the profile
:data:`DETERMINISTIC_TEST_PROFILE`: one worker, a fixed ``random_seed``, and
solution/branch limits in place of wall-clock time. Both profiles are named
:class:`SolverProfile` values; nothing in this module invents a third.

**Stage bodies are contracts, not imports.** The CP-SAT stages (§5.2/§5.3) and the
critic's geometry-dependent sub-scores live in :mod:`services.solver.stages` and
:mod:`services.solver.critic`. This driver calls them through :class:`StageSet`, which
tests replace with pure-Python fakes — so the orchestration, the §15 events, the §5.6
gates, the relax-once pass, resumability and §5.7 partial re-solve are all provable on
a machine where ``ortools`` is not installed. ``ortools`` is never imported here.
"""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from typing import Any

from services.common.assumptions import Assumption
from services.common.logging import get_logger
from services.solver import gates
from services.solver.diversity import select_diverse, signature, signature_summary
from services.solver.envelope import derive_envelope
from services.solver.program import CIRCULATION_TYPES
from services.solver.stages import Candidate, GridSpec, grid_envelope
from services.solver.types import (
    BuildableEnvelope,
    PlanOption,
    RoomPlacement,
    RoomRequest,
    ScoreBreakdown,
    SolveParams,
    SolveResult,
    StairAnchor,
)

log = get_logger("solver.pipeline")

#: Placement types whose area stage A already counts in ``circulation_area_mm2``
#: (mirrors ``ProgramRoom.is_circulation``): the passage/foyer/lobby set plus the
#: stair well. Used to keep the footprint arithmetic below honest across both
#: stage-A generations.
_CIRCULATION_PLACEMENT_TYPES = frozenset(CIRCULATION_TYPES) | {"staircase"}


# ---------------------------------------------------------------------------
# Solver profiles (§5.2 + testing strategy §16)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SolverProfile:
    """How hard, how parallel, and how reproducibly one solve runs.

    Why two named profiles and not knobs scattered through the code:

    * **Production** (§5.2, verbatim): ``num_search_workers=8``, 15s wall clock per
      stair candidate. Eight workers is a portfolio race — the fastest way to a good
      layout — and a wall-clock budget is the honest way to keep §14's "3 options
      ≤60s". Neither property is deterministic: the winning strategy and the depth
      reached both vary run to run and machine to machine.
    * **Deterministic test**: ``num_search_workers=1`` (one strategy, one ordering),
      a **fixed** ``random_seed``, and ``max_solutions`` / ``max_branches`` in place
      of ``max_time``. Solution and branch counts are machine-independent, so the
      same inputs stop the search at the same node on a laptop and in CI — which is
      what lets §16's plan-JSON goldens compare with tolerance 0.

    Stage A implementations map these onto CP-SAT: ``solver.parameters.num_search_workers``,
    ``solver.parameters.random_seed``, ``solver.parameters.max_time_in_seconds`` when
    ``time_budget_seconds`` is set, and a solution-limit callback + ``max_number_of_branches``
    when the limits are set instead.
    """

    name: str
    #: CP-SAT workers per solve (``solver.parameters.num_search_workers``).
    num_search_workers: int
    #: Wall-clock budget per stair candidate; ``None`` means "use the limits below".
    time_budget_seconds: int | None
    #: How many stair candidates solve concurrently (§5.2 "solve per candidate,
    #: parallel workers"). Bounded by the worker pool budget, not by len(anchors).
    candidate_parallelism: int
    #: Fixed CP-SAT seed; ``None`` means "derive from SolveParams.seed".
    random_seed: int | None = None
    #: Stop after this many improving solutions (deterministic replacement for time).
    max_solutions: int | None = None
    #: Stop after this many branches (deterministic replacement for time).
    max_branches: int | None = None

    def seed_for(self, params: SolveParams) -> int:
        return self.random_seed if self.random_seed is not None else params.seed


#: §5.2 verbatim: "Time budget: 15s/stair-candidate, num_search_workers=8".
#: ``candidate_parallelism=2`` keeps two candidates in flight so 4–6 anchors fit the
#: §14 60s budget without oversubscribing a typical 16-thread worker pod (2×8).
PRODUCTION_PROFILE = SolverProfile(
    name="production",
    num_search_workers=8,
    time_budget_seconds=15,
    candidate_parallelism=2,
)

#: See the class docstring: single worker + fixed seed + node/solution limits is the
#: only combination under which CP-SAT output is a pure function of its inputs.
DETERMINISTIC_TEST_PROFILE = SolverProfile(
    name="deterministic-test",
    num_search_workers=1,
    time_budget_seconds=None,
    candidate_parallelism=1,
    random_seed=0,
    max_solutions=64,
    max_branches=200_000,
)


# ---------------------------------------------------------------------------
# §15 staged messages — the one place this copy lives
# ---------------------------------------------------------------------------

#: Which authority's name appears in the "Checking … setbacks" message. §15's example
#: is Bengaluru ("checking BBMP setbacks"); the other seeded packs get their own
#: authority so the message stays honest when the plot is not in Bengaluru.
AUTHORITY_BY_PACK: Mapping[str, str] = {
    "blr": "BBMP",
    "hyd": "GHMC",
    "ncr": "DDA",
    "nbc-core": "NBC",
}

#: (stage id, user-facing message, percent when the stage BEGINS).
#: The copy is §15's; the percentages are honest stage boundaries. A slow stage makes
#: the bar pause — which is true — rather than crawl a fake percent.
STAGES: tuple[tuple[str, str, int], ...] = (
    ("envelope", "Working out the buildable area…", 4),
    ("program", "Sizing rooms from the brief…", 8),
    ("stairs", "Placing staircase…", 12),
    ("topology", "Packing rooms…", 20),
    ("refine", "Snapping walls to the brick grid, adding doors and windows…", 55),
    ("critic", "Checking {authority} setbacks…", 68),
    ("vastu", "Scoring Vastu…", 78),
    ("diversity", "Picking the options that are genuinely different…", 88),
    ("relax", "Loosening soft preferences for one more look…", 90),
)


def authority_for_pack(city_pack: str) -> str:
    return AUTHORITY_BY_PACK.get(city_pack, AUTHORITY_BY_PACK["nbc-core"])


#: Progress reporter shape, narrowed to what this module uses. Structural, so the
#: pipeline is testable with a plain recorder and no Redis.
ProgressFn = Callable[..., Awaitable[Any]]


# ---------------------------------------------------------------------------
# Stage contracts
# ---------------------------------------------------------------------------

#: ``anchors(envelope, params) -> Sequence[StairAnchor]`` — §5.2 "stairs first".
AnchorsFn = Callable[[BuildableEnvelope, SolveParams], Sequence[StairAnchor]]
#: ``stage_a(grid, params, anchor, *, profile, relaxed) -> Candidate | None``.
StageAFn = Callable[..., Candidate | None]
#: ``stage_b(candidate, params, envelope) -> model document | None`` (§5.3).
StageBFn = Callable[[Candidate, SolveParams, BuildableEnvelope], Mapping[str, Any] | None]
#: ``build_ops(placements, params, model=...) -> ops`` — §4: the solver emits ops.
BuildOpsFn = Callable[..., Sequence[Mapping[str, Any]]]
#: ``compliance(model, params) -> rules-engine result rows`` (§5.4 hard-rule pass).
ComplianceFn = Callable[[Mapping[str, Any], SolveParams], Sequence[Mapping[str, Any]]]
#: ``critique(placements, params, envelope, footprint_mm2) -> ScoreBreakdown``.
CritiqueFn = Callable[
    [Sequence[RoomPlacement], SolveParams, BuildableEnvelope, int], ScoreBreakdown
]


@dataclass(frozen=True)
class StageSet:
    """The five stage bodies this driver calls, injectable for ortools-free tests.

    Defaults (:func:`default_stage_set`) bind the real implementations in
    :mod:`services.solver.stages` / :mod:`services.solver.critic`. The **relax pass**
    (§5.6 "relax soft weights once") reaches stage A as ``relaxed=True``; a stage A
    that does not accept the keyword cannot honour it, so the driver skips the pass
    and says so in the log rather than re-running an identical solve.
    """

    anchors: AnchorsFn
    stage_a: StageAFn
    stage_b: StageBFn
    build_ops: BuildOpsFn
    compliance: ComplianceFn
    critique: CritiqueFn

    def stage_a_supports_relax(self) -> bool:
        return _accepts_keyword(self.stage_a, "relaxed")


def _accepts_keyword(fn: Callable[..., Any], name: str) -> bool:
    try:
        parameters = inspect.signature(fn).parameters
    except (TypeError, ValueError):  # builtins / C callables: assume permissive
        return True
    if any(p.kind is inspect.Parameter.VAR_KEYWORD for p in parameters.values()):
        return True
    return name in parameters


def _call_with_supported(fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    """Call ``fn`` passing only the keywords its signature accepts.

    This is how one driver spans two stage-A generations: the Phase-2 stub signature
    (``time_budget_seconds``/``num_search_workers``) and the Phase-3 CP-SAT signature
    (``profile``/``relaxed``). Inspected, not try/excepted — a ``TypeError`` raised
    *inside* the stage must never be mistaken for a signature mismatch.
    """
    try:
        parameters = inspect.signature(fn).parameters
    except (TypeError, ValueError):
        return fn(*args, **kwargs)
    if any(p.kind is inspect.Parameter.VAR_KEYWORD for p in parameters.values()):
        return fn(*args, **kwargs)
    return fn(*args, **{k: v for k, v in kwargs.items() if k in parameters})


def default_stage_set() -> StageSet:
    """The real stage bodies. Imported lazily so fakes never load them at all."""
    from services.solver import critic, stages

    compliance = getattr(critic, "evaluate_compliance", None)
    if compliance is None:

        def compliance(
            model: Mapping[str, Any], params: SolveParams
        ) -> Sequence[Mapping[str, Any]]:
            raise NotImplementedError(
                "critic.evaluate_compliance(model, params) is the §5.4 hard-rule pass "
                "and must delegate to the garh_rules engine (rulepacks/). It lands with "
                "the CP-SAT stages; the pipeline only defines the contract."
            )

    def stage_a(
        grid: GridSpec,
        params: SolveParams,
        anchor: StairAnchor,
        *,
        profile: SolverProfile,
        relaxed: bool = False,
        shortfalls: list[Any] | None = None,
    ) -> Candidate | None:
        return _call_with_supported(
            stages.stage_a_topology,
            grid,
            params,
            anchor,
            profile=profile,
            relaxed=relaxed,
            time_budget_seconds=profile.time_budget_seconds or 0,
            num_search_workers=profile.num_search_workers,
            # Collected so a run that produces nothing can say WHY. `_call_with_supported`
            # drops the keyword for a stage implementation that predates it.
            shortfalls=shortfalls,
        )

    def build_ops(
        placements: Sequence[RoomPlacement],
        params: SolveParams,
        *,
        model: Mapping[str, Any] | None = None,
    ) -> Sequence[Mapping[str, Any]]:
        return _call_with_supported(stages.placements_to_ops, placements, params, model=model)

    return StageSet(
        anchors=stages.enumerate_stair_anchors,
        stage_a=stage_a,
        stage_b=stages.stage_b_refine,
        build_ops=build_ops,
        compliance=compliance,
        critique=critic.critique,
    )


# ---------------------------------------------------------------------------
# The program stage (§5 input shaping — pure, real today)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Program:
    """The brief, normalised into per-storey room lists with visible defaults."""

    rooms_by_storey: tuple[tuple[int, tuple[RoomRequest, ...]], ...]
    total_target_area_mm2: int
    total_min_area_mm2: int
    assumptions: tuple[Assumption, ...] = ()
    notes: tuple[str, ...] = ()

    def storey_indexes(self) -> tuple[int, ...]:
        return tuple(index for index, _ in self.rooms_by_storey)


def build_program(params: SolveParams, envelope: BuildableEnvelope) -> Program:
    """Normalise the brief into a per-storey program. **Implemented — pure.**

    Two defaults, both recorded as assumption chips (golden rule 4):

    * rooms without a storey go to the ground floor — entry-adjacent is the safe
      Indian default (living, kitchen and one bath on ground);
    * a program whose minimum areas exceed what the footprint can carry across all
      storeys is *noted*, not silently truncated — stage A will prove infeasibility,
      and the note tells the architect why before the solver spends its budget.
    """
    assumptions: list[Assumption] = []
    notes: list[str] = []

    defaulted = [room.key for room in params.rooms if room.storey_index is None]
    if defaulted:
        assumptions.append(
            Assumption(
                field="brief.rooms.storeyIndex",
                value=0,
                reason=(
                    "The brief didn't say which floor %s should be on, so we placed "
                    "them on the ground floor." % ", ".join(sorted(defaulted))
                ),
                source="solver-program",
            )
        )

    by_storey: dict[int, list[RoomRequest]] = {}
    for room in params.rooms:
        index = room.storey_index if room.storey_index is not None else 0
        by_storey.setdefault(index, []).append(room)

    total_target = sum(max(room.target_area_mm2, room.min_area_mm2) for room in params.rooms)
    total_min = sum(room.min_area_mm2 for room in params.rooms)
    capacity = envelope.effective_footprint_mm2 * max(1, params.storeys)
    if total_min > capacity:
        notes.append(
            "The rooms' minimum areas add up to %d m2 but the footprint can carry "
            "at most %d m2 across %d floor(s) — expect fewer or no options unless "
            "the brief shrinks or a floor is added."
            % (total_min // 1_000_000, capacity // 1_000_000, max(1, params.storeys))
        )

    ordered = tuple((index, tuple(by_storey[index])) for index in sorted(by_storey.keys()))
    return Program(
        rooms_by_storey=ordered,
        total_target_area_mm2=total_target,
        total_min_area_mm2=total_min,
        assumptions=tuple(assumptions),
        notes=tuple(notes),
    )


# ---------------------------------------------------------------------------
# Context
# ---------------------------------------------------------------------------


@dataclass
class SolveContext:
    """Everything the pipeline needs beyond the params themselves.

    Field compatibility note: :mod:`services.solver.handler` (Phase 2) constructs
    this with only the first five fields — every field added since has a default, so
    both call sites stay valid.
    """

    params: SolveParams
    #: ``await progress(stage_id, message, percent=..., **data)``.
    progress: ProgressFn
    #: Raises when the job was cancelled. Called between stages and candidates.
    check_cancelled: Callable[[], None]
    #: Legacy knob, honoured when ``profile`` is None (handler.py still sets it).
    num_search_workers: int = 8
    #: Set by the worker when resuming a retried job (golden rule 9) — the shape is
    #: whatever :func:`run_solver` last passed to ``save_state``.
    resume_state: Mapping[str, Any] | None = None
    #: Thread-safe progress bridge for the CPU-bound part of stage A, which runs in a
    #: worker thread and cannot await. ``None`` outside the worker (tests, scripts).
    progress_from_thread: Callable[..., None] | None = None
    #: Which :class:`SolverProfile` this run uses. ``None`` → production profile with
    #: the legacy ``num_search_workers`` folded in.
    profile: SolverProfile | None = None
    #: Stage bodies. ``None`` → :func:`default_stage_set` (the real solver).
    stages: StageSet | None = None
    #: ``await save_state(state)`` — checkpoint hook; the worker wires it to
    #: :class:`services.common.checkpoint.JobCheckpoint`. Facts only, never percents.
    save_state: Callable[[dict], Awaitable[Any]] | None = None
    #: §5.7 hooks, set by :mod:`services.solver.resolve` and nobody else ----------
    #: Locked-room polygons become fixed obstacles: transform the coarse grid.
    grid_transform: Callable[[GridSpec], GridSpec] | None = None
    #: Post-pass over every stage-B model (shared-wall dedupe, locked side wins).
    #: Returning ``None`` discards the candidate.
    stage_b_post: Callable[[Mapping[str, Any]], Mapping[str, Any] | None] | None = None
    #: Augment placements before scoring/signature (locked rooms re-join the plan).
    placements_augment: Callable[[tuple[RoomPlacement, ...]], tuple[RoomPlacement, ...]] | None = (
        None
    )
    #: Cap on stair candidates (§5.7 budget); ``None`` = stage's own 3–6.
    max_stair_candidates: int | None = None

    def effective_profile(self) -> SolverProfile:
        if self.profile is not None:
            return self.profile
        return replace(PRODUCTION_PROFILE, num_search_workers=self.num_search_workers)

    def stage_set(self) -> StageSet:
        return self.stages if self.stages is not None else default_stage_set()


# ---------------------------------------------------------------------------
# The driver
# ---------------------------------------------------------------------------


async def run_solver(context: SolveContext) -> SolveResult:
    """Run the whole §5 pipeline, emitting a real event at every stage entry."""
    params = context.params
    profile = context.effective_profile()
    stage_set = context.stage_set()
    authority = authority_for_pack(params.profile.city_pack)
    stage_by_id = {entry[0]: entry for entry in STAGES}

    async def announce(stage_id: str, **data: Any) -> None:
        _, message, percent = stage_by_id[stage_id]
        await context.progress(
            stage_id, message.format(authority=authority), percent=percent, **data
        )

    #: (anchor_id, stage, reason) for every candidate that died, in order. Logged as
    #: it happens and surfaced on the diversity event — never silently dropped.
    discards: list[dict[str, str]] = []

    def discard(anchor_id: str, stage: str, reason: str) -> None:
        discards.append({"anchor": anchor_id, "stage": stage, "reason": reason})
        log.info("solver.candidate_discarded", anchor=anchor_id, stage=stage, reason=reason)

    # -- §5.1 envelope (real) -------------------------------------------
    await announce("envelope")
    envelope = derive_envelope(
        params.plot_polygon, params.edges, params.profile, storeys=params.storeys
    )
    await context.progress(
        "envelope",
        "Buildable area: %d m2 across %d floor(s)."
        % (envelope.area_mm2 // 1_000_000, params.storeys),
        percent=6,
        envelopeAreaMm2=envelope.area_mm2,
        assumptionCount=len(envelope.assumptions),
    )
    context.check_cancelled()

    # -- program (real) --------------------------------------------------
    await announce("program", roomCount=len(params.rooms))
    program = build_program(params, envelope)
    for note in program.notes:
        await context.progress("program", note, percent=10)
    context.check_cancelled()

    grid = grid_envelope(envelope)
    if context.grid_transform is not None:
        grid = context.grid_transform(grid)
    log.info(
        "solver.grid",
        cols=grid.cols,
        rows=grid.rows,
        buildable_cells=grid.buildable_cells(),
    )

    # -- §5.2 stairs first ------------------------------------------------
    await announce("stairs")
    anchors = tuple(stage_set.anchors(envelope, params))
    if context.max_stair_candidates is not None:
        anchors = anchors[: max(1, context.max_stair_candidates)]
    context.check_cancelled()

    # -- §5.2 stage A per candidate, parallel within the pool budget -------
    await announce("topology", stairCandidates=len(anchors))
    checkpoint: dict[str, Any] = dict(context.resume_state or {})
    # Why each storey failed, collected across every anchor. Only read when the run
    # ends with nothing to show — then it is the only thing the architect gets.
    shortfalls: list[Any] = []
    candidates = await _solve_candidates(
        context,
        grid,
        anchors,
        profile=profile,
        stage_set=stage_set,
        relaxed=False,
        checkpoint=checkpoint,
        discard=discard,
        shortfalls=shortfalls,
    )

    scored = await _refine_and_score(
        context,
        candidates,
        envelope,
        program,
        profile=profile,
        stage_set=stage_set,
        announce=announce,
        discard=discard,
        relaxed=False,
    )

    await announce(
        "diversity",
        scored=len(scored),
        discarded=len(discards),
        discards=discards[:20],
    )
    result = finalise(scored, envelope, target=params.target_option_count, shortfalls=shortfalls)

    # -- §5.6: relax soft weights ONCE when fewer than target cleared ------
    if gates.should_relax_and_retry(
        len(result.options), already_relaxed=False, target=params.target_option_count
    ):
        if stage_set.stage_a_supports_relax():
            await announce("relax", presentable=len(result.options))
            relaxed_candidates = await _solve_candidates(
                context,
                grid,
                anchors,
                profile=profile,
                stage_set=stage_set,
                relaxed=True,
                checkpoint=None,  # the relax pass is cheap to redo; never checkpointed
                discard=discard,
                shortfalls=shortfalls,
            )
            relaxed_scored = await _refine_and_score(
                context,
                relaxed_candidates,
                envelope,
                program,
                profile=profile,
                stage_set=stage_set,
                announce=announce,
                discard=discard,
                relaxed=True,
            )
            combined = _dedupe_by_id(tuple(scored) + tuple(relaxed_scored))
            result = finalise(
                combined, envelope, target=params.target_option_count, shortfalls=shortfalls
            )
        else:
            log.warning(
                "solver.relax_unsupported",
                hint="stage A does not accept relaxed=; the §5.6 relax pass was "
                "skipped and the honest banner stands",
            )

    # §15: "plan silhouettes appearing as they pass gates" — one artifact per option
    # that actually cleared, published as it is chosen rather than at the end.
    for option in result.options:
        await context.progress(
            "option",
            "Found a plan scoring %d out of 100." % option.scores.composite,
            percent=95,
            artifactName="plan-option",
            optionId=option.id,
            rank=option.rank,
            composite=option.scores.composite,
        )
    log.info(
        "solver.pipeline_done",
        options=len(result.options),
        considered=result.considered,
        rejected=result.rejected_by_gates,
        discarded=len(discards),
    )
    return result


async def _solve_candidates(
    context: SolveContext,
    grid: GridSpec,
    anchors: Sequence[StairAnchor],
    *,
    profile: SolverProfile,
    stage_set: StageSet,
    relaxed: bool,
    checkpoint: dict[str, Any] | None,
    shortfalls: list[Any] | None = None,
    discard: Callable[[str, str, str], None],
) -> list[Candidate]:
    """Stage A once per stair anchor, ``candidate_parallelism`` at a time.

    Results are collected **in anchor order** regardless of completion order, so
    parallelism cannot change the output. Each solved candidate is checkpointed as a
    fact (golden rule 9): a retried job resumes past the anchors already solved.
    """
    params = context.params
    semaphore = asyncio.Semaphore(max(1, profile.candidate_parallelism))
    checkpoint_lock = asyncio.Lock()
    solved_key = "stageA"
    known: dict[str, Any] = dict((checkpoint or {}).get(solved_key) or {})

    async def solve_one(index: int, anchor: StairAnchor) -> Candidate | None:
        if anchor.id in known:
            entry = known[anchor.id]
            log.info("solver.candidate_resumed", anchor=anchor.id)
            return _candidate_from_json(entry) if entry is not None else None
        async with semaphore:
            context.check_cancelled()
            await context.progress(
                "topology",
                STAGES[3][1],
                percent=20 + (30 * index) // max(1, len(anchors)),
                stairCandidate=index + 1,
                stairCandidates=len(anchors),
                relaxed=relaxed or None,
            )
            candidate = await asyncio.to_thread(
                _call_with_supported,
                stage_set.stage_a,
                grid,
                params,
                anchor,
                profile=profile,
                relaxed=relaxed,
                shortfalls=shortfalls,
            )
        if candidate is None:
            discard(anchor.id, "stage-a", "no feasible topology for this stair anchor")
        if checkpoint is not None and context.save_state is not None:
            async with checkpoint_lock:
                stored = dict(checkpoint.get(solved_key) or {})
                stored[anchor.id] = None if candidate is None else _candidate_to_json(candidate)
                checkpoint[solved_key] = stored
                await context.save_state(dict(checkpoint))
        return candidate

    results = await asyncio.gather(
        *(solve_one(index, anchor) for index, anchor in enumerate(anchors))
    )
    context.check_cancelled()
    return [candidate for candidate in results if candidate is not None]


async def _refine_and_score(
    context: SolveContext,
    candidates: Sequence[Candidate],
    envelope: BuildableEnvelope,
    program: Program,
    *,
    profile: SolverProfile,
    stage_set: StageSet,
    announce: Callable[..., Awaitable[Any]],
    discard: Callable[[str, str, str], None],
    relaxed: bool,
) -> list[PlanOption]:
    """§5.3 stage B, then the two critic passes (§5.4), assembling PlanOptions."""
    params = context.params

    # -- §5.3 refinement --------------------------------------------------
    await announce("refine", candidates=len(candidates), relaxed=relaxed or None)
    refined: list[tuple[Candidate, Mapping[str, Any]]] = []
    for candidate in candidates:
        model = stage_set.stage_b(candidate, params, envelope)
        if model is None:
            discard(
                candidate.stair_anchor.id,
                "stage-b",
                "could not be refined onto the 115mm module without breaking an invariant",
            )
            continue
        if context.stage_b_post is not None:
            model = context.stage_b_post(model)
            if model is None:
                discard(
                    candidate.stair_anchor.id,
                    "stage-b",
                    "refinement touched a locked wall (§5.7: locked side wins)",
                )
                continue
        refined.append((candidate, model))
        context.check_cancelled()

    # -- §5.4 critic pass 1: hard rules ------------------------------------
    await announce("critic", refined=len(refined), relaxed=relaxed or None)
    with_compliance: list[tuple[Candidate, Mapping[str, Any], tuple[Mapping[str, Any], ...]]] = []
    for candidate, model in refined:
        rows = tuple(dict(row) for row in stage_set.compliance(model, params))
        failures = [row for row in rows if str(row.get("status")) == "fail"]
        if failures:
            discard(
                candidate.stair_anchor.id,
                "critic",
                "hard rule failures: %s"
                % ", ".join(sorted(str(row.get("ruleId", "?")) for row in failures)[:5]),
            )
            # Kept and gated (not dropped here): §5.6 owns the last word on
            # presentability, and the gate result carries the reasons to the log.
        with_compliance.append((candidate, model, rows))
        context.check_cancelled()

    # -- §5.4 critic pass 2: soft scores (this pass computes the Vastu score) --
    await announce("vastu", candidates=len(with_compliance), relaxed=relaxed or None)
    scored: list[PlanOption] = []
    for candidate, model, compliance_rows in with_compliance:
        placements = tuple(candidate.placements)
        if context.placements_augment is not None:
            placements = context.placements_augment(placements)
        occupied = sum(placement.area_mm2 for placement in placements)
        # Stage A's tiling contract (§5.2) makes circulation rooms placements, so
        # their area is already inside `occupied` and `circulation_area_mm2` merely
        # reports it again as the §5.2 metric. Only the portion NOT represented as
        # a placement (test fakes, older stage-A generations) still adds on top —
        # adding all of it would double-count every real candidate's passages and
        # quietly shrink the §5.6 circulation percentage.
        placed_circulation = sum(
            placement.area_mm2
            for placement in placements
            if placement.room_type in _CIRCULATION_PLACEMENT_TYPES
        )
        footprint = occupied + max(0, candidate.circulation_area_mm2 - placed_circulation)
        breakdown = stage_set.critique(placements, params, envelope, footprint)
        ops = tuple(dict(op) for op in stage_set.build_ops(placements, params, model=model))
        option_signature = signature(
            placements,
            envelope,
            stair_anchor_id=candidate.stair_anchor.id,
            north_deg=params.north_deg,
        )
        option_id = _option_id(params, candidate, option_signature)
        scored.append(
            PlanOption(
                id=option_id,
                rank=0,  # final rank stamped by select_diverse
                scores=breakdown,
                placements=placements,
                ops=ops,
                signature=option_signature,
                stair_anchor_id=candidate.stair_anchor.id,
                built_up_mm2=footprint,
                footprint_mm2=footprint,
                rationale_facts=_rationale_facts(breakdown, option_signature, candidate),
                assumptions=tuple(envelope.assumptions) + tuple(program.assumptions),
                compliance=compliance_rows,
            )
        )
        context.check_cancelled()
    return scored


def finalise(
    scored: Sequence[PlanOption],
    envelope: BuildableEnvelope,
    *,
    target: int = gates.TARGET_OPTION_COUNT,
    shortfalls: Sequence[Any] = (),
) -> SolveResult:
    """Gate, diversify and rank. Pure logic over scored options.

    Order matters and is not arbitrary: gates run *before* diversity so a rejected
    plan can never occupy one of the three slots (golden rule 2), and diversity runs
    before ranking so the survivors are the best *different* plans, not the best three.
    """
    presentable, results = gates.filter_presentable(scored)
    rejected = len(scored) - len(presentable)
    for option_id, result in results.items():
        if not result.passed:
            log.info("solver.gate_rejected", option_id=option_id, reasons=list(result.reasons))

    options = select_diverse(presentable, limit=max(target, gates.TARGET_OPTION_COUNT))
    # With nothing to show, the banner is the only thing the architect gets. Stage A's
    # diagnosis beats the generic count message there: "you are 8 m² short on the ground
    # floor" is something to act on; "0 options" is not.
    banner = gates.banner_for(len(options), target=target)
    if not options and shortfalls:
        from services.solver.diagnose import shortfall_banner

        banner = shortfall_banner(list(shortfalls)) or banner
    return SolveResult(
        options=options,
        envelope=envelope,
        banner=banner,
        considered=len(scored),
        rejected_by_gates=rejected,
    )


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _option_id(params: SolveParams, candidate: Candidate, option_signature: Sequence[str]) -> str:
    """Deterministic option id: same params + seed + layout ⇒ same id, always.

    Content-addressed rather than minted, because §16's goldens compare plan JSON
    byte-for-byte and a random id would make every run a diff.
    """
    payload = json.dumps(
        {
            "seed": params.seed,
            "anchor": candidate.stair_anchor.id,
            "signature": list(option_signature),
            "placements": [
                [p.room_key, p.storey_index, p.x_mm, p.y_mm, p.width_mm, p.depth_mm]
                for p in candidate.placements
            ],
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return "plan_%s" % hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def _rationale_facts(
    breakdown: ScoreBreakdown, option_signature: Sequence[str], candidate: Candidate
) -> tuple[str, ...]:
    """Structured facts for the Phase-6 rationale writer. Facts only, no prose:
    the LLM verbalises these and is forbidden from adding any (§5.5, §10)."""
    facts = [
        "composite:%d" % breakdown.composite,
        "circulationPercent:%d" % breakdown.circulation_percent,
        "vastu:%d" % breakdown.vastu,
        "daylight:%d" % breakdown.daylight,
        "stairAnchor:%s" % candidate.stair_anchor.id,
    ]
    for room_type, zone in sorted(signature_summary(option_signature).items()):
        if room_type != "stair":
            facts.append("zone:%s@%s" % (room_type, zone))
    return tuple(facts)


def _dedupe_by_id(options: Sequence[PlanOption]) -> tuple[PlanOption, ...]:
    """First occurrence wins — the relax pass may re-derive an identical plan."""
    seen: set[str] = set()
    kept: list[PlanOption] = []
    for option in options:
        if option.id in seen:
            continue
        seen.add(option.id)
        kept.append(option)
    return tuple(kept)


def _candidate_to_json(candidate: Candidate) -> dict[str, Any]:
    """Checkpoint form of a stage-A result. Facts, integer mm, small (§ checkpoint.py)."""
    anchor = candidate.stair_anchor
    return {
        "anchor": {
            "id": anchor.id,
            "origin": [anchor.origin[0], anchor.origin[1]],
            "widthMm": anchor.width_mm,
            "edgeIndex": anchor.edge_index,
            "prior": anchor.prior,
        },
        "placements": [
            {
                "key": p.room_key,
                "type": p.room_type,
                "storey": p.storey_index,
                "x": p.x_mm,
                "y": p.y_mm,
                "w": p.width_mm,
                "d": p.depth_mm,
                "roomId": p.room_id,
            }
            for p in candidate.placements
        ],
        "circulationAreaMm2": candidate.circulation_area_mm2,
        "objective": candidate.objective,
    }


def _candidate_from_json(data: Mapping[str, Any]) -> Candidate:
    anchor_raw = data["anchor"]
    anchor = StairAnchor(
        id=str(anchor_raw["id"]),
        origin=(int(anchor_raw["origin"][0]), int(anchor_raw["origin"][1])),
        width_mm=int(anchor_raw["widthMm"]),
        edge_index=int(anchor_raw["edgeIndex"]),
        prior=int(anchor_raw.get("prior", 0)),
    )
    placements = tuple(
        RoomPlacement(
            room_key=str(p["key"]),
            room_type=str(p["type"]),
            storey_index=int(p["storey"]),
            x_mm=int(p["x"]),
            y_mm=int(p["y"]),
            width_mm=int(p["w"]),
            depth_mm=int(p["d"]),
            room_id=p.get("roomId"),
        )
        for p in data.get("placements", [])
    )
    return Candidate(
        stair_anchor=anchor,
        placements=placements,
        circulation_area_mm2=int(data.get("circulationAreaMm2", 0)),
        objective=int(data.get("objective", 0)),
    )


__all__ = [
    "AUTHORITY_BY_PACK",
    "DETERMINISTIC_TEST_PROFILE",
    "PRODUCTION_PROFILE",
    "STAGES",
    "Program",
    "ProgressFn",
    "SolveContext",
    "SolverProfile",
    "StageSet",
    "authority_for_pack",
    "build_program",
    "default_stage_set",
    "finalise",
    "run_solver",
]
