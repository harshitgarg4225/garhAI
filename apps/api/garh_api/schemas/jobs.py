"""Schemas for worker jobs: solve, render, sheets, export, and the SSE event body.

Job responses all carry ``status`` from the same five-value lifecycle
(``queued|running|succeeded|failed|cancelled``) and an integer ``progress`` that only
ever comes from a real worker event. There is no synthesised progress anywhere in this
codebase — §15: "never a fake bar".
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import Field, StrictBool, StrictInt, StrictStr, field_validator

from garh_api.schemas import CamelModel, Mm, ResponseModel
from garh_api.schemas.sheets import RevisionRow, TitleBlockFields

#: Base64 payload ceiling per render input image. Bigger than a 1080p PNG of a viewport,
#: small enough that a hostile client cannot park 100MB in Redis (§13 upload limits).
MAX_RENDER_INPUT_B64_CHARS = 4 * 1024 * 1024


# ---------------------------------------------------------------------------
# Solver (§5, §11 POST /projects/:id/solve)
# ---------------------------------------------------------------------------


class SolveIn(CamelModel):
    """Start a layout generation.

    ``lockedRoomIds`` drives §5.7 partial re-solve: those rooms come back with the same
    ids and the same polygons, because annotations, locks and copilot references all
    hang off room ids.
    """

    locked_room_ids: list[StrictStr] = Field(
        default_factory=list, max_length=200, description="Rooms the solver must not move."
    )
    option_count: StrictInt = Field(default=3, ge=1, le=5, description="§5.5 keeps 3–5.")
    seed: StrictInt | None = Field(
        default=None, description="Fixes the run for reproducibility; omit for a fresh spread."
    )
    storeys: StrictInt | None = Field(default=None, ge=1, le=4)
    params: dict[str, Any] = Field(
        default_factory=dict,
        description="Extra solver knobs (weights, time budget). Integer values only.",
    )


class PlanOptionOut(ResponseModel):
    """One presentable option. Only options that cleared §5.6's gates are ever here."""

    index: StrictInt
    composite_score: StrictInt = Field(
        default=0, ge=0, le=100, description="0–100 critic composite (§5.4)."
    )
    scores: dict[str, StrictInt] = Field(default_factory=dict)
    rationale: StrictStr | None = None
    assumptions: list[dict[str, Any]] = Field(default_factory=list)
    stats: dict[str, Any] = Field(default_factory=dict)
    ops: list[dict[str, Any]] = Field(
        default_factory=list,
        description="The op group that applies this option (op 31 expands to it).",
    )


class SolverJobOut(ResponseModel):
    id: uuid.UUID
    project_id: uuid.UUID
    status: StrictStr
    progress: StrictInt = 0
    stage: StrictStr | None = None
    options: list[dict[str, Any]] | None = None
    option_count: StrictInt = 0
    error: StrictStr | None = None
    params: dict[str, Any] = Field(default_factory=dict)
    queue_depth: StrictInt | None = None
    events_url: StrictStr | None = None
    created_at: datetime
    updated_at: datetime

    @classmethod
    def of(
        cls,
        job: Any,
        *,
        queue_depth: int | None = None,
        events_url: str | None = None,
    ) -> SolverJobOut:
        options = job.options
        return cls(
            id=job.id,
            project_id=job.project_id,
            status=job.status,
            progress=job.progress,
            options=options,
            option_count=len(options) if options else 0,
            error=job.error,
            params=dict(job.params),
            queue_depth=queue_depth,
            events_url=events_url,
            created_at=job.created_at,
            updated_at=job.updated_at,
        )


# ---------------------------------------------------------------------------
# Renders (§9, §11 POST /projects/:id/renders)
# ---------------------------------------------------------------------------


