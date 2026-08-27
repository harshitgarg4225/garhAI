"""Plot repository — one plot per project (``unique(project_id)``)."""

from __future__ import annotations

import uuid
from typing import Any

from garh_api import models
from garh_api.repositories._guards import require_project_in_firm
from garh_api.repositories.domain import Plot
from garh_api.tenancy import EntityNotFoundError, ProjectScopedRepository, RepositoryUsageError


class PlotRepository(ProjectScopedRepository[models.Plot, Plot]):
    """The plot boundary + regulatory context for a project.

    ``PUT /projects/:id/plot`` is an upsert (§11), so :meth:`upsert` is the main
    entry point. Boundary coordinates are integer millimetres in plot-local space
    (origin SW, +X east, +Y north); this layer validates their *shape* and refuses
    floats — the geometric validation (closed ring, positive area, rect/L/T) belongs
    to the model core, which runs before the op that lands here.
    """

    row_type = models.Plot
    entity_name = "plot"

    def to_domain(self, row: models.Plot) -> Plot:
        return Plot.from_row(row)

    # -- reads ---------------------------------------------------------
    async def get_for_project(self, project_id: uuid.UUID) -> Plot | None:
        row = await self._first(self._project_scoped_select(project_id).limit(1))
        return None if row is None else self.to_domain(row)

    async def require_for_project(self, project_id: uuid.UUID) -> Plot:
        plot = await self.get_for_project(project_id)
        if plot is None:
            raise EntityNotFoundError("plot", project_id)
        return plot

    # -- writes --------------------------------------------------------
    async def upsert(
        self,
        project_id: uuid.UUID,
        *,
        boundary: list[Any] | None = None,
        north_deg: int | None = None,
        roads: list[Any] | None = None,
        reg_profile: dict[str, Any] | None = None,
        source: str | None = None,
    ) -> Plot:
        """Create or update the project's plot. Only supplied fields change."""
        self.ctx.require_write("editing the plot")
        await require_project_in_firm(self._session, self.firm_id, project_id)

        if boundary is not None:
            _validate_boundary(boundary)
        if north_deg is not None:
            _validate_north(north_deg)
        if roads is not None:
            _validate_roads(roads)
        if source is not None and source not in models.PLOT_SOURCES:
            raise RepositoryUsageError("source must be one of %s." % ", ".join(models.PLOT_SOURCES))

        row = await self._first(self._project_scoped_select(project_id).limit(1))
        if row is None:
            row = self._new_row(
                project_id=project_id,
                boundary=boundary if boundary is not None else [],
                north_deg=north_deg if north_deg is not None else 0,
                roads=roads if roads is not None else [],
                reg_profile=reg_profile if reg_profile is not None else {},
                source=source or "manual",
            )
            await self._insert(row)
            self._log.info("plot.created", project_id=str(project_id), source=row.source)
            return self.to_domain(row)

        if boundary is not None:
            row.boundary = boundary
        if north_deg is not None:
            row.north_deg = north_deg
        if roads is not None:
            row.roads = roads
        if reg_profile is not None:
            row.reg_profile = reg_profile
        if source is not None:
            row.source = source
        await self.flush()
        self._log.info("plot.updated", project_id=str(project_id))
        return self.to_domain(row)

    async def set_reg_profile(self, project_id: uuid.UUID, reg_profile: dict[str, Any]) -> Plot:
        """Replace the resolved regulatory profile.

        Overrides are audited (§13 lists "reg-profile overrides"), so the caller must
        also write an ``audit_log`` row.
        """
        return await self.upsert(project_id, reg_profile=reg_profile)

    async def set_north(self, project_id: uuid.UUID, north_deg: int) -> Plot:
        return await self.upsert(project_id, north_deg=north_deg)


# ---------------------------------------------------------------------------
# validation helpers (integer mm discipline lives here too)
# ---------------------------------------------------------------------------


def _validate_north(north_deg: Any) -> None:
    if isinstance(north_deg, bool) or not isinstance(north_deg, int):
        raise RepositoryUsageError("north_deg must be an integer number of degrees.")
    if not 0 <= north_deg < 360:
        raise RepositoryUsageError("north_deg must be between 0 and 359.")


def _validate_boundary(boundary: Any) -> None:
    if not isinstance(boundary, list):
        raise RepositoryUsageError("boundary must be a list of points.")
    if boundary and len(boundary) < 3:
        raise RepositoryUsageError("A plot boundary needs at least 3 points.")
    for i, point in enumerate(boundary):
        if not isinstance(point, dict) or "x" not in point or "y" not in point:
            raise RepositoryUsageError('boundary[%d] must be {"x": mm, "y": mm}.' % i)
        for axis in ("x", "y"):
            value = point[axis]
            if isinstance(value, bool) or not isinstance(value, int):
                raise RepositoryUsageError(
                    "boundary[%d].%s must be an integer number of millimetres (got %r)."
                    % (i, axis, value)
                )


def _validate_roads(roads: Any) -> None:
    if not isinstance(roads, list):
        raise RepositoryUsageError("roads must be a list.")
    for i, road in enumerate(roads):
        if not isinstance(road, dict) or "edgeIndex" not in road:
            raise RepositoryUsageError(
                'roads[%d] must be {"edgeIndex": int, "widthMm": int | null}.' % i
            )
        edge = road["edgeIndex"]
        if isinstance(edge, bool) or not isinstance(edge, int) or edge < 0:
            raise RepositoryUsageError("roads[%d].edgeIndex must be a non-negative int." % i)
        width = road.get("widthMm")
        if width is None:
            continue
        if isinstance(width, bool) or not isinstance(width, int) or width <= 0:
            raise RepositoryUsageError(
                "roads[%d].widthMm must be a positive integer number of millimetres." % i
            )


__all__ = ["PlotRepository"]
