"""``POST /projects/:id/copilot`` — natural language in, a previewable op diff out.

§13 containment boundary — read this before changing anything here
-------------------------------------------------------------------
**This route never writes ops.** It returns a *proposal*: typed ops that already
passed the op-catalog schema, a dry-run fold of the REAL ``garh_model`` core on a
fork of the current document, and the no-new-hard-failure rules diff. Applying the
proposal is the client's act: after the architect reviews the DiffPreview (§12) and
presses Apply, the client dispatches the same ops to ``POST /projects/:id/ops`` with
this proposal's ``groupId`` (one undo group) and ``source: "copilot"`` — the same
sequencer, the same validation, the same single-writer lock as a hand edit. There is
no side door: a prompt injection that survives every gate still only produces a diff
a human declines, and the model's output is data end to end — never executed text,
never geometry it invented (the fold recomputes all derived state).

What this route *does* write: one ``credit_events(kind='llm')`` metering row (the
call spends money whether or not the diff is applied) and the §10 eval-corpus log
line ``{command, opsCount, outcome}``. The applied/rejected half of that log arrives
later via ``POST /projects/:id/copilot/decision``, because only the client knows
what the human chose.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from typing import Any

from fastapi import APIRouter

from garh_api.billing.quotas import require_quota, require_spend_budget
from garh_api.config import get_settings
from garh_api.logging import get_logger
from garh_api.ratelimit import enforce_rate_limit, llm_per_firm_rule
from garh_api.repositories import CreditEventRepository
from garh_api.routers import (
    ApiError,
    SessionDep,
    TenantDep,
    active_branch,
    require_project,
)
from garh_api.routers.ops import load_project_state
from garh_api.schemas.copilot import (
    CopilotCommandIn,
    CopilotDecisionIn,
    CopilotDecisionOut,
    CopilotIssueOut,
    CopilotOpOut,
    CopilotProposeOut,
)

_log = get_logger(__name__)

router = APIRouter(tags=["copilot"])


class CopilotUnavailableError(ApiError):
    """The LLM pipeline (``services/llm`` + ``garh_model``) is not importable here.

    Honest 503, mirroring :class:`~garh_api.routers.ops.ModelEngineUnavailableError`:
    a copilot that answered without its validation pipeline would be the §13 side
    door this module's docstring forbids.
    """

    http_status = 503
    code = "copilot_unavailable"
    action = "The copilot is not loaded on this server. Contact support."


def _pipeline() -> Any:
    """Import the validation pipeline lazily, with one honest error when absent.

    ``services/llm`` lives at the repo root; like the brief parser, this route must
    not take the whole API down at import time when a deployment image mounts only
    ``apps/api``. Unlike the brief parser there is deliberately NO inline fallback:
    a copilot without the dry-run fold would return unvalidated ops, which is the one
    thing this file exists to prevent.
    """
    try:
        from garh_api import copilot_loop

        return copilot_loop
    except ImportError as exc:
        raise CopilotUnavailableError(
            "The copilot pipeline is not installed on this server.",
            extra={"importError": str(exc)},
        ) from exc


@router.post(
    "/projects/{project_id}/copilot",
    response_model=CopilotProposeOut,
    summary="Propose validated ops for a natural-language editing command",
    responses={
        402: {"description": "The plan's LLM allowance for this billing period is spent."},
        429: {"description": "The per-firm LLM budget for this hour is used up."},
        503: {"description": "The model engine or LLM pipeline is unavailable."},
    },
    # 402 before a token is spent. The hourly rule below is a burst ceiling; this is the
    # plan's monthly allowance, counted over the same ``credit_events`` rows this
    # handler writes.
    dependencies=[require_quota("llm"), require_spend_budget("llm")],
)
async def propose(
    project_id: uuid.UUID,
    body: CopilotCommandIn,
    session: SessionDep,
    ctx: TenantDep,
) -> CopilotProposeOut:
    """Command → ``{intent, ops[], plain language}`` | ``needsClarification`` | ``cannotDo``.

    Pipeline (§10): compact PII-free model summary → provider (mock by default) →
    op-catalog schema gate → dry-run fold on a fork → rules diff → ONE self-correction
    on failure → this response. Out-of-scope asks return ``cannotDo`` — never
    approximated ops. **Apply stays client-side through the op sequencer** (see the
    module docstring; that is the §13 containment boundary).
    """
    ctx.require_write("using the copilot")
    await require_project(session, ctx, project_id)
    settings = get_settings()

    # §13: the fail-closed per-firm LLM budget — the SAME rule as brief-parse, because
    # these are the two routes that spend money at a third party on every call.
    # Checked before any state is loaded, so a limited request costs nothing.
    await enforce_rate_limit(
        llm_per_firm_rule(settings, feature="copilot"), "firm:%s" % ctx.firm_id
    )

    loop = _pipeline()
    from services.llm.provider import get_llm_provider
    from services.llm.redaction import strip_pii
    from services.llm.types import LlmError

    # The current document — snapshot + tail through the model engine (503 when the
    # engine is absent; a copilot that cannot fold cannot validate).
    branch = body.version_branch or await active_branch(session, ctx, project_id)
    state = await load_project_state(session, ctx, project_id, branch)

    # Rules context for the prompt (§10): current violations, best effort. The gate
    # inside the pipeline recomputes its own baseline; this is only what the model
    # gets told about.
    violations = _current_violations(state.document)

    provider = get_llm_provider()
    service, folder, gate = loop.build_copilot_service(provider, document=state.document)
    try:
        proposal = await service.propose(
            body.text,
            model=state.document,
            violations=violations,
            active_storey_id=body.active_storey_id,
            selection_ids=body.selection_ids,
        )
    except LlmError as exc:
        # Provider trouble is not a 500: say what happened and what to do next
        # (golden rule 9). Nothing is metered — no answer came back.
        raise ApiError(
            exc.message,
            status=503 if exc.retryable else 502,
            code=exc.code,
            action=exc.action or "Try again in a moment.",
        ) from exc

    outcome = _outcome_of(proposal)

    # §2 metering: the call spent provider budget whether or not the architect
    # applies the diff. `mock` rows cost nothing and stay distinguishable.
    await CreditEventRepository(session, ctx).record(
        kind="llm",
        qty=1,
        meta={
            "route": "copilot",
            "projectId": str(project_id),
            "provider": provider.name,
            "model": provider.model,
            "outcome": outcome,
            "opsCount": len(proposal.ops),
            **proposal.usage.to_json(),
        },
    )

    # §10: "Log {command, ops, applied|rejected|invalid} for the eval set." This line
    # is the proposal half; /copilot/decision records the human half. The command is
    # user-typed free text, so obvious identifiers are masked before it is logged.
    _log.info(
        "copilot.command",
        project_id=str(project_id),
        dry_run_ms=round(folder.last_duration_ms, 3),
        rules_checked=gate.available,
        **proposal.log_record(strip_pii(body.text), outcome),
    )

    descriptions = list(proposal.plain_language)
    return CopilotProposeOut(
        outcome=outcome,
        intent=proposal.intent,
        ops=[
            CopilotOpOut(
                type=str(op.get("type")),
                payload=dict(op.get("payload") or {}),
                description=descriptions[index] if index < len(descriptions) else "",
            )
            for index, op in enumerate(proposal.ops)
        ],
        needs_clarification=proposal.needs_clarification,
        cannot_do=proposal.cannot_do,
        issues=[
            CopilotIssueOut(
                code=issue.code,
                message=issue.message,
                severity=issue.severity,
                element_ids=list(issue.element_ids),
                field=issue.field,
            )
            for issue in proposal.issues
        ],
        group_id=uuid.uuid4(),
        base_idx=state.head_idx,
        version_branch=branch,
        provider=provider.name,
        attempts=proposal.attempts,
        self_corrected=proposal.self_corrected,
        rules_checked=gate.available,
        dry_run_ms=round(folder.last_duration_ms, 3),
    )


@router.post(
    "/projects/{project_id}/copilot/decision",
    response_model=CopilotDecisionOut,
    summary="Record whether a copilot proposal was applied or rejected",
)
async def record_decision(
    project_id: uuid.UUID,
    body: CopilotDecisionIn,
    session: SessionDep,
    ctx: TenantDep,
) -> CopilotDecisionOut:
    """The human half of the §10 eval log.

    The apply itself already went through ``POST /ops`` (or didn't — that is the
    point of reject). This endpoint writes a log line and nothing else: no ops, no
    metering, no state. It exists so the eval corpus learns which proposals real
    architects accepted.
    """
    ctx.require_write("using the copilot")
    await require_project(session, ctx, project_id)
    try:
        from services.llm.redaction import strip_pii
    except ImportError:  # pragma: no cover - decision logging must not need the LLM stack

        def strip_pii(text: str) -> str:
            return text

    _log.info(
        "copilot.decision",
        project_id=str(project_id),
        command=strip_pii(body.command),
        outcome=body.outcome,
        opCount=body.ops_count,
        groupId=str(body.group_id) if body.group_id else None,
        intent=strip_pii(body.intent) if body.intent else None,
    )
    return CopilotDecisionOut(logged=True)


def _outcome_of(proposal: Any) -> str:
    """§10's outcome classes, as one word the eval log and the client key on."""
    if proposal.applicable:
        return "ops"
    if proposal.cannot_do:
        return "cannotDo"
    if proposal.needs_clarification:
        return "needsClarification"
    return "invalid"


def _current_violations(document: dict) -> Sequence[dict]:
    """Open fail/warn findings for the prompt's rules context. Never raises."""
    try:
        from garh_api.compliance import (
            ComplianceUnavailable,
            cannot_evaluate_reason,
            evaluate_document,
        )
    except ImportError:  # pragma: no cover - same image as the API
        return ()
    if cannot_evaluate_reason(document) is not None:
        return ()
    try:
        payload, _versions = evaluate_document(document)
    except ComplianceUnavailable:
        return ()
    return [
        row
        for row in payload.get("results") or []
        if isinstance(row, dict) and row.get("status") in ("fail", "warn")
    ]


__all__ = ["CopilotUnavailableError", "router"]