class RenderInputs(CamelModel):
    """The three control maps the client captures from the R3F viewport (§9).

    Either supply base64 PNG bytes (stored in Redis for the worker, TTL 1h) **or** an
    already-uploaded object URL. The mock provider works with just ``viewportPng``;
    ControlNet needs depth and edges too, and the worker says so honestly rather than
    quietly rendering something unrelated to the design.
    """

    viewport_png: StrictStr | None = Field(
        default=None, max_length=MAX_RENDER_INPUT_B64_CHARS, description="base64, no data: prefix."
    )
    depth_png: StrictStr | None = Field(default=None, max_length=MAX_RENDER_INPUT_B64_CHARS)
    edges_png: StrictStr | None = Field(default=None, max_length=MAX_RENDER_INPUT_B64_CHARS)
    viewport_url: StrictStr | None = Field(default=None, max_length=2000)
    depth_url: StrictStr | None = Field(default=None, max_length=2000)
    edges_url: StrictStr | None = Field(default=None, max_length=2000)


class RenderIn(CamelModel):
    """Start a render. ``mode`` is §9's precise/explore split — it changes ControlNet
    scale and denoise, so it is a first-class field, not a param."""

    mode: StrictStr = Field(default="precise", description="precise | explore")
    preset: StrictStr = Field(
        default="exterior-street-day",
        max_length=64,
        description="Prompt template id, e.g. exterior-34-dusk, interior-living.",
    )
    design_version_id: uuid.UUID | None = Field(
        default=None,
        description="Pins the render to a version. Omit to pin to the latest version.",
    )
    prompt_extras: StrictStr = Field(default="", max_length=1000)
    seed: StrictInt | None = Field(default=None, ge=0)
    width: StrictInt = Field(default=1536, ge=256, le=4096)
    height: StrictInt = Field(default=1024, ge=256, le=4096)
    view: dict[str, Any] = Field(
        default_factory=dict,
        description="Camera state: {eyeMm, targetMm, fovDeg, storeyId} — integer mm.",
    )
    inputs: RenderInputs = Field(default_factory=RenderInputs)

    @field_validator("mode")
    @classmethod
    def _check_mode(cls, value: str) -> str:
        from garh_api import models

        if value not in models.RENDER_MODES:
            raise ValueError("mode must be one of %s." % ", ".join(models.RENDER_MODES))
        return value


class RenderJobOut(ResponseModel):
    id: uuid.UUID
    project_id: uuid.UUID
    design_version_id: uuid.UUID | None = None
    mode: StrictStr
    provider: StrictStr
    status: StrictStr
    progress: StrictInt = 0
    output_url: StrictStr | None = None
    stale: StrictBool = Field(
        default=False, description="True once the design moved on (§9 banner)."
    )
    error: StrictStr | None = None
    view: dict[str, Any] = Field(default_factory=dict)
    params: dict[str, Any] = Field(default_factory=dict)
    #: §11. The board references this render followed, by id and the architect's own
    #: label. Never prompt text (§13). Distinct from ``params["references"]``, which
    #: is what the job was SENT — a render can carry a reference it could not apply.
    references_used: list[dict[str, Any]] = Field(default_factory=list)
    queue_depth: StrictInt | None = None
    events_url: StrictStr | None = None
    created_at: datetime
    updated_at: datetime

    @classmethod
    def of(
        cls,
        job: Any,
        *,
        queue_depth: int | None = None,
        events_url: str | None = None,
        output_url: str | None = None,
    ) -> RenderJobOut:
        return cls(
            id=job.id,
            project_id=job.project_id,
            design_version_id=job.design_version_id,
            mode=job.mode,
            provider=job.provider,
            status=job.status,
            progress=job.progress,
            output_url=output_url if output_url is not None else job.output_url,
            stale=job.stale,
            error=job.error,
            view=dict(job.view),
            params=dict(job.params),
            references_used=[dict(entry) for entry in getattr(job, "references_used", [])],
            queue_depth=queue_depth,
            events_url=events_url,
            created_at=job.created_at,
            updated_at=job.updated_at,
        )


