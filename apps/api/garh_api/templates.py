"""Project templates — code-defined op-log recipes applied at project creation.

Rayon greets a new user with starter templates; Garh started every project empty.
This module is the registry behind ``GET /templates`` and the optional
``templateId`` on ``POST /projects``: pick a template and the server appends its
recipe through the SAME dispatch path the seed uses (``routers.ops.dispatch_ops``,
``source="system"``, stable ``tpl-%02d`` client op ids), so a templated project is
indistinguishable from one a user built by hand — an op log first, projections
mirrored after, golden rule 1 intact.

Design decisions, so they are not re-litigated:

* **Templates are code, not DB rows.** Exactly like the seed's demo builders: a
  template is a function returning wire-shaped ``{type, payload}`` dicts, proven
  foldable by ``tests/test_templates.py`` against the real ``garh_model`` fold.
  A DB-backed registry would need migrations, tenancy, and an editor UI to earn
  its keep; four functions do not.
* **The Bengaluru house template REUSES the seed's builders.** ``demo_op_log``,
  ``solved_plan_ops`` and ``facade_ops`` are imported from
  :mod:`garh_api.seed.demo`, never copied — the demo content is the single most
  proven op log in the product (CP-SAT feasibility receipt in
  ``tests/test_seed_brief_feasible.py``), and a copy would rot the day the seed
  improves. The only difference from the demo project is the ``demo`` flag,
  which stays ``False`` (the route never sets it), so
  ``ProjectRepository.get_demo_project`` — the ONLY door into the seed's
  stale-demo rebuild — can never return a template project.
* **The starter-plot templates mirror the demo's op shapes.** Same op types, same
  payload keys (``plot.set_boundary``/``set_north``/``set_road``/
  ``set_reg_profile``, ``brief.update``, ``storey.add``), same fixture storey ids,
  integer millimetres throughout. Their briefs are starters for the architect to
  edit before Generate — they are NOT solver-feasibility-proven the way the demo
  brief is, and the tests deliberately assert folding, not solving.
* **No floats, anywhere.** The model document holds integers only; a float in a
  brief is an ``OP_FIELD_NOT_INT`` rejection at fold time, which is exactly how
  the fold-proof test would catch a careless edit here.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from pydantic import Field, StrictStr

from garh_api.schemas import ResponseModel

#: One wire-shaped op: ``{"type": ..., "payload": {...}}``.
WireOp = dict[str, Any]

# ---------------------------------------------------------------------------
# Shared geometry (integer mm; 1 ft = 304.8 mm exactly)
# ---------------------------------------------------------------------------

#: 40 ft and 60 ft in integer millimetres.
PLOT_40X60_WIDTH_MM = 12_192
PLOT_40X60_DEPTH_MM = 18_288
#: 20 ft and 30 ft in integer millimetres.
PLOT_20X30_WIDTH_MM = 6_096
PLOT_20X30_DEPTH_MM = 9_144


def _rect_polygon(width_mm: int, depth_mm: int) -> list[dict[str, int]]:
    """An open CCW ring with the origin at the SW corner — the demo plot's shape."""
    return [
        {"x": 0, "y": 0},
        {"x": width_mm, "y": 0},
        {"x": width_mm, "y": depth_mm},
        {"x": 0, "y": depth_mm},
    ]


# ---------------------------------------------------------------------------
# Builders
# ---------------------------------------------------------------------------


def _blank_ops() -> list[WireOp]:
    """The current behavior, listed explicitly: no ops, an empty op log."""
    return []


def _blr_30x40_g1_3bhk_ops() -> list[WireOp]:
    """The §17 demo house, verbatim from the seed's own builders (no copy).

    ``solved_plan_ops`` and ``facade_ops`` are included even though they return
    ``[]`` today: they are the seed's named extension points, and the day Phase 3/5
    fills them in, this template inherits the solved plan for free — exactly the
    reuse the design freezes.
    """
    from garh_api.seed import demo as demo_data

    ops = demo_data.demo_op_log(demo_data.load_demo_brief())
    storey_ids = list(demo_data.demo_storey_ids())
    ops.extend(demo_data.solved_plan_ops(storey_ids=storey_ids))
    ops.extend(demo_data.facade_ops(storey_ids=storey_ids))
    return ops


