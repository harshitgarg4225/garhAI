"""Fee and area estimate from a plot and a brief (G-5) — the job nobody serves.

An Indian architect's first conversation with a client is not about plans. It is "what
can I build on this, roughly what will it cost, and what will you charge me?" — asked
before anything is drawn, and today answered from a spreadsheet on somebody's laptop.
This module answers it from the project's own plot, its own brief and the same rule
packs the compliance tab quotes.

ONE SOURCE FOR THE COMPLIANCE NUMBERS
-------------------------------------
Every regulatory number here — FAR allowance, coverage allowance, height, storeys, the
per-edge setback requirement — is read out of :func:`garh_api.compliance.evaluate_document`,
which is the same call ``GET /projects/:id/compliance``, the sheet area statement and
``garh_api.solver_enqueue`` all make. Nothing in this file recomputes a ratio against a
plot area. That is not tidiness: a fee quote that says 2.25 FAR beside a compliance tab
that says something else is a liability bug in a product selling citable compliance
(playbook §7, "from rules results — same numbers, one source").

The two things this module derives that the area statement does not:

* the **buildable envelope** — the plot boundary offset inward by each edge's *required*
  setback, through ``garh_model.geometry.offset_polygon`` (the same offsetter the solver
  uses in ``services/solver/envelope.py``). The area statement reports the requirement
  per edge; nobody had turned the requirement into a polygon.
* which constraint actually **binds**. A FAR allowance is not a buildable area: on a
  small plot with deep setbacks the envelope stacked to the permissible storey count
  runs out first, and on a large plot with shallow setbacks FAR runs out first. Both
  happen inside the seeded blr pack on plots architects really draw, so the estimate
  states the binding constraint by name rather than quoting whichever number is prettier.

WHAT IS AUTHORED HERE, AND HOW HONEST IT IS
-------------------------------------------
The money is not derived from anything — it cannot be. :data:`CONSTRUCTION_RATES` and
:data:`FEE_SCALES` are tables authored in this file, carrying ``confidence: "seed"`` for
exactly the reason the rule packs do: they are a defensible starting point that a firm
must replace with its own rates before quoting from them. Every response says so, in a
``disclaimer`` field the UI is expected to render, not a comment nobody sees.

All money is **whole rupees** (the catalogue convention — ``priceInrPerSqm`` in
``routers/catalog.py``), all areas integer mm², all percentages integers ×100. There are
no floats in the output: a range whose ends are ``4999999.999999999`` is a range that
will eventually print wrong.

WHY THERE IS NO WRITE PATH
--------------------------
An estimate is a pure function of (document, packs, tables). Storing one would create a
second source for numbers that must move when the plot, the brief or a pack moves — and
a stale stored quote is worse than no quote. The route is a GET and it is behind the
firm's own token only, never a share link: the professional fee is the architect's
commercial position and a client viewer must not read it.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from fractions import Fraction
from typing import Any, Final

from garh_api.logging import get_logger

_log = get_logger(__name__)

#: Exactly one square foot in mm²: (12 × 25.4)² = 304.8² = 92903.04. Held as a Fraction
#: so the mm² → sqft → rupees chain is exact and the same on every machine.
MM2_PER_SQFT: Final = Fraction(9_290_304, 100)

#: Every authored number in this module carries the rule packs' own confidence
#: vocabulary. "seed" means: real, defensible, and not yet reviewed by anyone whose
#: name would go on a quotation.
ESTIMATE_CONFIDENCE: Final = "seed"

#: Rendered by the UI beside every rupee figure. Deliberately long: an indicative range
#: presented without this sentence is a quotation, and a quotation is a contract.
COST_DISCLAIMER: Final = (
    "Indicative only, from seed rates that no one has reviewed for your market. "
    "Construction cost excludes land, statutory and sanction fees, compound wall, "
    "landscaping, loose furniture and GST. Replace these rates with your firm's own "
    "before quoting a client."
)


# ---------------------------------------------------------------------------
# The authored tables
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ConstructionRate:
    """One finish tier, as a rupees-per-square-foot band.

    Per **square foot** and not per m², against the rest of the codebase's SI habit,
    because ₹/sqft is the unit every Indian builder, client and contractor quotes in.
    A rate table an architect cannot sanity-check at a glance is a rate table they will
    not trust. The conversion to the model's mm² is exact (:data:`MM2_PER_SQFT`).
    """

    tier: str
    label: str
    low_inr_per_sqft: int
    high_inr_per_sqft: int
    description: str
    confidence: str = ESTIMATE_CONFIDENCE

    def __post_init__(self) -> None:
        if self.low_inr_per_sqft < 1 or self.high_inr_per_sqft < self.low_inr_per_sqft:
            raise ValueError("construction rate %r has an inverted or zero band" % self.tier)


#: Turnkey residential construction, Indian metros, 2026 price level. Structure +
#: finishes + basic services; the exclusions are in :data:`COST_DISCLAIMER`. The bands
#: overlap at their edges on purpose — a "standard" job with imported fittings really
#: does land in "premium" territory, and pretending the tiers are disjoint would invent
#: a precision that does not exist.
CONSTRUCTION_RATES: Final[tuple[ConstructionRate, ...]] = (
    ConstructionRate(
        tier="basic",
        label="Basic / builder finish",
        low_inr_per_sqft=1600,
        high_inr_per_sqft=2000,
        description="RCC frame, plastered and painted, vitrified tiles, standard "
        "sanitaryware and CP fittings, no false ceiling.",
    ),
    ConstructionRate(
        tier="standard",
        label="Standard residential finish",
        low_inr_per_sqft=2000,
        high_inr_per_sqft=2600,
        description="The default a client means by 'good quality': branded fittings, "
        "modular kitchen, granite or engineered stone counters, part false ceiling.",
    ),
    ConstructionRate(
        tier="premium",
        label="Premium finish",
        low_inr_per_sqft=2600,
        high_inr_per_sqft=3500,
        description="Designed joinery, stone and veneer, full false ceiling with "
        "lighting design, split ACs provisioned, home-automation ready.",
    ),
    ConstructionRate(
        tier="luxury",
        label="Luxury / bespoke",
        low_inr_per_sqft=3500,
        high_inr_per_sqft=5200,
        description="Bespoke joinery and stone, imported sanitaryware, central AC, "
        "lift, landscaped terraces, full automation.",
    ),
)


@dataclass(frozen=True)
class FeeScale:
    """One scope of architectural service, as a percentage band of construction cost.

    Percentages are integers ×100 (``500`` = 5.00%), the same trick
    ``garh_api.solver_enqueue`` uses for FAR — a fee band is money arithmetic and no
    float belongs in it.
    """

    scope: str
    label: str
    low_percent_x100: int
    high_percent_x100: int
    description: str
    basis: str
    confidence: str = ESTIMATE_CONFIDENCE

    def __post_init__(self) -> None:
        if self.low_percent_x100 < 1 or self.high_percent_x100 < self.low_percent_x100:
            raise ValueError("fee scale %r has an inverted or zero band" % self.scope)


#: Professional fee as a share of construction cost. The comprehensive row is anchored
#: on a real, citable document — the Council of Architecture's *Conditions of Engagement
#: and Scale of Charges*, which sets 5% for comprehensive architectural services on an
#: individual residence — and the band around it is market practice. The two narrower
#: scopes are the usual Indian residential split of that same scale, and are marked
#: "seed" like everything else here.
FEE_SCALES: Final[tuple[FeeScale, ...]] = (
    FeeScale(
        scope="concept",
        label="Concept and preliminary drawings",
        low_percent_x100=150,
        high_percent_x100=300,
        description="Site study, brief, concept options, preliminary plans and one "
        "presentation set. Stops before the municipal submission.",
        basis="Pro-rata share of the CoA comprehensive scale as split in Indian "
        "residential practice.",
    ),
    FeeScale(
        scope="submission",
        label="Up to municipal sanction",
        low_percent_x100=300,
        high_percent_x100=500,
        description="Concept through the sanction drawing set, submission to the "
        "authority and queries answered until the plan is approved.",
        basis="Pro-rata share of the CoA comprehensive scale as split in Indian "
        "residential practice.",
    ),
    FeeScale(
        scope="comprehensive",
        label="Comprehensive services",
        low_percent_x100=500,
        high_percent_x100=800,
        description="Everything above plus working drawings, tender and contract "
        "support, and periodic site supervision to completion.",
        basis="Council of Architecture, Conditions of Engagement and Scale of Charges "
        "— 5% for comprehensive services on an individual residence.",
    ),
)


# ---------------------------------------------------------------------------
# Exact money arithmetic
# ---------------------------------------------------------------------------


def round_half_away(value: Fraction) -> int:
    """The repo-wide rounding contract, on an exact rational.

    ``garh_model.round_half_away_from_zero`` takes a float; this takes the Fraction the
    rupee chain actually produces, so a cost never depends on binary rounding.
    """
    if value >= 0:
        return math.floor(value + Fraction(1, 2))
    return -math.floor(-value + Fraction(1, 2))


def area_sqft_x100(area_mm2: int) -> int:
    """Area in square feet ×100 — for display only; costs use the exact Fraction."""
    return round_half_away(Fraction(area_mm2 * 100) / MM2_PER_SQFT)


def construction_cost_inr(area_mm2: int, rate_inr_per_sqft: int) -> int:
    """Whole rupees for ``area_mm2`` at ``rate_inr_per_sqft``. Exact, then rounded once."""
    if area_mm2 <= 0:
        return 0
    return round_half_away(Fraction(area_mm2 * rate_inr_per_sqft) / MM2_PER_SQFT)


def fee_inr(construction_inr: int, percent_x100: int) -> int:
    """Whole rupees of professional fee at ``percent_x100`` (``500`` = 5.00%)."""
    if construction_inr <= 0:
        return 0
    return round_half_away(Fraction(construction_inr * percent_x100, 10_000))


# ---------------------------------------------------------------------------
# Result shapes
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SetbackRequirement:
    """One plot edge and the setback the loaded packs require of it."""

    edge_index: int
    role: str
    required_mm: int
    regulated: bool
    rule_ids: tuple[str, ...]

    def to_json(self) -> dict[str, Any]:
        return {
            "edgeIndex": self.edge_index,
            "role": self.role,
            "requiredMm": self.required_mm,
            "regulated": self.regulated,
            "ruleIds": list(self.rule_ids),
        }


@dataclass(frozen=True)
class FeeBand:
    scale: FeeScale
    low_inr: int
    high_inr: int

    def to_json(self) -> dict[str, Any]:
        return {
            "scope": self.scale.scope,
            "label": self.scale.label,
            "description": self.scale.description,
            "basis": self.scale.basis,
            "lowPercentX100": self.scale.low_percent_x100,
            "highPercentX100": self.scale.high_percent_x100,
            "lowInr": self.low_inr,
            "highInr": self.high_inr,
            "confidence": self.scale.confidence,
        }


@dataclass(frozen=True)
class CostBand:
    rate: ConstructionRate
    low_inr: int
    high_inr: int
    fees: tuple[FeeBand, ...]

    def to_json(self) -> dict[str, Any]:
        return {
            "tier": self.rate.tier,
            "label": self.rate.label,
            "description": self.rate.description,
            "ratePerSqftLowInr": self.rate.low_inr_per_sqft,
            "ratePerSqftHighInr": self.rate.high_inr_per_sqft,
            "lowInr": self.low_inr,
            "highInr": self.high_inr,
            "confidence": self.rate.confidence,
            "fees": [fee.to_json() for fee in self.fees],
        }


@dataclass(frozen=True)
class Estimate:
    """Everything the estimate route returns, in integer mm² and whole rupees."""

    plot_area_mm2: int
    envelope_polygon_mm: tuple[tuple[int, int], ...] | None
    envelope_area_mm2: int
    envelope_note: str | None
    setbacks: tuple[SetbackRequirement, ...]
    far_allowed_mm2: int | None
    coverage_allowed_mm2: int | None
    max_ground_floor_area_mm2: int
    ground_floor_binding: str
    max_storeys: int | None
    max_height_mm: int | None
    max_built_up_area_mm2: int | None
    built_up_binding: str
    basis_area_mm2: int
    basis: str
    basis_storeys: int
    basis_note: str
    costs: tuple[CostBand, ...]
    rule_ids: Mapping[str, tuple[str, ...]]
    pack_versions: Mapping[str, Any]
    warnings: tuple[str, ...] = ()

    def to_json(self) -> dict[str, Any]:
        return {
            "plot": {
                "areaMm2": self.plot_area_mm2,
                "areaSqftX100": area_sqft_x100(self.plot_area_mm2),
            },
            "envelope": {
                "polygonMm": (
                    [{"x": x, "y": y} for x, y in self.envelope_polygon_mm]
                    if self.envelope_polygon_mm is not None
                    else None
                ),
                "areaMm2": self.envelope_area_mm2,
                "areaSqftX100": area_sqft_x100(self.envelope_area_mm2),
                "note": self.envelope_note,
                "setbacks": [row.to_json() for row in self.setbacks],
            },
            "limits": {
                "farAllowedMm2": self.far_allowed_mm2,
                "coverageAllowedMm2": self.coverage_allowed_mm2,
                "maxStoreys": self.max_storeys,
                "maxHeightMm": self.max_height_mm,
                "ruleIds": {key: list(value) for key, value in self.rule_ids.items()},
            },
            "buildable": {
                "maxGroundFloorAreaMm2": self.max_ground_floor_area_mm2,
                "groundFloorBinding": self.ground_floor_binding,
                "maxBuiltUpAreaMm2": self.max_built_up_area_mm2,
                "builtUpBinding": self.built_up_binding,
            },
            "basis": {
                "areaMm2": self.basis_area_mm2,
                "areaSqftX100": area_sqft_x100(self.basis_area_mm2),
                "storeys": self.basis_storeys,
                "source": self.basis,
                "note": self.basis_note,
            },
            "costs": [band.to_json() for band in self.costs],
            "confidence": ESTIMATE_CONFIDENCE,
            "disclaimer": COST_DISCLAIMER,
            "packVersions": dict(self.pack_versions),
            "warnings": list(self.warnings),
        }


# ---------------------------------------------------------------------------
# Building it
# ---------------------------------------------------------------------------

#: A "not regulated by the loaded packs" setback becomes 0 mm here, exactly as
#: ``solver_enqueue._plot_payload`` does it: an unregulated edge honestly permits
#: building to the line, and inventing a bye-law number nobody wrote would be worse.
UNREGULATED_SETBACK_MM: Final = 0

#: Storeys assumed when neither the brief nor the model says. One, not the permissible
#: maximum: quoting a client for G+3 because the bye-law allows it is how an estimate
#: becomes a complaint.
DEFAULT_BASIS_STOREYS: Final = 1


def _int_or_none(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _briefed_storeys(document: Mapping[str, Any]) -> tuple[int | None, str]:
    """How many storeys the client has actually asked for, and where that came from.

    Order matters: a modelled storey stack is a decision someone made in the app and
    beats the brief's headline number, which is a form field that may predate it.
    """
    house = document.get("house")
    modelled = len(list((house or {}).get("storeys") or ())) if isinstance(house, Mapping) else 0
    if modelled >= 1:
        return modelled, "model"
    brief = document.get("brief")
    brief_data = (brief or {}).get("data") if isinstance(brief, Mapping) else None
    if not isinstance(brief_data, Mapping):
        return None, "default"
    floors = _int_or_none(brief_data.get("floorsAboveGround"))
    if floors is not None and floors >= 0:
        # "G+1" is two storeys. The brief field counts floors ABOVE ground.
        return floors + 1, "brief"
    return None, "default"


def _envelope(
    boundary: Sequence[Mapping[str, Any]], setbacks: Sequence[SetbackRequirement]
) -> tuple[tuple[tuple[int, int], ...] | None, int, str | None]:
    """Offset the boundary inward by each edge's requirement. Never guesses."""
    from garh_model.geometry import Pt, offset_polygon, polygon_area_mm2

    polygon = [Pt(int(p["x"]), int(p["y"])) for p in boundary]
    by_edge = {row.edge_index: row.required_mm for row in setbacks}
    # offset_polygon wants one distance per edge i (poly[i] -> poly[i+1]), which is the
    # same indexing compliance.build_evaluation_context gives the setback rows.
    distances = [float(by_edge.get(index, UNREGULATED_SETBACK_MM)) for index in range(len(polygon))]

    offset = offset_polygon(polygon, distances)
    if offset is None:
        # Documented contract: None means "no buildable envelope", never an empty
        # polygon. Deep setbacks really do consume small plots, and saying so is the
        # single most useful thing this endpoint can tell an architect about a bad site.
        return (
            None,
            0,
            "The required setbacks leave no buildable area on this plot — check the "
            "plot dimensions, the road width and the city pack.",
        )
    return tuple((p.x, p.y) for p in offset), polygon_area_mm2(offset), None


