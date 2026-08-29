"""TOTP enrolment repository (F-4) — one row per user, always firm-scoped.

Data access only. Every rule about *when* a code counts lives in
:mod:`garh_api.twofactor`; this module's job is that no query can ever reach another
firm's row, which it gets for free from :class:`~garh_api.tenancy.Repository`.

Two shapes of write deserve a note:

* **Row-level locks.** :meth:`spend_recovery_hash` and :meth:`record_counter` are
  read-modify-write on a security counter. Two tabs submitting the same recovery code
  at the same moment would otherwise both see it unspent and both succeed, which turns
  a single-use code into a multi-use one. Both take ``FOR UPDATE``.
* **Whole-list assignment.** ``recovery_hashes`` is JSONB, and SQLAlchemy only notices
  a *replacement*. Mutating the list in place would flush nothing and silently keep a
  spent code alive — so every write here assigns a new list.

The domain dataclass lives in this module rather than in ``repositories/domain.py``
because that file belongs to another workstream; moving it there is a mechanical
follow-up (see the handoff note).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from garh_api import models
from garh_api.tenancy import Repository, RepositoryUsageError


@dataclass(frozen=True)
class TwoFactorEnrolment:
    """One user's second factor, as the service layer sees it."""

    id: uuid.UUID
    firm_id: uuid.UUID
    user_id: uuid.UUID
    secret: str
    confirmed_at: datetime | None
    last_counter: int
    recovery_hashes: list[str] = field(default_factory=list)

    @property
    def is_confirmed(self) -> bool:
        """A staged-but-never-proved enrolment does not gate sign-in."""
        return self.confirmed_at is not None

    @classmethod
    def from_row(cls, row: Any) -> TwoFactorEnrolment:
        return cls(
            id=row.id,
            firm_id=row.firm_id,
            user_id=row.user_id,
            secret=row.secret,
            confirmed_at=row.confirmed_at,
            last_counter=int(row.last_counter),
            recovery_hashes=[str(value) for value in (row.recovery_hashes or [])],
        )


class TwoFactorRepository(Repository[models.UserTwoFactor, TwoFactorEnrolment]):
    """``user_two_factor`` for one firm."""

    row_type = models.UserTwoFactor
    entity_name = "two_factor_enrolment"

    def to_domain(self, row: models.UserTwoFactor) -> TwoFactorEnrolment:
        return TwoFactorEnrolment.from_row(row)

    # -- reads ---------------------------------------------------------
    async def _row_for_user(
        self, user_id: uuid.UUID, *, for_update: bool = False
    ) -> models.UserTwoFactor | None:
        stmt = self._scoped_select().where(models.UserTwoFactor.user_id == user_id).limit(1)
        if for_update:
            stmt = stmt.with_for_update()
        return await self._first(stmt)

    async def for_user(self, user_id: uuid.UUID) -> TwoFactorEnrolment | None:
        row = await self._row_for_user(user_id)
        return None if row is None else self.to_domain(row)

    async def count_enabled(self) -> int:
        """How many seats in this firm have a *confirmed* factor. For the audit view."""
        return await self._count(
            self._scoped_select().where(models.UserTwoFactor.confirmed_at.isnot(None))
        )

    # -- writes --------------------------------------------------------
    async def upsert_pending(self, user_id: uuid.UUID, *, secret: str) -> TwoFactorEnrolment:
        """Stage a fresh, unconfirmed secret for this user.

        Replaces an existing *unconfirmed* secret (re-scanning the QR code) and
        refuses to touch a confirmed one — losing a live secret by accident is a
        lock-out, so it takes the deliberate ``remove`` path instead.
        """
        clean = (secret or "").strip()
        if not clean:
            raise RepositoryUsageError("A two-factor enrolment needs a secret.")
        row = await self._row_for_user(user_id, for_update=True)
        if row is not None:
            if row.confirmed_at is not None:
                raise RepositoryUsageError(
                    "This user already has a confirmed second factor; remove it first."
                )
            row.secret = clean
            # A new secret starts a new replay ledger: the old high-water mark belongs
            # to a key that no longer exists and would reject the first valid code.
            row.last_counter = -1
            row.recovery_hashes = []
            await self.flush()
            return self.to_domain(row)

        created = self._new_row(
            user_id=user_id,
            secret=clean,
            confirmed_at=None,
            last_counter=-1,
            recovery_hashes=[],
        )
        await self._insert(created)
        self._log.info("two_factor.staged", entity_id=str(user_id))
        return self.to_domain(created)

    async def confirm(
        self,
        user_id: uuid.UUID,
        *,
        last_counter: int,
        recovery_hashes: list[str],
        confirmed_at: datetime,
    ) -> TwoFactorEnrolment:
        """Mark the staged secret proved and install its recovery set."""
        row = await self._row_for_user(user_id, for_update=True)
        if row is None:
            raise RepositoryUsageError("There is no staged enrolment for this user.")
        row.confirmed_at = confirmed_at
        row.last_counter = int(last_counter)
        row.recovery_hashes = list(recovery_hashes)
        await self.flush()
        self._log.info("two_factor.confirmed", entity_id=str(user_id))
        return self.to_domain(row)

    async def record_counter(self, user_id: uuid.UUID, counter: int) -> int:
        """Raise the replay high-water mark. Never lowers it.

        ``max`` rather than assignment because two concurrent verifications can
        legitimately match different steps inside the drift window, and the *lower*
        one landing last would re-open the higher step for replay.
        """
        row = await self._row_for_user(user_id, for_update=True)
        if row is None:
            raise RepositoryUsageError("There is no enrolment for this user.")
        row.last_counter = max(int(row.last_counter), int(counter))
        await self.flush()
        return int(row.last_counter)

    async def spend_recovery_hash(self, user_id: uuid.UUID, digest: str) -> int:
        """Remove one recovery digest. Returns how many are left.

        Removing rather than flagging is what makes "codes remaining" a ``len()``
        with no second field to drift out of step with reality.
        """
        row = await self._row_for_user(user_id, for_update=True)
        if row is None:
            raise RepositoryUsageError("There is no enrolment for this user.")
        remaining = [str(value) for value in (row.recovery_hashes or []) if str(value) != digest]
        row.recovery_hashes = remaining
        await self.flush()
        return len(remaining)

    async def replace_recovery_hashes(
        self, user_id: uuid.UUID, hashes: list[str]
    ) -> TwoFactorEnrolment:
        row = await self._row_for_user(user_id, for_update=True)
        if row is None:
            raise RepositoryUsageError("There is no enrolment for this user.")
        row.recovery_hashes = list(hashes)
        await self.flush()
        return self.to_domain(row)

    async def remove(self, user_id: uuid.UUID) -> bool:
        """Delete the enrolment. Used by "turn 2FA off" and by DPDP erasure."""
        row = await self._row_for_user(user_id)
        if row is None:
            return False
        deleted = await self._delete_by_id(row.id)
        if deleted:
            self._log.info("two_factor.removed", entity_id=str(user_id))
        return deleted


__all__ = ["TwoFactorEnrolment", "TwoFactorRepository"]
