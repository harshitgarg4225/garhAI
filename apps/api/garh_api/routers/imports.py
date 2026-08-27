"""DXF boundary import (Phase 2, playbook F1: "DXF import (layer picker → boundary)").

The pipeline, per §13's upload rules:

1. ``POST /projects/:id/import/dxf`` — the architect uploads a survey DXF (≤ the shared
   ``MAX_DXF_UPLOAD_BYTES`` cap, 20 MB by default). The body is read with a hard byte
   ceiling, **content-sniffed** (DXF is recognisable text; the filename and declared
   content type are treated as claims, not facts), stored, and a
   ``drawings.import_dxf`` job is enqueued. Returns 202 with the job.
2. The drawings worker parses the file inside a time-boxed, memory-capped subprocess
   (``services/drawings/dxf_import.py`` — the §13 crash-safety boundary) and publishes
   per-layer closed-boundary candidates in integer millimetres.
3. ``GET /import-jobs/:id`` — the client polls (or watches the SSE stream) and, once
   succeeded, renders the layer picker from ``result.layers``. The chosen ring becomes
   a ``plot.set_boundary {polygon, source: "dxf"}`` op, assembled **client-side** and
   dispatched through the ordinary op sequencer — this router never writes ops.

Where the job state lives
-------------------------

Import jobs reuse the Redis-backed record that export/sheets jobs use (see the
"Export jobs" DECISION in ``garh_api.queue``): an import is an ephemeral producer of a
one-off result, nothing reads import history later, and the record's firm-scoped key
is the tenancy boundary. The record ``kind`` is ``dxf-import`` so the two surfaces
cannot be confused. Live progress streams from the **existing**
``GET /export-jobs/:id/events`` endpoint — both job families are drawings-queue jobs
with identical event plumbing, and ``eventsUrl`` in every response points there so no
client hard-codes the path.

Where the bytes live
--------------------

Workers hold no storage credentials (§13); they read job assets through the envelope.
Two paths, chosen by size, both deterministic:

* ``<= 1 MiB`` (the envelope's own inline ceiling): the bytes travel inline in the
  envelope, base64. A plot-boundary DXF is typically a few KB; this keeps dev, tests
  and the common case free of any storage dependency.
* ``> 1 MiB``: the API PUTs the object to S3/minio and passes the worker a presigned
  GET (≤10 min, §13). The SigV4 presigner used to live here, stdlib-only; when the
  second uploader appeared (the underlay image route) it was promoted to
  ``garh_api/storage.py`` exactly as this paragraph used to promise. ``_sigv4_presign``
  stays re-exported below because renders.py, sheets.py and jobs.py had already
  imported it from here — one signer, one audit point, no churn in three routers.

The multipart body is parsed with the stdlib ``email`` parser rather than FastAPI's
``UploadFile``: ``File(...)`` imports ``python-multipart`` at route-registration time,
so a missing optional dependency would take down the whole app import, and the §13
byte ceiling has to be enforced while reading the stream anyway. A raw
(non-multipart) body is also accepted as the file itself, which is what ``curl
--data-binary`` and the e2e suite send.
"""

from __future__ import annotations

import base64
import contextlib
import email.parser
import email.policy
import hashlib
import json
import os
import re
import uuid
from typing import Any

from fastapi import APIRouter, Query, Request, status

from garh_api import queue
from garh_api.config import Settings, get_settings
from garh_api.errors import (
    InvalidRequestError,
    PayloadTooLargeError,
    ServiceUnavailableError,
    UnsupportedMediaTypeError,
)
from garh_api.logging import get_logger
from garh_api.routers import (
    IdempotencyGuard,
    IdempotencyKeyDep,
    SessionDep,
    TenantDep,
    enforce_rate_limit,
    require_project,
)
from garh_api.schemas.imports import DXF_IMPORT_JOB_KIND, DxfImportJobOut

# The old private name is kept as an alias (see the module docstring): three other
# routers import ``_sigv4_presign`` from here.
from garh_api.storage import put_object
from garh_api.storage import sigv4_presign as _sigv4_presign
from garh_api.tenancy import EntityNotFoundError

_log = get_logger(__name__)

router = APIRouter(tags=["imports"])

#: §13 rate limit for uploads. Not in ``Settings`` (yet): imports are free-tier work
#: like everything else on the drawings queue, and twenty an hour is generous for a
#: human importing a survey while staying cheap to exhaust for a script — the same
#: reasoning as the OTP constants in ``garh_api.ratelimit``.
DXF_IMPORTS_PER_FIRM_PER_HOUR = 20

