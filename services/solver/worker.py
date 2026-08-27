"""``python -m services.solver.worker`` — the solver queue consumer (§5).

Consumes ``QUEUE_SOLVER`` (default ``garh:queue:solver``) through the shared runtime
in :mod:`services.common.runtime` and handles ``solver.generate`` (full §5 pipeline)
and ``solver.resolve`` (§5.7 partial re-solve).

**Where job rows get written — and why not here.** The worker holds no database
connection (``services/common/jobstore.py`` states the three reasons: stateless
workers, one writer per row, durable delivery). Every lifecycle transition this
process emits lands on the ``garh:events:jobs`` Redis Stream, and the API-side
consumer — ``garh_api.routers.jobs.consume_job_events`` — turns them into
``SolverJobRepository`` calls: ``started → mark_running``, ``succeeded →
succeed(options)`` (which persists the options JSON), ``failed``/``dead_lettered`` →
``fail(user-facing copy)``, ``cancelled → cancel``. That repository layer is the one
writer of ``solver_jobs``; this worker's whole persistence contract is "publish
honest events".

**Honest failure states.** :class:`~services.common.errors.InvalidJobError` (bad
payload, impossible envelope) fails permanently on attempt 1 with copy the architect
can act on; unexpected exceptions retry with backoff and then dead-letter — all
handled by the runtime, none of it re-implemented here.

**Resumability** (golden rule 9). The handler keys a
:class:`~services.common.checkpoint.JobCheckpoint` by the payload's ``inputs_hash``
and hands the pipeline a ``save_state`` hook; every solved stair candidate is
checkpointed as a fact, so attempt 2 of a retried job resumes past the anchors
attempt 1 already solved instead of re-spending their CP-SAT budget. A changed
payload changes the hash and the checkpoint is ignored — stale plans cannot leak
into a resumed solve.
"""

from __future__ import annotations

import asyncio
import sys
from dataclasses import replace
from typing import Any

from services.common.checkpoint import inputs_hash
from services.common.config import get_worker_settings
from services.common.errors import InvalidJobError
from services.common.jobstore import JobResult
from services.common.logging import configure_worker_logging, get_logger
from services.common.runtime import JobContext, run_worker
from services.solver import resolve as resolve_mod
from services.solver.envelope import EnvelopeError
from services.solver.handler import DEFAULT_TIMEOUT_SECONDS, SolverJobHandler, _parse_params
from services.solver.pipeline import PRODUCTION_PROFILE, SolveContext, run_solver

log = get_logger("solver.worker")

#: §5.7: a partial re-solve is an *edit* — ≤15s of CP-SAT plus parse/refine/critic
#: overhead. The wall clock is deliberately looser than the solve budget so the job
#: dies from "the solve took too long" (an honest, resumable timeout) rather than
#: from clock skew between the two limits.
RESOLVE_TIMEOUT_SECONDS = 60


class SolverQueueHandler(SolverJobHandler):
    """The Phase-3 handler: per-kind budgets, checkpoints, and the §5.7 route.

    Extends the Phase-2 :class:`~services.solver.handler.SolverJobHandler` rather
    than replacing it: payload parsing (`_parse_params`) and the progress/thread
    bridge stay the single implementations the Phase-2 tests already pin.
    """

    kinds = ("solver.generate", "solver.resolve")

    def timeout_for(self, ctx: JobContext) -> int:
        """Per-JOB wall-clock budget, resolved by the runner before the handler runs
        (the Phase-2 pattern: assigning ``self.timeout_seconds`` inside ``handle``
        would be one job late and race across concurrent jobs)."""
        if ctx.envelope.kind == "solver.resolve":
            return RESOLVE_TIMEOUT_SECONDS
        return DEFAULT_TIMEOUT_SECONDS

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

        # §5.2 production numbers come from settings (already defaulted to 8 / 15s);
        # the deterministic profile is for tests only and is never selected here.
        profile = replace(
            PRODUCTION_PROFILE,
            num_search_workers=ctx.settings.solver_num_search_workers,
            time_budget_seconds=ctx.settings.solver_time_budget_seconds,
        )

        # Resumability: checkpoint keyed by the payload hash (checkpoint.py rules —
        # facts only, refuse to resume when the inputs changed).
        payload_hash = inputs_hash(dict(ctx.payload))
        resume_state = await ctx.checkpoint.load(inputs_hash=payload_hash)

        async def save_state(state: dict) -> None:
            await ctx.checkpoint.save(state, inputs_hash=payload_hash)

        context = SolveContext(
            params=params,
            progress=progress,
            check_cancelled=ctx.raise_if_cancelled,
            num_search_workers=ctx.settings.solver_num_search_workers,
            resume_state=resume_state or None,
            progress_from_thread=progress_from_thread,
            profile=profile,
            save_state=save_state,
        )

        extra: dict[str, Any] = {}
        try:
            if ctx.envelope.kind == "solver.resolve":
                locked = resolve_mod.parse_locked_rooms(ctx.payload)
                outcome = await resolve_mod.run_resolve(
                    context,
                    locked,
                    previous_rooms=resolve_mod.parse_previous_rooms(ctx.payload),
                )
                result = outcome.result
                extra = outcome.to_extra_data()
            else:
                result = await run_solver(context)
        except EnvelopeError as exc:
            raise InvalidJobError(exc.message, action=exc.action, detail=exc.detail) from exc

        data: dict[str, Any] = result.to_json()
        data.update(extra)
        log.info(
            "solver.job.done",
            job_kind=ctx.envelope.kind,
            option_count=len(result.options),
            considered=result.considered,
            rejected=result.rejected_by_gates,
            resumed=bool(resume_state),
        )
        message = (
            result.banner if result.banner else "Generated %d plan options." % len(result.options)
        )
        return JobResult(data=data, message=message)


def main() -> int:
    settings = get_worker_settings()
    configure_worker_logging(settings)

    try:
        from ortools.sat.python import cp_model  # noqa: F401 - import is the check
    except ImportError as exc:
        log.error(
            "solver.worker.missing_dependency",
            error=str(exc),
            hint="ortools is a base dependency of garh-services; run "
            "`pip install -e services/` (it is Apache-2.0, no GPU needed)",
        )
        return 2

    log.info(
        "solver.worker.boot",
        num_search_workers=settings.solver_num_search_workers,
        time_budget_seconds=settings.solver_time_budget_seconds,
        generate_timeout_seconds=DEFAULT_TIMEOUT_SECONDS,
        resolve_timeout_seconds=RESOLVE_TIMEOUT_SECONDS,
    )
    return run_worker(name="solver", handler=SolverQueueHandler())


if __name__ == "__main__":
    sys.exit(main())
