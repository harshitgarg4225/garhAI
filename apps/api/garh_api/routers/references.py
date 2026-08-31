"""The per-project inspiration board (§11).

Upload the pictures a client sent, say what each one is for, and see — before a render
is made — exactly what the board will contribute and what needs settling first.

The upload rules are ``underlay.py``'s, for the same §13 reasons: the body is read under
a cap, the bytes are sniffed rather than trusted, and a firm-scoped storage key means a
stale signed URL cannot reach another tenant's image.

## Why there is a review endpoint

A render is a thing a client is shown. "Which kitchen did you mean" is a question with a
real answer, and the moment to ask it is before the picture is made, not in the meeting.
``GET .../references/review`` answers it for one preset: what applies, what does not,
what conflicts, and the exact prompt fragments the model will receive — so the
instruction the architect wrote and the instruction the model gets are visibly the same
thing.
"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Request

from garh_api.config import get_settings
from garh_api.errors import ApiError, InvalidRequestError, UnsupportedMediaTypeError
from garh_api.logging import get_logger
from garh_api.repositories import ProjectReference, ReferenceRepository
from garh_api.routers import (
    SessionDep,
    TenantDep,
    enforce_rate_limit,
    require_project,
)
from garh_api.routers.imports import _file_from_multipart, _read_body_capped
from garh_api.routers.underlay import sniff_image
from garh_api.schemas import Ack
from garh_api.schemas.references import (
    ReferenceConflictOut,
    ReferenceListOut,
    ReferenceOut,
    ReferencePatchIn,
    ReferenceReviewOut,
)
from garh_api.storage import delete_object, put_object, sigv4_presign

_log = get_logger(__name__)

router = APIRouter(tags=["references"])

#: Same reasoning as the underlay's limit: generous for an architect pinning a client's
#: photos, cheap to exhaust for a script pushing megabytes at storage.
REFERENCE_UPLOADS_PER_FIRM_PER_HOUR = 60


class ReferenceReviewUnavailableError(ApiError):
    """``services.render`` is not importable on this server.

    Honest 503, the copilot route's pattern. Pinning, annotating and listing all keep
    working without it — only the pre-render review needs the render side's own
    vocabulary, and answering that with a guess would mean the board the architect sees
    and the board the worker reads are two different things.
    """

    http_status = 503
    code = "reference_review_unavailable"
    action = "Reference review is not loaded on this server. Contact support."


def _render_references() -> tuple[Any, Any]:
    """Import the render side lazily, with one honest error when absent.

    ``services/render`` lives at the repo root, so an image that mounts only
    ``apps/api`` must not fail at import time — and must not answer with a stub.
    """
    try:
        from services.render import references as module
        from services.render.types import PRESETS

        return module, PRESETS
    except ImportError as exc:
        raise ReferenceReviewUnavailableError(
            "The render package is not installed on this server.",
            extra={"importError": str(exc)},
        ) from exc


def _storage_key(firm_id: uuid.UUID, project_id: uuid.UUID) -> str:
    """Firm-scoped, with a fresh uuid per upload so a stale signed URL goes nowhere."""
    return "references/%s/%s/%s" % (firm_id, project_id, uuid.uuid4())


def _out(reference: ProjectReference, *, settings: object = None) -> ReferenceOut:
    active = settings or get_settings()
    return ReferenceOut(
        id=reference.id,
        project_id=reference.project_id,
        label=reference.label,
        scope=reference.scope,
        why=reference.why,
        ignore=reference.ignore_note,
        intent=reference.intent,
        position=reference.position,
        filename=reference.filename,
        width_px=reference.width_px,
        height_px=reference.height_px,
        image_url=sigv4_presign(
            "GET",
            reference.object_key,
            ttl_seconds=active.s3_signed_url_ttl_seconds,  # type: ignore[attr-defined]
            settings=active,  # type: ignore[arg-type]
        ),
        created_at=reference.created_at,
    )


@router.get(
    "/projects/{project_id}/references",
    response_model=ReferenceListOut,
    summary="The project's inspiration board",
)
async def list_references(
    project_id: uuid.UUID, session: SessionDep, ctx: TenantDep
) -> ReferenceListOut:
    await require_project(session, ctx, project_id)
    ctx.require_scope("projects")
    settings = get_settings()
    board = await ReferenceRepository(session, ctx).list_for_project(project_id)
    return ReferenceListOut(references=[_out(item, settings=settings) for item in board])


@router.post(
    "/projects/{project_id}/references",
    response_model=ReferenceOut,
    status_code=201,
    summary="Pin a reference image to the board",
    openapi_extra={
        "requestBody": {
            "required": True,
            "content": {
                "multipart/form-data": {
                    "schema": {
                        "type": "object",
                        "properties": {"file": {"type": "string", "format": "binary"}},
                    }
                },
                "image/png": {"schema": {"type": "string", "format": "binary"}},
                "image/jpeg": {"schema": {"type": "string", "format": "binary"}},
            },
        }
    },
)
async def add_reference(
    project_id: uuid.UUID, request: Request, session: SessionDep, ctx: TenantDep
) -> ReferenceOut:
    """Accept the image, verify it really is one, store it, pin it to the board.

    It arrives UNANNOTATED on purpose. The architect says what it is for in a second
    step, and until they do, the review endpoint asks them to — which is better than
    this route guessing a scope from a filename and being quietly wrong.
    """
    ctx.require_write("adding a reference image")
    await require_project(session, ctx, project_id)
    await enforce_rate_limit(
        "reference_uploads",
        ctx.firm_id,
        limit=REFERENCE_UPLOADS_PER_FIRM_PER_HOUR,
        window_seconds=3600,
        what="Reference image upload",
    )

    settings = get_settings()
    body = await _read_body_capped(request, settings.max_image_upload_bytes)
    content_type = request.headers.get("content-type", "")
    filename = ""
    if content_type.lower().startswith("multipart/form-data"):
        extracted = _file_from_multipart(content_type, body)
        if extracted is None:
            raise InvalidRequestError(
                "The upload arrived without a file in it.",
                action='Send the image as a form part named "file" and try again.',
            )
        data, filename = extracted
    else:
        data = body
    if not data:
        raise InvalidRequestError(
            "That upload was empty.", action="Choose the picture and try again."
        )

    sniffed = sniff_image(data)
    if sniffed is None:
        raise UnsupportedMediaTypeError(
            "That file isn't a PNG or JPEG.",
            action="Save the picture as a PNG or JPEG and upload it again.",
        )
    media_type, width_px, height_px = sniffed

    key = _storage_key(ctx.firm_id, project_id)
    await put_object(key, data, content_type=media_type, settings=settings)
    reference = await ReferenceRepository(session, ctx).add(
        project_id,
        object_key=key,
        content_type=media_type,
        width_px=width_px,
        height_px=height_px,
        filename=filename or "",
    )
    _log.info(
        "references.added",
        project_id=str(project_id),
        reference_id=str(reference.id),
        width_px=width_px,
        height_px=height_px,
    )
    return _out(reference, settings=settings)


@router.patch(
    "/projects/{project_id}/references/{reference_id}",
    response_model=ReferenceOut,
    summary="Say what a reference is for — where, what to take, what to leave, how hard",
)
async def annotate_reference(
    project_id: uuid.UUID,
    reference_id: uuid.UUID,
    body: ReferencePatchIn,
    session: SessionDep,
    ctx: TenantDep,
) -> ReferenceOut:
    await require_project(session, ctx, project_id)
    repo = ReferenceRepository(session, ctx)
    existing = await repo.require(reference_id)
    if existing.project_id != project_id:
        from garh_api.tenancy import EntityNotFoundError

        raise EntityNotFoundError("reference", reference_id)
    updated = await repo.annotate(reference_id, body.model_dump(exclude_unset=True))
    return _out(updated)


@router.delete(
    "/projects/{project_id}/references/{reference_id}",
    response_model=Ack,
    summary="Take a reference off the board",
)
async def delete_reference(
    project_id: uuid.UUID, reference_id: uuid.UUID, session: SessionDep, ctx: TenantDep
) -> Ack:
    await require_project(session, ctx, project_id)
    repo = ReferenceRepository(session, ctx)
    existing = await repo.require(reference_id)
    if existing.project_id != project_id:
        from garh_api.tenancy import EntityNotFoundError

        raise EntityNotFoundError("reference", reference_id)
    key = await repo.delete(reference_id)
    # Best-effort, exactly as the underlay does: the row is the record, and a storage
    # object left behind is cheaper than a request that fails after the row is gone.
    try:
        await delete_object(key, settings=get_settings())
    except Exception as exc:  # pragma: no cover - storage is not the source of truth
        _log.warning("references.storage_delete_failed", key=key, error=str(exc))
    return Ack()


@router.get(
    "/projects/{project_id}/references/review",
    response_model=ReferenceReviewOut,
    summary="What the board contributes to one render, and what to settle first",
)
async def review_references(
    project_id: uuid.UUID,
    session: SessionDep,
    ctx: TenantDep,
    preset: str,
) -> ReferenceReviewOut:
    """Ask before rendering, not after.

    A render is shown to a client. Two pictures both marked "match closely" for the
    kitchen is a question with a real answer, and a bathroom picture on a street
    elevation is worth saying out loud rather than dropping in silence — the architect
    chose it, and silence is how someone concludes the board does nothing.
    """
    await require_project(session, ctx, project_id)
    ctx.require_scope("projects")
    settings = get_settings()
    board = await ReferenceRepository(session, ctx).list_for_project(project_id)

    module, presets = _render_references()
    Reference = module.Reference
    applicable_references = module.applicable_references
    find_conflicts = module.find_conflicts
    reference_prompt = module.reference_prompt

    preset_def = presets.get(preset)
    if preset_def is None:
        raise InvalidRequestError(
            "There's no render preset called %r." % preset,
            action="Call GET /render-presets to see what's available.",
        )

    as_refs = [
        Reference(
            id=str(item.id),
            label=item.label,
            scope=item.scope,  # type: ignore[arg-type]
            why=item.why,
            ignore=item.ignore_note,
            intent=item.intent,  # type: ignore[arg-type]
        )
        for item in board
    ]
    applies_ids = {r.id for r in applicable_references(as_refs, preset_def)}
    positive, negative = reference_prompt(as_refs, preset_def)
    conflicts = find_conflicts(as_refs, preset_def)

    return ReferenceReviewOut(
        project_id=project_id,
        preset=preset,
        applies=[_out(i, settings=settings) for i in board if str(i.id) in applies_ids],
        not_in_view=[_out(i, settings=settings) for i in board if str(i.id) not in applies_ids],
        conflicts=[
            ReferenceConflictOut(
                kind=c.kind,
                reference_ids=list(c.reference_ids),
                question=c.question,
                default=c.default,
            )
            for c in conflicts
        ],
        positive=positive,
        negative=negative,
    )