#: Mirrors ``services.common.envelope.BlobRef.BLOB_MAX_INLINE_BYTES``. At or under
#: this, the file rides inline in the envelope; over it, object storage.
INLINE_DXF_LIMIT_BYTES = 1_048_576

DEFAULT_FILENAME = "drawing.dxf"

_FILENAME_SAFE = re.compile(r"[^A-Za-z0-9._ -]+")


# ---------------------------------------------------------------------------
# Body handling — §13: sniff content, never trust extension or content type
# ---------------------------------------------------------------------------


async def _read_body_capped(request: Request, cap: int) -> bytes:
    """Read the request body, refusing past ``cap`` **while streaming** — an oversize
    upload is rejected after cap+1 bytes, not buffered to completion first.

    (Bodies over the global ``max_request_body_bytes`` never reach here — the
    middleware in ``main.py`` already 413s them; this enforces the DXF-specific cap
    once the route is on its large-body allowlist.)
    """
    cap_mb = cap // (1024 * 1024)
    chunks: list[bytes] = []
    total = 0
    async for chunk in request.stream():
        total += len(chunk)
        if total > cap:
            raise PayloadTooLargeError(
                "That DXF is larger than the %d MB limit." % cap_mb,
                action="Export just the plot boundary layer and try again.",
            )
        if chunk:
            chunks.append(chunk)
    return b"".join(chunks)


def _clean_filename(raw: str | None) -> str:
    """A display-safe filename: basename only, conservative charset, bounded length."""
    name = os.path.basename((raw or "").strip().replace("\\", "/"))
    name = _FILENAME_SAFE.sub("_", name).strip(" .")
    return name[:120] if name else DEFAULT_FILENAME


def _file_from_multipart(content_type: str, body: bytes) -> tuple[bytes, str] | None:
    """Extract the first file part from a multipart/form-data body, stdlib-only.

    Returns ``(bytes, filename)`` or ``None`` when no usable part exists. Prefers the
    first part carrying a filename (that is the file input), falling back to the first
    part with any payload.
    """
    try:
        prefix = ("Content-Type: %s\r\nMIME-Version: 1.0\r\n\r\n" % content_type).encode("latin-1")
    except UnicodeEncodeError:
        return None
    try:
        message = email.parser.BytesParser(policy=email.policy.default).parsebytes(prefix + body)
    except Exception:
        return None
    if not message.is_multipart():
        return None
    fallback: tuple[bytes, str] | None = None
    for part in message.iter_parts():
        try:
            payload = part.get_payload(decode=True)
        except Exception:
            continue
        if not isinstance(payload, bytes) or not payload:
            continue
        filename = part.get_filename()
        if filename:
            return payload, _clean_filename(filename)
        if fallback is None:
            fallback = (payload, DEFAULT_FILENAME)
    return fallback


def _looks_like_dxf(data: bytes) -> bool:
    """Cheap content sniff — the API-edge mirror of the drawings worker's check
    (``services/drawings/handler.py``), enforced twice on purpose like the size cap.

    ASCII DXF shows a ``SECTION`` group early; binary DXF opens with a fixed sentinel.
    """
    if data.startswith(b"AutoCAD Binary DXF"):
        return True
    head = data[:2048].upper()
    return b"SECTION" in head and (b"HEADER" in head or b"ENTITIES" in head or b"CLASSES" in head)


# ---------------------------------------------------------------------------
# Object storage (files > 1 MiB) — machinery lives in garh_api.storage now
# ---------------------------------------------------------------------------


def dxf_object_key(firm_id: Any, job_id: str) -> str:
    """Firm-scoped object key. The firm id in the path keeps a listing auditable."""
    return "imports/dxf/%s/%s.dxf" % (firm_id, job_id)


async def _store_dxf(key: str, data: bytes, settings: Settings) -> None:
    """PUT the upload to storage. Thin wrapper so this module's call sites (and the
    phase-2 docs that reference ``_store_dxf``) keep their name."""
    await put_object(key, data, content_type="application/dxf", settings=settings)


def _build_asset(
    data: bytes, *, firm_id: Any, job_id: str, settings: Settings
) -> tuple[queue.BlobRef, str | None]:
    """The envelope asset for the upload: inline when small, else a storage key to
    upload to. Returns ``(ref, key_to_upload or None)`` — the PUT itself happens after
    the idempotency claim so a replayed request never re-uploads."""
    digest = hashlib.sha256(data).hexdigest()
    if len(data) <= INLINE_DXF_LIMIT_BYTES:
        return (
            queue.BlobRef(
                inline_base64=base64.b64encode(data).decode("ascii"),
                content_type="application/dxf",
                sha256=digest,
                size_bytes=len(data),
            ),
            None,
        )
    key = dxf_object_key(firm_id, job_id)
    get_url = _sigv4_presign(
        "GET", key, ttl_seconds=settings.s3_signed_url_ttl_seconds, settings=settings
    )
    return (
        queue.BlobRef(
            get_url=get_url,
            key=key,
            content_type="application/dxf",
            sha256=digest,
            size_bytes=len(data),
        ),
        key,
    )


