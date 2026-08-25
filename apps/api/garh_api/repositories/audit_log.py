"""Audit-log repository (§13).

What must be audited: auth events, exports, share-link creation, regulatory-profile
overrides, deletions — plus every use of
:func:`~garh_api.tenancy.system_unscoped_session` (written by tenancy itself).

Append-only: no update, no delete. :meth:`AuditLogRepository.delete` raises. The table
has no foreign keys so entries outlive the firm and user they describe.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from garh_api import models
from garh_api.repositories.domain import AuditEntry
from garh_api.tenancy import Page, Repository, RepositoryUsageError

# Canonical action strings. Extend this list rather than inventing strings at call
# sites — the security review greps for them, and typos are invisible failures.
ACTION_AUTH_OTP_REQUESTED = "auth.otp_requested"
ACTION_AUTH_OTP_VERIFIED = "auth.otp_verified"
ACTION_AUTH_OTP_FAILED = "auth.otp_failed"
ACTION_AUTH_SIGNUP = "auth.signup"
ACTION_AUTH_LOGOUT = "auth.logout"
ACTION_AUTH_LOGOUT_ALL = "auth.logout_all"
ACTION_AUTH_TOKEN_REFRESHED = "auth.token_refreshed"
#: §13 "audit_log on auth events". Refresh-token reuse means a stolen token was
#: replayed; it is the single most important row in this table, and it was the one
#: action declared only inside ``garh_api.auth`` rather than here.
ACTION_AUTH_REFRESH_REUSE = "auth.refresh_reuse_detected"
ACTION_PROJECT_DELETED = "project.deleted"
ACTION_PROJECT_ARCHIVED = "project.archived"
ACTION_EXPORT_CREATED = "export.created"
ACTION_EXPORT_DOWNLOADED = "export.downloaded"
ACTION_SHARE_CREATED = "share.created"
ACTION_SHARE_REVOKED = "share.revoked"
ACTION_REG_PROFILE_OVERRIDDEN = "reg_profile.overridden"
ACTION_COMPLIANCE_OVERRIDDEN = "compliance.overridden"
ACTION_USER_ROLE_CHANGED = "user.role_changed"
ACTION_USER_REMOVED = "user.removed"
ACTION_FIRM_SETTINGS_CHANGED = "firm.settings_changed"

AUDIT_ACTIONS: tuple[str, ...] = (
    ACTION_AUTH_OTP_REQUESTED,
    ACTION_AUTH_OTP_VERIFIED,
    ACTION_AUTH_OTP_FAILED,
    ACTION_AUTH_SIGNUP,
    ACTION_AUTH_LOGOUT,
    ACTION_AUTH_LOGOUT_ALL,
    ACTION_AUTH_TOKEN_REFRESHED,
    ACTION_AUTH_REFRESH_REUSE,
    ACTION_PROJECT_DELETED,
    ACTION_PROJECT_ARCHIVED,
    ACTION_EXPORT_CREATED,
    ACTION_EXPORT_DOWNLOADED,
    ACTION_SHARE_CREATED,
    ACTION_SHARE_REVOKED,
    ACTION_REG_PROFILE_OVERRIDDEN,
    ACTION_COMPLIANCE_OVERRIDDEN,
    ACTION_USER_ROLE_CHANGED,
    ACTION_USER_REMOVED,
    ACTION_FIRM_SETTINGS_CHANGED,
)


class AuditLogRepository(Repository[models.AuditLog, AuditEntry]):
    """Write and read the firm's audit trail."""

    row_type = models.AuditLog
    entity_name = "audit_entry"

    def to_domain(self, row: models.AuditLog) -> AuditEntry:
        return AuditEntry.from_row(row)

    async def delete(self, entity_id: Any) -> bool:
        raise RepositoryUsageError("The audit log is append-only.")

    # -- writes --------------------------------------------------------
    async def record(
        self,
        action: str,
        *,
        entity: str,
        entity_id: Any = None,
        meta: dict[str, Any] | None = None,
        user_id: uuid.UUID | None = None,
    ) -> AuditEntry:
        """Append an entry. ``user_id`` defaults to the context's actor.

        ``meta`` must not contain secrets or PII beyond what the action inherently
        needs — an OTP audit records the email *domain*, never the code.
        """
        clean_action = (action or "").strip()
        clean_entity = (entity or "").strip()
        if not clean_action:
            raise RepositoryUsageError("An audit entry needs an action.")
        if not clean_entity:
            raise RepositoryUsageError("An audit entry needs an entity.")
        row = self._new_row(
            user_id=user_id if user_id is not None else self.actor_id,
            action=clean_action,
            entity=clean_entity,
            entity_id=None if entity_id is None else str(entity_id),
            meta=meta or {},
        )
        await self._insert(row)
        self._log.info(
            "audit.recorded",
            audit_action=clean_action,
            entity=clean_entity,
            entity_id=None if entity_id is None else str(entity_id),
        )
        return self.to_domain(row)

    # -- reads ---------------------------------------------------------
    async def list_recent(
        self,
        *,
        limit: int | None = None,
        cursor: str | None = None,
        action: str | None = None,
        entity: str | None = None,
        since: datetime | None = None,
    ) -> Page[AuditEntry]:
        stmt = self._scoped_select()
        if action is not None:
            stmt = stmt.where(models.AuditLog.action == action)
        if entity is not None:
            stmt = stmt.where(models.AuditLog.entity == entity)
        if since is not None:
            stmt = stmt.where(models.AuditLog.created_at >= since)
        return await self._page(stmt, limit=limit, cursor=cursor, newest_first=True)

    async def list_for_entity(
        self, entity: str, entity_id: Any, *, limit: int = 100
    ) -> list[AuditEntry]:
        stmt = (
            self._scoped_select()
            .where(models.AuditLog.entity == entity)
            .where(models.AuditLog.entity_id == str(entity_id))
            .order_by(models.AuditLog.created_at.desc())
            .limit(max(1, min(limit, 500)))
        )
        return [self.to_domain(row) for row in await self._all(stmt)]


__all__ = [
    "ACTION_AUTH_LOGOUT",
    "ACTION_AUTH_LOGOUT_ALL",
    "ACTION_AUTH_OTP_FAILED",
    "ACTION_AUTH_OTP_REQUESTED",
    "ACTION_AUTH_OTP_VERIFIED",
    "ACTION_AUTH_REFRESH_REUSE",
    "ACTION_AUTH_SIGNUP",
    "ACTION_AUTH_TOKEN_REFRESHED",
    "ACTION_COMPLIANCE_OVERRIDDEN",
    "ACTION_EXPORT_CREATED",
    "ACTION_EXPORT_DOWNLOADED",
    "ACTION_FIRM_SETTINGS_CHANGED",
    "ACTION_PROJECT_ARCHIVED",
    "ACTION_PROJECT_DELETED",
    "ACTION_REG_PROFILE_OVERRIDDEN",
    "ACTION_SHARE_CREATED",
    "ACTION_SHARE_REVOKED",
    "ACTION_USER_REMOVED",
    "ACTION_USER_ROLE_CHANGED",
    "AUDIT_ACTIONS",
    "AuditLogRepository",
]
