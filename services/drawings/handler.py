"""Drawings worker job handlers (§7): sheet generation, export, DXF import.

Three job kinds share this handler because they share the queue and the safety
envelope, but they are separate methods with separate budgets:

``drawings.generate_sheets``
    model → the six municipal sheets. §14 budget: <=5 min for a G+1 3BHK.
``drawings.export``
    sheets → PDF set / DXF / glTF / PNG pack, uploaded to a presigned URL.
``drawings.import_dxf``
    an uploaded DXF → per-layer closed-boundary candidates for the plot (Phase 2,
    fully implemented — the parse lives in :mod:`services.drawings.dxf_import`).

**§13 applies hardest to the third one.** "file uploads (DXF <=20MB, images <=10MB)
type-sniffed, parsed in worker with 10s timeout + memory cap (malicious DXF =
crash-safe)". The size cap and the sniff are enforced here; the timeout and the memory
cap are enforced by the subprocess boundary inside ``dxf_import.parse_dxf_bytes`` — a
hostile file kills a disposable child interpreter, never this worker.

Where the drawing actually happens
----------------------------------
Nowhere in this module. :mod:`services.drawings.pipeline` turns a model document into
sheets as one deterministic, dependency-free function; this file adds only what a pure
function cannot have — progress events, blob IO and a wall-clock budget. That split is
why ``services/drawings/tests/test_pipeline.py`` can exercise all ten sheets of a G+1
on a bare interpreter with no queue, no database and no ``ezdxf``.

The envelope contract (the API's enqueue helper must match exactly)
------------------------------------------------------------------
``drawings.generate_sheets``

=================================  =========================================
``assets["model"]``                folded ``ProjectDoc`` JSON. **Required.**
``assets["areas"]``                ``AreaStatement.to_json()``. Optional; without
                                   it the area-statement sheet is skipped with a
                                   note rather than invented.
``assets["previousModel"]``        the folded ``ProjectDoc`` JSON of the state the
                                   PREVIOUS revision was issued at. Optional; with
                                   it (and a valid revision register in the payload)
                                   the floor plans carry revision clouds around what
                                   changed. Without it the set draws exactly as
                                   before — no clouds, no cost.
``payload``                        designVersionId · kinds · scaleDenominator ·
                                   sheetSize · dimToJamb · titleBlock ·
                                   revisions · numberPrefix · formats
``outputs["<slug>.<fmt>"]``        one presigned PUT per sheet per format. Sheets
                                   whose slug has no output are still drawn and
                                   reported — they just carry no download.
=================================  =========================================

``drawings.export`` takes the same two assets, ``payload.kind`` from
:data:`EXPORT_KINDS`, and writes one artefact to ``outputs["export"]``.

The model is never inlined in the payload — ``JobEnvelope`` says so itself, and an
envelope carrying a folded G+2 would be re-queued in full on every retry.
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
import os
import shutil
import subprocess
import tempfile
import time
from collections.abc import Mapping, Sequence
from typing import Any

from services.common.errors import InvalidJobError
from services.common.jobstore import JobResult
from services.common.logging import get_logger
from services.common.runtime import BaseJobHandler, JobContext
from services.drawings import dxf_import
from services.drawings.pipeline import (
    PipelineError,
    SheetBundle,
    SheetSetResult,
    TransportStatement,
    build_sheets,
    canonical_sheet_kinds,
    sheet_dxf_bytes,
    sheet_glb_bytes,
    sheet_png_manifest,
    svg_set_to_pdf_bytes,
)

log = get_logger("drawings.handler")

PHASE_SHEETS = "Phase 8 (Drawings & exports)"
PHASE_IMPORT = "Phase 2 (Plot, brief, rules engine)"

#: §14: "Sheet set G+1 3BHK <= 5min". The handler budget adds headroom for upload.
SHEET_TIMEOUT_SECONDS = 420
EXPORT_TIMEOUT_SECONDS = 600

#: §13 upload caps — the DEFAULT only. The live value is
#: ``ctx.settings.max_dxf_upload_bytes`` (env ``MAX_DXF_UPLOAD_BYTES``), which is the
#: same variable the API enforces at the edge.
#:
#: This constant used to be the value the handler compared against, so it silently
#: ignored the env var: raising ``MAX_DXF_UPLOAD_BYTES`` to 30 MB made the API accept
#: a 25 MB upload and this worker reject it afterwards — the architect saw a failed
#: job with no explanation instead of an immediate 413. It stays exported because
#: tests and docs reference the playbook's 20 MB number.
MAX_DXF_BYTES = 20 * 1024 * 1024

#: Asset / output names in the envelope. The API must use exactly these keys.
ASSET_DXF = "dxf"
ASSET_MODEL = "model"
ASSET_AREAS = "areas"
#: The previous issue's folded document, for revision clouds (D-1). Optional: a first
#: issue has no previous state, and a set that has never been revised must still draw.
ASSET_PREVIOUS_MODEL = "previousModel"
OUTPUT_SHEETS = "sheets"
OUTPUT_EXPORT = "export"

EXPORT_KINDS = ("pdf-set", "dxf", "gltf", "png-pack")

#: Formats a sheet is published in. SVG always works (pure string output); DXF needs
#: ``ezdxf``; PDF needs a converter binary. A format that cannot be produced is
#: reported as an unavailable format, never as a failed job — see :meth:`_publish`.
DEFAULT_SHEET_FORMATS: tuple[str, ...] = ("svg", "dxf")

_CONTENT_TYPES: dict[str, str] = {
    # Not `image/svg+xml`: §13 requires sanitised SVG and forbids serving drawings as a
    # type a browser will execute scripts inside. `routers/jobs.py` makes the same call
    # for the same reason.
    "svg": "application/octet-stream",
    "dxf": "application/dxf",
    "pdf": "application/pdf",
    "gltf": "model/gltf-binary",
    "png-pack": "application/zip",
}

#: How long a single sheet may take to rasterise for the PNG pack.
PNG_TIMEOUT_SECONDS = 60


class DrawingsJobHandler(BaseJobHandler):
    """Generates sheets, exports them, and imports DXF boundaries."""

    kinds = ("drawings.generate_sheets", "drawings.export", "drawings.import_dxf")
    timeout_seconds: int | None = EXPORT_TIMEOUT_SECONDS

    def timeout_for(self, ctx: JobContext) -> int | None:
        """Per-kind budget, resolved by the runner BEFORE the job starts.

        This must live here, not as an assignment inside :meth:`handle`: the
        runner captures the timeout before the handler coroutine first runs, so
        an in-``handle`` assignment only took effect for the NEXT job on this
        (shared) handler instance. Import gets ``max(30, 3× parse timeout)`` —
        the real §13 kill is the subprocess boundary inside ``parse_dxf_bytes``;
        this outer budget only bounds fetch + queue plumbing around it.
        """
        kind = ctx.envelope.kind
        if kind == "drawings.generate_sheets":
            return SHEET_TIMEOUT_SECONDS
        if kind == "drawings.import_dxf":
            return max(30, ctx.settings.dxf_parse_timeout_seconds * 3)
        return EXPORT_TIMEOUT_SECONDS

    async def handle(self, ctx: JobContext) -> JobResult:
        kind = ctx.envelope.kind
        if kind == "drawings.generate_sheets":
            return await self._generate_sheets(ctx)
        if kind == "drawings.export":
            return await self._export(ctx)
        if kind == "drawings.import_dxf":
            return await self._import_dxf(ctx)
        raise InvalidJobError(
            "This job was sent to the wrong place.",
            detail="drawings worker received kind=%r" % kind,
        )

    # ------------------------------------------------------------------
    # Sheets (§7, F7-A)
    # ------------------------------------------------------------------
    async def _generate_sheets(self, ctx: JobContext) -> JobResult:
        """model → sheets → stored artefacts, with a real progress event per sheet.

        The timing breakdown is logged and returned (``timings``) because §14 asks for
        a 5-minute budget and "where the time goes" is only answerable if it is
        measured. On the demo G+1 the geometry is ~30 ms and everything else is IO:
        fetching the model, and one upload per sheet per format.
        """
        if not ctx.envelope.design_version_id:
            raise InvalidJobError(
                "These drawings aren't linked to a saved version of your design.",
                action="Save the design and generate the sheets again.",
                detail="drawings.generate_sheets requires designVersionId so sheets "
                "pin to the model they were drawn from",
            )
        requested = _requested_kinds(ctx.payload)
        formats = _requested_formats(ctx.payload)
        await ctx.progress.stage(
            "sheets",
            "Setting up the drawing sheets…",
            percent=2,
            sheetCount=len(requested),
        )

        timings: dict[str, int] = {}
        started = time.perf_counter()
        bundle = await self._load_bundle(ctx, kinds=requested)
        timings["loadMs"] = _ms_since(started)

        ctx.raise_if_cancelled()
        await ctx.progress.stage(
            "draw", "Drawing the sheets…", percent=15, sheets=len(bundle.kinds)
        )

        # Sheet geometry is pure CPU. It is milliseconds on a house, but `to_thread`
        # keeps the heartbeat flowing if a pathological model ever makes it seconds —
        # a worker that stops answering gets its job redelivered while still running it.
        drawing_started = time.perf_counter()
        progress_events: list[tuple[int, int, str, bool]] = []

        def record(index: int, total: int, sheet: Any, slug: str) -> None:
            progress_events.append((index, total, slug, sheet is not None))

        try:
            result: SheetSetResult = await asyncio.to_thread(build_sheets, bundle, on_sheet=record)
        except PipelineError as exc:
            raise InvalidJobError(
                exc.message, action=exc.action or None, detail=exc.detail
            ) from exc
        timings["drawMs"] = _ms_since(drawing_started)

        # Replayed after the thread rather than from inside it: publishing to Redis is
        # async, and marshalling every callback back onto the loop would serialise the
        # drawing behind the network. The events are still real and in order — they
        # describe work that has happened, which is what §15 forbids faking.
        for index, total, slug, drawn in progress_events:
            await ctx.progress.stage(
                "draw",
                "Drew %s (%d of %d)." % (slug, index + 1, total)
                if drawn
                else "Skipped %s (%d of %d)." % (slug, index + 1, total),
                percent=15 + int(45 * (index + 1) / max(1, total)),
                sheetId=slug,
            )

        ctx.raise_if_cancelled()
        publish_started = time.perf_counter()
        artifacts, format_notes = await self._publish(ctx, result, formats)
        timings["publishMs"] = _ms_since(publish_started)
        timings["totalMs"] = _ms_since(started)

        payload: dict[str, Any] = result.to_json()
        payload["designVersionId"] = ctx.envelope.design_version_id
        payload["notes"] = list(result.notes) + format_notes
        payload["timings"] = timings
        payload["formats"] = list(formats)
        for layout in payload["sheets"]:
            layout["artifacts"] = artifacts.get(layout["sheetId"], {})

        log.info(
            "drawings.sheets.done",
            sheets=len(result.sheets),
            skipped=len(result.skipped),
            chains=result.chain_count,
            label_collisions=result.label_collisions,
            draw_ms=timings["drawMs"],
            publish_ms=timings["publishMs"],
            total_ms=timings["totalMs"],
        )
        if result.label_collisions:
            # A quality defect, surfaced rather than swallowed (§16 wants zero).
            await ctx.progress.warning(
                "%d dimension label%s overlap on this set — check them before you print."
                % (result.label_collisions, "" if result.label_collisions == 1 else "s"),
                labelCollisions=result.label_collisions,
            )
        await ctx.progress.stage(
            "sheets", "Drawing set ready.", percent=100, sheets=len(result.sheets)
        )
        return JobResult(
            data=payload,
            message="Drew %d sheet%s."
            % (len(result.sheets), "" if len(result.sheets) == 1 else "s"),
        )

    async def _load_bundle(self, ctx: JobContext, *, kinds: Sequence[str]) -> SheetBundle:
        """Fetch the model (and the area statement, if the API sent one)."""
        model_ref = ctx.envelope.require_asset(ASSET_MODEL)
        raw = await ctx.blobs.fetch(model_ref, what="design")
        document = _decode_json(raw, what="design")

        areas: TransportStatement | None = None
        areas_ref = ctx.envelope.assets.get(ASSET_AREAS)
        if areas_ref is not None:
            areas_raw = await ctx.blobs.fetch(areas_ref, what="area statement")
            areas = TransportStatement.from_json(_decode_json(areas_raw, what="area statement"))

        # The previous issue, for revision clouds. Fetched only when the API sent it, so
        # a first issue costs no extra round trip.
        previous: Mapping[str, Any] | None = None
        previous_ref = ctx.envelope.assets.get(ASSET_PREVIOUS_MODEL)
        if previous_ref is not None:
            previous = _decode_json(
                await ctx.blobs.fetch(previous_ref, what="previous design"),
                what="previous design",
            )

        payload = dict(ctx.payload)
        payload["kinds"] = list(kinds)
        try:
            bundle = SheetBundle.from_payload(payload, document=document)
        except PipelineError as exc:
            raise InvalidJobError(
                exc.message, action=exc.action or None, detail=exc.detail
            ) from exc
        # `from_payload` does not decode the statement or the previous design (they are
        # assets, not payload fields), so they are attached here — one replace, one place.
        return dataclasses.replace(bundle, areas=areas, previous_document=previous)

    async def _publish(
        self, ctx: JobContext, result: SheetSetResult, formats: Sequence[str]
    ) -> tuple[dict[str, dict[str, str]], list[str]]:
        """Write each sheet's artefacts to their presigned destinations.

        Returns ``({slug: {fmt: getUrl}}, notes)``. A format the server cannot produce
        (no ``ezdxf``, no PDF converter) is reported ONCE as a note and omitted from
        every sheet's artifacts — so the UI's download menu shows what actually exists
        instead of offering a button that 404s. Only SVG is treated as mandatory; it
        needs nothing installed, so its failure is a real failure.
        """
        artifacts: dict[str, dict[str, str]] = {}
        notes: list[str] = []
        unavailable: dict[str, str] = {}
        outputs = ctx.envelope.outputs

        for sheet in result.sheets:
            per_sheet: dict[str, str] = {}
            for fmt in formats:
                if fmt in unavailable:
                    continue
                ref = outputs.get("%s.%s" % (sheet.slug, fmt))
                if ref is None:
                    continue
                try:
                    data = await asyncio.to_thread(_sheet_bytes, sheet, fmt)
                except PipelineError as exc:
                    unavailable[fmt] = exc.message
                    notes.append("%s %s" % (exc.message, exc.action or ""))
                    log.warning("drawings.format_unavailable", sheet_format=fmt, reason=exc.detail)
                    continue
                stored = await ctx.blobs.put(
                    ref,
                    data,
                    content_type=_CONTENT_TYPES.get(fmt, "application/octet-stream"),
                    what="%s %s" % (sheet.number, fmt.upper()),
                )
                # The object KEY, not the presigned GET. A presigned URL expires in
                # ≤10 minutes (§13), and this value is persisted in `sheets.layout`
                # where it may be read tomorrow; the API re-signs from the key on
                # every request (exactly what `renders.fresh_image_url` does for the
                # same reason). Falls back to the URL only when no key was minted, so
                # a developer's file:// run still produces something openable.
                per_sheet[fmt] = stored.key or ref.key or ref.get_url or ""
                if not per_sheet[fmt]:
                    per_sheet.pop(fmt, None)
            if per_sheet:
                artifacts[sheet.slug] = per_sheet
            elif outputs:
                # Outputs were minted but none took: the API can still show the sheet,
                # and the absence of a download is visible rather than mysterious.
                log.warning("drawings.sheet_not_published", sheet_id=sheet.slug)
        return (artifacts, [note.strip() for note in notes if note.strip()])

    # ------------------------------------------------------------------
    # Exports (§11 POST /projects/:id/export)
    # ------------------------------------------------------------------
    async def _export(self, ctx: JobContext) -> JobResult:
        """One artefact, one presigned destination, one honest download URL.

        Exports **re-derive** the sheets from the same model+statement bundle rather
        than fetching the nine SVGs a previous sheets job stored. Generation is
        deterministic (asserted in ``test_pipeline.py``), so the two agree byte for
        byte, and an export therefore works on a project whose sheets have never been
        generated — which is what an architect expects from a Download button.
        """
        kind = str(ctx.payload.get("kind") or "")
        if kind not in EXPORT_KINDS:
            raise InvalidJobError(
                "We don't recognise that export format.",
                action="Pick a format from the download menu and try again.",
                detail="export kind=%r, expected one of %s" % (kind, ", ".join(EXPORT_KINDS)),
            )
        ref = ctx.envelope.require_output(OUTPUT_EXPORT)
        await ctx.progress.stage("export", "Preparing your download…", percent=5, kind=kind)

        started = time.perf_counter()
        if kind == "gltf":
            # The 3D bridge needs the model and nothing else — no sheets, no rules.
            model_ref = ctx.envelope.require_asset(ASSET_MODEL)
            document = _decode_json(await ctx.blobs.fetch(model_ref, what="design"), what="design")
            await ctx.progress.stage("export", "Building the 3D model…", percent=40, kind=kind)
            data = await asyncio.to_thread(sheet_glb_bytes, document)
            meta: dict[str, Any] = {"bytes": len(data)}
        else:
            bundle = await self._load_bundle(ctx, kinds=_requested_kinds(ctx.payload))
            await ctx.progress.stage("export", "Drawing the sheets…", percent=30, kind=kind)
            try:
                result = await asyncio.to_thread(build_sheets, bundle)
            except PipelineError as exc:
                raise InvalidJobError(
                    exc.message, action=exc.action or None, detail=exc.detail
                ) from exc
            selected = _select_sheets(result, ctx.payload.get("sheetIds"))
            ctx.raise_if_cancelled()
            await ctx.progress.stage(
                "export",
                "Writing the %s…" % _format_word(kind),
                percent=60,
                kind=kind,
                sheets=len(selected),
            )
            data, meta = await self._render_export(ctx, kind, selected)

        ctx.raise_if_cancelled()
        stored = await ctx.blobs.put(
            ref,
            data,
            content_type=_CONTENT_TYPES.get(kind, "application/octet-stream"),
            what="%s export" % kind,
        )
        elapsed_ms = _ms_since(started)
        log.info("drawings.export.done", export_kind=kind, bytes=len(data), duration_ms=elapsed_ms)
        await ctx.progress.stage("export", "Your download is ready.", percent=100, kind=kind)
        return JobResult(
            data={
                "kind": kind,
                "downloadUrl": ref.get_url or stored.key or "",
                "bytes": len(data),
                "durationMs": elapsed_ms,
                **meta,
            },
            message="Your %s is ready." % _format_word(kind),
        )

    async def _render_export(
        self, ctx: JobContext, kind: str, sheets: Sequence[Any]
    ) -> tuple[bytes, dict[str, Any]]:
        if not sheets:
            raise InvalidJobError(
                "There are no sheets to export.",
                action="Generate the drawing set first, then export.",
                detail="sheet selection resolved to zero sheets",
            )
        try:
            if kind == "dxf":
                data = await asyncio.to_thread(sheet_dxf_bytes, [sheet.drawing for sheet in sheets])
                return (data, {"sheets": len(sheets)})
            if kind == "pdf-set":
                data, report = await asyncio.to_thread(
                    svg_set_to_pdf_bytes, [sheet.svg for sheet in sheets]
                )
                return (data, {"pages": report.get("pages"), "mergeTool": report.get("mergeTool")})
            if kind == "png-pack":
                return await asyncio.to_thread(_png_pack_zip, sheets)
        except PipelineError as exc:
            # Not a job failure with a traceback: a missing converter is a *format*
            # that is unavailable on this server, and the message says which one and
            # what to do instead (golden rule 9).
            raise InvalidJobError(
                exc.message, action=exc.action or None, detail=exc.detail
            ) from exc
        raise InvalidJobError(
            "We don't recognise that export format.",
            action="Pick a format from the download menu and try again.",
            detail="unhandled export kind=%r" % kind,
        )

    # ------------------------------------------------------------------
    async def _import_dxf(self, ctx: JobContext) -> JobResult:
        """Parse an uploaded DXF into per-layer plot-boundary candidates (Phase 2).

        Order of the guards matters: claimed size (cheap), fetch, measured size,
        content sniff, and only then the sandboxed parse. The result's ``layers``
        array is exactly what the client's layer picker renders; the chosen ring
        becomes a ``plot.set_boundary`` op client-side.
        """
        # Read the cap from settings, not from MAX_DXF_BYTES: the API enforces the
        # same MAX_DXF_UPLOAD_BYTES at the edge, and two independent numbers means one
        # side accepts what the other refuses.
        cap = ctx.settings.max_dxf_upload_bytes
        cap_mb = cap // (1024 * 1024)
        ref = ctx.envelope.require_asset(ASSET_DXF)
        # sizeBytes is the uploader's CLAIM. Checking it first is a cheap way to
        # refuse an oversized file before spending a blob fetch on it; the
        # post-fetch check below is the one that is actually authoritative.
        if ref.size_bytes is not None and ref.size_bytes > cap:
            raise InvalidJobError(
                "That DXF is larger than the %d MB limit." % cap_mb,
                action="Export just the plot boundary layer and try again.",
                detail="dxf is %d bytes, cap is %d" % (ref.size_bytes, cap),
            )
        await ctx.progress.stage("import", "Reading your DXF…", percent=10)
        data = await ctx.blobs.fetch(ref, what="DXF drawing")
        if len(data) > cap:
            raise InvalidJobError(
                "That DXF is larger than the %d MB limit." % cap_mb,
                action="Export just the plot boundary layer and try again.",
                detail="dxf is %d bytes, cap is %d" % (len(data), cap),
            )
        if not _looks_like_dxf(data):
            raise InvalidJobError(
                "That file doesn't look like a DXF drawing.",
                action="Export a DXF from your CAD software and upload that.",
                detail="content sniff failed: no DXF section marker in the first bytes",
            )

        ctx.raise_if_cancelled()
        await ctx.progress.stage(
            "parse",
            "Looking for closed boundaries…",
            percent=40,
            sizeBytes=len(data),
        )
        # The parse spawns a memory-capped, time-boxed subprocess (the §13 crash-safety
        # boundary); to_thread keeps this event loop serving heartbeats meanwhile.
        result = await asyncio.to_thread(
            dxf_import.parse_dxf_bytes,
            data,
            timeout_seconds=ctx.settings.dxf_parse_timeout_seconds,
            memory_limit_mb=ctx.settings.dxf_parse_memory_limit_mb,
        )
        ctx.raise_if_cancelled()

        polyline_count, layer_count = dxf_import.candidate_count(result)
        await ctx.progress.stage(
            "layers",
            "Found %d closed boundar%s on %d layer%s."
            % (
                polyline_count,
                "y" if polyline_count == 1 else "ies",
                layer_count,
                "" if layer_count == 1 else "s",
            ),
            percent=90,
            polylines=polyline_count,
            layers=layer_count,
        )
        # `data` is published verbatim in the succeeded event — the API surfaces it as
        # the job result the layer picker reads (units/skipped ride along as chips).
        return JobResult(
            data=result,
            message="Pick the layer that holds your plot boundary.",
        )


# ---------------------------------------------------------------------------
# Payload helpers
# ---------------------------------------------------------------------------
def _requested_kinds(payload: Mapping[str, Any]) -> tuple[str, ...]:
    """The requested sheet kinds, in the canonical (DB) vocabulary.

    Accepts either spelling — ``floor`` or ``floor-plan``. It used to validate only
    the drawing vocabulary, so ``POST /sheets/generate {kinds:["floor"]}`` passed the
    API's validator (which checks the DB vocabulary) and then failed here.
    """
    raw = payload.get("kinds")
    if raw is None:
        return canonical_sheet_kinds(None)
    if not isinstance(raw, list) or not raw:
        raise InvalidJobError(
            "We couldn't tell which sheets to draw.",
            detail="payload.kinds must be a non-empty array when present",
        )
    try:
        return canonical_sheet_kinds(raw)
    except PipelineError as exc:
        raise InvalidJobError(exc.message, action=exc.action or None, detail=exc.detail) from exc


def _requested_formats(payload: Mapping[str, Any]) -> tuple[str, ...]:
    raw = payload.get("formats")
    if not isinstance(raw, list) or not raw:
        return DEFAULT_SHEET_FORMATS
    formats = []
    for item in raw:
        key = str(item).lower()
        if key not in _CONTENT_TYPES or key not in ("svg", "dxf", "pdf"):
            raise InvalidJobError(
                "We can't publish a sheet as %r." % item,
                action="Ask for svg, dxf or pdf.",
                detail="unknown sheet format %r" % item,
            )
        if key not in formats:
            formats.append(key)
    # SVG is the on-screen viewer's source and costs nothing to produce; a set without
    # it would render as a list of filenames.
    if "svg" not in formats:
        formats.insert(0, "svg")
    return tuple(formats)


def _decode_json(raw: bytes, *, what: str) -> dict[str, Any]:
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise InvalidJobError(
            "We couldn't read the %s attached to this job." % what,
            action="Try again from the app.",
            detail="%s is not UTF-8 JSON: %s" % (what, exc),
        ) from exc
    if not isinstance(value, dict):
        raise InvalidJobError(
            "We couldn't read the %s attached to this job." % what,
            action="Try again from the app.",
            detail="%s decoded to %s, expected an object" % (what, type(value).__name__),
        )
    return value


def _select_sheets(result: SheetSetResult, sheet_ids: Any) -> list[Any]:
    """Honour ``payload.sheetIds`` (slugs, numbers or DB uuids) or take the whole set.

    Unknown ids are ignored rather than fatal: a stale UI selection referring to a
    sheet the regeneration dropped should still export the sheets that do exist.
    """
    if not isinstance(sheet_ids, list) or not sheet_ids:
        return list(result.sheets)
    wanted = {str(item) for item in sheet_ids}
    chosen = [sheet for sheet in result.sheets if sheet.slug in wanted or sheet.number in wanted]
    return chosen or list(result.sheets)


def _sheet_bytes(sheet: Any, fmt: str) -> bytes:
    if fmt == "svg":
        return sheet.svg.encode("utf-8")
    if fmt == "dxf":
        return sheet_dxf_bytes([sheet.drawing])
    if fmt == "pdf":
        data, _report = svg_set_to_pdf_bytes([sheet.svg])
        return data
    raise PipelineError(
        "We can't publish a sheet as %s." % fmt.upper(),
        action="Ask for SVG, DXF or PDF.",
        detail="unknown sheet format %r" % fmt,
    )


def _format_word(kind: str) -> str:
    return {
        "pdf-set": "PDF",
        "dxf": "DXF",
        "gltf": "3D model",
        "png-pack": "image pack",
    }.get(kind, kind)


def _ms_since(started: float) -> int:
    return int((time.perf_counter() - started) * 1000)


# ---------------------------------------------------------------------------
# PNG pack
# ---------------------------------------------------------------------------
def _png_pack_zip(sheets: Sequence[Any]) -> tuple[bytes, dict[str, Any]]:
    """Rasterise each sheet and zip it, at the sizes ``pack_plan`` specified.

    Rasterising needs the same converter binary the PDF path needs; there is no pure
    Python SVG renderer in this dependency set and there will not be a hand-rolled one.
    When no converter is installed this raises the same actionable
    :class:`PipelineError` the PDF path raises — the zip is never filled with SVGs
    named ``.png``, because a file that lies about its format is worse than a missing
    download.
    """
    import zipfile

    from services.drawings.export.pdf import CONVERTERS, find_converter

    converter = find_converter()
    if converter is None:
        raise PipelineError(
            "PNG export isn't available on this server yet.",
            action="Download the PDF or DXF instead, or ask your administrator to "
            "install an SVG renderer.",
            detail="no rasteriser found; tried %s" % ", ".join(name for name, _d, _h in CONVERTERS),
        )
    binary, name, _hint = converter
    manifest = sheet_png_manifest(list(sheets))

    buffer = tempfile.TemporaryDirectory(prefix="garh-png-")
    try:
        import io

        archive = io.BytesIO()
        with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
            for sheet, entry in zip(sheets, manifest, strict=False):
                svg_path = os.path.join(buffer.name, "%s.svg" % entry["filename"])
                png_path = os.path.join(buffer.name, entry["filename"])
                with open(svg_path, "w", encoding="utf-8") as handle:
                    handle.write(sheet.svg)
                argv = _png_command(
                    binary, name, svg_path, png_path, int(entry["widthPx"]), int(entry["heightPx"])
                )
                completed = subprocess.run(
                    argv,
                    capture_output=True,
                    timeout=PNG_TIMEOUT_SECONDS,
                )
                if completed.returncode != 0 or not os.path.exists(png_path):
                    raise PipelineError(
                        "We couldn't turn %s into an image." % sheet.number,
                        action="Download the PDF instead.",
                        detail="%s exit %d: %s"
                        % (
                            name,
                            completed.returncode,
                            completed.stderr.decode("utf-8", "replace")[:400],
                        ),
                    )
                with open(png_path, "rb") as handle:
                    bundle.writestr(entry["filename"], handle.read())
            bundle.writestr(
                "manifest.json",
                json.dumps(
                    {"sheets": list(manifest), "rasteriser": name}, indent=2, sort_keys=True
                ),
            )
        return (archive.getvalue(), {"images": len(manifest), "rasteriser": name})
    finally:
        shutil.rmtree(buffer.name, ignore_errors=True)


def _png_command(
    binary: str, name: str, svg_path: str, png_path: str, width: int, height: int
) -> list[str]:
    if name == "rsvg-convert":
        return [
            binary,
            "--format=png",
            "--width=%d" % width,
            "--height=%d" % height,
            "--keep-aspect-ratio",
            "--background-color=white",
            "--output=%s" % png_path,
            svg_path,
        ]
    if name in ("chromium", "chromium-browser"):
        return [
            binary,
            "--headless=new",
            "--disable-gpu",
            "--no-sandbox",
            "--disable-extensions",
            "--default-background-color=FFFFFFFF",
            "--window-size=%d,%d" % (width, height),
            "--screenshot=%s" % png_path,
            "file://%s" % svg_path,
        ]
    if name == "inkscape":
        return [
            binary,
            "--export-type=png",
            "--export-width=%d" % width,
            "--export-height=%d" % height,
            "--export-background=white",
            "--export-filename=%s" % png_path,
            svg_path,
        ]
    raise PipelineError(
        "PNG export isn't available on this server yet.",
        action="Download the PDF or DXF instead.",
        detail="no PNG argv recipe for converter %r" % name,
    )


def _looks_like_dxf(data: bytes) -> bool:
    """Cheap content sniff (§13 "type-sniffed").

    Both ASCII and binary DXF start recognisably: ASCII with a ``SECTION`` group early
    in the file, binary with a fixed sentinel. Checking the head only — a hostile file
    should be rejected before anything parses the rest of it.
    """
    if data.startswith(b"AutoCAD Binary DXF"):
        return True
    head = data[:2048].upper()
    return b"SECTION" in head and (b"HEADER" in head or b"ENTITIES" in head or b"CLASSES" in head)


__all__ = [
    "ASSET_AREAS",
    "ASSET_DXF",
    "ASSET_MODEL",
    "ASSET_PREVIOUS_MODEL",
    "DEFAULT_SHEET_FORMATS",
    "EXPORT_KINDS",
    "EXPORT_TIMEOUT_SECONDS",
    "MAX_DXF_BYTES",
    "OUTPUT_EXPORT",
    "OUTPUT_SHEETS",
    "PHASE_IMPORT",
    "PHASE_SHEETS",
    "SHEET_TIMEOUT_SECONDS",
    "DrawingsJobHandler",
]
