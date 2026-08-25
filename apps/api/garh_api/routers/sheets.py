"""Sheet-specific routes and the wiring the sheets job needs (§7, F7-A, D13).

``routers/jobs.py`` owns the §11 job surface — ``POST /projects/:id/sheets/generate``,
``GET /projects/:id/sheets``, ``GET /projects/:id/sheets/:sid.(svg|dxf|pdf)``, and the
export job. Five things sit outside it, and they are what this module is:

1. **Everything the drawings worker needs in its envelope.** The worker holds no
   database connection and never inlines the model in a payload, so the API must
   upload the folded document, attach the frozen area statement, resolve the firm's
   title block and mint one presigned PUT per sheet per format. :func:`build_sheets_job`
   does all of it; ``jobs.generate_sheets`` calls it.
2. **Persisting the result.** :func:`persist_sheet_set` turns the worker's succeeded
   event into ``sheets`` rows through ``SheetRepository.replace_for_version`` — which
   re-anchors annotations by ``(kind, number)`` instead of cascading them away.
3. **Download links that still work tomorrow.** ``sheets.layout.artifacts`` holds
   object *keys*, not presigned URLs (those expire in ≤10 minutes, §13).
   :func:`fresh_sheet_url` re-signs on every read, exactly as
   ``renders.fresh_image_url`` does.
4. **The sheet content endpoint**, which is how the zoomable viewer gets markup it can
   pan and hit-test, with §13's sanitiser run once more on the way out.
5. **The Review Tray** (D13) and the annotation projection underneath it.

Annotations: where writes happen, and where they do not
-------------------------------------------------------
There is no ``POST``/``PATCH``/``DELETE`` for an annotation in this file, and that is
deliberate. Golden rule 1: the op is the atom, and op 32 (``annotation.set``) already
exists in the catalog with ``add | edit | delete`` and an ``orphaned`` field. Every
annotation write therefore goes through ``POST /projects/{id}/ops`` like a wall move —
which is what gives notes undo/redo, version pinning, provenance and copilot
addressability for free. Adding a second write path would give annotations none of
that and would let the folded document and the table disagree.

The ``annotations`` table is a **projection** of ``ProjectDoc.annotations``, kept for
the two things a folded document cannot give cheaply: a foreign key to ``sheets.id``
(the model addresses sheets by slug), and "every orphan in this project" without a
fold. :func:`reconcile_annotations` is the projector, and it is also where §7's
regeneration contract is enforced:

    orphaned  ⟺  anchor_element_id ∉ element ids of the current folded document

Derived, not remembered. A stored flag goes stale the moment an undo brings the
element back; a derivation cannot. It also means a *manual* delete of the anchor
element routes the note to the tray, which is stricter than D13's minimum (which only
promises it for solver re-runs) and is stated in the tray's own copy rather than left
as a surprise. No fuzzy matching, in either direction — ``NO_FUZZY_REANCHOR_POLICY``
is the sentence the UI prints and this module is what makes it true.
"""

from __future__ import annotations

import json
import uuid
from typing import Any, Dict, List, Optional, Sequence, Tuple

import httpx
from fastapi import APIRouter, Query

from garh_api import queue
from garh_api.config import Settings, get_settings
from garh_api.logging import get_logger
from garh_api.repositories import (
    AnnotationRepository,
    ComplianceReportRepository,
    DesignVersionRepository,
    FirmRepository,
    ProjectRepository,
    SheetRepository,
    TenantCtx,
)
from garh_api.routers import (
    ApiError,
    SessionDep,
    TenantDep,
    active_branch,
    require_project,
)
from garh_api.routers.imports import _sigv4_presign
from garh_api.schemas.sheets import (
    NO_FUZZY_REANCHOR_POLICY,
    AnnotationOut,
    DrawingPreferencesIn,
    DrawingPreferencesOut,
    ReviewTrayOut,
    RevisionRow,
    SheetContentOut,
    SheetSetSummaryOut,
    TitleBlockFields,
)
from garh_api.tenancy import EntityNotFoundError

_log = get_logger(__name__)

router = APIRouter(tags=["sheets"])

#: Where the firm's drafting template lives inside ``firms.settings``.
FIRM_SETTINGS_KEY = "drawings"

#: Reserved key inside a projected annotation's ``payload`` holding the op log's own
#: annotation id (``annotation_01J…``). The table's PK is a UUID and the model's is a
#: ULID-shaped string, so the projection has to carry the source id somewhere; the UI
#: reads it back as ``modelAnnotationId`` and uses it as op 32's ``id``.
MODEL_ID_KEY = "__modelId"

#: TTL for the presigned PUTs the worker writes sheets through. Must comfortably
#: outlive queue wait + the §14 five-minute sheet budget.
PUT_URL_TTL_SECONDS = 3600

#: How long the API will wait to read one sheet's SVG back out of storage.
CONTENT_FETCH_TIMEOUT_SECONDS = 15

#: Refuse to inline anything larger than this in :func:`get_sheet_content`. A sheet is
#: 10–30 kB; a megabyte means something is wrong, and buffering it through the event
#: loop to a browser that cannot use it helps nobody.
MAX_INLINE_SVG_BYTES = 4 * 1024 * 1024

#: Sheet formats, mirroring ``services.drawings.handler.DEFAULT_SHEET_FORMATS``.
#: SVG needs nothing installed; DXF needs ezdxf in the worker image. PDF is available
#: per-sheet only when the worker has a converter, so it is not requested by default —
#: the whole-set PDF goes through the export job, which reports its own tool problems.
DEFAULT_SHEET_FORMATS: Tuple[str, ...] = ("svg", "dxf")

#: Mirrors ``services.drawings.pipeline.DB_KIND_ORDER``.
SHEET_KIND_ORDER: Tuple[str, ...] = (
    "site",
    "floor",
    "elevation",
    "section",
    "schedule",
    "area-statement",
)
_ELEVATION_DIRECTIONS: Tuple[str, ...] = ("N", "E", "S", "W")


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------
class SheetsUnavailableError(ApiError):
    http_status = 503
    code = "sheets_unavailable"
    action = "Try generating the set again in a moment."