def _starter_plot_ops(
    *,
    width_mm: int,
    depth_mm: int,
    road_width_mm: int,
    road_name: str,
    brief_patch: dict[str, Any],
    completeness: int,
) -> list[WireOp]:
    """A plot + starter brief + G+1 storeys, in the demo op log's exact shapes.

    Every payload key here is copied from what :func:`garh_api.seed.demo.demo_op_log`
    emits — the one op list this codebase has proven foldable, solvable and
    e2e-walkable — not guessed. North is up (+Y), the road abuts edge 0 (the south
    edge under ``northDeg`` 0), and the reg profile is the demo's Bengaluru pack so
    setbacks/FAR/coverage evaluate from the first fold; the plot panel can switch
    city packs later, as with any project.
    """
    from garh_api.seed import demo as demo_data

    ground, first = demo_data.demo_storey_ids()
    return [
        {
            "type": "plot.set_boundary",
            "payload": {"polygon": _rect_polygon(width_mm, depth_mm), "source": "seed"},
        },
        {"type": "plot.set_north", "payload": {"deg": 0}},
        {
            "type": "plot.set_road",
            "payload": {"edgeIndex": 0, "widthMm": road_width_mm, "name": road_name},
        },
        {
            "type": "plot.set_reg_profile",
            "payload": {"cityPack": demo_data.DEMO_CITY_PACK, "overrides": {}},
        },
        {
            "type": "brief.update",
            "payload": {
                "patch": brief_patch,
                # Same rationale as DEMO_BRIEF_VASTU_MODE: "off" is the honest mode
                # the solver can actually generate under today.
                "vastuMode": "off",
                "completeness": completeness,
            },
        },
        {
            "type": "storey.add",
            "payload": {
                "id": ground,
                "index": 0,
                "name": "Ground Floor",
                "heightMm": demo_data.DEMO_STOREY_HEIGHT_MM,
            },
        },
        {
            "type": "storey.add",
            "payload": {
                "id": first,
                "index": 1,
                "name": "First Floor",
                "heightMm": demo_data.DEMO_STOREY_HEIGHT_MM,
            },
        },
    ]


