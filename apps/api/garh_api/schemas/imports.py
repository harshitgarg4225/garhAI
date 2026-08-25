"""Schemas for DXF boundary import (Phase 2, playbook F1 + §13 upload rules).

The upload request itself is a file body (multipart or raw ``application/dxf``), so
there is no request model here — the router enforces the §13 limits on the raw bytes.
These are the response shapes: the import *job* and, once the drawings worker has
parsed the file, its *result* — layers of closed-boundary candidates in integer
millimetres, already normalised (CCW, plot-local origin) so the client can turn the
chosen ring straight into a ``plot.set_boundary`` op with ``source: "dxf"``.

The wire shape of ``result`` is byte-identical to what the worker publishes in its
``succeeded`` event (see ``services/drawings/dxf_import.py`` — that docstring is the
contract); these models exist to document it in OpenAPI and to guarantee the API
never re-shapes it in passing.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Optional

from pydantic import Field, StrictBool, StrictInt, StrictStr

from garh_api.schemas import PointMm, ResponseModel

#: The ``kind`` stored on the Redis job record. Distinguishes import jobs from the
#: export/sheets jobs that share the same record machinery (see ``garh_api.queue``).
DXF_IMPORT_JOB_KIND = "dxf-import"


class DxfPolylineOut(ResponseModel):
    """One closed-boundary candidate: a CCW integer-mm ring in plot-local coordinates.

    ``points`` does not repeat the first vertex; the ring's bounding-box minimum is at
    the origin. Exactly the polygon ``plot.set_boundary`` accepts.
    """

    points: list[PointMm]
    closed_area: StrictInt = Field(
        ge=0, description="Enclosed area in mm² (shoelace on the integer-mm ring)."
    )


class DxfLayerOut(ResponseModel):
    """One DXF layer as the picker shows it. Empty ``polylines`` is meaningful — the
    layer exists but holds nothing closed, which teaches more than omitting it."""

    name: StrictStr
    polylines: list[DxfPolylineOut] = Field(default_factory=list)


class DxfUnitsOut(ResponseModel):
    """How drawing units were mapped to millimetres (the documented rule).

    ``assumed`` is True when ``$INSUNITS`` was 0/unknown and millimetres were assumed;
    the client renders that as an editable assumption chip (golden rule 4).
    ``mmPerUnit`` is a decimal string ("25.4"), never a float — no float crosses the
    geometry boundary, even as metadata.
    """

    insunits: StrictInt
    mm_per_unit: StrictStr
    assumed: StrictBool


class DxfImportResultOut(ResponseModel):
    """The parsed drawing: what the layer picker renders."""

    layers: list[DxfLayerOut] = Field(default_factory=list)
    units: Optional[DxfUnitsOut] = None
    skipped: dict[str, StrictInt] = Field(
        default_factory=dict,
        description="Entities dropped and why (openPolylines, overVertexCap, "
        "degenerate, unsupported, polylinesOverCap, layersOverCap).",
    )


class DxfImportJobOut(ResponseModel):
    """An import job. Lives in Redis like export jobs (same lifecycle machinery);
    ``result`` appears once the worker has succeeded and stays until the record's
    24h TTL. A succeeded job whose ``result`` is null outlived its result window —
    the client's next action is to upload the file again."""

    id: StrictStr
    project_id: uuid.UUID
    kind: StrictStr = DXF_IMPORT_JOB_KIND
    status: StrictStr
    progress: StrictInt = 0
    filename: Optional[StrictStr] = None
    size_bytes: Optional[StrictInt] = None
    error: Optional[StrictStr] = None
    events_url: Optional[StrictStr] = None
    result: Optional[DxfImportResultOut] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    @classmethod
    def of(
        cls,
        record: Any,
        *,
        events_url: Optional[str] = None,
        result: Optional[dict[str, Any]] = None,
    ) -> "DxfImportJobOut":
        params = dict(record.params or {})
        size = params.get("sizeBytes")
        return cls(
            id=str(record.id),
            project_id=uuid.UUID(str(record.project_id)),
            kind=record.kind,
            status=record.status,
            progress=record.progress,
            filename=params.get("filename"),
            size_bytes=int(size) if isinstance(size, int) else None,
            error=record.error,
            events_url=events_url,
            result=DxfImportResultOut.model_validate(result) if isinstance(result, dict) else None,
            created_at=_parse_iso(record.created_at),
            updated_at=_parse_iso(record.updated_at),
        )


def _parse_iso(value: Any) -> Optional[datetime]:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


__all__ = [
    "DXF_IMPORT_JOB_KIND",
    "DxfImportJobOut",
    "DxfImportResultOut",
    "DxfLayerOut",
    "DxfPolylineOut",
    "DxfUnitsOut",
]
