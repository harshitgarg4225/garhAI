"""Pre-authentication directory — the ONLY non-tenant read of ``users``/``firms``.

Why this exists: at ``POST /auth/verify`` there is no ``firm_id`` yet. The whole point
of the OTP flow is to *discover* which firm an email belongs to, so a firm-scoped
repository is logically impossible here — you would need the answer to ask the
question.

Why it is safe:

* It returns only :class:`~garh_api.repositories.domain.AuthPrincipal` — user id,
  firm id, role, email, name, firm name — or, for the invite path, an
  :class:`~garh_api.repositories.domain.InviteOffer` (the invite row plus the firm's
  and inviter's names). It cannot reach projects, plots, briefs, ops, renders, sheets
  or comments. There is no generic query method.
* Lookup is by exact normalised email, by user id, or by the ``sha256`` of an invite
  token. There is no listing, no wildcard, no "find users in other firms".
* Every call is logged with the email **domain** only (§13: model summaries and logs
  exclude PII).

Once a principal is returned, the caller mints a JWT and everything afterwards goes
through :class:`~garh_api.tenancy.TenantCtx` and normal scoped repositories.

CI lint: this module and :func:`garh_api.tenancy.system_unscoped_session` are the only
places allowed to query without a ``TenantCtx``, alongside ``otp.py``, ``flags.py``
and ``ShareTokenResolver``.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from garh_api import models
from garh_api.logging import get_logger
from garh_api.repositories.domain import AuthPrincipal, FirmInvite, InviteOffer
from garh_api.repositories.users import normalise_email
from garh_api.tenancy import RepositoryUsageError

_log = get_logger(__name__)


def _email_domain(email: str) -> str:
    _, _, domain = email.partition("@")
    return domain or "unknown"


class AuthDirectoryRepository:
    """Narrow, audited, non-tenant lookups needed before a tenant context exists.

    Constructor::

        AuthDirectoryRepository(session: AsyncSession)

    Note the absent second argument: there is deliberately no ``TenantCtx`` here, and
    the class does not subclass :class:`~garh_api.tenancy.Repository` so it inherits
    none of its scoped machinery.
    """

    entity_name = "principal"

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def find_principal_by_email(self, email: str) -> AuthPrincipal | None:
        """Resolve a login email to its principal, or None if unknown.

        Callers must give the same response to both outcomes (send-OTP always
        succeeds) so this endpoint cannot be used to enumerate customers.
        """
        clean = normalise_email(email)
        stmt = (
            select(models.User, models.Firm.name)
            .join(models.Firm, models.Firm.id == models.User.firm_id)
            .where(models.User.email == clean)
            .limit(1)
        )
        result = await self._session.execute(stmt)
        row = result.first()
        _log.info(
            "auth_directory.lookup",
            found=row is not None,
            email_domain=_email_domain(clean),
        )
        if row is None:
            return None
        user, firm_name = row[0], row[1]
        return AuthPrincipal(
            user_id=user.id,
            firm_id=user.firm_id,
            role=user.role,
            email=user.email,
            name=user.name,
            firm_name=firm_name,
        )

    async def get_principal(self, user_id: uuid.UUID) -> AuthPrincipal | None:
        """Re-resolve a principal by id — used on refresh-token rotation, so a role
        change or a removed seat takes effect without waiting for token expiry."""
        stmt = (
            select(models.User, models.Firm.name)
            .join(models.Firm, models.Firm.id == models.User.firm_id)
            .where(models.User.id == user_id)
            .limit(1)
        )
        result = await self._session.execute(stmt)
        row = result.first()
        if row is None:
            return None
        user, firm_name = row[0], row[1]
        return AuthPrincipal(
            user_id=user.id,
            firm_id=user.firm_id,
            role=user.role,
            email=user.email,
            name=user.name,
            firm_name=firm_name,
        )

    async def create_firm_with_owner(
        self,
        *,
        firm_name: str,
        email: str,
        name: str,
        coa_number: str | None = None,
    ) -> AuthPrincipal:
        """Signup: create the tenant and its first admin in one transaction.

        The bootstrap case — a firm with no users cannot be administered, and a user
        with no firm has nowhere to live, so the two rows are created together or not
        at all. The caller commits.
        """
        clean_email = normalise_email(email)
        clean_firm = firm_name.strip()
        clean_name = name.strip()
        if not clean_firm:
            raise RepositoryUsageError("Firm name cannot be blank.")
        if not clean_name:
            raise RepositoryUsageError("Your name cannot be blank.")
        if "@" not in clean_email:
            raise RepositoryUsageError("That doesn't look like an email address.")

        firm = models.Firm(name=clean_firm, settings={})
        self._session.add(firm)
        await self._session.flush()

        user = models.User(
            firm_id=firm.id,
            email=clean_email,
            name=clean_name,
            role="admin",
            coa_number=(coa_number or "").strip() or None,
        )
        self._session.add(user)
        await self._session.flush()

        _log.info(
            "auth_directory.firm_created",
            firm_id=str(firm.id),
            user_id=str(user.id),
            email_domain=_email_domain(clean_email),
        )
        return AuthPrincipal(
            user_id=user.id,
            firm_id=firm.id,
            role=user.role,
            email=user.email,
            name=user.name,
            firm_name=firm.name,
        )

    async def email_exists(self, email: str) -> bool:
        """Signup-time uniqueness pre-check (the DB unique index is the real guard)."""
        stmt = select(models.User.id).where(models.User.email == normalise_email(email)).limit(1)
        result = await self._session.execute(stmt)
        return result.first() is not None

    # -- invites (J01) -------------------------------------------------
    def _offer_select(self) -> Any:
        inviter = aliased(models.User)
        return (
            select(models.FirmInvite, models.Firm.name, inviter.name)
            .join(models.Firm, models.Firm.id == models.FirmInvite.firm_id)
            .outerjoin(inviter, inviter.id == models.FirmInvite.invited_by)
        )

    @staticmethod
    def _offer(row: Any) -> InviteOffer:
        return InviteOffer(
            invite=FirmInvite.from_row(row[0]), firm_name=row[1], invited_by_name=row[2]
        )

    async def find_open_invite_by_email(
        self, email: str, *, now: datetime | None = None
    ) -> InviteOffer | None:
        """The newest OPEN invite for an address, across every firm, or None.

        Open means not accepted, not withdrawn, not expired. Newest-first is the tie
        rule when two practices invited the same address: the most recent offer is
        the one the person most plausibly just received. The other stays pending until
        it lapses — ``users.email`` is unique, so only one can ever be accepted.

        Callers must answer identically whether this returns a row or not
        (``POST /auth/otp`` always says "sent"), for the same reason
        :meth:`find_principal_by_email` demands it.
        """
        clean = normalise_email(email)
        moment = now or datetime.now(UTC)
        stmt = (
            self._offer_select()
            .where(models.FirmInvite.email == clean)
            .where(models.FirmInvite.accepted_at.is_(None))
            .where(models.FirmInvite.revoked_at.is_(None))
            .where(models.FirmInvite.expires_at > moment)
            .order_by(models.FirmInvite.created_at.desc(), models.FirmInvite.id.desc())
            .limit(1)
        )
        result = await self._session.execute(stmt)
        row = result.first()
        _log.info(
            "auth_directory.invite_lookup",
            found=row is not None,
            email_domain=_email_domain(clean),
        )
        return None if row is None else self._offer(row)

    async def find_invite_by_token_hash(self, token_hash: str) -> InviteOffer | None:
        """Resolve a link to its invite, whatever state it is in.

        Any state, deliberately: the pre-auth status page exists to tell the holder of
        a real link that it expired or was withdrawn. The token is 256 random bits
        delivered to the invitee's mailbox, so knowing it is the authorisation to read
        this much — firm name, inviter, role, the address it was sent to.
        """
        stmt = self._offer_select().where(models.FirmInvite.token_hash == token_hash).limit(1)
        result = await self._session.execute(stmt)
        row = result.first()
        return None if row is None else self._offer(row)

    async def accept_invite(self, offer: InviteOffer) -> AuthPrincipal:
        """Turn an open invite into a member of the inviting firm. The caller commits.

        Only reachable from :meth:`garh_api.auth.AuthService.verify_otp` after a code
        sent to the invite's address has been verified — control of the mailbox IS the
        acceptance. Refuses (rather than trusts the caller) if the invite is no longer
        open or the address meanwhile gained an account: both are races this method
        must lose loudly.
        """
        invite = offer.invite
        if not invite.is_open():
            raise RepositoryUsageError("Only an open invite can be accepted.")
        if await self.email_exists(invite.email):
            raise RepositoryUsageError("That address already has an account.")

        user = models.User(
            firm_id=invite.firm_id,
            email=invite.email,
            name=invite.name,
            role=invite.role,
        )
        self._session.add(user)
        await self._session.flush()

        row = await self._session.get(models.FirmInvite, invite.id)
        if row is None:  # pragma: no cover - the offer was just read from this row
            raise RepositoryUsageError("The invite vanished while being accepted.")
        row.accepted_at = datetime.now(UTC)
        row.accepted_user_id = user.id
        await self._session.flush()

        _log.info(
            "auth_directory.invite_accepted",
            invite_id=str(invite.id),
            firm_id=str(invite.firm_id),
            user_id=str(user.id),
            user_role=user.role,
            email_domain=_email_domain(invite.email),
        )
        return AuthPrincipal(
            user_id=user.id,
            firm_id=invite.firm_id,
            role=user.role,
            email=user.email,
            name=user.name,
            firm_name=offer.firm_name,
        )


__all__ = ["AuthDirectoryRepository"]
