"""Sheet and annotation repositories (playbook §7)."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import delete, select

from garh_api import models
from garh_api.repositories._guards import (
    require_design_version_in_firm,
    require_project_in_firm,
    require_sheet_in_firm,
)
from garh_api.repositories.domain import Annotation, Sheet
from garh_api.tenancy import (
    EntityNotFoundError,
    ProjectScopedRepository,
    Repository,
    RepositoryUsageError,
)


class SheetRepository(ProjectScopedRepository[models.Sheet, Sheet]):
    """Generated drawing sheets for a project/version.

    Sheets are regenerated, not edited: ``POST /sheets/generate`` replaces the set for
    a design version. Annotations survive that replacement — they belong to the sheet's
    *content*, not its rendering — which is why :meth:`replace_for_version` re-attaches
    them by ``(kind, number)`` instead of cascading them away.
    """

    row_type = models.Sheet
    entity_name = "sheet"

    def to_domain(self, row: models.Sheet) -> Sheet:
        return Sheet.from_row(row)

    # -- reads ---------------------------------------------------------
    async def list_for_version(
        self, project_id: uuid.UUID, design_version_id: uuid.UUID
    ) -> list[Sheet]:
        stmt = (
            self._project_scoped_select(project_id)
            .where(models.Sheet.design_version_id == design_version_id)
            .order_by(models.Sheet.number.asc().nullslast(), models.Sheet.created_at.asc())
        )
        return [self.to_domain(row) for row in await self._all(stmt)]

    async def get_by_kind(
        self,
        project_id: uuid.UUID,
        design_version_id: uuid.UUID,
        kind: str,
    ) -> list[Sheet]:
        _validate_kind(kind)
        stmt = (
            self._project_scoped_select(project_id)
            .where(models.Sheet.design_version_id == design_version_id)
            .where(models.Sheet.kind == kind)
            .order_by(models.Sheet.number.asc().nullslast())
        )
        return [self.to_domain(row) for row in await self._all(stmt)]

    # -- writes --------------------------------------------------------
    async def create(
        self,
        project_id: uuid.UUID,
        *,
        kind: str,
        design_version_id: uuid.UUID | None = None,
        number: str | None = None,
        layout: dict[str, Any] | None = None,
        generated_at: datetime | None = None,
    ) -> Sheet:
        self.ctx.require_write("generating sheets")
        _validate_kind(kind)
        await require_project_in_firm(self._session, self.firm_id, project_id)
        if design_version_id is not None:
            await require_design_version_in_firm(
                self._session, self.firm_id, design_version_id
            )
        row = self._new_row(
            project_id=project_id,
            design_version_id=design_version_id,
            kind=kind,
            number=(number or "").strip() or None,
            layout=layout or {},
            generated_at=generated_at or datetime.now(timezone.utc),
        )
        await self._insert(row)
        return self.to_domain(row)

    async def replace_for_version(
        self,
        project_id: uuid.UUID,
        design_version_id: uuid.UUID,
        sheets: list[dict[str, Any]],
    ) -> list[Sheet]:
        """Regenerate the sheet set for a version, preserving annotations.

        Each entry: ``{"kind": str, "number": str | None, "layout": dict}``.
        Annotations on a replaced sheet move to the new sheet with the same
        ``(kind, number)``; if that pairing disappears from the set, they are flagged
        ``orphaned`` for the Review Tray (§7) rather than deleted.
        """
        self.ctx.require_write("generating sheets")
        await require_project_in_firm(self._session, self.firm_id, project_id)
        await require_design_version_in_firm(self._session, self.firm_id, design_version_id)
        for i, spec in enumerate(sheets):
            if not isinstance(spec, dict) or "kind" not in spec:
                raise RepositoryUsageError("sheets[%d] needs a 'kind'." % i)
            _validate_kind(str(spec["kind"]))

        existing = await self._all(
            self._project_scoped_select(project_id).where(
                models.Sheet.design_version_id == design_version_id
            )
        )
        old_ids_by_key: dict[tuple[str, str | None], uuid.UUID] = {}
        for row in existing:
            old_ids_by_key[(row.kind, row.number)] = row.id

        now = datetime.now(timezone.utc)
        new_rows: list[models.Sheet] = []
        for spec in sheets:
            number = spec.get("number")
            new_rows.append(
                self._new_row(
                    project_id=project_id,
                    design_version_id=design_version_id,
                    kind=str(spec["kind"]),
                    number=(str(number).strip() if number else None) or None,
                    layout=spec.get("layout") or {},
                    generated_at=now,
                )
            )
        await self._insert_many(new_rows)

        # Re-anchor annotations by (kind, number), then drop the superseded sheets.
        new_ids_by_key = {(row.kind, row.number): row.id for row in new_rows}
        for key, old_id in old_ids_by_key.items():
            new_id = new_ids_by_key.get(key)
            if new_id is not None:
                await self._session.execute(
                    models.Annotation.__table__.update()
                    .where(models.Annotation.sheet_id == old_id)
                    .where(models.Annotation.firm_id == self.firm_id)
                    .values(sheet_id=new_id)
                )
            else:
                await self._session.execute(
                    models.Annotation.__table__.update()
                    .where(models.Annotation.sheet_id == old_id)
                    .where(models.Annotation.firm_id == self.firm_id)
                    .values(orphaned=True)
                )
        if old_ids_by_key:
            await self._session.execute(
                delete(models.Sheet)
                .where(models.Sheet.firm_id == self.firm_id)
                .where(models.Sheet.id.in_(list(old_ids_by_key.values())))
            )
        await self.flush()
        self._log.info(
            "sheets.regenerated",
            project_id=str(project_id),
            design_version_id=str(design_version_id),
            count=len(new_rows),
            replaced=len(old_ids_by_key),
        )
        return [self.to_domain(row) for row in new_rows]

    async def set_layout(self, sheet_id: uuid.UUID, layout: dict[str, Any]) -> Sheet:
        self.ctx.require_write("editing a sheet")
        row = await self._require_row(sheet_id)
        row.layout = layout
        row.generated_at = datetime.now(timezone.utc)
        await self.flush()
        return self.to_domain(row)


class AnnotationRepository(Repository[models.Annotation, Annotation]):
    """Sheet annotations, anchored to model element ids.

    Scoped by ``firm_id`` and always addressed via a ``sheet_id`` that has itself been
    checked against the firm — so a valid sheet id from another firm yields nothing.
    """

    row_type = models.Annotation
    entity_name = "annotation"

    def to_domain(self, row: models.Annotation) -> Annotation:
        return Annotation.from_row(row)

    def _sheet_scoped_select(self, sheet_id: uuid.UUID) -> Any:
        return self._scoped_select().where(models.Annotation.sheet_id == sheet_id)

    # -- reads ---------------------------------------------------------
    async def list_for_sheet(
        self, sheet_id: uuid.UUID, *, include_orphaned: bool = True
    ) -> list[Annotation]:
        stmt = self._sheet_scoped_select(sheet_id)
        if not include_orphaned:
            stmt = stmt.where(models.Annotation.orphaned.is_(False))
        stmt = stmt.order_by(models.Annotation.created_at.asc())
        return [self.to_domain(row) for row in await self._all(stmt)]

    async def list_orphaned_for_project(self, project_id: uuid.UUID) -> list[Annotation]:
        """The Review Tray feed (§7): orphans across every sheet of a project."""
        sheet_ids = (
            select(models.Sheet.id)
            .where(models.Sheet.firm_id == self.firm_id)
            .where(models.Sheet.project_id == project_id)
        )
        stmt = (
            self._scoped_select()
            .where(models.Annotation.orphaned.is_(True))
            .where(models.Annotation.sheet_id.in_(sheet_ids))
            .order_by(models.Annotation.created_at.asc())
        )
        return [self.to_domain(row) for row in await self._all(stmt)]

    # -- writes --------------------------------------------------------
    async def create(
        self,
        sheet_id: uuid.UUID,
        *,
        anchor_kind: str = "element",
        anchor_element_id: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> Annotation:
        self.ctx.require_write("adding an annotation")
        _validate_anchor_kind(anchor_kind)
        if anchor_kind != "sheet" and not anchor_element_id:
            raise RepositoryUsageError(
                "anchor_element_id is required unless anchor_kind is 'sheet'."
            )
        await require_sheet_in_firm(self._session, self.firm_id, sheet_id)
        row = self._new_row(
            sheet_id=sheet_id,
            anchor_kind=anchor_kind,
            anchor_element_id=anchor_element_id,
            payload=payload or {},
            orphaned=False,
        )
        await self._insert(row)
        return self.to_domain(row)

    async def update_payload(
        self, annotation_id: uuid.UUID, payload: dict[str, Any]
    ) -> Annotation:
        self.ctx.require_write("editing an annotation")
        row = await self._require_row(annotation_id)
        row.payload = payload
        await self.flush()
        return self.to_domain(row)

    async def reattach(
        self, annotation_id: uuid.UUID, *, anchor_element_id: str, anchor_kind: str | None = None
    ) -> Annotation:
        """Review Tray "re-attach": point an orphan at a surviving element."""
        self.ctx.require_write("re-attaching an annotation")
        if not anchor_element_id:
            raise RepositoryUsageError("anchor_element_id is required.")
        if anchor_kind is not None:
            _validate_anchor_kind(anchor_kind)
        row = await self._require_row(annotation_id)
        row.anchor_element_id = anchor_element_id
        if anchor_kind is not None:
            row.anchor_kind = anchor_kind
        row.orphaned = False
        await self.flush()
        self._log.info("annotation.reattached", entity_id=str(annotation_id))
        return self.to_domain(row)

    async def mark_orphaned(self, element_ids: list[str], sheet_ids: list[uuid.UUID]) -> int:
        """Flag annotations whose anchors did not survive a solver re-run (§7).

        ``element_ids`` are the ids that *disappeared*. No fuzzy re-anchoring in MVP —
        the user decides in the Review Tray.
        """
        if not element_ids or not sheet_ids:
            return 0
        stmt = (
            self._scoped_select()
            .where(models.Annotation.sheet_id.in_(sheet_ids))
            .where(models.Annotation.anchor_element_id.in_(element_ids))
            .where(models.Annotation.orphaned.is_(False))
        )
        rows = await self._all(stmt)
        for row in rows:
            row.orphaned = True
        if rows:
            await self.flush()
            self._log.info("annotation.orphaned", count=len(rows))
        return len(rows)

    async def require_for_sheet(
        self, sheet_id: uuid.UUID, annotation_id: uuid.UUID
    ) -> Annotation:
        row = await self._first(
            self._sheet_scoped_select(sheet_id)
            .where(models.Annotation.id == annotation_id)
            .limit(1)
        )
        if row is None:
            raise EntityNotFoundError(type(self).entity_name, annotation_id)
        return self.to_domain(row)


def _validate_kind(kind: str) -> None:
    if kind not in models.SHEET_KINDS:
        raise RepositoryUsageError("kind must be one of %s." % ", ".join(models.SHEET_KINDS))


def _validate_anchor_kind(anchor_kind: str) -> None:
    if anchor_kind not in models.ANNOTATION_ANCHOR_KINDS:
        raise RepositoryUsageError(
            "anchor_kind must be one of %s." % ", ".join(models.ANNOTATION_ANCHOR_KINDS)
        )


__all__ = ["AnnotationRepository", "SheetRepository"]
