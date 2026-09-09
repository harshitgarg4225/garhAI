"""Team invites — the admin's half (J01 "set up the practice").

An invite is a standing offer of a seat to an email address. This module owns the
admin-side policy: who may invite, what an invite may not collide with, the seat
gate, the rate limits on the email it sends, the email itself, and the audit rows.
The invitee's half — the code sent at sign-in and the acceptance that creates the
``users`` row — lives in :class:`garh_api.auth.AuthService`, because it happens
before any tenant context exists.

THE ONE PROPERTY THAT SHAPES EVERYTHING HERE
--------------------------------------------
``POST /firm/invites`` must not tell the admin whether the address already has an
account with some OTHER practice. ``users.email`` is globally unique, so such an
address can never accept; but refusing the invite would turn every admin into an
account-enumeration oracle over every address they care to type. So the row is
created, the email goes out, the response is identical — and the person who owns
the mailbox is the one who learns, on signing in, that they landed in their own
practice rather than the inviting one. The two checks this module DOES make are
both scoped to the caller's own firm (already a member; already invited), which
reveal nothing about anywhere else.

Seats: an invite promises a seat type. The gate that can honestly refuse is here, at
creation, counting open editor invites against what the plan still has room for.
Acceptance then assigns the seat best-effort (:func:`assign_invited_seat`) — a plan
that shrank meanwhile yields a seatless member the admin can see, not a sign-in
refused with a billing error to someone who cannot act on it.
"""

from __future__ import annotations

import hashlib
import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Final

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from garh_api.auth import RequestOrigin, deliver_message
from garh_api.billing.errors import SeatLimitError
from garh_api.billing.repositories import Seat, SeatRepository
from garh_api.billing.seats import PAID_SEAT_TYPE, seat_summary
from garh_api.config import Settings, get_settings
from garh_api.errors import (
    AlreadyMemberError,
    InvitePendingError,
    ServiceUnavailableError,
)
from garh_api.logging import get_logger
from garh_api.mailer import build_invite_message
from garh_api.ratelimit import (
    enforce_rate_limit,
    otp_per_email_rule,
    otp_resend_identity,
    otp_resend_rule,
    reset_rate_limit,
)
from garh_api.repositories.audit_log import (
    ACTION_INVITE_CREATED,
    ACTION_INVITE_RESENT,
    ACTION_INVITE_REVOKED,
    AuditLogRepository,
)
from garh_api.repositories.domain import AuthPrincipal, FirmInvite, InviteOffer
from garh_api.repositories.firm_invites import FirmInviteRepository
from garh_api.repositories.firms import FirmRepository
from garh_api.repositories.users import UserRepository, normalise_email
from garh_api.security import email_domain, pseudonymise
from garh_api.tenancy import EntityNotFoundError, TenantCtx

_log = get_logger(__name__)

#: How long a link stays live. A week: long enough for a colleague on leave, short
#: enough that a forgotten invite to a departed address does not sit open for a year.
INVITE_TTL_DAYS: Final = 7


def new_invite_token() -> str:
    """256 random bits, URL-safe. Delivered once by email; only its hash is stored."""
    return secrets.token_urlsafe(32)


def hash_invite_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def invite_url(settings: Settings, token: str) -> str:
    """The link in the email. ``/login?invite=`` — the ordinary sign-in screen, which
    reads the token, shows who is asking, and pre-fills the address."""
    return "%s/login?invite=%s" % (settings.app_url.rstrip("/"), token)


def _email_identity(email: str) -> str:
    # Same bucket shape as AuthService: hashed, because Redis is not a PII store.
    return "email:%s" % pseudonymise(email)


@dataclass(frozen=True)
class SentInvite:
    """An invite plus the plaintext link — returned ONCE, on create and on resend."""

    invite: FirmInvite
    url: str
    channel: str


