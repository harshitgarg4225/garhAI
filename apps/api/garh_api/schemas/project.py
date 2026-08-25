"""Schemas for projects, plots, briefs, versions and compliance (§11).

Everything here maps 1:1 onto a repository domain dataclass. The mapping functions
(``*_out``) live beside the models rather than in the routers so there is exactly one
place that decides what leaves the building — notably that ``firm_id`` never does.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Optional

from pydantic import Field, StrictBool, StrictInt, StrictStr, field_validator

from garh_api.schemas import CamelModel, Mm, PointMm, ResponseModel, RoadEdge

# Mirrors of the DB CHECK vocabularies. Imported from ``garh_api.models`` at validation
# time (below) rather than retyped, so a schema can never drift from the constraint.
_MAX_NAME_LENGTH = 200


# ---------------------------------------------------------------------------
# Projects
# ---------------------------------------------------------------------------


class ProjectCreate(CamelModel):
    """``POST /projects``.

    Only ``name`` is required — golden rule 8 ("empty states teach") wants a one-field
    create so a new user reaches a project in one click; everything else is set later
    from the plot/brief screens.
    """

    name: StrictStr = Field(min_length=1, max_length=_MAX_NAME_LENGTH)
    units: StrictStr = Field(default="ft-in", description="Display units: ft-in | m.")
    city_pack: Optional[StrictStr] = Field(
        default=None, description="Rule pack id, e.g. 'blr'. Null = nbc-core only."
    )
    architect_of_record: Optional[uuid.UUID] = Field(
        default=None,
        description="Must be a member of your firm (Architects Act 1972 — only a "
        "registered architect signs a submission).",
    )
    status: StrictStr = Field(default="draft")

    @field_validator("units")
    @classmethod
    def _check_units(cls, value: str) -> str:
        from garh_api import models

        if value not in models.PROJECT_UNITS:
            raise ValueError("units must be one of %s." % ", ".join(models.PROJECT_UNITS))
        return value

    @field_validator("status")
    @classmethod
    def _check_status(cls, value: str) -> str:
        from garh_api import models

        if value not in models.PROJECT_STATUSES:
            raise ValueError("status must be one of %s." % ", ".join(models.PROJECT_STATUSES))
        return value


class ProjectUpdate(CamelModel):
    """``PATCH /projects/:id``. Absent field = leave alone (never "set to null")."""

    name: Optional[StrictStr] = Field(default=None, min_length=1, max_length=_MAX_NAME_LENGTH)
    status: Optional[StrictStr] = None
    units: Optional[StrictStr] = None
    city_pack: Optional[StrictStr] = None
    architect_of_record: Optional[uuid.UUID] = None
    clear_architect_of_record: StrictBool = Field(
        default=False,
        description="Explicit clear — a null cannot mean both 'unchanged' and 'remove'.",
    )

    @field_validator("units")
    @classmethod
    def _check_units(cls, value: Optional[str]) -> Optional[str]:
        from garh_api import models

        if value is not None and value not in models.PROJECT_UNITS:
            raise ValueError("units must be one of %s." % ", ".join(models.PROJECT_UNITS))
        return value

    @field_validator("status")
    @classmethod
    def _check_status(cls, value: Optional[str]) -> Optional[str]:
        from garh_api import models

        if value is not None and value not in models.PROJECT_STATUSES:
            raise ValueError("status must be one of %s." % ", ".join(models.PROJECT_STATUSES))
        return value


class ProjectOut(ResponseModel):
    id: uuid.UUID
    name: StrictStr
    status: StrictStr
    units: StrictStr
    city_pack: Optional[StrictStr] = None
    architect_of_record: Optional[uuid.UUID] = None
    demo: StrictBool = False
    created_at: datetime
    updated_at: datetime

    @classmethod
    def of(cls, project: Any) -> "ProjectOut":
        return cls(
            id=project.id,
            name=project.name,
            status=project.status,
            units=project.units,
            city_pack=project.city_pack,
            architect_of_record=project.architect_of_record,
            demo=project.demo,
            created_at=project.created_at,
            updated_at=project.updated_at,
        )


class ProjectDetailOut(ResponseModel):
    """``GET /projects/:id`` — one round trip to render the project shell.

    Deliberately includes plot and brief: the editor cannot draw anything without the
    plot, and a second request would just add latency to the §15 "<2s to interactive"
    budget. The (large) folded model is NOT here — that is ``GET /projects/:id/model``.
    """

    project: ProjectOut
    plot: Optional["PlotOut"] = None
    brief: Optional["BriefOut"] = None
    version_branch: uuid.UUID
    head_idx: StrictInt = Field(
        description="Highest op index on the active branch; -1 when no ops exist yet."
    )
    latest_version: Optional["VersionOut"] = None
    open_comment_count: StrictInt = 0


# ---------------------------------------------------------------------------
# Plot
# ---------------------------------------------------------------------------


class PlotIn(CamelModel):
    """``PUT /projects/:id/plot`` — an upsert; absent fields keep their value.

    ``boundary`` is an open ring (do not repeat the first point) of integer-mm points
    in plot-local space. An empty list is the legal "clear it" form, matching the model
    core's ``plot.set_boundary`` with an empty polygon.
    """

    boundary: Optional[list[PointMm]] = None
    north_deg: Optional[StrictInt] = Field(
        default=None, ge=0, le=359, description="True north, degrees clockwise from +Y."
    )
    roads: Optional[list[RoadEdge]] = None
    reg_profile: Optional[dict[str, Any]] = Field(
        default=None,
        description="Resolved regulatory profile (city pack id + overrides). Overrides "
        "are audited.",
    )
    source: Optional[StrictStr] = Field(default=None, description="manual | dxf | seed")

    @field_validator("boundary")
    @classmethod
    def _check_ring(cls, value: Optional[list[PointMm]]) -> Optional[list[PointMm]]:
        if value is not None and 0 < len(value) < 3:
            raise ValueError("A plot boundary needs at least 3 points (or none, to clear it).")
        return value

    @field_validator("source")
    @classmethod
    def _check_source(cls, value: Optional[str]) -> Optional[str]:
        from garh_api import models

        if value is not None and value not in models.PLOT_SOURCES:
            raise ValueError("source must be one of %s." % ", ".join(models.PLOT_SOURCES))
        return value


class PlotOut(ResponseModel):
    id: uuid.UUID
    project_id: uuid.UUID
    boundary: list[dict[str, Any]] = Field(default_factory=list)
    north_deg: StrictInt = 0
    roads: list[dict[str, Any]] = Field(default_factory=list)
    reg_profile: dict[str, Any] = Field(default_factory=dict)
    source: StrictStr = "manual"
    updated_at: datetime

    @classmethod
    def of(cls, plot: Any) -> "PlotOut":
        return cls(
            id=plot.id,
            project_id=plot.project_id,
            boundary=list(plot.boundary),
            north_deg=plot.north_deg,
            roads=list(plot.roads),
            reg_profile=dict(plot.reg_profile),
            source=plot.source,
            updated_at=plot.updated_at,
        )


# ---------------------------------------------------------------------------
# Brief
# ---------------------------------------------------------------------------


class BriefIn(CamelModel):
    """``PUT /projects/:id/brief``.

    ``data`` is the Brief document (rooms, adjacency wishes, facing, budget, style,
    ``assumptions[]``). It is free-form JSON here because the brief schema belongs to
    the model core, not to the transport — but see ``merge`` below: the copilot's
    ``brief.update`` op is a merge-patch, and this endpoint offers the same semantics so
    the form and the copilot share one write path.
    """

    data: Optional[dict[str, Any]] = None
    merge: StrictBool = Field(
        default=False,
        description="True = treat 'data' as an RFC 7386 merge-patch (null deletes a key).",
    )
    vastu_mode: Optional[StrictStr] = Field(default=None, description="off | advisory | strict")
    completeness: Optional[StrictInt] = Field(default=None, ge=0, le=100)

    @field_validator("vastu_mode")
    @classmethod
    def _check_mode(cls, value: Optional[str]) -> Optional[str]:
        from garh_api import models

        if value is not None and value not in models.VASTU_MODES:
            raise ValueError("vastuMode must be one of %s." % ", ".join(models.VASTU_MODES))
        return value


class BriefOut(ResponseModel):
    id: uuid.UUID
    project_id: uuid.UUID
    data: dict[str, Any] = Field(default_factory=dict)
    vastu_mode: StrictStr = "off"
    completeness: StrictInt = 0
    updated_at: datetime

    @classmethod
    def of(cls, brief: Any) -> "BriefOut":
        return cls(
            id=brief.id,
            project_id=brief.project_id,
            data=dict(brief.data),
            vastu_mode=brief.vastu_mode,
            completeness=brief.completeness,
            updated_at=brief.updated_at,
        )


class BriefParseIn(CamelModel):
    """``POST /projects/:id/brief/parse`` — free text in, structured brief out.

    The LLM never sees or emits geometry (locked decision). It returns brief *fields*
    and an assumption for every value it had to invent.
    """

    text: StrictStr = Field(min_length=1, max_length=20_000)
    known_fields: dict[str, Any] = Field(
        default_factory=dict, description="Values already captured in the form; not re-guessed."
    )
    apply: StrictBool = Field(
        default=False,
        description="False (default) = preview only. True = merge the parse into the brief.",
    )


class BriefAssumption(ResponseModel):
    """Golden rule 4: every default the AI used is a visible, editable chip."""

    field: StrictStr
    value: Any = None
    reason: StrictStr
    cite: Optional[StrictStr] = None


class BriefParseOut(ResponseModel):
    provider: StrictStr = Field(description="mock | anthropic — shown in the UI in dev.")
    data: dict[str, Any] = Field(
        default_factory=dict, description="Parsed brief fields (merge-patch shaped)."
    )
    assumptions: list[BriefAssumption] = Field(default_factory=list)
    completeness: StrictInt = Field(default=0, ge=0, le=100)
    applied: StrictBool = False
    brief: Optional[BriefOut] = None
    warnings: list[StrictStr] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Versions (§11 POST/GET /versions, POST /versions/:vid/restore)
# ---------------------------------------------------------------------------


class VersionCreate(CamelModel):
    """``POST /projects/:id/versions`` — a user-named snapshot of the current state."""

    name: StrictStr = Field(min_length=1, max_length=_MAX_NAME_LENGTH)


class VersionOut(ResponseModel):
    id: uuid.UUID
    project_id: uuid.UUID
    name: Optional[StrictStr] = None
    kind: StrictStr = Field(description="auto | named | option")
    parent_id: Optional[uuid.UUID] = None
    version_branch: uuid.UUID
    op_seq_start: Optional[StrictInt] = None
    op_seq_end: Optional[StrictInt] = None
    snapshot_hash: Optional[StrictStr] = None
    has_snapshot: StrictBool = False
    created_at: datetime

    @classmethod
    def of(cls, version: Any) -> "VersionOut":
        return cls(
            id=version.id,
            project_id=version.project_id,
            name=version.name,
            kind=version.kind,
            parent_id=version.parent_id,
            version_branch=version.version_branch,
            op_seq_start=version.op_seq_start,
            op_seq_end=version.op_seq_end,
            snapshot_hash=version.snapshot_hash,
            has_snapshot=bool(getattr(version, "snapshot", None) is not None)
            or bool(getattr(version, "snapshot_hash", None)),
            created_at=version.created_at,
        )


class VersionRestoreOut(ResponseModel):
    """Restore forks a branch instead of deleting history (§2's op log is append-only).

    ``versionBranch`` is the new active branch; the client must rebase its op queue onto
    ``headIdx`` and reload the model.
    """

    version: VersionOut
    version_branch: uuid.UUID
    head_idx: StrictInt
    ops_copied: StrictInt
    state_hash: Optional[StrictStr] = None


# ---------------------------------------------------------------------------
# Compliance (§11 GET /projects/:id/compliance)
# ---------------------------------------------------------------------------


class ComplianceOut(ResponseModel):
    """A frozen rules-engine run for a design version (§6).

    ``evaluated=False`` with an empty ``results`` is an honest "nobody has run the rules
    against this version yet" — never an implied pass. The UI shows "Not checked yet",
    not a green badge.
    """

    evaluated: StrictBool = False
    project_id: uuid.UUID
    design_version_id: Optional[uuid.UUID] = None
    report_id: Optional[uuid.UUID] = None
    pack_versions: dict[str, Any] = Field(default_factory=dict)
    results: list[dict[str, Any]] = Field(default_factory=list)
    counts: dict[str, StrictInt] = Field(default_factory=dict)
    created_at: Optional[datetime] = None
    live: StrictBool = Field(
        default=False,
        description="True when this run happened just now against the working state and "
        "was NOT persisted. False means it is the frozen report for a design version — "
        "the same numbers the sheets and the share link quote (§7).",
    )
    reason: Optional[StrictStr] = Field(
        default=None,
        description="Present only when evaluated=False: why the rules could not run "
        "(usually 'no plot boundary yet'). This is what the UI shows instead of a badge; "
        "'not checked' and 'checked and clean' must never look the same (§15).",
    )
    worst_status: Optional[StrictStr] = Field(
        default=None,
        description="pass | warn | fail | not_applicable across every rule, so the chip "
        "strip needs no client-side reduction.",
    )
    notes: list[StrictStr] = Field(
        default_factory=list,
        description="Approximations the projection made, verbatim from the engine's "
        "report. An architect reading a stored report years later needs these.",
    )

    @classmethod
    def of(cls, project_id: uuid.UUID, report: Any) -> "ComplianceOut":
        results = list(report.results)
        counts = {"pass": 0, "warn": 0, "fail": 0, "not_applicable": 0}
        for item in results:
            if isinstance(item, dict):
                status = str(item.get("status") or "")
                if status in counts:
                    counts[status] += 1
        return cls(
            evaluated=True,
            project_id=project_id,
            design_version_id=report.design_version_id,
            report_id=report.id,
            pack_versions=dict(report.pack_versions),
            results=results,
            counts=counts,
            created_at=report.created_at,
        )

    @classmethod
    def live_run(
        cls,
        project_id: uuid.UUID,
        payload: dict[str, Any],
        pack_versions: dict[str, Any],
    ) -> "ComplianceOut":
        """An unpersisted run against the current working state.

        Named ``live_run`` rather than ``live`` because the model already has a FIELD
        called ``live``: pydantic claims every annotated name as a field and strips a
        same-named classmethod from the class namespace, so ``ComplianceOut.live(...)``
        raised ``AttributeError`` at request time — a 500 on every project whose rules
        actually evaluate.

        ``report_id``/``created_at``/``design_version_id`` stay null on purpose: there
        is no row, and handing the client a fabricated id would let it think it could
        fetch this again.
        """
        results = [r for r in (payload.get("results") or []) if isinstance(r, dict)]
        counts = {"pass": 0, "warn": 0, "fail": 0, "not_applicable": 0}
        raw_counts = payload.get("counts")
        if isinstance(raw_counts, dict):
            for key, value in raw_counts.items():
                if key in counts and isinstance(value, int):
                    counts[key] = value
        return cls(
            evaluated=True,
            live=True,
            project_id=project_id,
            pack_versions=dict(pack_versions),
            results=results,
            counts=counts,
            worst_status=(
                str(payload.get("worstStatus")) if payload.get("worstStatus") else None
            ),
            notes=[str(n) for n in (payload.get("notes") or [])],
        )

    @classmethod
    def not_evaluated(
        cls,
        project_id: uuid.UUID,
        design_version_id: Optional[uuid.UUID],
        *,
        reason: Optional[str] = None,
    ) -> "ComplianceOut":
        return cls(
            evaluated=False,
            project_id=project_id,
            design_version_id=design_version_id,
            results=[],
            counts={"pass": 0, "warn": 0, "fail": 0, "not_applicable": 0},
            reason=reason,
        )


# ---------------------------------------------------------------------------
# Comments (shared with the share-link viewer surface)
# ---------------------------------------------------------------------------


class CommentIn(CamelModel):
    body: StrictStr = Field(min_length=1, max_length=4000)
    author_name: Optional[StrictStr] = Field(default=None, max_length=200)
    anchor: dict[str, Any] = Field(
        default_factory=dict,
        description="Where the pin sits: {kind, sheetId?, elementId?, ptMm?}.",
    )


class CommentOut(ResponseModel):
    id: uuid.UUID
    project_id: uuid.UUID
    body: StrictStr
    author_name: StrictStr
    anchor: dict[str, Any] = Field(default_factory=dict)
    resolved: StrictBool = False
    from_share_link: StrictBool = False
    created_at: datetime

    @classmethod
    def of(cls, comment: Any) -> "CommentOut":
        return cls(
            id=comment.id,
            project_id=comment.project_id,
            body=comment.body,
            author_name=comment.author_name,
            anchor=dict(comment.anchor),
            resolved=comment.resolved,
            from_share_link=comment.share_link_id is not None,
            created_at=comment.created_at,
        )


# ---------------------------------------------------------------------------
# Share links (§11 POST /projects/:id/share, §13 scoped signed tokens)
# ---------------------------------------------------------------------------


class ShareCreate(CamelModel):
    """Mint a read-only client link.

    ``sections`` is the allowlist the viewer surface enforces on every read; leaving it
    out shares everything the MVP viewer can render. ``expiresInDays`` beats an absolute
    timestamp for a UI that offers "7 days / 30 days / no expiry".
    """

    sections: Optional[list[StrictStr]] = Field(default=None, max_length=8)
    can_comment: StrictBool = True
    expires_in_days: Optional[StrictInt] = Field(default=30, ge=1, le=365)

    @field_validator("sections")
    @classmethod
    def _check_sections(cls, value: Optional[list[str]]) -> Optional[list[str]]:
        from garh_api.repositories.share_links import SHARE_SECTIONS

        if value is None:
            return None
        unknown = [s for s in value if s not in SHARE_SECTIONS]
        if unknown:
            raise ValueError(
                "Unknown section(s): %s. Allowed: %s."
                % (", ".join(unknown), ", ".join(SHARE_SECTIONS))
            )
        return value


class ShareLinkOut(ResponseModel):
    """A share link. ``token`` and ``url`` appear **only** in the create response —
    the token is stored hashed and cannot be shown again (§13)."""

    id: uuid.UUID
    project_id: uuid.UUID
    sections: list[StrictStr] = Field(default_factory=list)
    can_comment: StrictBool = False
    expires_at: Optional[datetime] = None
    revoked: StrictBool = False
    created_at: datetime
    token: Optional[StrictStr] = None
    url: Optional[StrictStr] = None
    whatsapp_url: Optional[StrictStr] = Field(
        default=None, description="wa.me deep link with a preformatted message (§15)."
    )

    @classmethod
    def of(
        cls,
        link: Any,
        *,
        token: Optional[str] = None,
        url: Optional[str] = None,
        whatsapp_url: Optional[str] = None,
    ) -> "ShareLinkOut":
        scope = dict(link.scope)
        raw_sections = scope.get("sections")
        return cls(
            id=link.id,
            project_id=link.project_id,
            sections=[str(s) for s in raw_sections] if isinstance(raw_sections, list) else [],
            can_comment=bool(scope.get("canComment")),
            expires_at=link.expires_at,
            revoked=link.revoked,
            created_at=link.created_at,
            token=token,
            url=url,
            whatsapp_url=whatsapp_url,
        )


class SharedProjectOut(ResponseModel):
    """What an anonymous viewer sees at ``GET /share/:token``.

    Scrupulously narrow: the project's display name, its units, what the link may show,
    and nothing about the firm, the members, or any other project.
    """

    project_name: StrictStr
    units: StrictStr
    city_pack: Optional[StrictStr] = None
    sections: list[StrictStr] = Field(default_factory=list)
    can_comment: StrictBool = False
    expires_at: Optional[datetime] = None
    design_version_id: Optional[uuid.UUID] = None
    updated_at: datetime


ProjectDetailOut.model_rebuild()


__all__ = [
    "BriefAssumption",
    "BriefIn",
    "BriefOut",
    "BriefParseIn",
    "BriefParseOut",
    "CommentIn",
    "CommentOut",
    "ComplianceOut",
    "Mm",
    "PlotIn",
    "PlotOut",
    "ProjectCreate",
    "ProjectDetailOut",
    "ProjectOut",
    "ProjectUpdate",
    "ShareCreate",
    "ShareLinkOut",
    "SharedProjectOut",
    "VersionCreate",
    "VersionOut",
    "VersionRestoreOut",
]