def build_estimate(
    document: Mapping[str, Any],
    *,
    city_pack: str | None = None,
) -> Estimate:
    """Plot + brief + resolved packs → the buildable envelope, a cost band and a fee band.

    Raises :class:`garh_api.compliance.ComplianceUnavailable` for the same reasons
    ``GET /compliance`` does (no plot boundary being the common one), so the route can
    answer with the same actionable 4xx rather than inventing a zero-area estimate.
    """
    from garh_api.compliance import evaluate_document

    report, pack_versions = evaluate_document(document, city_pack=city_pack)
    areas_raw = report.get("areas")
    areas: Mapping[str, Any] = areas_raw if isinstance(areas_raw, Mapping) else {}

    warnings: list[str] = [str(w) for w in (areas.get("warnings") or ())]

    plot_area_mm2 = _int_or_none(areas.get("plotAreaMm2")) or 0
    far_allowed_mm2 = _int_or_none(areas.get("farAllowedMm2"))
    coverage_allowed_mm2 = _int_or_none(areas.get("coverageAllowedMm2"))
    max_storeys = _int_or_none(areas.get("floorsAllowed"))
    max_height_mm = _int_or_none(areas.get("heightAllowedMm"))
    rule_id_map = areas.get("ruleIds")
    rule_ids: dict[str, tuple[str, ...]] = {
        str(key): tuple(str(v) for v in value)
        for key, value in (rule_id_map or {}).items()
        if isinstance(value, list | tuple)
    }

    setbacks: list[SetbackRequirement] = []
    for row in areas.get("setbacks") or ():
        if not isinstance(row, Mapping):
            continue
        index = _int_or_none(row.get("edgeIndex"))
        if index is None:
            continue
        required = _int_or_none(row.get("requiredMm"))
        setbacks.append(
            SetbackRequirement(
                edge_index=index,
                role=str(row.get("role") or "other"),
                required_mm=required if required is not None else UNREGULATED_SETBACK_MM,
                regulated=required is not None,
                rule_ids=tuple(str(r) for r in (row.get("ruleIds") or ())),
            )
        )
    setbacks.sort(key=lambda r: r.edge_index)

    boundary = list((document.get("plot") or {}).get("boundary") or ())
    polygon, envelope_area_mm2, envelope_note = _envelope(boundary, setbacks)

    # -- what may actually be built ---------------------------------------
    #
    # The ground floor is capped by TWO independent things and the smaller wins: the
    # envelope you can physically stand a wall inside, and the coverage ratio the pack
    # allows. Both bite on plots architects really draw — a 30x40 ft blr plot with a
    # 3 m front setback is envelope-bound (58.5 m² against a 78.0 m² coverage cap),
    # while a shallow-setback plot on a narrow road is coverage-bound.
    if coverage_allowed_mm2 is None:
        max_ground_floor_area_mm2 = envelope_area_mm2
        ground_floor_binding = "envelope"
        warnings.append(
            "No coverage rule applied, so the ground floor is limited only by the "
            "setback envelope."
        )
    elif coverage_allowed_mm2 < envelope_area_mm2:
        max_ground_floor_area_mm2 = coverage_allowed_mm2
        ground_floor_binding = "coverage"
    else:
        max_ground_floor_area_mm2 = envelope_area_mm2
        ground_floor_binding = "envelope"

    # Likewise the built-up ceiling: FAR, or the envelope stacked to the permissible
    # storey count, whichever runs out first.
    stacked_mm2 = max_ground_floor_area_mm2 * max_storeys if max_storeys is not None else None
    candidates = [
        (value, name)
        for value, name in ((far_allowed_mm2, "far"), (stacked_mm2, "envelope"))
        if value is not None
    ]
    if candidates:
        # ``key=`` so a tie reports the FIRST listed constraint ("far") rather than the
        # alphabetically smaller name — a silent tie-break on a label is how a binding
        # constraint ends up mis-attributed in a report someone quotes.
        max_built_up_area_mm2, built_up_binding = min(candidates, key=lambda pair: pair[0])
    else:
        max_built_up_area_mm2, built_up_binding = None, "unregulated"
        warnings.append(
            "Neither a FAR rule nor a storey limit applied, so no built-up ceiling is "
            "stated. The estimate below is for the storeys the brief asks for."
        )

    # -- what to price ----------------------------------------------------
    #
    # NOT the permissible maximum by default. An architect quoting a fee prices the
    # house the client asked for; pricing the bye-law ceiling would inflate every quote
    # on every generously-zoned plot. The maximum is still returned above, as the
    # separate fact it is.
    briefed, briefed_source = _briefed_storeys(document)
    if briefed is None:
        basis_storeys = DEFAULT_BASIS_STOREYS
        basis = "default"
        basis_note = (
            "The brief doesn't say how many floors yet, so this prices a single storey. "
            "Set 'floors above ground' on the Brief tab for a real number."
        )
    else:
        permitted = max_storeys
        basis_storeys = briefed if permitted is None else min(briefed, permitted)
        basis = briefed_source
        if permitted is not None and basis_storeys < briefed:
            basis_note = (
                "The brief asks for %d storeys but the loaded packs permit %d, so this "
                "prices %d." % (briefed, permitted, basis_storeys)
            )
            warnings.append(basis_note)
        else:
            basis_note = "Priced for the %d storeys the %s asks for." % (
                basis_storeys,
                "brief" if briefed_source == "brief" else "modelled design",
            )

    basis_area_mm2 = max_ground_floor_area_mm2 * basis_storeys
    if far_allowed_mm2 is not None and basis_area_mm2 > far_allowed_mm2:
        # A brief can ask for more than FAR permits. Price what is buildable and say so
        # — the alternative is a quote the client cannot ever be given the house for.
        basis_area_mm2 = far_allowed_mm2
        # Appended as its own sentence, and only the new sentence goes in `warnings` —
        # re-appending the whole running note would show the storey warning twice in a
        # list the UI renders verbatim.
        capped = (
            "The FAR allowance caps it below %d full storeys, so this prices the "
            "FAR-permissible area." % basis_storeys
        )
        basis_note = "%s %s" % (basis_note, capped)
        warnings.append(capped)

    costs = tuple(
        CostBand(
            rate=rate,
            low_inr=construction_cost_inr(basis_area_mm2, rate.low_inr_per_sqft),
            high_inr=construction_cost_inr(basis_area_mm2, rate.high_inr_per_sqft),
            fees=tuple(
                FeeBand(
                    scale=scale,
                    low_inr=fee_inr(
                        construction_cost_inr(basis_area_mm2, rate.low_inr_per_sqft),
                        scale.low_percent_x100,
                    ),
                    high_inr=fee_inr(
                        construction_cost_inr(basis_area_mm2, rate.high_inr_per_sqft),
                        scale.high_percent_x100,
                    ),
                )
                for scale in FEE_SCALES
            ),
        )
        for rate in CONSTRUCTION_RATES
    )

    if envelope_note is not None:
        warnings.append(envelope_note)

    _log.info(
        "estimate.built",
        plot_area_mm2=plot_area_mm2,
        envelope_area_mm2=envelope_area_mm2,
        basis_area_mm2=basis_area_mm2,
        basis=basis,
        ground_floor_binding=ground_floor_binding,
        built_up_binding=built_up_binding,
    )

    return Estimate(
        plot_area_mm2=plot_area_mm2,
        envelope_polygon_mm=polygon,
        envelope_area_mm2=envelope_area_mm2,
        envelope_note=envelope_note,
        setbacks=tuple(setbacks),
        far_allowed_mm2=far_allowed_mm2,
        coverage_allowed_mm2=coverage_allowed_mm2,
        max_ground_floor_area_mm2=max_ground_floor_area_mm2,
        ground_floor_binding=ground_floor_binding,
        max_storeys=max_storeys,
        max_height_mm=max_height_mm,
        max_built_up_area_mm2=max_built_up_area_mm2,
        built_up_binding=built_up_binding,
        basis_area_mm2=basis_area_mm2,
        basis=basis,
        basis_storeys=basis_storeys,
        basis_note=basis_note,
        costs=costs,
        rule_ids=rule_ids,
        pack_versions=dict(pack_versions),
        warnings=tuple(warnings),
    )


__all__ = [
    "COST_DISCLAIMER",
    "CONSTRUCTION_RATES",
    "DEFAULT_BASIS_STOREYS",
    "ESTIMATE_CONFIDENCE",
    "FEE_SCALES",
    "MM2_PER_SQFT",
    "UNREGULATED_SETBACK_MM",
    "ConstructionRate",
    "CostBand",
    "Estimate",
    "FeeBand",
    "FeeScale",
    "SetbackRequirement",
    "area_sqft_x100",
    "build_estimate",
    "construction_cost_inr",
    "fee_inr",
    "round_half_away",
]
