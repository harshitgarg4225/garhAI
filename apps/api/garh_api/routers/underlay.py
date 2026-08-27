"""Tracing underlay — upload a plan image, calibrate it, trace over it (Rayon-style).

The §13 upload rules, applied the way ``routers/imports.py`` applies them to DXF:

* the body is read with a hard byte ceiling **while streaming**
  (``settings.max_image_upload_bytes``; the route is on ``main.py``'s
  large-body allowlist so it must enforce its own cap);
* the bytes are **magic-byte sniffed** — the filename and declared content type
  are claims, not facts. PNG and JPEG only: they are what phone cameras and
  scanners produce, and each has an unambiguous signature;
* the pixel dimensions are parsed server-side from the actual bytes. The API
  image has no PIL and does not want one for two fixed header layouts — the
  parsers below are deterministic stdlib byte-walks (PNG's IHDR is at a fixed
  offset; JPEG requires walking segments to the SOF marker), unit-tested and
  negative-tested in ``tests/test_underlay.py``.

Storage is the shared machinery in ``garh_api.storage`` (the promoted signer the
imports router used to own): presigned PUT within this request, presigned GET
minted per response, best-effort DELETE when the underlay is removed or its
image replaced. Workers are not involved — there is no job here, the upload IS
the result, which is also why the route needs no Idempotency-Key: replaying an
upload overwrites the same one-per-project row with the same bytes.

The underlay is deliberately NOT model state (no ops, no undo, no TS/Python
twin) — see ``models.ProjectUnderlay``. These four routes are plain
project-scoped CRUD behind ``TenantDep``, write-role gated for mutations.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Request

from garh_api.config import Settings, get_settings
from garh_api.errors import InvalidRequestError, UnsupportedMediaTypeError
from garh_api.logging import get_logger
from garh_api.repositories import MAX_UNDERLAY_EDGE_PX, Underlay, UnderlayRepository
from garh_api.routers import (
    ApiError,
    SessionDep,
    TenantDep,
    enforce_rate_limit,
    require_project,
)
from garh_api.routers.imports import _file_from_multipart, _read_body_capped
from garh_api.schemas import Ack
from garh_api.schemas.underlay import UnderlayOut, UnderlayPatchIn
from garh_api.storage import delete_object, put_object, sigv4_presign

_log = get_logger(__name__)

router = APIRouter(tags=["underlay"])

#: §13 rate limit, same reasoning as ``DXF_IMPORTS_PER_FIRM_PER_HOUR``: generous
#: for a human replacing a scan a few times while calibrating, cheap to exhaust
#: for a script pushing 10 MB bodies at storage.
IMAGE_UPLOADS_PER_FIRM_PER_HOUR = 30

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"

#: JPEG SOF markers that carry the frame dimensions. C4/C8/CC look like SOFs by
#: range but are DHT/JPG/DAC — tables, not frames — and must be skipped.
_JPEG_SOF_MARKERS = frozenset(range(0xC0, 0xD0)) - {0xC4, 0xC8, 0xCC}

#: Markers with no length payload: TEM, RSTn, SOI, EOI.
_JPEG_STANDALONE = frozenset({0x01, *range(0xD0, 0xDA)})


# ---------------------------------------------------------------------------
# Dimension parsing — stdlib byte-walks, no image library
# ---------------------------------------------------------------------------


def png_dimensions(data: bytes) -> tuple[int, int] | None:
    """Width/height from a PNG, or None when the bytes are not an honest PNG.

    The layout is fixed by the spec: the 8-byte signature, then the IHDR chunk
    MUST come first — 4-byte length (always 13), the literal ``IHDR``, then
    big-endian 4-byte width and height. Anything else is malformed.
    """
    if len(data) < 24 or not data.startswith(PNG_SIGNATURE):
        return None
    if data[8:16] != b"\x00\x00\x00\x0dIHDR":
        return None
    width = int.from_bytes(data[16:20], "big")
    height = int.from_bytes(data[20:24], "big")
    if width <= 0 or height <= 0:
        return None
    return width, height


def jpeg_dimensions(data: bytes) -> tuple[int, int] | None:
    """Width/height from a JPEG, or None.

    JPEG keeps its dimensions in the Start-Of-Frame segment, which sits at no
    fixed offset — walk the segment chain (marker, 2-byte big-endian length that
    includes itself) until an SOF appears. The walk is bounded by the data
    length and every step advances, so it terminates on any input.
    """
    if len(data) < 4 or data[0:3] != b"\xff\xd8\xff":
        return None
    i = 2
    end = len(data)
    while i + 3 < end:
        if data[i] != 0xFF:
            return None  # lost sync: not a marker where a marker must be
        # Fill bytes: consecutive 0xFF before a marker are legal padding.
        while i < end and data[i] == 0xFF:
            i += 1
        if i >= end:
            return None
        marker = data[i]
        i += 1
        if marker in _JPEG_STANDALONE:
            continue
        if i + 1 >= end:
            return None
        length = int.from_bytes(data[i : i + 2], "big")
        if length < 2:
            return None
        if marker in _JPEG_SOF_MARKERS:
            # length(2) precision(1) height(2) width(2)
            if i + 7 > end:
                return None
            height = int.from_bytes(data[i + 3 : i + 5], "big")
            width = int.from_bytes(data[i + 5 : i + 7], "big")
            if width <= 0 or height <= 0:
                return None
            return width, height
        i += length
    return None


def sniff_image(data: bytes) -> tuple[str, int, int] | None:
    """``(kind, width, height)`` for a PNG/JPEG body, or None. Magic bytes only —
    the declared content type never reaches this function on purpose."""
    if data.startswith(PNG_SIGNATURE):
        dims = png_dimensions(data)
        return None if dims is None else ("png", dims[0], dims[1])
    if data[0:3] == b"\xff\xd8\xff":
        dims = jpeg_dimensions(data)
        return None if dims is None else ("jpg", dims[0], dims[1])
    return None


# ---------------------------------------------------------------------------
# Shared plumbing
# ---------------------------------------------------------------------------


def underlay_object_key(firm_id: object, project_id: object, kind: str) -> str:
    """Firm-scoped storage key. A fresh uuid per upload so a stale cached URL can
    never show the replacement image (and vice versa); the old object is deleted
    best-effort after a replace."""
    return "underlays/%s/%s/%s.%s" % (firm_id, project_id, uuid.uuid4().hex, kind)


def _record(underlay: Underlay, settings: Settings) -> UnderlayOut:
    """The wire record, with the presigned GET minted NOW (§13: never stored)."""
    return UnderlayOut(
        object_key=underlay.object_key,
        image_url=sigv4_presign(
            "GET",
            underlay.object_key,
            ttl_seconds=settings.s3_signed_url_ttl_seconds,
            settings=settings,
        ),
        width_px=underlay.width_px,
        height_px=underlay.height_px,
        mm_per_px=underlay.mm_per_px,
        origin_x_mm=underlay.origin_x_mm,
        origin_y_mm=underlay.origin_y_mm,
        opacity=underlay.opacity,
        locked=underlay.locked,
        visible=underlay.visible,
    )


def _no_underlay(project_id: uuid.UUID) -> ApiError:
    """404 with its own code so the client can tell "no underlay yet" (render the
    upload button) from "no such project" (bail out) without string-matching."""
    return ApiError(
        "This project has no underlay image.",
        status=404,
        code="no_underlay",
        action="Upload a plan image (PNG or JPEG) to trace over.",
        extra={"projectId": str(project_id)},
    )


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.post(
    "/projects/{project_id}/underlay/image",
    response_model=UnderlayOut,
    summary="Upload (or replace) the project's tracing-underlay image",
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
async def upload_underlay_image(
    project_id: uuid.UUID,
    request: Request,
    session: SessionDep,
    ctx: TenantDep,
) -> UnderlayOut:
    """Accept the image, verify it really is a PNG/JPEG, store it, upsert the row.

    200 with the full record — unlike the DXF import there is no job: the upload
    IS the result, and the response's ``imageUrl`` is immediately loadable.
    """
    ctx.require_write("uploading an underlay")
    await require_project(session, ctx, project_id)

    await enforce_rate_limit(
        "underlay_uploads",
        ctx.firm_id,
        limit=IMAGE_UPLOADS_PER_FIRM_PER_HOUR,
        window_seconds=3600,
        what="Underlay image upload",
    )

    settings = get_settings()
    body = await _read_body_capped(request, settings.max_image_upload_bytes)

    content_type = request.headers.get("content-type", "")
    if content_type.lower().startswith("multipart/form-data"):
        extracted = _file_from_multipart(content_type, body)
        if extracted is None:
            raise InvalidRequestError(
                "The upload arrived without a file in it.",
                action='Send the image as a form part named "file" and try again.',
            )
        data, _name = extracted
    else:
        data = body

    if not data:
        raise InvalidRequestError(
            "That upload was empty.",
            action="Choose the plan image and try again.",
        )

    sniffed = sniff_image(data)
    if sniffed is None:
        raise UnsupportedMediaTypeError(
            "That file doesn't look like a PNG or JPEG image.",
            action="Export or scan the plan as PNG or JPEG and upload that.",
        )
    kind, width_px, height_px = sniffed
    if width_px > MAX_UNDERLAY_EDGE_PX or height_px > MAX_UNDERLAY_EDGE_PX:
        raise InvalidRequestError(
            "That image is %d×%d px; the limit is %d px on an edge."
            % (width_px, height_px, MAX_UNDERLAY_EDGE_PX),
            action="Downscale the scan (plans trace fine at ~150 dpi) and upload again.",
        )

    repo = UnderlayRepository(session, ctx)
    previous = await repo.get_for_project(project_id)

    # Store BEFORE the row: either the object is durable or the request failed
    # (503 from put_object) — a row pointing at nothing must be unrepresentable.
    key = underlay_object_key(ctx.firm_id, project_id, kind)
    await put_object(
        key, data, content_type="image/png" if kind == "png" else "image/jpeg", settings=settings
    )

    underlay = await repo.upsert_image(
        project_id, object_key=key, width_px=width_px, height_px=height_px
    )

    # The replaced image is now unreachable from any row; sweep it best-effort.
    if previous is not None and previous.object_key != key:
        await delete_object(previous.object_key, settings=settings)

    _log.info(
        "underlay.uploaded",
        project_id=str(project_id),
        kind=kind,
        size_bytes=len(data),
        width_px=width_px,
        height_px=height_px,
        replaced=previous is not None,
    )
    return _record(underlay, settings)


@router.get(
    "/projects/{project_id}/underlay",
    response_model=UnderlayOut,
    summary="The project's underlay, with a fresh presigned image URL",
)
async def get_underlay(
    project_id: uuid.UUID,
    session: SessionDep,
    ctx: TenantDep,
) -> UnderlayOut:
    """404 ``no_underlay`` when none exists — a distinct code, because for the
    canvas that is a normal state (show the upload affordance), not an error."""
    await require_project(session, ctx, project_id)
    underlay = await UnderlayRepository(session, ctx).get_for_project(project_id)
    if underlay is None:
        raise _no_underlay(project_id)
    return _record(underlay, get_settings())


@router.patch(
    "/projects/{project_id}/underlay",
    response_model=UnderlayOut,
    summary="Adjust calibration or view state (partial update)",
)
async def patch_underlay(
    project_id: uuid.UUID,
    patch: UnderlayPatchIn,
    session: SessionDep,
    ctx: TenantDep,
) -> UnderlayOut:
    """Calibration (``mmPerPx``, origin) and view flags only — the image fields
    always come from real uploaded bytes, never from a JSON claim (§13)."""
    ctx.require_write("adjusting the underlay")
    await require_project(session, ctx, project_id)
    repo = UnderlayRepository(session, ctx)
    if await repo.get_for_project(project_id) is None:
        raise _no_underlay(project_id)
    underlay = await repo.patch(
        project_id,
        mm_per_px=patch.mm_per_px,
        origin_x_mm=patch.origin_x_mm,
        origin_y_mm=patch.origin_y_mm,
        opacity=patch.opacity,
        locked=patch.locked,
        visible=patch.visible,
    )
    return _record(underlay, get_settings())


@router.delete(
    "/projects/{project_id}/underlay",
    response_model=Ack,
    summary="Remove the underlay (best-effort storage delete)",
)
async def delete_underlay(
    project_id: uuid.UUID,
    session: SessionDep,
    ctx: TenantDep,
) -> Ack:
    """The row goes, the object goes best-effort: an orphaned image is a cost
    problem for us, a failed delete is a trust problem for the architect."""
    ctx.require_write("removing the underlay")
    await require_project(session, ctx, project_id)
    repo = UnderlayRepository(session, ctx)
    if await repo.get_for_project(project_id) is None:
        raise _no_underlay(project_id)
    removed = await repo.delete_for_project(project_id)
    await delete_object(removed.object_key, settings=get_settings())
    return Ack()


__all__ = [
    "IMAGE_UPLOADS_PER_FIRM_PER_HOUR",
    "jpeg_dimensions",
    "png_dimensions",
    "router",
    "sniff_image",
    "underlay_object_key",
]