class SheetContentMissingError(ApiError):
    """The sheet row exists but its drawing does not. A 409, never a 404."""

    http_status = 409
    code = "sheet_not_rendered"
    action = "Generate the drawing set again."


# ---------------------------------------------------------------------------
# Object keys and signed links
# ---------------------------------------------------------------------------
def sheet_object_key(firm_id: Any, design_version_id: Any, slug: str, fmt: str) -> str:
    """Deterministic, firm- and version-scoped key for one sheet artefact.

    Deterministic so a fresh link can be minted from ``(firm, version, slug, format)``
    alone, and version-scoped so regenerating a set for a *new* version cannot
    overwrite the artefacts an older version's sheets still point at — a submission
    already sent to a corporation must keep rendering what was sent.
    """
    return "sheets/%s/%s/%s.%s" % (firm_id, design_version_id, slug, fmt)


def model_object_key(firm_id: Any, job_id: str) -> str:
    return "sheets/%s/jobs/%s/model.json" % (firm_id, job_id)


def areas_object_key(firm_id: Any, job_id: str) -> str:
    return "sheets/%s/jobs/%s/areas.json" % (firm_id, job_id)


def fresh_sheet_url(
    sheet: Any, fmt: str, firm_id: Any, settings: Optional[Settings] = None
) -> Optional[str]:
    """A working link for one sheet artefact, signed now.

    ``layout.artifacts[fmt]`` holds an object key (what the worker reports). A value
    that already looks like a URL — a developer's ``file://`` run, or a worker image
    with no key minted — is passed through untouched, because failing on it would
    break the one mode that needs no object store.
    """
    stored = dict(sheet.layout or {}).get("artifacts")
    if not isinstance(stored, dict):
        return None
    value = stored.get(fmt)
    if not value or not isinstance(value, str):
        return None
    if value.startswith(("http://", "https://", "file://")):
        return value
    cfg = settings or get_settings()
    return _sigv4_presign(
        "GET", value, ttl_seconds=cfg.s3_signed_url_ttl_seconds, settings=cfg
    )


def sheet_formats_available(sheet: Any) -> List[str]:
    stored = dict(sheet.layout or {}).get("artifacts")
    if not isinstance(stored, dict):
        return []
    return [fmt for fmt in ("svg", "dxf", "pdf") if stored.get(fmt)]


# ---------------------------------------------------------------------------
# The sheet plan — a MIRROR of services.drawings.pipeline._sheet_plan
# ---------------------------------------------------------------------------
def sheet_slugs_for(document: Dict[str, Any], kinds: Sequence[str]) -> List[Tuple[str, str]]:
    """``[(slug, kind)]`` the worker will produce for this document.

    **This mirrors ``services.drawings.pipeline._sheet_plan``** and exists for one
    reason: presigned PUTs are per-key, so the API must know the sheet slugs before the
    worker computes them. The API deliberately does not import ``services.*`` (the same
    rule that makes ``garh_api.queue`` a hand-mirror of the worker envelope), so the
    duplication is explicit and tested:
    ``services/drawings/tests/test_pipeline.py::test_the_api_slug_mirror_agrees``
    folds a real model through both and compares.

    A slug the worker produces but the API did not predict costs that sheet its
    download, not the set — :meth:`handler._publish` draws it either way. Predicting a
    slug that never appears costs one unused presigned URL, which costs nothing.
    """
    house = document.get("house") or {}
    storeys = house.get("storeys") or []
    walls = house.get("walls") or []
    with_walls = {str(wall.get("storeyId")) for wall in walls if wall.get("storeyId")}
    wanted = set(kinds)

    plan: List[Tuple[str, str]] = []
    if "site" in wanted:
        plan.append(("site-plan", "site"))
    if "floor" in wanted:
        for storey in storeys:
            storey_id = str(storey.get("id") or "")
            if storey_id and storey_id in with_walls:
                plan.append(("floor-plan-%s" % storey_id, "floor"))
    if "elevation" in wanted:
        plan.extend(("elevation-%s" % d.lower(), "elevation") for d in _ELEVATION_DIRECTIONS)
    if "section" in wanted:
        plan.append(("section-a", "section"))
    if "schedule" in wanted:
        plan.append(("door-window-schedule", "schedule"))
    if "area-statement" in wanted:
        plan.append(("area-statement", "area-statement"))
    return plan


