"""Platform-owner endpoints: the deployment's own knobs, not a firm's.

Today that is one knob, the platform fee (``billing/markup.py``). Who may turn it is
``Settings.platform_owner_emails`` — a sign-in email allowlist, deliberately NOT a firm
role: a firm admin runs a practice, the owner runs the platform, and the fee every
practice pays is the owner's to set. An empty allowlist refuses everyone, on purpose.

Reading the fee needs only a signed-in user: the usage card shows it to every
architect, so hiding it here would hide nothing. The read does say whether the CALLER
may change it (``canSet``), which is how the web app decides whether to show the owner's
page at all — a hint for the UI, never the gate: the ``PUT`` re-checks the allowlist on
every call and answers 403 on its own.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated

import structlog
from fastapi import APIRouter, Depends
from pydantic import Field

from garh_api.billing.markup import (
    MarkupValueError,
    current_markup_bps,
    describe_markup,
    percent_to_bps,
    set_markup_bps,
)
from garh_api.config import get_settings
from garh_api.errors import ApiError
from garh_api.errors import InvalidRequestError as ValidationError
from garh_api.repositories import UserRepository
from garh_api.routers import SessionDep, TenantDep
from garh_api.schemas import CamelModel, ResponseModel
from garh_api.tenancy import TenantCtx


class _Forbidden(ApiError):
    http_status = 403
    code = "forbidden"
    action = "Sign in as a platform owner to change this."


_log = structlog.get_logger("garh_api.admin")

router = APIRouter(prefix="/admin", tags=["admin"])


class MarkupOut(ResponseModel):
    """The platform fee as it stands. ``source`` says whether the owner has set it."""

    percent: str
    bps: int
    source: str  # "setting" | "default"
    updated_at: str | None = None
    updated_by: str | None = None
    #: The sign-in email of the owner who set it, so the page can say who without a
    #: cross-tenant lookup. ``None`` while the boot default is in force.
    updated_by_email: str | None = None
    #: Whether the CALLER may change it. A hint for the UI; the PUT decides for real.
    can_set: bool = False


class MarkupIn(CamelModel):
    percent: float | int | str = Field(
        description="The fee as a percentage of provider cost: 5, 7.5, '0'. 0-100, two decimals."
    )


@dataclass(frozen=True, slots=True)
class PlatformOwner:
    """A caller who passed the allowlist, with the email that did — for the audit row."""

    ctx: TenantCtx
    email: str


async def platform_owner_email(session: SessionDep, ctx: TenantDep) -> str | None:
    """The caller's email if it is on the owner allowlist, else ``None``.

    Looked up by ``user_id`` on every call rather than trusted from a token claim: an
    email that was on the list when the token was minted may not be now. An empty
    allowlist means nobody, on purpose.
    """
    owners = get_settings().platform_owners()
    if ctx.user_id is None or not owners:
        return None
    user = await UserRepository(session, ctx).get(ctx.user_id)
    email = user.email.strip().lower() if user is not None else ""
    return email if email in owners else None


async def require_platform_owner(session: SessionDep, ctx: TenantDep) -> PlatformOwner:
    """403 unless the signed-in user's email is on the owner allowlist."""
    email = await platform_owner_email(session, ctx)
    if email is None:
        _log.info("platform.owner.refused", user_id=str(ctx.user_id))
        raise _Forbidden("Only a platform owner can change this.")
    return PlatformOwner(ctx=ctx, email=email)


OwnerDep = Annotated[PlatformOwner, Depends(require_platform_owner)]


async def _describe(session: SessionDep, *, can_set: bool) -> MarkupOut:
    state = await describe_markup(session)
    return MarkupOut(
        percent=state.percent,
        bps=state.bps,
        source=state.source,
        updated_at=state.updated_at.isoformat() if state.updated_at is not None else None,
        updated_by=str(state.updated_by) if state.updated_by is not None else None,
        updated_by_email=state.updated_by_email,
        can_set=can_set,
    )


@router.get("/billing/markup", response_model=MarkupOut, summary="The platform fee")
async def get_markup(session: SessionDep, ctx: TenantDep) -> MarkupOut:
    owner = await platform_owner_email(session, ctx)
    return await _describe(session, can_set=owner is not None)


@router.put("/billing/markup", response_model=MarkupOut, summary="Set the platform fee")
async def put_markup(body: MarkupIn, session: SessionDep, owner: OwnerDep) -> MarkupOut:
    try:
        bps = percent_to_bps(body.percent)
    except MarkupValueError as exc:
        raise ValidationError(str(exc)) from exc
    before = await current_markup_bps(session)
    await set_markup_bps(session, bps, updated_by=owner.ctx.user_id, updated_by_email=owner.email)
    await session.commit()
    _log.info(
        "platform.markup.changed",
        markup_bps_before=before,
        markup_bps_after=bps,
        user_id=str(owner.ctx.user_id),
    )
    return await _describe(session, can_set=True)


__all__ = ["OwnerDep", "PlatformOwner", "platform_owner_email", "require_platform_owner", "router"]
