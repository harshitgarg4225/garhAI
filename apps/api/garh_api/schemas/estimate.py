"""Response schema for ``GET /projects/:id/estimate`` (G-5).

A 1:1 mirror of :meth:`garh_api.estimator.Estimate.to_json`. It is a separate module
rather than a corner of ``schemas/project.py`` because the estimate is the only response
in the API that carries money, and money has its own rules here: every rupee figure is a
whole ``StrictInt``, every percentage an integer ×100, and every band that came from an
authored table (rather than from a rule pack) carries its own ``confidence``.

``ResponseModel`` ignores extras, which is right for a response but means a field
renamed on one side of this mirror would silently vanish from the wire instead of
erroring. ``tests/test_estimate.py`` asserts the specific keys a client reads, through
real HTTP, for exactly that reason.
"""

from __future__ import annotations

import uuid
from typing import Any

from pydantic import Field, StrictBool, StrictInt, StrictStr

from garh_api.schemas import Mm, PointMm, ResponseModel


class EstimatePlotOut(ResponseModel):
    area_mm2: StrictInt
    #: Square feet ×100 — the unit an Indian client hears the number in, carried as an
    #: integer so a display value can never drift from the mm² it came from.
    area_sqft_x100: StrictInt


class SetbackRequirementOut(ResponseModel):
    """One plot edge and what the loaded packs require of it."""

    edge_index: StrictInt
    role: StrictStr
    required_mm: Mm
    #: False = no rule in the loaded packs bands this edge, and ``requiredMm`` is 0
    #: because an unregulated edge permits building to the line — not because the
    #: requirement is zero.
    regulated: StrictBool
    rule_ids: list[StrictStr] = Field(default_factory=list)


class EstimateEnvelopeOut(ResponseModel):
    """The plot boundary offset inward by each edge's required setback."""

    #: ``null`` when the setbacks consume the plot — never an empty polygon.
    polygon_mm: list[PointMm] | None = None
    area_mm2: StrictInt
    area_sqft_x100: StrictInt
    note: StrictStr | None = None
    setbacks: list[SetbackRequirementOut] = Field(default_factory=list)


class EstimateLimitsOut(ResponseModel):
    """Allowances straight from the rules engine. ``null`` = not regulated by the
    loaded packs, which is a different fact from zero and from unlimited."""

    far_allowed_mm2: StrictInt | None = None
    coverage_allowed_mm2: StrictInt | None = None
    max_storeys: StrictInt | None = None
    max_height_mm: Mm | None = None
    rule_ids: dict[str, list[StrictStr]] = Field(default_factory=dict)


class EstimateBuildableOut(ResponseModel):
    """What may be built, and which constraint runs out first."""

    max_ground_floor_area_mm2: StrictInt
    #: ``envelope`` | ``coverage``
    ground_floor_binding: StrictStr
    max_built_up_area_mm2: StrictInt | None = None
    #: ``far`` | ``envelope`` | ``unregulated``
    built_up_binding: StrictStr


class EstimateBasisOut(ResponseModel):
    """The area the money below is actually priced on, and why that area."""

    area_mm2: StrictInt
    area_sqft_x100: StrictInt
    storeys: StrictInt
    #: ``model`` | ``brief`` | ``default``
    source: StrictStr
    note: StrictStr


class FeeBandOut(ResponseModel):
    scope: StrictStr
    label: StrictStr
    description: StrictStr
    basis: StrictStr
    #: Integer ×100: ``500`` is 5.00%.
    low_percent_x100: StrictInt
    high_percent_x100: StrictInt
    low_inr: StrictInt
    high_inr: StrictInt
    confidence: StrictStr


class CostBandOut(ResponseModel):
    tier: StrictStr
    label: StrictStr
    description: StrictStr
    rate_per_sqft_low_inr: StrictInt
    rate_per_sqft_high_inr: StrictInt
    low_inr: StrictInt
    high_inr: StrictInt
    confidence: StrictStr
    fees: list[FeeBandOut] = Field(default_factory=list)


class EstimateOut(ResponseModel):
    """``GET /projects/:id/estimate``."""

    project_id: uuid.UUID
    plot: EstimatePlotOut
    envelope: EstimateEnvelopeOut
    limits: EstimateLimitsOut
    buildable: EstimateBuildableOut
    basis: EstimateBasisOut
    costs: list[CostBandOut] = Field(default_factory=list)
    #: The rule packs' own vocabulary, applied to the tables authored in
    #: :mod:`garh_api.estimator`. "seed" means nobody has reviewed them for your market.
    confidence: StrictStr
    disclaimer: StrictStr
    pack_versions: dict[str, Any] = Field(default_factory=dict)
    warnings: list[StrictStr] = Field(default_factory=list)


__all__ = [
    "CostBandOut",
    "EstimateBasisOut",
    "EstimateBuildableOut",
    "EstimateEnvelopeOut",
    "EstimateLimitsOut",
    "EstimateOut",
    "EstimatePlotOut",
    "FeeBandOut",
    "SetbackRequirementOut",
]