# ---------------------------------------------------------------------------
# Building the job (called by routers/jobs.generate_sheets)
# ---------------------------------------------------------------------------
async def build_sheets_job(
    session: Any,
    ctx: TenantCtx,
    project_id: uuid.UUID,
    design_version_id: uuid.UUID,
    *,
    job_id: str,
    kinds: Optional[Sequence[str]] = None,
    scale_denominator: int = 100,
    sheet_size: str = "A2",
    dim_to_jamb: Optional[bool] = None,
    title_block: Optional[TitleBlockFields] = None,
    revisions: Optional[Sequence[RevisionRow]] = None,
    formats: Optional[Sequence[str]] = None,
) -> Tuple[Dict[str, Any], Dict[str, queue.BlobRef], Dict[str, queue.BlobRef]]:
    """``(payload, assets, outputs)`` for one ``drawings.generate_sheets`` envelope.

    Four things happen here that cannot happen in the worker:

    * **the model is uploaded**, because the envelope must stay small and re-queueable
      (``JobEnvelope`` says the payload never carries a folded model);
    * **the area statement is attached from the frozen compliance report** for this
      version — §7's "same numbers, one source" is satisfied by *transport*, not by
      recomputation. The sheet cannot disagree with the compliance tab because it is
      handed the tab's own ``AreaStatement``. No report → no ``areas`` asset → the
      worker skips that one sheet with a note.
    * **the firm's drafting template is resolved**, so a request that sends no title
      block still prints the firm's letterhead;
    * **one presigned PUT per sheet per format is minted**, because the worker holds no
      storage credentials (§13).
    """
    settings = get_settings()
    resolved_kinds = list(kinds) if kinds else list(SHEET_KIND_ORDER)

    document = await _load_version_document(session, ctx, project_id, design_version_id)
    prefs = await load_drawing_preferences(session, ctx)
    project = await ProjectRepository(session, ctx).require(project_id)

    block = _merge_title_block(prefs.title_block, title_block, project_name=project.name)
    revision_rows = list(revisions) if revisions is not None else list(prefs.revisions)
    chosen_formats = _resolve_formats(formats)
    jamb = prefs.dim_to_jamb if dim_to_jamb is None else bool(dim_to_jamb)

    areas, areas_source = await _area_statement_for_version(
        session, ctx, project_id, design_version_id, document
    )

    payload: Dict[str, Any] = {
        "designVersionId": str(design_version_id),
        "kinds": resolved_kinds,
        "scaleDenominator": int(scale_denominator or prefs.default_scale_denominator),
        "sheetSize": sheet_size or prefs.default_sheet_size,
        "dimToJamb": jamb,
        "numberPrefix": prefs.sheet_number_prefix,
        "titleBlock": block.model_dump(by_alias=True),
        "revisions": [row.model_dump(by_alias=True) for row in revision_rows],
        "formats": list(chosen_formats),
        "areasSource": areas_source,
    }

    assets: Dict[str, queue.BlobRef] = {
        "model": await _upload_json(
            document, model_object_key(ctx.firm_id, job_id), settings, what="design"
        )
    }
    if areas is not None:
        assets["areas"] = await _upload_json(
            areas, areas_object_key(ctx.firm_id, job_id), settings, what="area statement"
        )

    outputs: Dict[str, queue.BlobRef] = {}
    for slug, _kind in sheet_slugs_for(document, resolved_kinds):
        for fmt in chosen_formats:
            key = sheet_object_key(ctx.firm_id, design_version_id, slug, fmt)
            outputs["%s.%s" % (slug, fmt)] = queue.BlobRef(
                put_url=_sigv4_presign(
                    "PUT", key, ttl_seconds=PUT_URL_TTL_SECONDS, settings=settings
                ),
                get_url=_sigv4_presign(
                    "GET", key, ttl_seconds=settings.s3_signed_url_ttl_seconds, settings=settings
                ),
                key=key,
                content_type=_CONTENT_TYPES[fmt],
            )

    _log.info(
        "sheets.job_built",
        project_id=str(project_id),
        design_version_id=str(design_version_id),
        kinds=",".join(resolved_kinds),
        planned_sheets=len(outputs) // max(1, len(chosen_formats)),
        has_area_statement=areas is not None,
    )
    return (payload, assets, outputs)


#: kind → (extension, content type). Mirrors ``services.drawings.export.EXPORTERS``
#: and ``garh_api.routers.jobs._EXPORT_EXTENSIONS``.
EXPORT_ARTEFACTS: Dict[str, Tuple[str, str]] = {
    "pdf-set": ("pdf", "application/pdf"),
    "dxf": ("dxf", "application/dxf"),
    "gltf": ("glb", "model/gltf-binary"),
    "png-pack": ("zip", "application/zip"),
}


def export_object_key(firm_id: Any, job_id: str, kind: str) -> str:
    extension, _ctype = EXPORT_ARTEFACTS[kind]
    return "exports/%s/%s.%s" % (firm_id, job_id, extension)


async def build_export_job(
    session: Any,
    ctx: TenantCtx,
    project_id: uuid.UUID,
    design_version_id: uuid.UUID,
    *,
    job_id: str,
    kind: str,
) -> Tuple[Dict[str, queue.BlobRef], Dict[str, queue.BlobRef]]:
    """``(assets, outputs)`` for one ``drawings.export`` envelope.

    The same two assets a sheets job gets, because an export **re-derives** the sheets
    rather than fetching the nine SVGs a previous job stored. Generation is
    deterministic (asserted in ``test_pipeline.py``), so the two agree byte for byte —
    and the Download button then works on a project whose sheets have never been
    generated, which is what an architect expects it to do.

    ``glTF`` skips the area statement: it is model geometry, not a drawing, and asking
    the rules engine for it would make a 3D download fail on a project with no plot.
    """
    if kind not in EXPORT_ARTEFACTS:
        raise ApiError(
            "We don't recognise that export format.",
            status=422,
            code="unsupported_format",
            action="Pick a format from the download menu.",
        )
    settings = get_settings()
    document = await _load_version_document(session, ctx, project_id, design_version_id)

    assets: Dict[str, queue.BlobRef] = {
        "model": await _upload_json(
            document, model_object_key(ctx.firm_id, job_id), settings, what="design"
        )
    }
    if kind != "gltf":
        areas, _source = await _area_statement_for_version(
            session, ctx, project_id, design_version_id, document
        )
        if areas is not None:
            assets["areas"] = await _upload_json(
                areas, areas_object_key(ctx.firm_id, job_id), settings, what="area statement"
            )

    key = export_object_key(ctx.firm_id, job_id, kind)
    _extension, content_type = EXPORT_ARTEFACTS[kind]
    outputs = {
        "export": queue.BlobRef(
            put_url=_sigv4_presign(
                "PUT", key, ttl_seconds=PUT_URL_TTL_SECONDS, settings=settings
            ),
            get_url=_sigv4_presign(
                "GET", key, ttl_seconds=settings.s3_signed_url_ttl_seconds, settings=settings
            ),
            key=key,
            content_type=content_type,
        )
    }
    return (assets, outputs)


_CONTENT_TYPES: Dict[str, str] = {
    # Never image/svg+xml — §13 forbids serving a drawing as a type a browser executes
    # scripts inside, and routers/jobs.py makes the same call for downloads.
    "svg": "application/octet-stream",
    "dxf": "application/dxf",
    "pdf": "application/pdf",
}


def _resolve_formats(formats: Optional[Sequence[str]]) -> Tuple[str, ...]:
    if not formats:
        return DEFAULT_SHEET_FORMATS
    chosen: List[str] = []
    for item in formats:
        key = str(item).lower()
        if key not in _CONTENT_TYPES:
            raise ApiError(
                "We can't publish a sheet as %r." % item,
                status=422,
                code="unsupported_format",
                action="Ask for svg, dxf or pdf.",
            )
        if key not in chosen:
            chosen.append(key)
    if "svg" not in chosen:
        chosen.insert(0, "svg")
    return tuple(chosen)