# ---------------------------------------------------------------------------
# Shared response plumbing
# ---------------------------------------------------------------------------


def _events_url(request: Request, job_id: str) -> str:
    """Import jobs stream from the export-jobs SSE endpoint — same Redis record, same
    lifecycle plumbing (see the module docstring)."""
    settings = get_settings()
    return (
        str(request.base_url).rstrip("/")
        + settings.api_prefix
        + ("/export-jobs/%s/events" % job_id)
    )


def _problem_message(event: queue.ProgressEvent) -> str:
    """User-facing failure copy from a worker's problem body (golden rule 9)."""
    message = event.data.get("message") or event.message
    action = event.data.get("action")
    text = str(message or "That import didn't finish.")
    return "%s %s" % (text, action) if action else text


async def _reconcile(record: queue.ExportJob) -> queue.ExportJob:
    """Fold any terminal worker event the lifecycle consumer has not applied yet into
    the record, and pin a succeeded job's ``result`` onto it.

    Idempotent with ``routers.jobs._apply_drawings_lifecycle`` (the consumer only
    writes status/progress/error; this additionally persists ``params.result``, which
    only this surface needs). Reading the progress backlog directly means the layer
    picker works even when the consumer lags — and, because the record has a 24h TTL
    while the backlog has 1h, the result survives long after the events expire.
    """
    if record.status == "succeeded" and "result" in record.params:
        return record
    if record.status == "cancelled" or (record.status == "failed" and record.error):
        return record

    events = await queue.read_progress_backlog(record.id)
    terminal = None
    for event in events:
        if event.terminal:
            terminal = event
    if terminal is None:
        return record

    changes: dict[str, Any] = {"status": queue.status_for_event(terminal.type)}
    if terminal.type == "succeeded":
        changes["progress"] = 100
        result = {
            name: terminal.data[name]
            for name in ("layers", "units", "skipped")
            if name in terminal.data
        }
        if result.get("layers") is not None:
            changes["params"] = {**record.params, "result": result}
    elif terminal.type in ("failed", "dead_lettered"):
        changes["error"] = _problem_message(terminal)

    updated = record.evolve(**changes)
    # Serving the reconciled view still beats failing the read; the write is
    # retried on the next GET.
    with contextlib.suppress(queue.QueueUnavailableError):
        await queue.put_export_job(updated)
    return updated


