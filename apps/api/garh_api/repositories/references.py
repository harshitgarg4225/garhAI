"""Inspiration-board repository — many reference images per project.

Many rows per project, unlike ``underlays.py`` which is one: a board with a single
picture on it is not a board. Like the underlay these are a SIDECAR — a reference steers
a render's prompt and touches no geometry, so it is plain CRUD rather than ops, and its
edits are deliberately not undoable.

Validation posture mirrors ``underlays.py``: the HTTP schemas type-check the boundary,
and this re-asserts the invariants a bad write would corrupt quietly — a scope the
render side does not recognise is a picture that contributes nothing to any render,
forever, with no message.
"""

from __future__ import annotations

import uuid
from typing import Any

from garh_api import models
from garh_api.repositories._guards import require_project_in_firm
from garh_api.repositories.domain import ProjectReference
from garh_api.tenancy import (
    ProjectScopedRepository,
    RepositoryUsageError,
)

#: Bounds on the parsed pixel dimensions, same reasoning as the underlay's: the floor
#: rejects a degenerate raster, the ceiling stops a hostile gigapixel PNG.
MAX_REFERENCE_EDGE_PX = 16_384

#: A board is a working set, not an archive. Past this it stops being something an
#: architect can hold in their head, and every render would carry a prompt nobody wrote.
MAX_REFERENCES_PER_PROJECT = 40


class ReferenceRepository(ProjectScopedRepository[models.ProjectReference, ProjectReference]):
    """A project's inspiration board. All writes require a write role."""

    row_type = models.ProjectReference
    entity_name = "reference"

    def to_domain(self, row: models.ProjectReference) -> ProjectReference:
        return ProjectReference.from_row(row)

    # -- reads ---------------------------------------------------------
    async def list_for_project(self, project_id: uuid.UUID) -> list[ProjectReference]:
        """The board in the architect's own order.

        That order is the only ranking the render side applies, so it is sorted here
        rather than left to insertion order — two boards that look different must not
        produce the same prompt.
        """
        stmt = self._project_scoped_select(project_id).order_by(
            models.ProjectReference.position, models.ProjectReference.created_at
        )
        rows = await self._all(stmt)
        return [self.to_domain(row) for row in rows]

    async def require(self, reference_id: uuid.UUID) -> ProjectReference:
        row = await self._require_row(reference_id)
        return self.to_domain(row)

    # -- writes --------------------------------------------------------
    async def add(
        self,
        project_id: uuid.UUID,
        *,
        object_key: str,
        content_type: str,
        width_px: int,
        height_px: int,
        filename: str = "",
        label: str = "",
        scope: str = "whole-house",
        why: str = "",
        ignore_note: str = "",
        intent: str = "guide",
    ) -> ProjectReference:
        """Pin one picture to the board.

        The annotation defaults are deliberately the *weakest* ones: ``whole-house`` and
        ``guide`` claim the least about a picture nobody has annotated yet, and an empty
        ``why`` is what makes the render side raise "what should this contribute?"
        rather than inventing an answer.
        """
        self.ctx.require_write("adding a reference image")
        # The router checks this too. Kept here because a repository that will happily
        # write a row into another firm's project is one lazy import away from being
        # called without a router in front of it.
        await require_project_in_firm(self._session, self.firm_id, project_id)
        self._check_vocabulary(scope, intent)
        for name, value in (("width_px", width_px), ("height_px", height_px)):
            if not 0 < value <= MAX_REFERENCE_EDGE_PX:
                raise RepositoryUsageError(
                    "%s must be between 1 and %d." % (name, MAX_REFERENCE_EDGE_PX)
                )
        if not object_key.strip():
            raise RepositoryUsageError("A reference needs the storage key of its image.")

        existing = await self.list_for_project(project_id)
        if len(existing) >= MAX_REFERENCES_PER_PROJECT:
            raise RepositoryUsageError(
                "This board already holds %d references, which is the limit."
                % MAX_REFERENCES_PER_PROJECT
            )

        row = models.ProjectReference(
            id=uuid.uuid4(),
            firm_id=self.firm_id,
            project_id=project_id,
            object_key=object_key.strip(),
            filename=filename[:200],
            content_type=content_type,
            width_px=width_px,
            height_px=height_px,
            # A label the architect recognises. The filename is a better fallback than a
            # uuid, and "Reference 3" is better than a blank chip in a conflict question.
            label=(label.strip() or filename.strip() or "Reference %d" % (len(existing) + 1))[:120],
            scope=scope,
            why=why[:400],
            ignore_note=ignore_note[:400],
            intent=intent,
            position=len(existing),
        )
        await self._insert(row)
        return self.to_domain(row)

    async def annotate(self, reference_id: uuid.UUID, patch: dict[str, Any]) -> ProjectReference:
        """Update any of the architect's four answers. Absent keys are left alone."""
        self.ctx.require_write("annotating a reference image")
        row = await self._require_row(reference_id)
        self._check_vocabulary(patch.get("scope"), patch.get("intent"))

        if patch.get("label") is not None:
            label = str(patch["label"]).strip()
            if not label:
                raise RepositoryUsageError("A reference needs a name you will recognise.")
            row.label = label[:120]
        for field, column in (("scope", "scope"), ("intent", "intent")):
            if patch.get(field) is not None:
                setattr(row, column, str(patch[field]))
        if patch.get("why") is not None:
            row.why = str(patch["why"])[:400]
        if patch.get("ignore") is not None:
            row.ignore_note = str(patch["ignore"])[:400]
        if patch.get("position") is not None:
            row.position = max(0, int(patch["position"]))
        await self.flush()
        return self.to_domain(row)

    async def delete(self, reference_id: uuid.UUID) -> str:
        """Remove one, returning its storage key so the caller can delete the bytes."""
        self.ctx.require_write("removing a reference image")
        row = await self._require_row(reference_id)
        object_key = row.object_key
        await self._delete_by_id(row.id)
        return object_key

    # -- guards --------------------------------------------------------
    def _check_vocabulary(self, scope: Any, intent: Any) -> None:
        """A value the render side cannot read is a picture that steers nothing.

        The database CHECK would catch it too, but as a 500 at flush time rather than a
        sentence naming what is allowed.
        """
        if scope is not None and str(scope) not in models.REFERENCE_SCOPES:
            raise RepositoryUsageError(
                "scope must be one of %s." % ", ".join(models.REFERENCE_SCOPES)
            )
        if intent is not None and str(intent) not in models.REFERENCE_INTENTS:
            raise RepositoryUsageError(
                "intent must be one of %s." % ", ".join(models.REFERENCE_INTENTS)
            )


__all__ = [
    "MAX_REFERENCES_PER_PROJECT",
    "MAX_REFERENCE_EDGE_PX",
    "ReferenceRepository",
]
