"""Compliance overrides — golden rule 5: "architects can override anything; overrides
are logged."

The rules engine has supported rule acknowledgements since Phase 2:
``plot.regProfile.overrides[ruleId] = {reason, byUserId, at}`` marks a row
``overridden``, keeps it in the report (and in the annexure), and takes it out of the
solver gate's ``blocking_failures``. Nothing wrote one. The Compliance tab promised
"the override is logged, not prevented" over a list with no control behind it.

These two routes are the control. They follow ``PATCH /plot``'s precedent rather than
letting the browser dispatch the op itself, for three reasons:

* **who and when are stamped by the server.** A client-authored ``byUserId`` is a
  claim; this one is the session's.
* **the audit row and the op land in one transaction.** ``compliance.overridden`` /
  ``compliance.override_revoked`` are §13 audit actions; emitting them from the ops
  route would mean sniffing payloads for a reserved shape.
* **the rule id is validated against the packs the document loads.** An
  acknowledgement for a rule no pack carries would sit in the profile forever, doing
  nothing, looking like a decision.

The op still goes through the op log (``dispatch_ops``) — an override is part of the
design's history, undone by revoking it, visible on every version after it.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, status
from pydantic import Field, StrictStr, field_validator

from garh_api.compliance import ComplianceUnavailable, pack_rule_ids
from garh_api.logging import get_logger
from garh_api.repositories import AuditLogRepository
from garh_api.repositories.audit_log import (
    ACTION_COMPLIANCE_OVERRIDDEN,
    ACTION_COMPLIANCE_OVERRIDE_REVOKED,
)
from garh_api.routers import ApiError, SessionDep, TenantDep, active_branch, require_project
from garh_api.routers.ops import dispatch_ops, load_project_state
from garh_api.schemas import CamelModel, ResponseModel
from garh_api.schemas.ops import OpIn

router = APIRouter(tags=["compliance"])
_log = get_logger(__name__)

#: Reserved key inside ``regProfile.overrides`` that is NOT a rule id (the plot
#: panel's value overrides live under it). Mirrors ``garh_rules.context``.
_VALUE_OVERRIDES_KEY = "values"

REASON_MIN = 3
REASON_MAX = 500


class ComplianceOverrideIn(CamelModel):
    """``POST /projects/:id/compliance/overrides`` — accept one failing rule."""

    rule_id: StrictStr = Field(min_length=1, max_length=120)
    reason: StrictStr = Field(min_length=REASON_MIN, max_length=REASON_MAX)

    @field_validator("reason")
    @classmethod
    def _reason_has_words(cls, value: str) -> str:
        cleaned = " ".join(value.split())
        if len(cleaned) < REASON_MIN:
            raise ValueError(
                "Give a reason a reviewer could read — at least %d characters." % REASON_MIN
            )
        return cleaned


class ComplianceOverrideOut(ResponseModel):
    """What the profile now carries for the rule; ``head_idx`` lets the client sync."""

    rule_id: StrictStr
    reason: StrictStr
    by_user_id: uuid.UUID | None = None
    by_name: StrictStr | None = None
    at: StrictStr
    head_idx: int


class ComplianceOverrideRevokedOut(ResponseModel):
    rule_id: StrictStr
    revoked: bool = True
    head_idx: int


def _profile_of(document: dict[str, Any]) -> tuple[str | None, dict[str, Any]]:
    """``(cityPack, overrides)`` from the folded document, tolerating an empty profile."""
    plot = document.get("plot") if isinstance(document.get("plot"), dict) else {}
    profile = plot.get("regProfile") if isinstance(plot.get("regProfile"), dict) else {}
    city_pack = profile.get("cityPack")
    raw = profile.get("overrides")
    overrides = dict(raw) if isinstance(raw, dict) else {}
    return (str(city_pack) if city_pack else None), overrides


async def _require_known_rule(document: dict[str, Any], rule_id: str) -> None:
    if rule_id == _VALUE_OVERRIDES_KEY:
        raise ApiError(
            "'values' is the value-override map, not a rule.",
            status=status.HTTP_422_UNPROCESSABLE_CONTENT,
            code="not_a_rule",
        )
    try:
        known = pack_rule_ids(document)
    except ComplianceUnavailable as exc:
        raise ApiError(
            "The rule packs for this project could not be loaded: %s" % exc,
            status=status.HTTP_503_SERVICE_UNAVAILABLE,
            code="compliance_unavailable",
            action="Try again in a moment.",
        ) from exc
    if rule_id not in known:
        raise ApiError(
            "No loaded rule pack carries a rule '%s'." % rule_id,
            status=status.HTTP_404_NOT_FOUND,
            code="unknown_rule",
            action="Override a rule from the Compliance tab, which lists only the rules that ran.",
        )


async def _actor_name(session: Any, ctx: Any) -> str | None:
    """The signed-in user's display name, for the row on the tab. Best effort."""
    if ctx.user_id is None:
        return None
    from garh_api.repositories import UserRepository

    user = await UserRepository(session, ctx).get(ctx.user_id)
    name = getattr(user, "name", None) if user is not None else None
    return str(name) if name else None


