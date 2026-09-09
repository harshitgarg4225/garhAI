"""Firm-invite repository — the admin's side of the team surface (J01).

Everything here is firm-scoped: an admin lists, creates, resends and withdraws the
invites of their own practice and can never see another's. The two reads that happen
BEFORE a tenant exists — "is there an open invite for this address?" at sign-in, and
"what does this link point at?" on the pre-auth status page — live in
:class:`~garh_api.repositories.auth_directory.AuthDirectoryRepository`, next to the
other pre-auth lookups, so this class stays unconditionally scoped.

Only the token's ``sha256`` is ever stored. The caller mints the plaintext
(:func:`garh_api.invites.new_invite_token`) and hands it to the mailer; a database
dump therefore contains no usable link, the same discipline as ``share_links``.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from garh_api import models
from garh_api.repositories.domain import FirmInvite
from garh_api.repositories.users import normalise_email
from garh_api.tenancy import Repository, RepositoryUsageError


class FirmInviteRepository(Repository[models.FirmInvite, FirmInvite]):
    """Invites of the caller's firm.

    Constructor::

        FirmInviteRepository(session: AsyncSession, ctx: TenantCtx)
    """

    row_type = models.FirmInvite
    entity_name = "invite"

    def to_domain(self, row: models.FirmInvite) -> FirmInvite:
        return FirmInvite.from_row(row)

    # -- reads ---------------------------------------------------------
    async def list_open(self) -> list[FirmInvite]:
        """Every invite that has been neither accepted nor withdrawn, oldest first.

        Expired ones are included on purpose: the admin needs to see that an invite
        lapsed and press *Resend*, which refreshes it. Accepted and revoked invites are
        history — the members list and the audit trail carry those.
        """
        stmt = (
            self._scoped_select()
            .where(models.FirmInvite.accepted_at.is_(None))
            .where(models.FirmInvite.revoked_at.is_(None))
            .order_by(models.FirmInvite.created_at.asc(), models.FirmInvite.id.asc())
        )
        return [self.to_domain(row) for row in await self._all(stmt)]

    async def get_open_for_email(self, email: str) -> FirmInvite | None:
        """The one un-accepted, un-withdrawn invite for an address in this firm, if any."""
        stmt = (
            self._scoped_select()
            .where(models.FirmInvite.email == normalise_email(email))
            .where(models.FirmInvite.accepted_at.is_(None))
            .where(models.FirmInvite.revoked_at.is_(None))
            .limit(1)
        )
        row = await self._first(stmt)
        return None if row is None else self.to_domain(row)

    async def count_pending_of_seat_type(
        self, seat_type: str, *, now: datetime | None = None
    ) -> int:
        """Open, unexpired invites that promise a seat of this type.

        The seat gate counts these alongside the seats already held: an invite is a
        promise the practice has made, and a promise that cannot be kept at acceptance
        is a worse experience than a refusal at creation.
        """
        moment = now or datetime.now(UTC)
        stmt = (
            self._scoped_select()
            .where(models.FirmInvite.seat_type == seat_type)
            .where(models.FirmInvite.accepted_at.is_(None))
            .where(models.FirmInvite.revoked_at.is_(None))
            .where(models.FirmInvite.expires_at > moment)
        )
        return await self._count(stmt)

    # -- writes --------------------------------------------------------
    async def create(
        self,
        *,
        email: str,
        name: str,
        role: str,
        seat_type: str,
        token_hash: str,
        expires_at: datetime,
    ) -> FirmInvite:
        self.ctx.require_admin("inviting a team member")
        if role not in models.USER_ROLES:
            raise RepositoryUsageError("role must be one of %s." % ", ".join(models.USER_ROLES))
        if seat_type not in models.INVITE_SEAT_TYPES:
            raise RepositoryUsageError(
                "seat_type must be one of %s." % ", ".join(models.INVITE_SEAT_TYPES)
            )
        clean_name = name.strip()
        if not clean_name:
            raise RepositoryUsageError("The colleague's name cannot be blank.")
        row = self._new_row(
            email=normalise_email(email),
            name=clean_name,
            role=role,
            seat_type=seat_type,
            token_hash=token_hash,
            invited_by=self.actor_id,
            expires_at=expires_at,
        )
        await self._insert(row)
        self._log.info("invite.created", entity_id=str(row.id), user_role=role, seat_type=seat_type)
        return self.to_domain(row)

    async def mark_resent(
        self, invite_id: uuid.UUID, *, token_hash: str, expires_at: datetime
    ) -> FirmInvite:
        """A fresh token and a fresh expiry; the old link stops resolving."""
        self.ctx.require_admin("resending an invite")
        row = await self._require_row(invite_id)
        if row.accepted_at is not None or row.revoked_at is not None:
            raise RepositoryUsageError("Only an open invite can be resent.")
        row.token_hash = token_hash
        row.expires_at = expires_at
        row.last_sent_at = datetime.now(UTC)
        row.send_count = row.send_count + 1
        await self.flush()
        self._log.info("invite.resent", entity_id=str(invite_id), send_count=row.send_count)
        return self.to_domain(row)

    async def revoke(self, invite_id: uuid.UUID) -> FirmInvite:
        """Withdraw. Idempotent on an already-withdrawn invite; refuses an accepted one."""
        self.ctx.require_admin("withdrawing an invite")
        row = await self._require_row(invite_id)
        if row.accepted_at is not None:
            raise RepositoryUsageError(
                "An accepted invite cannot be withdrawn — remove the member."
            )
        if row.revoked_at is None:
            row.revoked_at = datetime.now(UTC)
            await self.flush()
            self._log.info("invite.revoked", entity_id=str(invite_id))
        return self.to_domain(row)


__all__ = ["FirmInviteRepository"]
