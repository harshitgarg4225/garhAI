"""Schemas for the tracing underlay (the "import a plan and trace over it" aid).

The record shape is frozen by the task design: ``objectKey``, ``imageUrl`` (a
presigned GET minted per response, §13 ≤10 min), the parsed pixel dimensions,
the calibration (``mmPerPx`` float + integer-mm origin), and the three view
flags. ``mmPerPx`` is the one float on this surface, documented in
``models.ProjectUnderlay`` — it is a raster display scale, not a length, so the
StrictInt-mm rule does not apply to it. Everything positional stays ``Mm``.

The upload request is a file body (multipart or raw image bytes), so like the
DXF import there is no request model for it — the router enforces the §13
limits (size cap while streaming, magic-byte sniff, server-side dimension
parse) on the raw bytes.
"""

from __future__ import annotations

from pydantic import Field, StrictBool, StrictInt

from garh_api.schemas import CamelModel, Mm, ResponseModel


class UnderlayOut(ResponseModel):
    """The one underlay of a project, with a fresh presigned image URL."""

    object_key: str
    #: Presigned GET, minted per response — never stored (§13, ≤10 min TTL).
    image_url: str
    width_px: StrictInt = Field(gt=0)
    height_px: StrictInt = Field(gt=0)
    #: Model millimetres per image pixel — the two-point calibration result.
    mm_per_px: float = Field(gt=0)
    #: Model-space position of image pixel (0,0); the image extends east and
    #: south of it (image y grows downward, model Y grows north).
    origin_x_mm: Mm
    origin_y_mm: Mm
    opacity: float = Field(ge=0, le=1)
    locked: StrictBool
    visible: StrictBool


class UnderlayPatchIn(CamelModel):
    """Partial update — every field optional, only supplied fields change.

    Deliberately excludes the image fields: ``objectKey``/``widthPx``/``heightPx``
    come from the actual uploaded bytes via the upload route, never from a claim
    in a JSON body (§13: sniff content, don't trust the client).
    """

    mm_per_px: float | None = Field(default=None, gt=0)
    origin_x_mm: Mm | None = None
    origin_y_mm: Mm | None = None
    opacity: float | None = Field(default=None, ge=0, le=1)
    locked: StrictBool | None = None
    visible: StrictBool | None = None


__all__ = ["UnderlayOut", "UnderlayPatchIn"]
