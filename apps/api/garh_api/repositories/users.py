"""User repository — firm members only."""

from __future__ import annotations

import uuid

from garh_api import models
from garh_api.repositories.domain import User
from garh_api.tenancy import (
    EntityNotFoundError,
    Page,
    Repository,
    RepositoryUsageError,
)


def normalise_email(email: str) -> str:
    """Lowercase + strip. The DB has a matching CHECK, so this is not optional."""
    return email.strip().lower()


class UserRepository(Repository[models.User, User]):
    """Members of the caller's firm.

    Login-time lookup (email → firm, before a context exists) is NOT here — it lives
    in :class:`~garh_api.repositories.auth_directory.AuthDirectoryRepository`. Keeping
    them apart is what lets this class stay unconditionally firm-scoped.
    """

    row_type = models.User
    entity_name = "user"

    def to_domain(self, row: models.User) -> User:
        return User.from_row(row)

    # -- reads ---------------------------------------------------------
    async def get_by_email(self, email: str) -> User | None:
        """Find a member of *this* firm by email."""
        stmt = self._scoped_select().where(models.User.email == normalise_email(email)).limit(1)
        row = await self._first(stmt)
        return None if row is None else self.to_domain(row)

    async def list_members(
        self, *, limit: int | None = None, cursor: str | None = None
    ) -> Page[User]:
        return await self._page(limit=limit, cursor=cursor, newest_first=False)

    async def list_admins(self) -> list[User]:
        rows = await self._all(self._scoped_select().where(models.User.role == "admin"))
        return [self.to_domain(row) for row in rows]

    async def count_admins(self) -> int:
        return await self._count(self._scoped_select().where(models.User.role == "admin"))

    # -- writes --------------------------------------------------------
    async def create(
        self,
        *,
        email: str,
        name: str,
        role: str = "member",
        coa_number: str | None = None,
    ) -> User:
        """Invite/create a member. Admin-only (seat management)."""
        self.ctx.require_admin("adding a team member")
        if role not in models.USER_ROLES:
            raise RepositoryUsageError("role must be one of %s." % ", ".join(models.USER_ROLES))
        clean_name = name.strip()
        if not clean_name:
            raise RepositoryUsageError("User name cannot be blank.")
        row = self._new_row(
            email=normalise_email(email),
            name=clean_name,
            role=role,
            coa_number=(coa_number or "").strip() or None,
        )
        await self._insert(row)
        self._log.info("user.created", entity_id=str(row.id), user_role=role)
        return self.to_domain(row)

    async def update_profile(
        self,
        user_id: uuid.UUID,
        *,
        name: str | None = None,
        coa_number: str | None = None,
    ) -> User:
        """Self-service profile edit; an admin may edit any member of the firm."""
        if not self.ctx.is_admin and self.actor_id != user_id:
            self.ctx.require_admin("editing another member's profile")
        row = await self._require_row(user_id)
        patch: dict[str, object] = {}
        if name is not None:
            clean = name.strip()
            if not clean:
                raise RepositoryUsageError("User name cannot be blank.")
            patch["name"] = clean
        if coa_number is not None:
            row.coa_number = coa_number.strip() or None
        if patch:
            await self._apply_patch(row, patch)
        else:
            await self.flush()
        return self.to_domain(row)

    async def set_role(self, user_id: uuid.UUID, role: str) -> User:
        """Promote/demote. Refuses to remove the firm's last admin."""
        self.ctx.require_admin("changing a member's role")
        if role not in models.USER_ROLES:
            raise RepositoryUsageError("role must be one of %s." % ", ".join(models.USER_ROLES))
        row = await self._require_row(user_id)
        if row.role == "admin" and role != "admin" and await self.count_admins() <= 1:
            raise RepositoryUsageError(
                "This is the firm's only admin — promote someone else first."
            )
        row.role = role
        await self.flush()
        self._log.info("user.role_changed", entity_id=str(user_id), user_role=role)
        return self.to_domain(row)

    async def remove(self, user_id: uuid.UUID) -> bool:
        """Remove a member. Refuses to remove the last admin or the caller."""
        self.ctx.require_admin("removing a team member")
        if self.actor_id == user_id:
            raise RepositoryUsageError("You cannot remove your own account.")
        row = await self._row_by_id(user_id)
        if row is None:
            raise EntityNotFoundError(type(self).entity_name, user_id)
        if row.role == "admin" and await self.count_admins() <= 1:
            raise RepositoryUsageError(
                "This is the firm's only admin — promote someone else first."
            )
        deleted = await self._delete_by_id(user_id)
        if deleted:
            self._log.info("user.removed", entity_id=str(user_id))
        return deleted


__all__ = ["UserRepository", "normalise_email"]