def _merge_title_block(
    firm_block: TitleBlockFields,
    override: Optional[TitleBlockFields],
    *,
    project_name: str,
) -> TitleBlockFields:
    """Firm template, then the request's overrides, then the project's own name.

    The project name is a fallback, not a default: an architect who typed a different
    project title into the editor means it, and this must not overwrite it.
    """
    merged = override or firm_block
    if not merged.project_name:
        merged = merged.model_copy(update={"project_name": project_name})
    if not merged.firm_name and firm_block.firm_name:
        merged = merged.model_copy(update={"firm_name": firm_block.firm_name})
    if not merged.logo_url and firm_block.logo_url:
        merged = merged.model_copy(update={"logo_url": firm_block.logo_url})
    return merged


async def _load_version_document(
    session: Any, ctx: TenantCtx, project_id: uuid.UUID, design_version_id: uuid.UUID
) -> Dict[str, Any]:
    """The folded document for a version: its snapshot, or a fold up to its head.

    A version whose snapshot was pruned is not a dead end — ``load_project_state``
    re-folds from the newest surviving anchor, which is the same path
    ``GET /compliance`` takes.
    """
    from garh_api.routers.ops import load_project_state, unwrap_snapshot

    version = await DesignVersionRepository(session, ctx).require(design_version_id)
    if version.project_id != project_id:
        raise EntityNotFoundError("design_version", design_version_id)
    if version.snapshot is not None:
        unwrapped = unwrap_snapshot(version.snapshot)
        if unwrapped is not None:
            return dict(unwrapped.document)

    branch = version.version_branch
    state = await load_project_state(
        session, ctx, project_id, branch, upto_idx=version.op_seq_end
    )
    return dict(state.document)


async def _area_statement_for_version(
    session: Any,
    ctx: TenantCtx,
    project_id: uuid.UUID,
    design_version_id: uuid.UUID,
    document: Dict[str, Any],
) -> Tuple[Optional[Dict[str, Any]], str]:
    """``(AreaStatement.to_json(), source)`` for this version. §7's one source.

    "One source" means one **implementation**, not one cached row.
    ``compliance_reports`` stores ``results`` and ``pack_versions`` only — the
    ``areas`` block of an evaluation is not persisted — so the statement is produced
    by calling ``garh_api.compliance.evaluate_document``: the same function
    ``GET /compliance`` and ``freeze_compliance_report`` call, over the same folded
    document, with the same packs. Evaluation is pure and deterministic (§6), so the
    numbers on the sheet are the numbers on the compliance tab by construction.

    And it is checked, not assumed: when a frozen report exists for this version, the
    per-rule statuses of the fresh evaluation are compared against it and any
    divergence is logged at ``error`` with the rule ids. That turns "they agree" from
    a claim in a docstring into an alertable signal.

    ``(None, reason)`` when the design cannot be evaluated at all (no plot, no
    storeys). The worker then omits the area-statement sheet with a note, which is the
    honest outcome — the alternative is a drawings package that grows its own FAR
    arithmetic, and then two answers to a regulatory question.
    """
    from garh_api.compliance import ComplianceUnavailable, evaluate_document

    project = await ProjectRepository(session, ctx).require(project_id)
    try:
        payload, _packs = evaluate_document(document, city_pack=project.city_pack)
    except ComplianceUnavailable as exc:
        _log.info(
            "sheets.areas_unavailable",
            project_id=str(project_id),
            design_version_id=str(design_version_id),
            reason=str(exc),
        )
        return (None, "unavailable: %s" % exc)

    areas = payload.get("areas")
    if not isinstance(areas, dict) or not areas.get("rows"):
        _log.warning("sheets.evaluation_has_no_areas", design_version_id=str(design_version_id))
        return (None, "evaluation produced no area statement")

    await _assert_matches_frozen_report(session, ctx, project_id, design_version_id, payload)
    return (areas, "rules-engine")


async def _assert_matches_frozen_report(
    session: Any,
    ctx: TenantCtx,
    project_id: uuid.UUID,
    design_version_id: uuid.UUID,
    fresh: Dict[str, Any],
) -> None:
    """Log loudly if a fresh evaluation disagrees with the version's frozen report.

    Not fatal. A rule pack legitimately gets a new version between the freeze and the
    sheet, and refusing to draw would be the wrong response to that. But a silent
    disagreement between the compliance tab and the sheet is exactly the liability
    this product sells against, so it is an ``error``-level line naming the rules.
    """
    report = await ComplianceReportRepository(session, ctx).latest_for_version(
        project_id, design_version_id
    )
    if report is None:
        return
    frozen = {
        str(r.get("ruleId")): str(r.get("status"))
        for r in report.results
        if isinstance(r, dict) and r.get("ruleId")
    }
    now = {
        str(r.get("ruleId")): str(r.get("status"))
        for r in (fresh.get("results") or ())
        if isinstance(r, dict) and r.get("ruleId")
    }
    changed = sorted(
        rule_id for rule_id, status in now.items() if rule_id in frozen and frozen[rule_id] != status
    )
    if changed:
        _log.error(
            "sheets.compliance_drift",
            project_id=str(project_id),
            design_version_id=str(design_version_id),
            rules=",".join(changed[:20]),
            count=len(changed),
            frozen_pack_versions=json.dumps(report.pack_versions, sort_keys=True),
        )


async def _upload_json(
    payload: Dict[str, Any], key: str, settings: Settings, *, what: str
) -> queue.BlobRef:
    """Store one JSON asset and hand back a ref the worker can read.

    Uploaded from the request rather than inlined in the envelope: a folded G+2 is
    hundreds of kilobytes, and an envelope carrying it would be re-queued in full on
    every retry. Failure is a 503 with a retry, not a job that starts and then cannot
    find its input.
    """
    body = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    put_url = _sigv4_presign("PUT", key, ttl_seconds=PUT_URL_TTL_SECONDS, settings=settings)
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(30.0)) as client:
            response = await client.put(
                put_url, content=body, headers={"content-type": "application/json"}
            )
    except httpx.HTTPError as exc:
        raise SheetsUnavailableError(
            "We couldn't hand your design to the drawing service just now."
        ) from exc
    if response.status_code >= 400:
        _log.error(
            "sheets.asset_upload_failed",
            asset=what,
            status_code=response.status_code,
            key=key,
        )
        raise SheetsUnavailableError(
            "We couldn't hand your design to the drawing service just now."
        )
    return queue.BlobRef(
        get_url=_sigv4_presign(
            "GET", key, ttl_seconds=PUT_URL_TTL_SECONDS, settings=settings
        ),
        key=key,
        content_type="application/json",
        size_bytes=len(body),
    )