async def _require_import_job(ctx: Any, job_id: str) -> queue.ExportJob:
    """Load an import job for this firm or 404. The firm id is part of the Redis key,
    so another tenant's job id resolves to nothing — indistinguishable from absent,
    which is the §13 answer. A non-import record 404s too rather than leak that the
    id exists on another surface."""
    if not job_id or len(job_id) > 64:
        raise EntityNotFoundError("import_job", job_id[:64] if job_id else "")
    record = await queue.get_export_job(ctx.firm_id, job_id)
    if record is None or record.kind != DXF_IMPORT_JOB_KIND:
        raise EntityNotFoundError("import_job", job_id)
    return record


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.post(
    "/projects/{project_id}/import/dxf",
    response_model=DxfImportJobOut,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Upload a DXF and queue the boundary extraction",
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
                "application/dxf": {"schema": {"type": "string", "format": "binary"}},
            },
        }
    },
)
async def import_dxf(
    project_id: uuid.UUID,
    request: Request,
    session: SessionDep,
    ctx: TenantDep,
    filename: str | None = Query(
        default=None,
        max_length=200,
        description="Display name for raw-body uploads; multipart carries its own.",
    ),
    idempotency_key: IdempotencyKeyDep = None,
) -> DxfImportJobOut:
    """Accept the file, verify it is plausibly a DXF, store it, queue the parse.

    202, not 201: the layer candidates do not exist yet. Watch ``eventsUrl`` or poll
    ``GET /import-jobs/:id`` — §15's skeleton state, never a dead spinner.
    """
    ctx.require_write("importing a DXF")
    await require_project(session, ctx, project_id)

    # Idempotency replay FIRST — before the rate-limit slot is charged and before
    # the (up to 20 MB) body is buffered. A proxy or flaky-uplink retry of the same
    # upload must return the stored job for free: the audience is on mobile data
    # (§15), and automatic retries were burning 1 of the firm's hourly slots each.
    # The guard key is firm-scoped, so answering before the rate limit leaks nothing.
    guard = IdempotencyGuard(scope="import-dxf", key=idempotency_key, firm_id=ctx.firm_id)
    replayed = await guard.begin()
    if replayed is not None:
        return DxfImportJobOut.model_validate(replayed)

    try:
        await enforce_rate_limit(
            "dxf_imports",
            ctx.firm_id,
            limit=DXF_IMPORTS_PER_FIRM_PER_HOUR,
            window_seconds=3600,
            what="DXF import",
        )

        settings = get_settings()
        body = await _read_body_capped(request, settings.max_dxf_upload_bytes)

        content_type = request.headers.get("content-type", "")
        if content_type.lower().startswith("multipart/form-data"):
            extracted = _file_from_multipart(content_type, body)
            if extracted is None:
                raise InvalidRequestError(
                    "The upload arrived without a file in it.",
                    action='Send the DXF as a form part named "file" and try again.',
                )
            data, name = extracted
        else:
            data, name = body, _clean_filename(filename)
        if filename:
            name = _clean_filename(filename)

        if not data:
            raise InvalidRequestError(
                "That upload was empty.",
                action="Choose the DXF file and try again.",
            )
        if len(data) > settings.max_dxf_upload_bytes:
            raise PayloadTooLargeError(
                "That DXF is larger than the %d MB limit."
                % (settings.max_dxf_upload_bytes // (1024 * 1024)),
                action="Export just the plot boundary layer and try again.",
            )
        if not _looks_like_dxf(data):
            raise UnsupportedMediaTypeError(
                "That file doesn't look like a DXF drawing.",
                action="Export a DXF (R12 or newer) from your CAD software and upload that.",
            )

        job_id = queue.new_job_id()
        asset, storage_key = _build_asset(
            data, firm_id=ctx.firm_id, job_id=job_id, settings=settings
        )
        if storage_key is not None:
            await _store_dxf(storage_key, data, settings)

        payload: dict[str, Any] = {"filename": name, "sizeBytes": len(data)}
        record = await queue.put_export_job(
            queue.ExportJob(
                id=job_id,
                firm_id=str(ctx.firm_id),
                project_id=str(project_id),
                kind=DXF_IMPORT_JOB_KIND,
                status="queued",
                params=payload,
            )
        )
        try:
            await queue.enqueue(
                queue.JobEnvelope(
                    job_id=job_id,
                    kind=queue.JOB_DRAWINGS_IMPORT_DXF,
                    firm_id=str(ctx.firm_id),
                    project_id=str(project_id),
                    actor_user_id=str(ctx.user_id) if ctx.user_id else None,
                    request_id=ctx.request_id,
                    idempotency_key=idempotency_key,
                    payload=payload,
                    assets={"dxf": asset},
                )
            )
        except queue.QueueUnavailableError as exc:
            raise ServiceUnavailableError(
                "We couldn't start reading that file — the job queue is unreachable.",
                dependency="redis",
                retry_after_seconds=5,
            ) from exc

        out = DxfImportJobOut.of(record, events_url=_events_url(request, job_id))
    except Exception:
        await guard.release()
        raise

    await guard.store(json.loads(out.model_dump_json(by_alias=True)))
    _log.info(
        "dxf_import.queued",
        job_id=job_id,
        size_bytes=len(data),
        inline=storage_key is None,
    )
    return out


@router.get(
    "/import-jobs/{job_id}",
    response_model=DxfImportJobOut,
    summary="Import job state — carries the layer candidates once succeeded",
)
async def get_import_job(
    job_id: str,
    request: Request,
    ctx: TenantDep,
) -> DxfImportJobOut:
    """Poll target for the layer picker.

    On success the response's ``result.layers`` holds the per-layer closed rings; the
    client shows the picker and dispatches ``plot.set_boundary`` with the chosen
    polygon. On failure ``error`` is the worker's own copy — what happened and what to
    do next, verbatim (golden rule 9).
    """
    record = await _require_import_job(ctx, job_id)
    record = await _reconcile(record)
    return DxfImportJobOut.of(
        record,
        events_url=_events_url(request, record.id),
        result=record.params.get("result"),
    )


__all__ = [
    "DXF_IMPORTS_PER_FIRM_PER_HOUR",
    "INLINE_DXF_LIMIT_BYTES",
    "dxf_object_key",
    "router",
]