def _brief_4bhk() -> dict[str, Any]:
    """A starter 4BHK program for a 40×60 ft plot.

    Mirrors :func:`garh_api.seed.demo.demo_brief_data` field for field — every room
    type appears once (counts expand), the ground-floor pins use ``guest_bedroom``/
    ``wc`` for the same key-collision reason, and there is deliberately no
    ``staircase`` row (the solver synthesises the NBC well). Areas are integer mm².
    """
    return {
        "bedrooms": 4,
        "bathrooms": 3,
        "floorsAboveGround": 1,
        "hasStilt": False,
        "hasBasement": False,
        "carParking": 1,
        "twoWheelerParking": 2,
        "poojaRoom": True,
        "servantRoom": False,
        "study": False,
        "lift": False,
        "plotFacing": "south",
        "budgetInr": 12_500_000,
        "styleId": "contemporary",
        "familySize": 6,
        "rooms": [
            {
                "type": "living_dining",
                "count": 1,
                "minAreaMm2": 18_000_000,
                "targetAreaMm2": 26_000_000,
                "minWidthMm": 3600,
            },
            {
                "type": "kitchen",
                "count": 1,
                "minAreaMm2": 7_000_000,
                "targetAreaMm2": 9_500_000,
                "minWidthMm": 2400,
            },
            {
                "type": "utility",
                "count": 1,
                "minAreaMm2": 2_200_000,
                "targetAreaMm2": 3_600_000,
                "minWidthMm": 1200,
            },
            {
                "type": "pooja",
                "count": 1,
                "minAreaMm2": 1_800_000,
                "targetAreaMm2": 3_000_000,
                "minWidthMm": 1200,
            },
            {
                "type": "guest_bedroom",
                "count": 1,
                "minAreaMm2": 10_500_000,
                "targetAreaMm2": 12_500_000,
                "minWidthMm": 3000,
                "storey": 0,
            },
            {
                "type": "wc",
                "count": 1,
                "minAreaMm2": 2_800_000,
                "targetAreaMm2": 4_200_000,
                "minWidthMm": 1500,
                "storey": 0,
            },
            {
                "type": "bedroom_master",
                "count": 1,
                "minAreaMm2": 12_000_000,
                "targetAreaMm2": 15_000_000,
                "minWidthMm": 3300,
            },
            {
                "type": "bedroom",
                "count": 2,
                "minAreaMm2": 10_000_000,
                "targetAreaMm2": 12_000_000,
                "minWidthMm": 3000,
            },
            {
                "type": "bath_wc",
                "count": 2,
                "minAreaMm2": 2_800_000,
                "targetAreaMm2": 4_200_000,
                "minWidthMm": 1500,
            },
        ],
        "adjacency": [
            {"a": "kitchen", "b": "living_dining", "wish": "adjacent", "weight": 80},
            {"a": "kitchen", "b": "utility", "wish": "adjacent", "weight": 60},
            {"a": "bedroom_master", "b": "living_dining", "wish": "apart", "weight": 40},
        ],
    }


def _brief_2bhk_compact() -> dict[str, Any]:
    """A compact 2BHK starter for a 20×30 ft urban infill plot. Same shape rules."""
    return {
        "bedrooms": 2,
        "bathrooms": 2,
        "floorsAboveGround": 1,
        "hasStilt": False,
        "hasBasement": False,
        "carParking": 1,
        "twoWheelerParking": 1,
        "poojaRoom": False,
        "servantRoom": False,
        "study": False,
        "lift": False,
        "plotFacing": "south",
        "budgetInr": 4_500_000,
        "styleId": "contemporary",
        "familySize": 4,
        "rooms": [
            {
                "type": "living_dining",
                "count": 1,
                "minAreaMm2": 12_000_000,
                "targetAreaMm2": 15_000_000,
                "minWidthMm": 3000,
            },
            {
                "type": "kitchen",
                "count": 1,
                "minAreaMm2": 4_500_000,
                "targetAreaMm2": 6_000_000,
                "minWidthMm": 2100,
            },
            {
                "type": "wc",
                "count": 1,
                "minAreaMm2": 2_000_000,
                "targetAreaMm2": 2_800_000,
                "minWidthMm": 1200,
                "storey": 0,
            },
            {
                "type": "bedroom_master",
                "count": 1,
                "minAreaMm2": 10_000_000,
                "targetAreaMm2": 11_500_000,
                "minWidthMm": 3000,
            },
            {
                "type": "bedroom",
                "count": 1,
                "minAreaMm2": 9_500_000,
                "targetAreaMm2": 10_500_000,
                "minWidthMm": 2400,
            },
            {
                "type": "bath_wc",
                "count": 1,
                "minAreaMm2": 2_800_000,
                "targetAreaMm2": 3_600_000,
                "minWidthMm": 1500,
            },
        ],
        "adjacency": [
            {"a": "kitchen", "b": "living_dining", "wish": "adjacent", "weight": 80},
        ],
    }


def _plot_40x60_empty_brief_ops() -> list[WireOp]:
    return _starter_plot_ops(
        width_mm=PLOT_40X60_WIDTH_MM,
        depth_mm=PLOT_40X60_DEPTH_MM,
        road_width_mm=9000,
        road_name="9 m road (south)",
        brief_patch=_brief_4bhk(),
        completeness=60,
    )