# ---------------------------------------------------------------------------
# Sheets (§7, §11 POST /projects/:id/sheets/generate)
# ---------------------------------------------------------------------------


class SheetsGenerateIn(CamelModel):
    """Generate (or regenerate) the municipal set for a design version.

    Omitting ``kinds`` generates the full MVP set: site plan, floor plans, 4 elevations,
    1 section, door/window schedule, area statement.
    """

    design_version_id: uuid.UUID | None = None
    kinds: list[StrictStr] | None = Field(default=None, max_length=16)
    scale_denominator: StrictInt = Field(
        default=100, ge=1, le=2000, description="1:100 default (§7)."
    )
    sheet_size: StrictStr = Field(default="A2", max_length=8)
    #: ``None`` means "use the firm's saved preference" (§7 step 6 calls dimToJamb a
    #: firm setting). An explicit true/false overrides it for this set only. It is
    #: nullable rather than defaulted to False so that "the caller said nothing" and
    #: "the caller said centreline" stay distinguishable — otherwise every generate
    #: from a client that omits the field would silently override the firm's template.
    dim_to_jamb: StrictBool | None = Field(
        default=None,
        description="Dimension openings to the jamb instead of the centreline. "
        "Omit to use the firm's saved preference.",
    )
    #: Title block for this set. Omit to use the firm's saved template. Stored on each
    #: generated sheet's layout, so a set records the block it was drawn with.
    title_block: TitleBlockFields | None = None
    #: The §7 auto revision table. Omit to use the firm's saved rows.
    revisions: list[RevisionRow] | None = Field(default=None, max_length=12)
    #: Formats to publish per sheet. Defaults to svg + dxf; svg is always included
    #: because the on-screen viewer reads it.
    formats: list[StrictStr] | None = Field(default=None, max_length=4)

    @field_validator("kinds")
    @classmethod
    def _check_kinds(cls, value: list[str] | None) -> list[str] | None:
        from garh_api import models

        if value is None:
            return None
        unknown = [k for k in value if k not in models.SHEET_KINDS]
        if unknown:
            raise ValueError(
                "Unknown sheet kind(s): %s. Allowed: %s."
                % (", ".join(unknown), ", ".join(models.SHEET_KINDS))
            )
        return value


class SheetOut(ResponseModel):
    id: uuid.UUID
    project_id: uuid.UUID
    design_version_id: uuid.UUID | None = None
    kind: StrictStr
    number: StrictStr | None = None
    title: StrictStr | None = None
    scale_denominator: StrictInt | None = None
    artifacts: dict[str, StrictStr] = Field(
        default_factory=dict,
        description="Available formats → download paths (svg | dxf | pdf).",
    )
    annotation_count: StrictInt = 0
    orphaned_annotation_count: StrictInt = 0
    generated_at: datetime | None = None

    @classmethod
    def of(cls, sheet: Any, *, artifacts: dict[str, str] | None = None) -> SheetOut:
        layout = dict(sheet.layout)
        return cls(
            id=sheet.id,
            project_id=sheet.project_id,
            design_version_id=sheet.design_version_id,
            kind=sheet.kind,
            number=sheet.number,
            title=layout.get("title"),
            scale_denominator=layout.get("scaleDenominator"),
            artifacts=artifacts or {},
            annotation_count=int(layout.get("annotationCount") or 0),
            orphaned_annotation_count=int(layout.get("orphanedAnnotationCount") or 0),
            generated_at=sheet.generated_at,
        )


class SheetSetOut(ResponseModel):
    project_id: uuid.UUID
    design_version_id: uuid.UUID | None = None
    sheets: list[SheetOut] = Field(default_factory=list)
    job: ExportJobOut | None = None
    generated_at: datetime | None = None


# ---------------------------------------------------------------------------
# Exports (§11 POST /projects/:id/export)
# ---------------------------------------------------------------------------