# ---------------------------------------------------------------------------
# Persisting the worker's result (called by the jobs lifecycle consumer)
# ---------------------------------------------------------------------------
async def persist_sheet_set(
    session: Any,
    firm_id: uuid.UUID,
    project_id: uuid.UUID,
    design_version_id: uuid.UUID,
    data: Dict[str, Any],
) -> int:
    """Turn a ``drawings.generate_sheets`` succeeded event into ``sheets`` rows.

    Runs under ``TenantCtx.for_system(firm_id)`` — the firm comes from the envelope the
    API itself minted, so the write is scoped exactly as a user's would be, and the
    repository is what enforces it (the worker never holds a context at all).

    ``replace_for_version`` is what makes regeneration safe: it re-anchors annotations
    onto the new row with the same ``(kind, number)`` and flags the rest orphaned,
    rather than letting a CASCADE delete an architect's notes. Immediately afterwards
    the projection is reconciled against the folded model, so the tray is populated
    before anyone opens it.
    """
    sheets = data.get("sheets")
    if not isinstance(sheets, list) or not sheets:
        _log.warning(
            "sheets.persist_skipped",
            reason="event carried no sheets",
            project_id=str(project_id),
        )
        return 0

    ctx = TenantCtx.for_system(firm_id)
    specs: List[Dict[str, Any]] = []
    for layout in sheets:
        if not isinstance(layout, dict):
            continue
        kind = str(layout.get("kind") or "")
        if kind not in SHEET_KIND_ORDER:
            _log.warning("sheets.unknown_kind_from_worker", sheet_kind=kind)
            continue
        stored = dict(layout)
        # Set-level facts belong on every row so a single sheet read can answer
        # "does this set's dimensions add up?" without loading the job record.
        stored["setNotes"] = list(data.get("notes") or [])
        stored["setSkipped"] = list(data.get("skipped") or [])
        stored["labelCollisions"] = int(data.get("labelCollisions") or 0)
        stored["modelStateHash"] = data.get("stateHash")
        specs.append(
            {"kind": kind, "number": str(layout.get("number") or "") or None, "layout": stored}
        )

    rows = await SheetRepository(session, ctx).replace_for_version(
        project_id, design_version_id, specs
    )
    try:
        await reconcile_annotations(session, ctx, project_id)
    except Exception as exc:  # noqa: BLE001 - sheets landing must not depend on this
        _log.error(
            "sheets.reconcile_after_generate_failed",
            project_id=str(project_id),
            error="%s: %s" % (type(exc).__name__, exc),
        )
    _log.info(
        "sheets.persisted",
        project_id=str(project_id),
        design_version_id=str(design_version_id),
        count=len(rows),
    )
    return len(rows)


# ---------------------------------------------------------------------------
# Annotation projection + the §7 / D13 regeneration contract
# ---------------------------------------------------------------------------
def document_element_ids(document: Dict[str, Any]) -> set:
    """Every addressable element id in a folded document.

    This set *is* the orphan test. It covers everything op 32 can anchor to: walls,
    openings, rooms, stairs, columns, balconies, slabs, storeys, furniture, facade
    components and plot edges.
    """
    ids: set = set()
    house = document.get("house") or {}
    for collection in (
        "walls",
        "openings",
        "rooms",
        "stairs",
        "columns",
        "balconies",
        "slabs",
        "storeys",
        "furniture",
        "facadeComponents",
    ):
        for item in house.get(collection) or ():
            if isinstance(item, dict) and item.get("id"):
                ids.add(str(item["id"]))
    plot = document.get("plot") or {}
    for edge in plot.get("edges") or ():
        if isinstance(edge, dict) and edge.get("id"):
            ids.add(str(edge["id"]))
    return ids


