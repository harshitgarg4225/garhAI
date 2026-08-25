"""Compliance-report repository (playbook §6).

The live editor runs the rules engine in-process (<100ms, debounced) and does not
persist every run — that would be thousands of rows per session. What lands here is
the *frozen* result for a design version: what the area statement quotes, what the
solver critic recorded, and what the client saw on a shared link. One source of
numbers for the sheet set and the compliance tab (§7: "same numbers, one source").
"""

from __future__ import annotations

import uuid
from typing import Any

from garh_api import models
from garh_api.repositories._guards import (
    require_design_version_in_firm,
    require_project_in_firm,
)
from garh_api.repositories.domain import ComplianceReport
from garh_api.tenancy import ProjectScopedRepository, RepositoryUsageError


class ComplianceReportRepository(
    ProjectScopedRepository[models.ComplianceReport, ComplianceReport]
):
    row_type = models.ComplianceReport
    entity_name = "compliance_report"

    def to_domain(self, row: models.ComplianceReport) -> ComplianceReport:
        return ComplianceReport.from_row(row)

    # -- reads ---------------------------------------------------------
    async def latest_for_version(
        self, project_id: uuid.UUID, design_version_id: uuid.UUID
    ) -> ComplianceReport | None:
        stmt = (
            self._project_scoped_select(project_id)
            .where(models.ComplianceReport.design_version_id == design_version_id)
            .order_by(models.ComplianceReport.created_at.desc())
            .limit(1)
        )
        row = await self._first(stmt)
        return None if row is None else self.to_domain(row)

    async def latest_for_project(self, project_id: uuid.UUID) -> ComplianceReport | None:
        stmt = (
            self._project_scoped_select(project_id)
            .order_by(models.ComplianceReport.created_at.desc())
            .limit(1)
        )
        row = await self._first(stmt)
        return None if row is None else self.to_domain(row)

    # -- writes --------------------------------------------------------
    async def record(
        self,
        project_id: uuid.UUID,
        *,
        results: list[Any],
        pack_versions: dict[str, Any] | None = None,
        design_version_id: uuid.UUID | None = None,
    ) -> ComplianceReport:
        """Freeze a rules-engine run.

        ``pack_versions`` must name every pack that contributed (``nbc-core`` plus the
        city pack plus ``vastu`` when enabled). Without it a stored report cannot be
        explained later, and bye-laws change.
        """
        self.ctx.require_write("saving a compliance report")
        if not isinstance(results, list):
            raise RepositoryUsageError("results must be a list of rule results.")
        await require_project_in_firm(self._session, self.firm_id, project_id)
        if design_version_id is not None:
            await require_design_version_in_firm(
                self._session, self.firm_id, design_version_id
            )
        row = self._new_row(
            project_id=project_id,
            design_version_id=design_version_id,
            pack_versions=pack_versions or {},
            results=results,
        )
        await self._insert(row)
        failures = sum(
            1 for r in results if isinstance(r, dict) and r.get("status") == "fail"
        )
        self._log.info(
            "compliance_report.recorded",
            entity_id=str(row.id),
            project_id=str(project_id),
            rules=len(results),
            failures=failures,
        )
        return self.to_domain(row)


__all__ = ["ComplianceReportRepository"]
