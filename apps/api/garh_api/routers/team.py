"""``/firm/**`` — the practice: its profile, its members, its invites (J01).

==========================================  ======  ==================================
route                                        auth    purpose
==========================================  ======  ==================================
``GET    /firm``                            bearer  name, logo, practice profile
``PATCH  /firm``                            admin   rename; address / GSTIN / registration
``GET    /firm/members``                    bearer  everyone, with role, seat, last sign-in
``PATCH  /firm/members/{user_id}``          admin   change a role (never the last admin)
``DELETE /firm/members/{user_id}``          admin   remove (never yourself, never the last admin)
``GET    /firm/invites``                    bearer  open invites
``POST   /firm/invites``                    admin   invite a colleague by email
``POST   /firm/invites/{invite_id}/resend`` admin   fresh link, fresh week
``DELETE /firm/invites/{invite_id}``        admin   withdraw
==========================================  ======  ==================================

Until this router existed every signup created a firm with exactly one admin and
nothing could add a second person, so presence, cursors and colleague comments were
built and unreachable. ``docs/trial-readiness.md`` carried the finding.

Every handler here is thin: policy lives in :class:`garh_api.invites.InviteService`
and the repositories. What lives here is the HTTP shape and two decisions that are
genuinely HTTP-level — that removing a member also ends their live sessions (a
removed colleague must not keep a 15-minute bearer), and that a rename reaches the
sheet title block on the next generation because the title-block template reads
``firms.name`` (``routers/sheets.load_drawing_preferences``).
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Path, status

from garh_api.billing.repositories import SeatRepository
from garh_api.deps import AdminTenant, AppSettings, DbSession, Origin, Sessions, Tenant
from garh_api.errors import PROBLEM_RESPONSES, LastAdminError
from garh_api.invites import InviteService, SentInvite
from garh_api.logging import get_logger
from garh_api.repositories.audit_log import (
    ACTION_FIRM_SETTINGS_CHANGED,
    ACTION_USER_REMOVED,
    ACTION_USER_ROLE_CHANGED,
    AuditLogRepository,
)
from garh_api.repositories.domain import FirmInvite
from garh_api.repositories.firms import FirmRepository
from garh_api.repositories.users import UserRepository
from garh_api.routers import not_found
from garh_api.schemas.team import (
    PRACTICE_SETTINGS_KEY,
    FirmProfileOut,
    FirmProfilePatch,
    InviteCreateIn,
    InviteOut,
    InvitesOut,
    MemberOut,
    MemberRolePatch,
    MembersOut,
    PracticeProfile,
    SeatSummaryOut,
)
from garh_api.tenancy import PermissionDeniedError, TenantCtx

_log = get_logger(__name__)

router = APIRouter(prefix="/firm", tags=["team"], responses=PROBLEM_RESPONSES)

UserId = Annotated[uuid.UUID, Path(description="A member of your firm.")]
InviteId = Annotated[uuid.UUID, Path(description="An open invite of your firm.")]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _member_names(session: DbSession, ctx: TenantCtx) -> dict[uuid.UUID, str]:
    page = await UserRepository(session, ctx).list_members(limit=200)
    return {user.id: user.name for user in page.items}


def _invite_out(
    invite: FirmInvite, names: dict[uuid.UUID, str], *, url: str | None = None
) -> InviteOut:
    inviter = names.get(invite.invited_by) if invite.invited_by else None
    return InviteOut.from_invite(invite, invited_by_name=inviter, url=url)


async def _sent_out(session: DbSession, ctx: TenantCtx, sent: SentInvite) -> InviteOut:
    return _invite_out(sent.invite, await _member_names(session, ctx), url=sent.url)


async def _firm_out(session: DbSession, ctx: TenantCtx) -> FirmProfileOut:
    firm = await FirmRepository(session, ctx).get_current()
    return FirmProfileOut(
        id=firm.id,
        name=firm.name,
        logo_url=firm.logo_url,
        practice=PracticeProfile.from_settings(firm.settings),
    )


# ---------------------------------------------------------------------------
# Firm profile
# ---------------------------------------------------------------------------


@router.get("", response_model=FirmProfileOut, summary="The practice's profile")
async def get_firm(session: DbSession, ctx: Tenant) -> FirmProfileOut:
    return await _firm_out(session, ctx)


@router.patch("", response_model=FirmProfileOut, summary="Rename, or edit the practice profile")
async def patch_firm(
    body: FirmProfilePatch, session: DbSession, ctx: AdminTenant, origin: Origin
) -> FirmProfileOut:
    """Merged, never replaced: a PATCH that names only ``gstin`` leaves the address alone.

    The name lands on ``firms.name``, which is what the sheet title block prints when
    the firm has not saved a template with its own ``firmName`` — so a rename here is
    on the next drawing set without a second edit.
    """
    repo = FirmRepository(session, ctx)
    changed: list[str] = []
    if body.name is not None:
        await repo.rename(body.name)
        changed.append("name")
    if body.practice is not None:
        firm = await repo.get_current()
        current = PracticeProfile.from_settings(firm.settings).model_dump()
        patch = body.practice.model_dump(exclude_none=True)
        merged = {**current, **patch}
        await repo.merge_settings({PRACTICE_SETTINGS_KEY: merged})
        changed.extend(sorted(patch))
    if changed:
        await AuditLogRepository(session, ctx).record(
            ACTION_FIRM_SETTINGS_CHANGED,
            entity="firm",
            entity_id=ctx.firm_id,
            meta={"fields": changed, "ip": origin.ip},
        )
    return await _firm_out(session, ctx)


# ---------------------------------------------------------------------------
# Members
# ---------------------------------------------------------------------------


@router.get("/members", response_model=MembersOut, summary="Everyone in the practice")
async def list_members(session: DbSession, ctx: Tenant) -> MembersOut:
    users = UserRepository(session, ctx)
    page = await users.list_members(limit=200)
    seats = {seat.user_id: seat for seat in await SeatRepository(session, ctx).list_all()}
    items = [
        MemberOut.from_user(
            user,
            seat=(
                SeatSummaryOut(id=seats[user.id].id, seat_type=seats[user.id].seat_type)
                if user.id in seats
                else None
            ),
        )
        for user in page.items
    ]
    return MembersOut(items=items, count=len(items), admins=await users.count_admins())


@router.patch("/members/{user_id}", response_model=MemberOut, summary="Change a member's role")
async def set_member_role(
    user_id: UserId, body: MemberRolePatch, session: DbSession, ctx: AdminTenant, origin: Origin
) -> MemberOut:
    """Promote or demote. The firm's only admin cannot be demoted — by anyone,
    including themselves — because a firm with no admin has nobody left to fix it.

    Takes effect on the member's next token refresh (at most fifteen minutes):
    ``AuthService.refresh`` re-reads the principal, so the new role is in the next
    access token without signing them out.
    """
    users = UserRepository(session, ctx)
    user = await users.get(user_id)
    if user is None:
        raise not_found("user", user_id)
    if user.role == "admin" and body.role != "admin" and await users.count_admins() <= 1:
        raise LastAdminError()
    updated = await users.set_role(user_id, body.role)
    await AuditLogRepository(session, ctx).record(
        ACTION_USER_ROLE_CHANGED,
        entity="user",
        entity_id=user_id,
        meta={"from": user.role, "to": body.role, "ip": origin.ip},
    )
    seat = await SeatRepository(session, ctx).for_user(user_id)
    return MemberOut.from_user(
        updated,
        seat=SeatSummaryOut(id=seat.id, seat_type=seat.seat_type) if seat else None,
    )


@router.delete(
    "/members/{user_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Remove a member from the practice",
)
async def remove_member(
    user_id: UserId, session: DbSession, ctx: AdminTenant, store: Sessions, origin: Origin
) -> None:
    """Remove the seat, the row, and every live session — in that order.

    * Their seat is released first, so the entitlement it held is free again.
    * ``UserRepository.remove`` refuses the caller's own row and the last admin.
    * Then every refresh family is revoked and the token generation bumped, exactly
      as ``POST /auth/logout-all`` does, so the removed colleague's still-valid
      15-minute bearer dies now rather than at its natural expiry. The ordering is
      deliberate: if the session sweep failed after the row was gone, the bearer
      would fail on its next request anyway (``require_tenant`` re-reads nothing, but
      every repository is scoped to a firm the user is no longer in).

    Projects that named them architect of record keep the project and lose the
    name (``ON DELETE SET NULL``); the audit trail keeps every row they wrote.
    """
    if ctx.user_id == user_id:
        raise PermissionDeniedError("You cannot remove your own account — ask another admin.")
    users = UserRepository(session, ctx)
    user = await users.get(user_id)
    if user is None:
        raise not_found("user", user_id)
    if user.role == "admin" and await users.count_admins() <= 1:
        raise LastAdminError()

    seats = SeatRepository(session, ctx)
    seat = await seats.for_user(user_id)
    if seat is not None:
        await seats.delete(seat.id)
    await users.remove(user_id)
    await AuditLogRepository(session, ctx).record(
        ACTION_USER_REMOVED,
        entity="user",
        entity_id=user_id,
        meta={"role": user.role, "seatReleased": seat is not None, "ip": origin.ip},
    )
    generation = await store.bump_generation(user_id)
    families = await store.revoke_all_families(user_id)
    _log.info(
        "team.member_removed",
        removed_user_id=str(user_id),
        generation=generation,
        families_revoked=families,
    )


# ---------------------------------------------------------------------------
# Invites
# ---------------------------------------------------------------------------


@router.get("/invites", response_model=InvitesOut, summary="Open invites")
async def list_invites(
    session: DbSession, ctx: Tenant, origin: Origin, settings: AppSettings
) -> InvitesOut:
    invites = await InviteService(session, ctx, origin, settings=settings).list_open()
    names = await _member_names(session, ctx)
    items = [_invite_out(invite, names) for invite in invites]
    return InvitesOut(items=items, count=len(items))


@router.post(
    "/invites",
    response_model=InviteOut,
    status_code=status.HTTP_201_CREATED,
    summary="Invite a colleague by email",
)
async def create_invite(
    body: InviteCreateIn,
    session: DbSession,
    ctx: AdminTenant,
    origin: Origin,
    settings: AppSettings,
) -> InviteOut:
    """Emails the invite and returns it with its link, once.

    Same 201 whether or not the address has an account with some other practice —
    see :mod:`garh_api.invites` for why that is the load-bearing property here.
    The refusals are all about THIS firm: already a member (409), already invited
    (409), no editor seat left to promise (402).
    """
    sent = await InviteService(session, ctx, origin, settings=settings).create(
        email=body.email, name=body.name, role=body.role, seat_type=body.seat_type
    )
    return await _sent_out(session, ctx, sent)


@router.post("/invites/{invite_id}/resend", response_model=InviteOut, summary="Resend an invite")
async def resend_invite(
    invite_id: InviteId, session: DbSession, ctx: AdminTenant, origin: Origin, settings: AppSettings
) -> InviteOut:
    """A fresh link and a fresh week. Rate-limited per address like a sign-in resend."""
    sent = await InviteService(session, ctx, origin, settings=settings).resend(invite_id)
    return await _sent_out(session, ctx, sent)


@router.delete(
    "/invites/{invite_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Withdraw an invite",
)
async def revoke_invite(
    invite_id: InviteId, session: DbSession, ctx: AdminTenant, origin: Origin, settings: AppSettings
) -> None:
    await InviteService(session, ctx, origin, settings=settings).revoke(invite_id)


__all__ = ["router"]