async def reconcile_annotations(
    session: Any,
    ctx: TenantCtx,
    project_id: uuid.UUID,
    document: Optional[Dict[str, Any]] = None,
) -> Dict[str, int]:
    """Project ``ProjectDoc.annotations`` onto the ``annotations`` table.

    Returns ``{"attached": n, "orphaned": n, "created": n, "removed": n}``.

    Called after a sheet set lands, after a solver option is applied
    (``solver_apply.snapshot_after_solver_apply``), and whenever the Review Tray is
    read. Idempotent: running it twice changes nothing, which is what lets it be
    called from a lifecycle consumer that may redeliver.

    Not called on every op append. Reconciling needs a fold, and §14 budgets 60 ops/s
    per firm; the tray read is the screen that needs truth, and it pays for it there.
    """
    if document is None:
        from garh_api.routers.ops import load_project_state

        branch = await active_branch(session, ctx, project_id)
        document = (await load_project_state(session, ctx, project_id, branch)).document

    live_ids = document_element_ids(document)
    model_annotations = [
        item for item in (document.get("annotations") or ()) if isinstance(item, dict)
    ]

    sheet_repo = SheetRepository(session, ctx)
    annotation_repo = AnnotationRepository(session, ctx)

    # Sheets of the newest generated set, indexed by slug — the model addresses sheets
    # by slug, the table by uuid, and this is the only place the two meet.
    sheets = await _all_project_sheets(session, ctx, project_id)
    by_slug: Dict[str, Any] = {}
    for sheet in sheets:
        slug = dict(sheet.layout or {}).get("sheetId")
        if slug:
            by_slug.setdefault(str(slug), sheet)

    # The table's primary key is a UUID; the model's annotation id is a ULID-shaped
    # string minted by the op log (`annotation_01J…`). They cannot be the same value,
    # so every projected row carries its source id under MODEL_ID_KEY. That is what
    # lets this function be an upsert instead of a delete-and-recreate — recreating
    # would churn ids the UI has in flight and lose `created_at` ordering in the tray.
    existing: Dict[str, Any] = {}
    for sheet in sheets:
        for row in await annotation_repo.list_for_sheet(sheet.id):
            model_id = dict(row.payload or {}).get(MODEL_ID_KEY)
            if model_id:
                existing[str(model_id)] = row

    stats = {"attached": 0, "orphaned": 0, "created": 0, "removed": 0}
    model_ids = {str(item.get("id")) for item in model_annotations if item.get("id")}

    for item in model_annotations:
        annotation_id = str(item.get("id") or "")
        if not annotation_id:
            continue
        sheet = by_slug.get(str(item.get("sheetId") or ""))
        if sheet is None:
            # The note names a sheet this project has not generated yet, or one whose
            # slug changed. If a row already exists it becomes an orphan (the tray is
            # the right place for "we cannot place this note"); if none exists there is
            # nothing to project, and it appears the moment that sheet is generated
            # because the model still holds it.
            stale = existing.get(annotation_id)
            if stale is not None and not stale.orphaned:
                await annotation_repo.mark_orphaned(
                    [str(stale.anchor_element_id or "")], [stale.sheet_id]
                )
                stats["orphaned"] += 1
            elif stale is not None:
                stats["orphaned"] += 1
            continue
        anchor = str(item.get("anchorElementId") or "")
        anchor_kind = _anchor_kind(item.get("anchorKind"))
        # A sheet-anchored note ("general notes" block) has no element to lose, so it
        # can never be orphaned. Everything else is orphaned exactly when its anchor id
        # is absent from the current folded document — the id-matching rule of §7,
        # derived on every reconcile rather than remembered.
        is_orphan = anchor_kind != "sheet" and (not anchor or anchor not in live_ids)
        stats["orphaned" if is_orphan else "attached"] += 1

        payload = dict(item.get("payload") or {})
        payload[MODEL_ID_KEY] = annotation_id
        row = existing.get(annotation_id)
        if row is None:
            row = await annotation_repo.create(
                sheet.id,
                anchor_kind=anchor_kind if anchor else "sheet",
                anchor_element_id=anchor or None,
                payload=payload,
            )
            stats["created"] += 1
        elif dict(row.payload or {}) != payload:
            row = await annotation_repo.update_payload(row.id, payload)

        if bool(row.orphaned) != is_orphan:
            if is_orphan:
                await annotation_repo.mark_orphaned([anchor], [sheet.id])
            else:
                await annotation_repo.reattach(row.id, anchor_element_id=anchor)

    # Rows whose annotation the op log no longer has. The model is authoritative: an
    # `annotation.set action=delete` op removed it, so the projection follows. Note the
    # test is against the model's id set, NOT against "did this loop see it" — an
    # annotation whose sheet is momentarily unresolvable is orphaned above, not deleted.
    for annotation_id, row in existing.items():
        if annotation_id not in model_ids:
            await annotation_repo.delete(row.id)
            stats["removed"] += 1

    if any(stats.values()):
        _log.info("annotations.reconciled", project_id=str(project_id), **stats)
    return stats


def _anchor_kind(raw: Any) -> str:
    from garh_api import models

    value = str(raw or "element")
    return value if value in models.ANNOTATION_ANCHOR_KINDS else "element"


async def _all_project_sheets(session: Any, ctx: TenantCtx, project_id: uuid.UUID) -> List[Any]:
    """Every sheet of a project's newest generated version, newest first."""
    repo = SheetRepository(session, ctx)
    version_id = await _latest_sheet_version(session, ctx, project_id)
    if version_id is None:
        return []
    return await repo.list_for_version(project_id, version_id)


async def _latest_sheet_version(
    session: Any, ctx: TenantCtx, project_id: uuid.UUID
) -> Optional[uuid.UUID]:
    branch = await active_branch(session, ctx, project_id)
    latest = await DesignVersionRepository(session, ctx).latest(project_id, branch)
    return latest.id if latest is not None else None


# ---------------------------------------------------------------------------
# Routes: firm drawing preferences (§7 title-block editor)
# ---------------------------------------------------------------------------
async def load_drawing_preferences(session: Any, ctx: TenantCtx) -> DrawingPreferencesOut:
    firm = await FirmRepository(session, ctx).get_current()
    stored = firm.settings.get(FIRM_SETTINGS_KEY)
    if not isinstance(stored, dict):
        # No saved template: fall back to the firm's own name and logo, which are the
        # only two things we can honestly pre-fill.
        return DrawingPreferencesOut(
            title_block=TitleBlockFields(firm_name=firm.name, logo_url=firm.logo_url),
            source="defaults",
            firm_logo_url=firm.logo_url,
        )
    block_raw = dict(stored.get("titleBlock") or {})
    block_raw.setdefault("firmName", firm.name)
    if firm.logo_url and not block_raw.get("logoUrl"):
        block_raw["logoUrl"] = firm.logo_url
    return DrawingPreferencesOut(
        title_block=TitleBlockFields.model_validate(block_raw),
        dim_to_jamb=bool(stored.get("dimToJamb")),
        sheet_number_prefix=str(stored.get("sheetNumberPrefix") or "A"),
        default_scale_denominator=int(stored.get("defaultScaleDenominator") or 100),
        default_sheet_size=str(stored.get("defaultSheetSize") or "A2"),
        revisions=[RevisionRow.model_validate(row) for row in stored.get("revisions") or ()],
        source="firm",
        firm_logo_url=firm.logo_url,
    )


@router.get(
    "/firm/drawing-preferences",
    response_model=DrawingPreferencesOut,
    summary="The firm's title-block template and drafting conventions",
)
async def get_drawing_preferences(
    session: SessionDep, ctx: TenantDep
) -> DrawingPreferencesOut:
    return await load_drawing_preferences(session, ctx)


