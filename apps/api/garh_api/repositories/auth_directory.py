"""Pre-authentication directory — the ONLY non-tenant read of ``users``/``firms``.

Why this exists: at ``POST /auth/verify`` there is no ``firm_id`` yet. The whole point
of the OTP flow is to *discover* which firm an email belongs to, so a firm-scoped
repository is logically impossible here — you would need the answer to ask the
question.

Why it is safe:

* It returns only :class:`~garh_api.repositories.domain.AuthPrincipal` — user id,
  firm id, role, email, name, firm name. It cannot reach projects, plots, briefs,
  ops, renders, sheets or comments. There is no generic query method.
* Lookup is by exact normalised email or by user id. There is no listing, no
  wildcard, no "find users in other firms".
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

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from garh_api import models
from garh_api.logging import get_logger
from garh_api.repositories.domain import AuthPrincipal
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


__all__ = ["AuthDirectoryRepository"]
