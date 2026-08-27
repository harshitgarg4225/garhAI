"""Underlay repository — the tracing-underlay sidecar, one row per project.

The underlay is a tracing AID (a raster the architect traces walls over), not
design state: it lives beside the model, is mutated by plain CRUD rather than
ops, and its edits are deliberately NOT undoable. The one-per-project rule is a
schema fact (``uq_project_underlays_project_id``), and this repository's
:meth:`upsert` is what "replacing uploads overwrite" means in code.

Validation posture mirrors ``plots.py``: the HTTP schemas already type-check the
boundary, but the repository re-asserts the invariants that would corrupt the
canvas silently (a zero/negative scale, an out-of-range opacity) because a
repository must not rely on being called only from one router.
"""

from __future__ import annotations

import math
import uuid
from typing import Any

from garh_api import models
from garh_api.repositories._guards import require_project_in_firm
from garh_api.repositories.domain import Underlay
from garh_api.tenancy import (
    EntityNotFoundError,
    ProjectScopedRepository,
    RepositoryUsageError,
)

#: Bounds on the parsed pixel dimensions. The floor rejects degenerate rasters;
#: the ceiling keeps a hostile 1-gigapixel PNG from becoming a GPU texture
#: allocation on every canvas that loads the project. 16k on an edge is beyond
#: any sane plan scan and still within common GPU texture limits.
MAX_UNDERLAY_EDGE_PX = 16_384


class UnderlayRepository(ProjectScopedRepository[models.ProjectUnderlay, Underlay]):
    """The one underlay of a project. All writes require a write role."""

    row_type = models.ProjectUnderlay
    entity_name = "underlay"

    def to_domain(self, row: models.ProjectUnderlay) -> Underlay:
        return Underlay.from_row(row)

    # -- reads ---------------------------------------------------------
    async def get_for_project(self, project_id: uuid.UUID) -> Underlay | None:
        row = await self._first(self._project_scoped_select(project_id).limit(1))
        return None if row is None else self.to_domain(row)

    # -- writes --------------------------------------------------------
    async def upsert_image(
        self,
        project_id: uuid.UUID,
        *,
        object_key: str,
        width_px: int,
        height_px: int,
    ) -> Underlay:
        """Create the underlay, or replace its image in place.

        Replacement keeps the architect's view state (opacity/locked/visible) —
        those are preferences about the workflow, not about one file. Calibration
        (``mm_per_px`` + origin) survives only when the new image has the SAME
        pixel dimensions: that is the "re-uploaded a cleaner scan of the same
        sheet" case, where losing a two-point calibration would be infuriating.
        Different dimensions mean a different drawing, and a stale scale factor
        silently lying about lengths is exactly what this feature must never do —
        so calibration resets to the 1 mm/px default.
        """
        self.ctx.require_write("uploading an underlay")
        _validate_dimensions(width_px, height_px)
        if not (object_key or "").strip():
            raise RepositoryUsageError("object_key must not be blank.")
        await require_project_in_firm(self._session, self.firm_id, project_id)

        row = await self._first(self._project_scoped_select(project_id).limit(1))
        if row is None:
            row = self._new_row(
                project_id=project_id,
                object_key=object_key,
                width_px=width_px,
                height_px=height_px,
                mm_per_px=1.0,
                origin_x_mm=0,
                origin_y_mm=0,
                opacity=0.5,
                locked=False,
                visible=True,
            )
            await self._insert(row)
            self._log.info(
                "underlay.created",
                project_id=str(project_id),
                width_px=width_px,
                height_px=height_px,
            )
            return self.to_domain(row)

        same_dimensions = row.width_px == width_px and row.height_px == height_px
        row.object_key = object_key
        row.width_px = width_px
        row.height_px = height_px
        if not same_dimensions:
            row.mm_per_px = 1.0
            row.origin_x_mm = 0
            row.origin_y_mm = 0
        await self.flush()
        self._log.info(
            "underlay.replaced",
            project_id=str(project_id),
            width_px=width_px,
            height_px=height_px,
            calibration_kept=same_dimensions,
        )
        return self.to_domain(row)

    async def patch(
        self,
        project_id: uuid.UUID,
        *,
        mm_per_px: float | None = None,
        origin_x_mm: int | None = None,
        origin_y_mm: int | None = None,
        opacity: float | None = None,
        locked: bool | None = None,
        visible: bool | None = None,
    ) -> Underlay:
        """Partial update — only supplied fields change. 404 when no underlay exists."""
        self.ctx.require_write("adjusting the underlay")
        await require_project_in_firm(self._session, self.firm_id, project_id)

        if mm_per_px is not None:
            _validate_mm_per_px(mm_per_px)
        if opacity is not None:
            _validate_opacity(opacity)
        for name, value in (("origin_x_mm", origin_x_mm), ("origin_y_mm", origin_y_mm)):
            if value is not None and (isinstance(value, bool) or not isinstance(value, int)):
                raise RepositoryUsageError("%s must be an integer number of millimetres." % name)

        row = await self._first(self._project_scoped_select(project_id).limit(1))
        if row is None:
            raise EntityNotFoundError("underlay", project_id)

        if mm_per_px is not None:
            row.mm_per_px = float(mm_per_px)
        if origin_x_mm is not None:
            row.origin_x_mm = origin_x_mm
        if origin_y_mm is not None:
            row.origin_y_mm = origin_y_mm
        if opacity is not None:
            row.opacity = float(opacity)
        if locked is not None:
            row.locked = locked
        if visible is not None:
            row.visible = visible
        await self.flush()
        self._log.info("underlay.patched", project_id=str(project_id))
        return self.to_domain(row)

    async def delete_for_project(self, project_id: uuid.UUID) -> Underlay:
        """Remove the row, returning it so the caller can best-effort delete the object."""
        self.ctx.require_write("removing the underlay")
        await require_project_in_firm(self._session, self.firm_id, project_id)
        row = await self._first(self._project_scoped_select(project_id).limit(1))
        if row is None:
            raise EntityNotFoundError("underlay", project_id)
        removed = self.to_domain(row)
        await self._delete_by_id(row.id)
        self._log.info("underlay.deleted", project_id=str(project_id))
        return removed


# ---------------------------------------------------------------------------
# validation helpers
# ---------------------------------------------------------------------------


def _validate_dimensions(width_px: Any, height_px: Any) -> None:
    for name, value in (("width_px", width_px), ("height_px", height_px)):
        if isinstance(value, bool) or not isinstance(value, int):
            raise RepositoryUsageError("%s must be an integer pixel count." % name)
        if not 0 < value <= MAX_UNDERLAY_EDGE_PX:
            raise RepositoryUsageError(
                "%s must be between 1 and %d pixels." % (name, MAX_UNDERLAY_EDGE_PX)
            )


def _validate_mm_per_px(value: Any) -> None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise RepositoryUsageError("mm_per_px must be a number.")
    if not math.isfinite(float(value)) or float(value) <= 0:
        raise RepositoryUsageError("mm_per_px must be a finite number greater than zero.")


def _validate_opacity(value: Any) -> None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise RepositoryUsageError("opacity must be a number.")
    if not math.isfinite(float(value)) or not 0.0 <= float(value) <= 1.0:
        raise RepositoryUsageError("opacity must be between 0 and 1.")


__all__ = ["MAX_UNDERLAY_EDGE_PX", "UnderlayRepository"]