@router.put(
    "/firm/drawing-preferences",
    response_model=DrawingPreferencesOut,
    summary="Save the firm's title-block template",
)
async def put_drawing_preferences(
    body: DrawingPreferencesIn, session: SessionDep, ctx: TenantDep
) -> DrawingPreferencesOut:
    """Merged into ``firms.settings``, so an unrelated setting is never clobbered.

    The logo also lands on ``firms.logo_url``: it is firm identity, not a drawings
    preference, and the dashboard reads it from there.
    """
    ctx.require_write("saving drawing preferences")
    repo = FirmRepository(session, ctx)
    await repo.merge_settings(
        {
            FIRM_SETTINGS_KEY: {
                "titleBlock": body.title_block.model_dump(by_alias=True),
                "dimToJamb": body.dim_to_jamb,
                "sheetNumberPrefix": body.sheet_number_prefix,
                "defaultScaleDenominator": body.default_scale_denominator,
                "defaultSheetSize": body.default_sheet_size,
                "revisions": [row.model_dump(by_alias=True) for row in body.revisions],
            }
        }
    )
    if body.title_block.logo_url:
        await repo.set_logo_url(body.title_block.logo_url)
    _log.info("sheets.preferences_saved", dim_to_jamb=body.dim_to_jamb)
    return await load_drawing_preferences(session, ctx)


# ---------------------------------------------------------------------------
# Routes: sheet content (the viewer)
# ---------------------------------------------------------------------------
@router.get(
    "/projects/{project_id}/sheets/{sheet_id}/content",
    response_model=SheetContentOut,
    summary="One sheet's sanitised SVG, inline for the viewer",
)
async def get_sheet_content(
    project_id: uuid.UUID,
    sheet_id: uuid.UUID,
    session: SessionDep,
    ctx: TenantDep,
) -> SheetContentOut:
    """Fetch the stored SVG and re-check it before a browser parses it.

    A *download* goes through a signed URL (§11) because the browser should stream a
    possibly-large PDF itself. A *viewer* is the opposite case: it needs the markup in
    the document to zoom and hit-test, the payload is tens of kilobytes, and pointing
    an ``<img>`` at object storage would require CORS on the bucket and would skip the
    §13 re-check. So this one proxies, deliberately and with a size cap.
    """
    await require_project(session, ctx, project_id)
    ctx.require_scope("sheets")
    sheet = await SheetRepository(session, ctx).require(sheet_id)
    if sheet.project_id != project_id:
        raise EntityNotFoundError("sheet", sheet_id)

    layout = dict(sheet.layout or {})
    url = fresh_sheet_url(sheet, "svg", ctx.firm_id)
    if not url:
        raise SheetContentMissingError("That sheet hasn't been drawn yet.")

    svg = await _fetch_svg(url, sheet_id)
    paper = _paper_size_mm(str(layout.get("paper") or "A2"))
    return SheetContentOut(
        sheet_id=sheet.id,
        slug=layout.get("sheetId"),
        number=sheet.number,
        title=layout.get("title"),
        kind=sheet.kind,
        scale_denominator=layout.get("scaleDenominator"),
        paper=layout.get("paper"),
        width_mm=paper[0],
        height_mm=paper[1],
        svg=svg,
        bytes=len(svg.encode("utf-8")),
        generated_at=sheet.generated_at,
    )


async def _fetch_svg(url: str, sheet_id: uuid.UUID) -> str:
    if url.startswith("file://"):
        # Developer/golden mode: the worker wrote to a local path.
        from pathlib import Path
        from urllib.parse import urlparse

        try:
            return Path(urlparse(url).path).read_text(encoding="utf-8")
        except OSError as exc:
            raise SheetContentMissingError("That sheet's drawing is not readable.") from exc
    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(float(CONTENT_FETCH_TIMEOUT_SECONDS)),
            follow_redirects=False,
        ) as client:
            response = await client.get(url)
    except httpx.HTTPError as exc:
        raise SheetsUnavailableError("We couldn't load that sheet just now.") from exc
    if response.status_code >= 400:
        _log.error(
            "sheets.content_fetch_failed",
            sheet_id=str(sheet_id),
            status_code=response.status_code,
        )
        raise SheetContentMissingError("That sheet's drawing is no longer in storage.")
    if len(response.content) > MAX_INLINE_SVG_BYTES:
        raise ApiError(
            "That sheet is too large to open in the browser.",
            status=413,
            code="sheet_too_large",
            action="Download it as a PDF or DXF instead.",
        )
    svg = response.text
    _assert_sanitary(svg, sheet_id)
    return svg


def _assert_sanitary(svg: str, sheet_id: uuid.UUID) -> None:
    """§13's SVG rule, enforced at the boundary the browser actually reads.

    The worker sanitises before storing, and this checks again on the way out. The
    duplication is the point: between those two moments the bytes sat in an object
    store, and "the producer promised" is not a security control. Mirrors the
    element/attribute allowlist in ``services/drawings/render/sanitize.py`` — the API
    must not import ``services.*``, so the *dangerous* half is checked here rather
    than the whole grammar, and the worker-side allowlist remains authoritative.
    """
    lowered = svg.lower()
    for token in (
        "<script",
        "<foreignobject",
        "<iframe",
        "<use",
        "javascript:",
        "onload=",
        "onerror=",
        "onclick=",
        "<!entity",
        "<!doctype",
    ):
        if token in lowered:
            _log.error("sheets.unsafe_svg_blocked", sheet_id=str(sheet_id), token=token)
            raise ApiError(
                "We couldn't open that sheet safely.",
                status=502,
                code="unsafe_sheet_content",
                action="Generate the drawing set again.",
            )


def _paper_size_mm(name: str) -> Tuple[Optional[int], Optional[int]]:
    """Landscape paper sizes, mirroring ``services.drawings.sheets.PAPER_SIZES``."""
    sizes = {
        "A0": (1189, 841),
        "A1": (841, 594),
        "A2": (594, 420),
        "A3": (420, 297),
        "A4": (297, 210),
    }
    return sizes.get(name.upper(), (None, None))


