"""The §5 pipeline driver, proven with fake stage bodies (no OR-Tools).

What matters here and why:

* **§15 generation theater is a contract, not vibes** — the staged messages
  ("Placing staircase…", "Packing rooms…", "Checking BBMP setbacks…", "Scoring
  Vastu…") must be emitted at real stage entry, in order, with non-decreasing
  percentages. These tests pin the copy and the ordering.
* **§5.6 is law** — a low-composite or hard-fail candidate never becomes an option;
  when fewer than three clear, soft weights relax exactly ONCE; the banner is honest.
* **Determinism** — the same params and fakes produce byte-identical result JSON,
  because §16's goldens compare with tolerance 0.
* **Resumability** — solved stair candidates are checkpointed as facts and a resumed
  run re-solves none of them.

Tests are synchronous functions driving the async pipeline via ``asyncio.run`` —
runnable by pytest in CI and by a bare ``python3`` runner where pytest is absent.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any, Mapping, Optional, Sequence

from services.solver import gates
from services.solver.pipeline import (
    DETERMINISTIC_TEST_PROFILE,
    PRODUCTION_PROFILE,
    STAGES,
    SolveContext,
    SolverProfile,
    StageSet,
    authority_for_pack,
    build_program,
    run_solver,
)
from services.solver.stages import Candidate, GridSpec
from services.solver.types import (
    PlotEdge,
    RegProfile,
    RoomPlacement,
    RoomRequest,
    ScoreBreakdown,
    SolveParams,
    StairAnchor,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

#: 18 x 18 m plot, 1.5 m setbacks all round → a 15 x 15 m envelope whose 3x3 zone
#: thirds are exactly 5000 mm — so anchor-indexed x-offsets land in distinct zones.
PLOT = ((0, 0), (18_000, 0), (18_000, 18_000), (0, 18_000))
EDGES = tuple(
    PlotEdge(index=i, role=("front" if i == 0 else "side"), setback_mm=1_500)
    for i in range(4)
)
PROFILE_BLR = RegProfile(
    city_pack="blr", coverage_percent=60, far_x100=175, max_height_mm=10_000, max_floors=3
)


def make_params(**overrides: Any) -> SolveParams:
    base: dict[str, Any] = dict(
        plot_polygon=PLOT,
        edges=EDGES,
        profile=PROFILE_BLR,
        rooms=(
            RoomRequest("living", "living", 12_000_000, 16_000_000, 3_000),
            RoomRequest("kitchen", "kitchen", 5_000_000, 9_000_000, 1_800, is_wet=True),
            RoomRequest("master", "bedroom_master", 9_500_000, 16_000_000, 2_400),
        ),
        storeys=1,
        seed=7,
    )
    base.update(overrides)
    return SolveParams(**base)


def placements_for(anchor_index: int) -> tuple[RoomPlacement, ...]:
    """Three rooms shifted a full zone-third per anchor, so signatures diverge."""
    x0 = 1_500 + anchor_index * 5_000
    return (
        RoomPlacement("living", "living", 0, x0, 1_500, 4_000, 4_000),
        RoomPlacement("kitchen", "kitchen", 0, x0, 5_800, 3_000, 3_000),
        RoomPlacement("master", "bedroom_master", 0, x0, 9_100, 4_000, 4_000),
    )


class FakeSolver:
    """Configurable pure-Python stage bodies implementing the StageSet contract."""

    def __init__(
        self,
        *,
        anchor_count: int = 3,
        composite_by_index: Optional[dict[int, int]] = None,
        stage_a_none: Sequence[str] = (),
        stage_a_relaxed_ok: Sequence[str] = (),
        stage_b_none: Sequence[str] = (),
        compliance_fail: Sequence[str] = (),
        supports_relax: bool = True,
    ) -> None:
        self.anchor_count = anchor_count
        self.composite_by_index = composite_by_index or {}
        self.stage_a_none = set(stage_a_none)
        self.stage_a_relaxed_ok = set(stage_a_relaxed_ok)
        self.stage_b_none = set(stage_b_none)
        self.compliance_fail = set(compliance_fail)
        self.supports_relax = supports_relax
        self.stage_a_calls: list[tuple[str, bool, SolverProfile]] = []
        self.stage_b_calls: list[str] = []

    # -- stage bodies ------------------------------------------------------
    def anchors(self, envelope: Any, params: SolveParams) -> tuple[StairAnchor, ...]:
        return tuple(
            StairAnchor(id="st%d" % i, origin=(1_500, 1_500), width_mm=1_000, edge_index=0, prior=i)
            for i in range(self.anchor_count)
        )

    def _solve(
        self, grid: GridSpec, params: SolveParams, anchor: StairAnchor, profile: SolverProfile, relaxed: bool
    ) -> Optional[Candidate]:
        self.stage_a_calls.append((anchor.id, relaxed, profile))
        if not relaxed and anchor.id in self.stage_a_none:
            return None
        if relaxed and self.stage_a_relaxed_ok and anchor.id not in self.stage_a_relaxed_ok:
            return None
        index = int(anchor.id[2:])
        return Candidate(
            stair_anchor=anchor,
            placements=placements_for(index),
            circulation_area_mm2=2_000_000,
            objective=1_000 - index,
        )

    def stage_a(
        self,
        grid: GridSpec,
        params: SolveParams,
        anchor: StairAnchor,
        *,
        profile: SolverProfile,
        relaxed: bool = False,
    ) -> Optional[Candidate]:
        return self._solve(grid, params, anchor, profile, relaxed)

    def stage_a_no_relax(
        self, grid: GridSpec, params: SolveParams, anchor: StairAnchor, *, profile: SolverProfile
    ) -> Optional[Candidate]:
        """A stage A that predates the relax keyword — the driver must cope."""
        return self._solve(grid, params, anchor, profile, False)

    def stage_b(
        self, candidate: Candidate, params: SolveParams, envelope: Any
    ) -> Optional[Mapping[str, Any]]:
        self.stage_b_calls.append(candidate.stair_anchor.id)
        if candidate.stair_anchor.id in self.stage_b_none:
            return None
        return {"walls": [], "anchor": candidate.stair_anchor.id}

    def build_ops(
        self, placements: Sequence[RoomPlacement], params: SolveParams, *, model: Any = None
    ) -> list[dict[str, Any]]:
        return [
            {"type": "room.assign", "payload": {"key": p.room_key}} for p in placements
        ]

    def compliance(
        self, model: Mapping[str, Any], params: SolveParams
    ) -> list[dict[str, Any]]:
        anchor = str(model.get("anchor"))
        if anchor in self.compliance_fail:
            return [{"ruleId": "blr.setback.front.9m", "status": "fail"}]
        return [{"ruleId": "nbc.room.area", "status": "pass"}]

    def critique(
        self,
        placements: Sequence[RoomPlacement],
        params: SolveParams,
        envelope: Any,
        footprint_mm2: int,
    ) -> ScoreBreakdown:
        index = (placements[0].x_mm - 1_500) // 5_000
        composite = self.composite_by_index.get(index, 80)
        occupied = sum(p.area_mm2 for p in placements)
        circulation = max(0, footprint_mm2 - occupied)
        percent = (circulation * 100) // footprint_mm2 if footprint_mm2 else 0
        return ScoreBreakdown(
            target_area_fit=90,
            adjacency=80,
            circulation=100,
            daylight=70,
            vastu=65,
            furniture_fit=100,
            plumbing_stack=60,
            privacy=75,
            compactness=70,
            composite=composite,
            circulation_percent=percent,
        )

    def stage_set(self, *, relax_capable: bool = True) -> StageSet:
        return StageSet(
            anchors=self.anchors,
            stage_a=self.stage_a if (relax_capable and self.supports_relax) else self.stage_a_no_relax,
            stage_b=self.stage_b,
            build_ops=self.build_ops,
            compliance=self.compliance,
            critique=self.critique,
        )


class Recorder:
    """Progress recorder — the §15 assertions read this."""

    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    async def __call__(self, stage: str, message: str, percent: Any = None, **data: Any) -> None:
        self.events.append(
            {"stage": stage, "message": message, "percent": percent, "data": data}
        )

    def stage_ids(self) -> list[str]:
        return [event["stage"] for event in self.events]

    def messages(self) -> list[str]:
        return [event["message"] for event in self.events]


def make_context(fake: FakeSolver, **overrides: Any) -> tuple[SolveContext, Recorder]:
    recorder = Recorder()
    fields: dict[str, Any] = dict(
        params=make_params(),
        progress=recorder,
        check_cancelled=lambda: None,
        profile=DETERMINISTIC_TEST_PROFILE,
        stages=fake.stage_set(),
    )
    fields.update(overrides)
    return SolveContext(**fields), recorder


def solve(context: SolveContext) -> Any:
    return asyncio.run(run_solver(context))


# ---------------------------------------------------------------------------
# §15 — staged, honest progress
# ---------------------------------------------------------------------------


def test_stages_fire_in_order_with_section_15_copy() -> None:
    context, recorder = make_context(FakeSolver())
    result = solve(context)
    assert len(result.options) == 3

    ids = recorder.stage_ids()
    order = [
        ids.index("envelope"),
        ids.index("program"),
        ids.index("stairs"),
        ids.index("topology"),
        ids.index("refine"),
        ids.index("critic"),
        ids.index("vastu"),
        ids.index("diversity"),
    ]
    assert order == sorted(order), "stages must be announced at entry, in §5 order"

    messages = recorder.messages()
    assert "Placing staircase…" in messages
    assert "Packing rooms…" in messages
    assert "Checking BBMP setbacks…" in messages, "blr pack ⇒ BBMP in the copy (§15)"
    assert "Scoring Vastu…" in messages


def test_authority_in_critic_copy_tracks_the_city_pack() -> None:
    assert authority_for_pack("blr") == "BBMP"
    assert authority_for_pack("hyd") == "GHMC"
    assert authority_for_pack("ncr") == "DDA"
    assert authority_for_pack("something-else") == "NBC"

    fake = FakeSolver()
    context, recorder = make_context(fake)
    context.params = make_params(
        profile=RegProfile(
            city_pack="hyd", coverage_percent=60, far_x100=175, max_height_mm=10_000, max_floors=3
        )
    )
    solve(context)
    assert "Checking GHMC setbacks…" in recorder.messages()


def test_percentages_never_decrease() -> None:
    context, recorder = make_context(FakeSolver())
    solve(context)
    percents = [event["percent"] for event in recorder.events if event["percent"] is not None]
    assert percents, "the pipeline must report stage-boundary percentages"
    assert percents == sorted(percents), "a progress bar that goes backwards is a lie"


def test_one_artifact_event_per_presented_option() -> None:
    context, recorder = make_context(FakeSolver())
    result = solve(context)
    artifacts = [e for e in recorder.events if e["data"].get("artifactName") == "plan-option"]
    assert len(artifacts) == len(result.options)
    assert {a["data"]["optionId"] for a in artifacts} == {o.id for o in result.options}


def test_stage_table_is_the_single_source_of_copy() -> None:
    ids = [entry[0] for entry in STAGES]
    assert ids == [
        "envelope", "program", "stairs", "topology", "refine",
        "critic", "vastu", "diversity", "relax",
    ]
    percents = [entry[2] for entry in STAGES]
    assert percents == sorted(percents)


# ---------------------------------------------------------------------------
# Options, scores, rationale seeds
# ---------------------------------------------------------------------------


def test_options_are_ranked_scored_and_carry_rationale_seeds() -> None:
    context, _ = make_context(FakeSolver())
    result = solve(context)
    assert [option.rank for option in result.options] == [0, 1, 2]
    composites = [option.scores.composite for option in result.options]
    assert composites == sorted(composites, reverse=True)
    for option in result.options:
        assert option.ops, "an option must carry the §4 ops that build it"
        assert option.compliance, "the critic's rules results ride along"
        assert any(fact.startswith("composite:") for fact in option.rationale_facts)
        assert any(fact.startswith("zone:") for fact in option.rationale_facts)
        assert all(":" in fact for fact in option.rationale_facts), (
            "rationale seeds are structured facts, never prose (§5.5)"
        )


def test_result_json_is_deterministic() -> None:
    first = solve(make_context(FakeSolver())[0])
    second = solve(make_context(FakeSolver())[0])
    assert json.dumps(first.to_json(), sort_keys=True) == json.dumps(
        second.to_json(), sort_keys=True
    ), "same params + seed must produce byte-identical plan JSON (§16 goldens)"


def test_options_are_diverse_not_near_duplicates() -> None:
    result = solve(make_context(FakeSolver())[0])
    signatures = [option.signature for option in result.options]
    assert len(set(signatures)) == len(signatures)


# ---------------------------------------------------------------------------
# §5.6 gates — never show a hard-fail plan
# ---------------------------------------------------------------------------


def test_low_composite_candidate_is_gated_out_with_honest_banner() -> None:
    fake = FakeSolver(composite_by_index={2: 40})  # third anchor scores below 55
    context, _ = make_context(fake)
    result = solve(context)
    assert len(result.options) == 2
    assert result.rejected_by_gates == 1
    assert result.banner == "2 strong options found for this plot."


def test_hard_rule_failure_never_becomes_an_option() -> None:
    fake = FakeSolver(compliance_fail=("st0",))
    context, _ = make_context(fake)
    result = solve(context)
    assert all(option.stair_anchor_id != "st0" for option in result.options)
    assert result.rejected_by_gates >= 1


# ---------------------------------------------------------------------------
# Discard reasons — logged per candidate, surfaced on the diversity event
# ---------------------------------------------------------------------------


def test_infeasible_stage_a_candidate_is_discarded_with_reason() -> None:
    fake = FakeSolver(stage_a_none=("st1",))
    context, recorder = make_context(fake)
    result = solve(context)
    diversity = [e for e in recorder.events if e["stage"] == "diversity"][0]
    discards = diversity["data"]["discards"]
    assert any(d["anchor"] == "st1" and d["stage"] == "stage-a" for d in discards)
    # The §5.6 relax pass then recovers st1 (soft weights loosened), so the final
    # answer is still three options — the discard is a logged fact, not a loss.
    assert len(result.options) == 3
    assert result.considered == 3


def test_unrefinable_candidate_is_discarded_with_reason() -> None:
    fake = FakeSolver(stage_b_none=("st2",))
    context, recorder = make_context(fake)
    result = solve(context)
    assert result.considered == 2, "st2 cannot refine even relaxed"
    diversity = [e for e in recorder.events if e["stage"] == "diversity"][0]
    assert any(
        d["anchor"] == "st2" and d["stage"] == "stage-b"
        for d in diversity["data"]["discards"]
    )
    assert result.banner == "2 strong options found for this plot."


# ---------------------------------------------------------------------------
# §5.6 relax-once
# ---------------------------------------------------------------------------


def test_relax_pass_runs_exactly_once_and_recovers_options() -> None:
    # Pass 1: only st0 solves. Relaxed pass: every anchor solves.
    fake = FakeSolver(stage_a_none=("st1", "st2"), stage_a_relaxed_ok=("st0", "st1", "st2"))
    context, recorder = make_context(fake)
    result = solve(context)

    relax_events = [e for e in recorder.events if e["stage"] == "relax"]
    assert len(relax_events) == 1, "§5.6: relax soft weights ONCE"
    relaxed_calls = [call for call in fake.stage_a_calls if call[1]]
    assert len(relaxed_calls) == 3, "the relaxed pass re-solves every anchor"
    assert len(result.options) == 3
    assert result.banner is None


def test_relax_does_not_loop_when_still_short() -> None:
    fake = FakeSolver(stage_a_none=("st1", "st2"), stage_a_relaxed_ok=("st0",))
    context, recorder = make_context(fake)
    result = solve(context)
    assert len([e for e in recorder.events if e["stage"] == "relax"]) == 1
    assert len(result.options) == 1
    assert result.banner == "1 strong option found for this plot."


def test_relax_skipped_when_stage_a_cannot_honour_it() -> None:
    fake = FakeSolver(stage_a_none=("st1", "st2"))
    context, recorder = make_context(fake, stages=fake.stage_set(relax_capable=False))
    result = solve(context)
    assert not [e for e in recorder.events if e["stage"] == "relax"], (
        "re-running an identical solve is not a relax pass; skip and stay honest"
    )
    assert len(result.options) == 1
    assert result.banner == "1 strong option found for this plot."


# ---------------------------------------------------------------------------
# Resumability (golden rule 9)
# ---------------------------------------------------------------------------


def test_checkpointed_candidates_are_not_resolved_on_resume() -> None:
    saved: list[dict[str, Any]] = []

    async def save_state(state: dict) -> None:
        saved.append(json.loads(json.dumps(state)))  # deep copy via the wire format

    first_fake = FakeSolver()
    context, _ = make_context(first_fake, save_state=save_state)
    first = solve(context)
    assert saved, "each solved stair candidate must be checkpointed as a fact"
    assert len(first_fake.stage_a_calls) == 3

    resumed_fake = FakeSolver()
    resumed_context, _ = make_context(resumed_fake, resume_state=saved[-1])
    second = solve(resumed_context)
    assert resumed_fake.stage_a_calls == [], "a resumed job re-solves no solved anchor"
    assert json.dumps(first.to_json(), sort_keys=True) == json.dumps(
        second.to_json(), sort_keys=True
    ), "resume must change cost, never output"


def test_resume_state_also_replays_infeasible_anchors() -> None:
    saved: list[dict[str, Any]] = []

    async def save_state(state: dict) -> None:
        saved.append(json.loads(json.dumps(state)))

    # target_option_count=2 keeps the relax pass out of the picture: this test is
    # about the checkpoint, and a relax pass never reads or writes checkpoints.
    params = make_params(target_option_count=2)
    fake = FakeSolver(stage_a_none=("st1",))
    context, _ = make_context(fake, save_state=save_state, params=params)
    solve(context)

    resumed_fake = FakeSolver(stage_a_none=("st1",))
    resumed_context, _ = make_context(resumed_fake, resume_state=saved[-1], params=params)
    result = solve(resumed_context)
    assert resumed_fake.stage_a_calls == []
    assert result.considered == 2, "a checkpointed 'infeasible' is a fact too"


# ---------------------------------------------------------------------------
# Cancellation
# ---------------------------------------------------------------------------


class _Cancelled(Exception):
    pass


def test_cancellation_between_stages_propagates() -> None:
    calls = {"n": 0}

    def check_cancelled() -> None:
        calls["n"] += 1
        if calls["n"] > 2:
            raise _Cancelled()

    context, _ = make_context(FakeSolver(), check_cancelled=check_cancelled)
    try:
        solve(context)
    except _Cancelled:
        pass
    else:
        raise AssertionError("cancellation must abort the pipeline between stages")


# ---------------------------------------------------------------------------
# The program stage
# ---------------------------------------------------------------------------


def test_build_program_defaults_storeys_visibly() -> None:
    from services.solver.envelope import derive_envelope

    params = make_params(
        rooms=(
            RoomRequest("living", "living", 12_000_000, 16_000_000, 3_000),
            RoomRequest("master", "bedroom_master", 9_500_000, 16_000_000, 2_400, storey_index=1),
        ),
        storeys=2,
    )
    envelope = derive_envelope(params.plot_polygon, params.edges, params.profile, storeys=2)
    program = build_program(params, envelope)
    assert program.storey_indexes() == (0, 1)
    assert len(program.assumptions) == 1, "a defaulted storey is a visible chip (rule 4)"
    assert "living" in program.assumptions[0].reason


def test_build_program_notes_an_overfull_brief() -> None:
    from services.solver.envelope import derive_envelope

    params = make_params(
        rooms=(RoomRequest("hall", "living", 400_000_000, 400_000_000, 3_000),),
    )
    envelope = derive_envelope(params.plot_polygon, params.edges, params.profile, storeys=1)
    program = build_program(params, envelope)
    assert program.notes, "an impossible program is said out loud before the solve"


# ---------------------------------------------------------------------------
# Profiles — determinism is configuration
# ---------------------------------------------------------------------------


def test_profiles_match_the_spec() -> None:
    assert PRODUCTION_PROFILE.num_search_workers == 8, "§5.2 verbatim"
    assert PRODUCTION_PROFILE.time_budget_seconds == 15, "§5.2 verbatim"

    assert DETERMINISTIC_TEST_PROFILE.num_search_workers == 1
    assert DETERMINISTIC_TEST_PROFILE.random_seed is not None
    assert DETERMINISTIC_TEST_PROFILE.time_budget_seconds is None, (
        "wall-clock budgets are machine-dependent; tests use solution/branch limits"
    )
    assert DETERMINISTIC_TEST_PROFILE.max_solutions is not None
    assert DETERMINISTIC_TEST_PROFILE.max_branches is not None
    assert DETERMINISTIC_TEST_PROFILE.candidate_parallelism == 1


def test_stage_a_receives_the_context_profile() -> None:
    fake = FakeSolver()
    context, _ = make_context(fake)
    solve(context)
    assert all(call[2] is DETERMINISTIC_TEST_PROFILE for call in fake.stage_a_calls)


def test_gate_thresholds_are_the_spec_numbers() -> None:
    assert gates.MAX_CIRCULATION_PERCENT == 18
    assert gates.MIN_COMPOSITE_SCORE == 55
    assert gates.TARGET_OPTION_COUNT == 3


# ---------------------------------------------------------------------------
# Bare-python runner (pytest is not installed on the build machine)
# ---------------------------------------------------------------------------

if __name__ == "__main__":  # pragma: no cover
    import sys
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