class InviteService:
    """Admin-side invite policy for one firm.

    Constructor::

        InviteService(session, ctx, origin, settings=None)

    ``ctx`` must be an admin's; every write checks it again in the repository.
    """

    def __init__(
        self,
        session: AsyncSession,
        ctx: TenantCtx,
        origin: RequestOrigin,
        *,
        settings: Settings | None = None,
    ) -> None:
        self._session = session
        self._ctx = ctx
        self._origin = origin
        self._settings = settings or get_settings()
        self._invites = FirmInviteRepository(session, ctx)
        self._users = UserRepository(session, ctx)
        self._audit = AuditLogRepository(session, ctx)

    # -- reads ---------------------------------------------------------
    async def list_open(self) -> list[FirmInvite]:
        return await self._invites.list_open()

    # -- writes --------------------------------------------------------
    async def create(self, *, email: str, name: str, role: str, seat_type: str) -> SentInvite:
        """Create and email an invite.

        Refusals, all firm-scoped (see the module docstring for why nothing else may
        refuse): already a member here (409), already invited here and still live
        (409), no editor seat left for the promise (402). An invite that exists here
        but has EXPIRED is refreshed and re-sent instead of refused — "invite again"
        is what the admin means.
        """
        self._ctx.require_admin("inviting a team member")
        clean = normalise_email(email)

        if await self._users.get_by_email(clean) is not None:
            raise AlreadyMemberError()

        existing = await self._invites.get_open_for_email(clean)
        if existing is not None:
            if existing.is_open():
                raise InvitePendingError()
            return await self.resend(existing.id)

        await self._assert_seat_available(seat_type)
        await self._charge_email_budget(clean)

        token = new_invite_token()
        try:
            invite = await self._invites.create(
                email=clean,
                name=name,
                role=role,
                seat_type=seat_type,
                token_hash=hash_invite_token(token),
                expires_at=datetime.now(UTC) + timedelta(days=INVITE_TTL_DAYS),
            )
        except IntegrityError as exc:
            # Two admins invited the same address at once; the partial unique index
            # is the real guard and it just fired. Same answer as the pre-check.
            await self._session.rollback()
            raise InvitePendingError() from exc

        channel = await self._send(invite, token, clean)
        await self._audit.record(
            ACTION_INVITE_CREATED,
            entity="firm_invite",
            entity_id=invite.id,
            meta=self._meta(
                role=role, seatType=seat_type, channel=channel, emailDomain=email_domain(clean)
            ),
        )
        return SentInvite(invite=invite, url=invite_url(self._settings, token), channel=channel)

    async def resend(self, invite_id: uuid.UUID) -> SentInvite:
        """A fresh link with a fresh week; the old link stops resolving.

        Rate-limited exactly like a sign-in resend — per address, per route — so an
        admin hammering the button cannot use the product to mail-bomb a colleague.
        """
        self._ctx.require_admin("resending an invite")
        invite = await self._invites.get(invite_id)
        if invite is None or invite.accepted_at is not None or invite.revoked_at is not None:
            raise EntityNotFoundError("invite", invite_id)

        await self._charge_email_budget(invite.email)
        token = new_invite_token()
        refreshed = await self._invites.mark_resent(
            invite.id,
            token_hash=hash_invite_token(token),
            expires_at=datetime.now(UTC) + timedelta(days=INVITE_TTL_DAYS),
        )
        channel = await self._send(refreshed, token, refreshed.email)
        await self._audit.record(
            ACTION_INVITE_RESENT,
            entity="firm_invite",
            entity_id=refreshed.id,
            meta=self._meta(sendCount=refreshed.send_count, channel=channel),
        )
        return SentInvite(invite=refreshed, url=invite_url(self._settings, token), channel=channel)

    async def revoke(self, invite_id: uuid.UUID) -> FirmInvite:
        self._ctx.require_admin("withdrawing an invite")
        invite = await self._invites.get(invite_id)
        if invite is None or invite.accepted_at is not None:
            raise EntityNotFoundError("invite", invite_id)
        revoked = await self._invites.revoke(invite_id)
        await self._audit.record(
            ACTION_INVITE_REVOKED,
            entity="firm_invite",
            entity_id=invite_id,
            meta=self._meta(),
        )
        return revoked

    # -- helpers -------------------------------------------------------
    def _meta(self, **extra: Any) -> dict[str, Any]:
        meta: dict[str, Any] = {"ip": self._origin.ip}
        ua = self._origin.short_user_agent()
        if ua:
            meta["userAgent"] = ua
        meta.update(extra)
        return meta

    async def _assert_seat_available(self, seat_type: str) -> None:
        """Editor invites count against the plan; viewers are free and unlimited."""
        if seat_type != PAID_SEAT_TYPE:
            return
        ent, summary = await seat_summary(self._session, self._ctx)
        promised = await self._invites.count_pending_of_seat_type(PAID_SEAT_TYPE)
        if summary.available - promised < 1:
            _log.info(
                "invite.seat_denied",
                entitled=summary.entitled,
                used=summary.editors_used,
                promised=promised,
                plan_code=ent.plan.code,
            )
            raise SeatLimitError(
                "Your %s plan includes %d editor seat(s); %d are held and %d more are "
                "promised to open invites."
                % (ent.plan.name, summary.entitled, summary.editors_used, promised),
                extra={
                    "planCode": ent.plan.code,
                    "entitled": summary.entitled,
                    "used": summary.editors_used,
                    "promised": promised,
                },
            )

    async def _charge_email_budget(self, clean_email: str) -> None:
        """The 60 s per-address cooldown on the ``invite`` route, then the hourly cap.

        Per route, so an invite does not spend the invitee's own sign-in cooldown
        (``ratelimit.otp_resend_identity`` explains the shape); the hourly cap is the
        shared spam ceiling on the address.
        """
        identity = _email_identity(clean_email)
        await enforce_rate_limit(
            otp_resend_rule(self._settings), otp_resend_identity(identity, "invite")
        )
        await enforce_rate_limit(otp_per_email_rule(self._settings), identity)

    async def _send(self, invite: FirmInvite, token: str, clean_email: str) -> str:
        firm = await FirmRepository(self._session, self._ctx).get_current()
        inviter = await self._users.get(self._ctx.user_id) if self._ctx.user_id else None
        url = invite_url(self._settings, token)
        message = build_invite_message(
            to_email=clean_email,
            from_addr=self._settings.smtp_from or "no-reply@garh.local",
            firm_name=firm.name,
            inviter_name=inviter.name if inviter is not None else firm.name,
            role=invite.role,
            url=url,
            ttl_days=INVITE_TTL_DAYS,
        )
        try:
            return await deliver_message(
                message,
                kind="invite",
                settings=self._settings,
                # The link, not a secret: acceptance still needs a code sent to the
                # address. Logged so a mail-less dev stack can still be walked.
                dev_echo={"invite_url": url},
            )
        except ServiceUnavailableError:
            # Same refund `_send_code` makes: the 503 invites a retry in 30 s, and the
            # cooldown charged above would turn that retry into a 429 for an email
            # that never left. The request transaction rolls the row/token back.
            await reset_rate_limit(
                otp_resend_rule(self._settings),
                otp_resend_identity(_email_identity(clean_email), "invite"),
                settings=self._settings,
            )
            raise