# ---------------------------------------------------------------------------
# Routes: the Review Tray (§7, D13)
# ---------------------------------------------------------------------------
@router.get(
    "/projects/{project_id}/sheets/review-tray",
    response_model=ReviewTrayOut,
    summary="Annotations whose anchor did not survive (D13 review tray)",
)
async def get_review_tray(
    project_id: uuid.UUID,
    session: SessionDep,
    ctx: TenantDep,
    reconcile: bool = Query(
        default=True,
        description="Re-check every anchor against the current model before answering.",
    ),
) -> ReviewTrayOut:
    """The list, with a re-attach picker's candidates already attached to each orphan.

    ``reconcile=true`` (the default) folds the current model and re-derives every
    ``orphaned`` flag before answering, so the tray is never stale. ``reconcile=false``
    is for a poll: it reads the projection as last written, and the response says which
    it did, because "3 notes need attention" and "3 notes needed attention when we last
    looked" are different claims.
    """
    await require_project(session, ctx, project_id)
    ctx.require_scope("sheets")

    reconciled = False
    if reconcile and ctx.can_write:
        try:
            await reconcile_annotations(session, ctx, project_id)
            reconciled = True
        except Exception as exc:  # noqa: BLE001 - a stale tray beats a 500
            _log.warning(
                "annotations.reconcile_failed",
                project_id=str(project_id),
                error="%s: %s" % (type(exc).__name__, exc),
            )

    sheets = await _all_project_sheets(session, ctx, project_id)
    by_id = {sheet.id: sheet for sheet in sheets}
    candidates_by_sheet = {
        sheet.id: [str(v) for v in (dict(sheet.layout or {}).get("elementIds") or ())]
        for sheet in sheets
    }

    annotation_repo = AnnotationRepository(session, ctx)
    orphans = await annotation_repo.list_orphaned_for_project(project_id)
    attached = 0
    for sheet in sheets:
        attached += len(await annotation_repo.list_for_sheet(sheet.id, include_orphaned=False))

    version_id = await _latest_sheet_version(session, ctx, project_id)
    return ReviewTrayOut(
        project_id=project_id,
        design_version_id=version_id,
        orphaned=[
            AnnotationOut.of(
                annotation,
                sheet=by_id.get(annotation.sheet_id),
                reattach_candidates=candidates_by_sheet.get(annotation.sheet_id, []),
            )
            for annotation in orphans
        ],
        attached_count=attached,
        policy=NO_FUZZY_REANCHOR_POLICY,
        reconciled=reconciled,
    )


@router.get(
    "/projects/{project_id}/sheets/{sheet_id}/annotations",
    response_model=List[AnnotationOut],
    summary="Annotations on one sheet",
)
async def list_sheet_annotations(
    project_id: uuid.UUID,
    sheet_id: uuid.UUID,
    session: SessionDep,
    ctx: TenantDep,
) -> List[AnnotationOut]:
    """Read-only. Writes are op 32 through ``POST /projects/{id}/ops`` — see the
    module docstring for why there is no write route here."""
    await require_project(session, ctx, project_id)
    ctx.require_scope("sheets")
    sheet = await SheetRepository(session, ctx).require(sheet_id)
    if sheet.project_id != project_id:
        raise EntityNotFoundError("sheet", sheet_id)
    candidates = [str(v) for v in (dict(sheet.layout or {}).get("elementIds") or ())]
    rows = await AnnotationRepository(session, ctx).list_for_sheet(sheet_id)
    return [
        AnnotationOut.of(
            row, sheet=sheet, reattach_candidates=candidates if row.orphaned else []
        )
        for row in rows
    ]


@router.get(
    "/projects/{project_id}/sheets/summary",
    response_model=SheetSetSummaryOut,
    summary="Set-level facts: chain count, the sum invariant, skipped sheets, notes",
)
async def get_sheet_set_summary(
    project_id: uuid.UUID,
    session: SessionDep,
    ctx: TenantDep,
    version: Optional[uuid.UUID] = Query(default=None),
) -> SheetSetSummaryOut:
    """What the Sheets tab shows above the thumbnails.

    ``chainSumOk`` re-checks §7's step-5 invariant from the persisted chains. The
    worker already asserted it before any sheet existed, so it is expected to be true —
    which is exactly why it is worth surfacing. It is the product's central claim about
    its own drawings, and a ``false`` here is something an architect must see before
    they print.
    """
    await require_project(session, ctx, project_id)
    ctx.require_scope("sheets")
    version_id = version or await _latest_sheet_version(session, ctx, project_id)
    if version_id is None:
        return SheetSetSummaryOut(project_id=project_id)
    sheets = await SheetRepository(session, ctx).list_for_version(project_id, version_id)
    if not sheets:
        return SheetSetSummaryOut(project_id=project_id, design_version_id=version_id)

    chain_count = 0
    chain_sum_ok = True
    formats: set = set()
    for sheet in sheets:
        layout = dict(sheet.layout or {})
        for chain in layout.get("chains") or ():
            if not isinstance(chain, dict):
                continue
            chain_count += 1
            segments = chain.get("segments") or ()
            total = sum(int(seg.get("lengthMm") or 0) for seg in segments if isinstance(seg, dict))
            if total != int(chain.get("overallMm") or -1):
                chain_sum_ok = False
                _log.error(
                    "sheets.chain_sum_violation",
                    sheet_id=str(sheet.id),
                    chain_id=str(chain.get("id")),
                    segments_mm=total,
                    overall_mm=chain.get("overallMm"),
                )
        formats.update(sheet_formats_available(sheet))

    first = dict(sheets[0].layout or {})
    generated = [s.generated_at for s in sheets if s.generated_at is not None]
    return SheetSetSummaryOut(
        project_id=project_id,
        design_version_id=version_id,
        sheet_count=len(sheets),
        chain_count=chain_count,
        chain_sum_ok=chain_sum_ok,
        label_collisions=int(first.get("labelCollisions") or 0),
        skipped=list(first.get("setSkipped") or []),
        notes=[str(note) for note in first.get("setNotes") or ()],
        formats_available=sorted(formats),
        generated_at=max(generated) if generated else None,
    )


__all__ = [
    "DEFAULT_SHEET_FORMATS",
    "EXPORT_ARTEFACTS",
    "MODEL_ID_KEY",
    "FIRM_SETTINGS_KEY",
    "SHEET_KIND_ORDER",
    "build_export_job",
    "build_sheets_job",
    "document_element_ids",
    "fresh_sheet_url",
    "load_drawing_preferences",
    "persist_sheet_set",
    "reconcile_annotations",
    "router",
    "sheet_formats_available",
    "export_object_key",
    "sheet_object_key",
    "sheet_slugs_for",
]