class ExportIn(CamelModel):
    """``{kind: pdf-set|dxf|gltf|png-pack}`` → a job → a short-lived signed URL."""

    kind: StrictStr = Field(description="pdf-set | dxf | gltf | png-pack")
    design_version_id: uuid.UUID | None = None
    sheet_ids: list[uuid.UUID] = Field(default_factory=list, max_length=64)
    include_disclaimer: StrictBool = Field(
        default=True,
        description="Architects Act framing surfaced at export, not buried in the ToS.",
    )
    options: dict[str, Any] = Field(default_factory=dict)

    @field_validator("kind")
    @classmethod
    def _check_kind(cls, value: str) -> str:
        from garh_api.queue import EXPORT_KINDS

        if value not in EXPORT_KINDS:
            raise ValueError("kind must be one of %s." % ", ".join(EXPORT_KINDS))
        return value


class ExportJobOut(ResponseModel):
    """Export jobs live in Redis, not Postgres — see ``garh_api.queue`` for why."""

    id: StrictStr
    project_id: uuid.UUID
    kind: StrictStr
    status: StrictStr
    progress: StrictInt = 0
    design_version_id: uuid.UUID | None = None
    download_url: StrictStr | None = None
    error: StrictStr | None = None
    events_url: StrictStr | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None

    @classmethod
    def of(cls, job: Any, *, events_url: str | None = None) -> ExportJobOut:
        return cls(
            id=str(job.id),
            project_id=uuid.UUID(str(job.project_id)),
            kind=job.kind,
            status=job.status,
            progress=job.progress,
            design_version_id=(uuid.UUID(job.design_version_id) if job.design_version_id else None),
            download_url=job.download_url,
            error=job.error,
            events_url=events_url,
            created_at=_parse_iso(job.created_at),
            updated_at=_parse_iso(job.updated_at),
        )


def _parse_iso(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


class DownloadOut(ResponseModel):
    """A short-lived signed download (§11, §13 "signed URLs ≤10min")."""

    url: StrictStr
    expires_at: datetime
    filename: StrictStr
    content_type: StrictStr


# ---------------------------------------------------------------------------
# SSE (documented here so the OpenAPI schema describes the stream body)
# ---------------------------------------------------------------------------


class JobEventOut(ResponseModel):
    """The JSON body of every SSE ``data:`` line on a job event stream.

    Wire format (``text/event-stream``)::

        id: 7
        event: progress
        data: {"eventVersion":1,"jobId":"…","jobKind":"solver","seq":7,…}

    Event names: ``state`` (the job row, sent once on connect), ``progress``, ``done``,
    ``error``, ``ping`` (keepalive). ``id`` is the event ``seq`` — reconnect with
    ``Last-Event-ID`` and the server replays everything after it from the backlog.
    """

    event_version: StrictInt = 1
    job_id: StrictStr
    job_kind: StrictStr
    seq: StrictInt = 0
    at: datetime
    status: StrictStr
    progress: StrictInt = 0
    stage: StrictStr | None = None
    message: StrictStr | None = None
    data: dict[str, Any] = Field(default_factory=dict)
    terminal: StrictBool = False


SheetSetOut.model_rebuild()
# TitleBlockFields / RevisionRow arrive as string annotations on SheetsGenerateIn
# (`from __future__ import annotations` makes every annotation lazy), so the model has
# to be rebuilt once they are in scope or FastAPI cannot build its request schema.
SheetsGenerateIn.model_rebuild()


__all__ = [
    "MAX_RENDER_INPUT_B64_CHARS",
    "DownloadOut",
    "ExportIn",
    "ExportJobOut",
    "JobEventOut",
    "Mm",
    "PlanOptionOut",
    "RenderIn",
    "RenderInputs",
    "RenderJobOut",
    "SheetOut",
    "SheetSetOut",
    "SheetsGenerateIn",
    "SolveIn",
    "SolverJobOut",
]