async def assign_invited_seat(
    session: AsyncSession, offer: InviteOffer, principal: AuthPrincipal
) -> Seat | None:
    """Give a just-accepted invitee the seat their invite promised, if the plan still
    has room. Returns ``None`` — and logs, loudly — when it does not.

    Runs under a system context for the inviting firm: the actor is the invitee, who
    is not an admin, and the admin who made the promise may no longer exist. The
    entitlement check is the same :func:`~garh_api.billing.seats.seat_summary` the
    admin route uses; ``assigned_by`` records the inviter, which is who decided.
    """
    invite = offer.invite
    ctx = TenantCtx.for_system(invite.firm_id)
    seats = SeatRepository(session, ctx)
    if await seats.for_user(principal.user_id) is not None:  # pragma: no cover - new row
        return None
    if invite.seat_type == PAID_SEAT_TYPE:
        ent, summary = await seat_summary(session, ctx)
        if summary.available < 1:
            _log.warning(
                "invite.seat_unavailable_at_accept",
                invite_id=str(invite.id),
                firm_id=str(invite.firm_id),
                user_id=str(principal.user_id),
                entitled=summary.entitled,
                used=summary.editors_used,
                plan_code=ent.plan.code,
                consequence="member created without a seat; assign one from the Team page",
            )
            return None
    return await seats.assign(
        user_id=principal.user_id, seat_type=invite.seat_type, assigned_by=invite.invited_by
    )


__all__ = [
    "INVITE_TTL_DAYS",
    "InviteService",
    "SentInvite",
    "assign_invited_seat",
    "hash_invite_token",
    "invite_url",
    "new_invite_token",
]