@router.post(
    "/projects/{project_id}/compliance/overrides",
    response_model=ComplianceOverrideOut,
    status_code=status.HTTP_201_CREATED,
    summary="Accept a failing rule with a reason (logged, never silenced)",
)
async def create_override(
    project_id: uuid.UUID,
    body: ComplianceOverrideIn,
    session: SessionDep,
    ctx: TenantDep,
) -> ComplianceOverrideOut:
    """Record ``{reason, byUserId, at}`` for ``ruleId`` on the project's reg profile.

    The rule still evaluates and still reports its real status; the row is marked
    ``overridden`` and stops blocking the solver gate. Re-posting for the same rule
    replaces the reason (and re-stamps who/when) — the audit trail keeps both.
    """
    await require_project(session, ctx, project_id)
    ctx.require_scope("compliance")
    ctx.require_write("overriding a compliance rule")

    branch = await active_branch(session, ctx, project_id)
    state = await load_project_state(session, ctx, project_id, branch)
    await _require_known_rule(state.document, body.rule_id)

    city_pack, overrides = _profile_of(state.document)
    at = datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")
    by_name = await _actor_name(session, ctx)
    entry: dict[str, Any] = {"reason": body.reason, "at": at}
    if ctx.user_id is not None:
        entry["byUserId"] = str(ctx.user_id)
    if by_name:
        entry["byName"] = by_name
    overrides[body.rule_id] = entry

    result = await dispatch_ops(
        session,
        ctx,
        project_id,
        [
            OpIn(
                type="plot.set_reg_profile",
                payload={"cityPack": city_pack, "overrides": overrides},
            )
        ],
        source="manual",
        group_id=uuid.uuid4(),
        branch=branch,
    )
    await AuditLogRepository(session, ctx).record(
        ACTION_COMPLIANCE_OVERRIDDEN,
        entity="rule",
        entity_id=project_id,
        meta={"ruleId": body.rule_id, "reason": body.reason, "at": at},
    )
    _log.info(
        "compliance.override.recorded",
        project_id=str(project_id),
        rule_id=body.rule_id,
        head_idx=result.head_idx,
    )
    return ComplianceOverrideOut(
        rule_id=body.rule_id,
        reason=body.reason,
        by_user_id=ctx.user_id,
        by_name=by_name,
        at=at,
        head_idx=result.head_idx,
    )


@router.delete(
    "/projects/{project_id}/compliance/overrides/{rule_id}",
    response_model=ComplianceOverrideRevokedOut,
    summary="Revoke a rule override",
)
async def revoke_override(
    project_id: uuid.UUID,
    rule_id: str,
    session: SessionDep,
    ctx: TenantDep,
) -> ComplianceOverrideRevokedOut:
    """Remove the acknowledgement; the rule blocks again on its next evaluation."""
    await require_project(session, ctx, project_id)
    ctx.require_scope("compliance")
    ctx.require_write("revoking a compliance override")

    branch = await active_branch(session, ctx, project_id)
    state = await load_project_state(session, ctx, project_id, branch)
    city_pack, overrides = _profile_of(state.document)
    if rule_id == _VALUE_OVERRIDES_KEY or rule_id not in overrides:
        raise ApiError(
            "Rule '%s' has no override on this project." % rule_id,
            status=status.HTTP_404_NOT_FOUND,
            code="override_not_found",
        )
    previous = overrides.pop(rule_id)

    result = await dispatch_ops(
        session,
        ctx,
        project_id,
        [
            OpIn(
                type="plot.set_reg_profile",
                payload={"cityPack": city_pack, "overrides": overrides},
            )
        ],
        source="manual",
        group_id=uuid.uuid4(),
        branch=branch,
    )
    await AuditLogRepository(session, ctx).record(
        ACTION_COMPLIANCE_OVERRIDE_REVOKED,
        entity="rule",
        entity_id=project_id,
        meta={
            "ruleId": rule_id,
            "previousReason": previous.get("reason") if isinstance(previous, dict) else None,
        },
    )
    _log.info(
        "compliance.override.revoked",
        project_id=str(project_id),
        rule_id=rule_id,
        head_idx=result.head_idx,
    )
    return ComplianceOverrideRevokedOut(rule_id=rule_id, head_idx=result.head_idx)


__all__ = [
    "ComplianceOverrideIn",
    "ComplianceOverrideOut",
    "ComplianceOverrideRevokedOut",
    "router",
]