def _plot_20x30_compact_ops() -> list[WireOp]:
    return _starter_plot_ops(
        width_mm=PLOT_20X30_WIDTH_MM,
        depth_mm=PLOT_20X30_DEPTH_MM,
        road_width_mm=6000,
        road_name="6 m road (south)",
        brief_patch=_brief_2bhk_compact(),
        completeness=60,
    )


# ---------------------------------------------------------------------------
# The registry
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ProjectTemplate:
    """One starter template: registry card + the op-log recipe behind it."""

    id: str
    name: str
    description: str
    #: Human chip for the picker card ("30 × 40 ft"). Empty for the blank template.
    plot_size_label: str
    tags: tuple[str, ...]
    #: Returns a FRESH list of wire ops on every call — callers may mutate.
    build: Callable[[], list[WireOp]]


BLANK_TEMPLATE_ID = "blank"

#: Ordered as the picker shows them: Blank first (and the default).
TEMPLATES: tuple[ProjectTemplate, ...] = (
    ProjectTemplate(
        id=BLANK_TEMPLATE_ID,
        name="Blank project",
        description="Start from nothing — draw the plot and capture the brief yourself.",
        plot_size_label="",
        tags=(),
        build=_blank_ops,
    ),
    ProjectTemplate(
        id="blr-30x40-g1-3bhk",
        name="30 × 40 Bengaluru 3BHK",
        description="The proven demo house: a 30 × 40 ft BBMP plot, 9 m south road, "
        "and a G+1 3BHK brief that generates plan options first try.",
        plot_size_label="30 × 40 ft",
        tags=("blr", "g+1", "3bhk"),
        build=_blr_30x40_g1_3bhk_ops,
    ),
    ProjectTemplate(
        id="plot-40x60-empty-brief",
        name="40 × 60 family 4BHK",
        description="A 40 × 60 ft plot with road, north and storeys set up, plus a "
        "starter 4BHK brief to edit before you generate.",
        plot_size_label="40 × 60 ft",
        tags=("blr", "g+1", "4bhk"),
        build=_plot_40x60_empty_brief_ops,
    ),
    ProjectTemplate(
        id="plot-20x30-compact",
        name="20 × 30 compact 2BHK",
        description="A compact 20 × 30 ft infill plot with a 2BHK starter brief — "
        "the small urban house, ready to refine.",
        plot_size_label="20 × 30 ft",
        tags=("blr", "g+1", "2bhk"),
        build=_plot_20x30_compact_ops,
    ),
)


def template_ids() -> tuple[str, ...]:
    return tuple(t.id for t in TEMPLATES)


def get_template(template_id: str) -> ProjectTemplate | None:
    for template in TEMPLATES:
        if template.id == template_id:
            return template
    return None


# ---------------------------------------------------------------------------
# Wire shape for GET /templates
# ---------------------------------------------------------------------------


class TemplateOut(ResponseModel):
    """One registry card. The recipe itself never goes over the wire."""

    id: StrictStr
    name: StrictStr
    description: StrictStr
    plot_size_label: StrictStr = ""
    tags: list[StrictStr] = Field(default_factory=list)

    @classmethod
    def of(cls, template: ProjectTemplate) -> TemplateOut:
        return cls(
            id=template.id,
            name=template.name,
            description=template.description,
            plot_size_label=template.plot_size_label,
            tags=list(template.tags),
        )


class TemplatesOut(ResponseModel):
    """``GET /templates`` — the whole registry, picker order."""

    templates: list[TemplateOut] = Field(default_factory=list)


__all__ = [
    "BLANK_TEMPLATE_ID",
    "PLOT_20X30_DEPTH_MM",
    "PLOT_20X30_WIDTH_MM",
    "PLOT_40X60_DEPTH_MM",
    "PLOT_40X60_WIDTH_MM",
    "ProjectTemplate",
    "TEMPLATES",
    "TemplateOut",
    "TemplatesOut",
    "WireOp",
    "get_template",
    "template_ids",
]
