"""Comment repository — pin comments from firm users and share-link viewers."""

from __future__ import annotations

import uuid
from typing import Any

from garh_api import models
from garh_api.repositories._guards import require_project_in_firm
from garh_api.repositories.domain import Comment
from garh_api.tenancy import (
    Page,
    PermissionDeniedError,
    ProjectScopedRepository,
    RepositoryUsageError,
)

MAX_COMMENT_LENGTH = 4000


class CommentRepository(ProjectScopedRepository[models.Comment, Comment]):
    """Comments pinned to a plan, sheet or render.

    Anonymous share-link viewers write here too, which is the one place a
    ``share_viewer`` context is allowed to insert. Two gates make that safe:
    :meth:`create_from_share` requires the link's scope to carry ``canComment``, and it
    stamps ``share_link_id`` from the context rather than from the request body, so a
    viewer cannot attribute a comment to a different link.
    """

    row_type = models.Comment
    entity_name = "comment"

    def to_domain(self, row: models.Comment) -> Comment:
        return Comment.from_row(row)

    # -- reads ---------------------------------------------------------
    async def list_open_for_project(self, project_id: uuid.UUID) -> list[Comment]:
        stmt = (
            self._project_scoped_select(project_id)
            .where(models.Comment.resolved.is_(False))
            .order_by(models.Comment.created_at.asc())
        )
        return [self.to_domain(row) for row in await self._all(stmt)]

    async def list_thread(
        self, project_id: uuid.UUID, *, limit: int | None = None, cursor: str | None = None
    ) -> Page[Comment]:
        return await self.list_for_project(
            project_id, limit=limit, cursor=cursor, newest_first=False
        )

    async def list_for_share_link(self, share_link_id: uuid.UUID) -> list[Comment]:
        stmt = (
            self._scoped_select()
            .where(models.Comment.share_link_id == share_link_id)
            .order_by(models.Comment.created_at.asc())
        )
        return [self.to_domain(row) for row in await self._all(stmt)]

    async def count_open(self, project_id: uuid.UUID) -> int:
        return await self._count(
            self._project_scoped_select(project_id).where(models.Comment.resolved.is_(False))
        )

    # -- writes --------------------------------------------------------
    async def create(
        self,
        project_id: uuid.UUID,
        *,
        body: str,
        author_name: str,
        anchor: dict[str, Any] | None = None,
        share_link_id: uuid.UUID | None = None,
    ) -> Comment:
        """Comment as a firm user (or a worker). Viewers use :meth:`create_from_share`."""
        self.ctx.require_write("commenting")
        return await self._insert_comment(
            project_id,
            body=body,
            author_name=author_name,
            anchor=anchor,
            share_link_id=share_link_id,
        )

    async def create_from_share(
        self,
        *,
        body: str,
        author_name: str,
        anchor: dict[str, Any] | None = None,
    ) -> Comment:
        """Comment as an anonymous share-link viewer.

        Project and share-link id both come from the resolved token in the context,
        never from the request.
        """
        ctx = self.ctx
        if not ctx.is_share_viewer or ctx.share_link_id is None:
            raise RepositoryUsageError(
                "create_from_share() requires a share_viewer context."
            )
        if not ctx.scope.get("canComment"):
            raise PermissionDeniedError("This link is view-only, so comments are off.")
        raw_project_id = ctx.scope.get("projectId")
        if not raw_project_id:
            raise RepositoryUsageError("The share scope is missing projectId.")
        return await self._insert_comment(
            uuid.UUID(str(raw_project_id)),
            body=body,
            author_name=author_name,
            anchor=anchor,
            share_link_id=ctx.share_link_id,
        )

    async def _insert_comment(
        self,
        project_id: uuid.UUID,
        *,
        body: str,
        author_name: str,
        anchor: dict[str, Any] | None,
        share_link_id: uuid.UUID | None,
    ) -> Comment:
        clean_body = (body or "").strip()
        if not clean_body:
            raise RepositoryUsageError("A comment needs some text.")
        if len(clean_body) > MAX_COMMENT_LENGTH:
            raise RepositoryUsageError(
                "Comments are limited to %d characters." % MAX_COMMENT_LENGTH
            )
        clean_author = (author_name or "").strip() or "Guest"
        await require_project_in_firm(self._session, self.firm_id, project_id)
        row = self._new_row(
            project_id=project_id,
            share_link_id=share_link_id,
            anchor=anchor or {},
            body=clean_body,
            author_name=clean_author[:200],
            resolved=False,
        )
        await self._insert(row)
        self._log.info(
            "comment.created",
            entity_id=str(row.id),
            project_id=str(project_id),
            from_share=share_link_id is not None,
        )
        return self.to_domain(row)

    async def set_resolved(self, comment_id: uuid.UUID, resolved: bool) -> Comment:
        """Resolve/unresolve. Firm users only — viewers must not close their own threads."""
        if self.ctx.is_share_viewer:
            raise PermissionDeniedError("Only the design team can resolve comments.")
        row = await self._require_row(comment_id)
        row.resolved = resolved
        await self.flush()
        return self.to_domain(row)


__all__ = ["MAX_COMMENT_LENGTH", "CommentRepository"]
