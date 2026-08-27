"""``garh_rules`` — the NBC / municipal / Vastu rules engine (playbook §6).

Regulations are **data** (``rulepacks/*.json``); this package is the only code
that reads them. It loads and validates packs, evaluates 18 typed check types
against a pre-derived model projection, scores Vastu, and derives the area
statement — purely, deterministically, in integer millimetres, inside 100 ms for a
house so it can run debounced on every canvas edit and inside the solver critic.

Typical use::

    from garh_rules import evaluate, load_pack_set

    packs = load_pack_set(["nbc-core", "blr", "vastu"])       # once, at boot
    report = evaluate(context, packs=packs)                   # per edit

    report.worst_status()          # 'fail' | 'warn' | 'pass' | 'not_applicable'
    report.blocking_failures()     # the §5.6 solver gate
    report.score.score             # the 0-100 Vastu score
    report.areas.rows()            # the §7 area statement, same numbers
    report.to_json()               # what compliance_reports.results stores

Three invariants worth knowing before reading further:

1. **A rule the engine cannot evaluate is a load error, never a pass.** Unknown
   check type, unknown ``when`` field, a parameter the context cannot supply —
   all raise :class:`~garh_rules.errors.PackLoadError` at load.
2. **One result row per rule.** Instances of a scope collapse: worst status wins,
   ``elements[]`` lists every offender, and ``instances[]`` carries the
   per-element detail a chip needs.
3. **The area statement is derived from the rule results.** §7 requires the sheet
   and the compliance panel to quote the same numbers, so there is one source.
"""

from __future__ import annotations

from .areas import (
    AreaRow,
    AreaStatement,
    SetbackRow,
    StoreyAreaRow,
    area_statement,
    build_area_statement,
)
from .checks import CHECK_SCOPES, CHECK_TYPES, RESULT_UNITS, result_unit_of, run_check, scope_of
from .context import (
    MODEL_FIELDS_NOT_IN_MODEL_CORE,
    ROOM_TYPE_ALIASES,
    EvaluationContext,
    ModelSummary,
    OpeningSummary,
    PlotEdge,
    PlotSummary,
    ProfileSummary,
    ProjectionSummary,
    RoomSummary,
    RuleOverride,
    ServiceElementSummary,
    StairSummary,
    StoreySummary,
    context_from_parts,
    normalise_room_type,
)
from .customfns import CUSTOM_FNS
from .engine import PERFORMANCE_BUDGET_MS, EvaluationReport, evaluate, evaluate_parts
from .errors import (
    ContextError,
    EvaluationError,
    GarhRulesError,
    PackLoadError,
    SchemaFeatureError,
    SchemaValidationError,
)
from .formatting import format_area_mm2, format_length_mm, format_ratio, render_message
from .geometry import polygon_area_mm2, polygon_centroid_mm, polygon_least_width_mm
from .packs import (
    ENGINE_LIMITS,
    SUPPORTED_SCHEMA_VERSION,
    AutoFix,
    Check,
    Pack,
    PackLoader,
    PackSet,
    Rule,
    Vocabulary,
    clear_pack_cache,
    load_pack_set,
    rulepack_dir,
)
from .predicates import BOUND_WHEN_FIELDS, OPERATORS
from .results import (
    FAIL,
    NOT_APPLICABLE,
    PASS,
    STATUSES,
    WARN,
    ResultInstance,
    RuleResult,
    worst_status,
)
from .scoring import GroupScore, RuleScore, VastuScore
from .zones import COMPASS8, ZONES, ZoneGrid, facing_of, zone_grid_for

__all__ = [
    # entry points
    "evaluate",
    "evaluate_parts",
    "area_statement",
    "build_area_statement",
    "load_pack_set",
    "clear_pack_cache",
    "rulepack_dir",
    "PackLoader",
    # reports
    "EvaluationReport",
    "RuleResult",
    "ResultInstance",
    "AreaStatement",
    "AreaRow",
    "SetbackRow",
    "StoreyAreaRow",
    "VastuScore",
    "RuleScore",
    "GroupScore",
    # context
    "EvaluationContext",
    "PlotSummary",
    "PlotEdge",
    "ProfileSummary",
    "RuleOverride",
    "ModelSummary",
    "StoreySummary",
    "RoomSummary",
    "OpeningSummary",
    "StairSummary",
    "ProjectionSummary",
    "ServiceElementSummary",
    "context_from_parts",
    "normalise_room_type",
    "ROOM_TYPE_ALIASES",
    "MODEL_FIELDS_NOT_IN_MODEL_CORE",
    # packs
    "PackSet",
    "Pack",
    "Rule",
    "Check",
    "AutoFix",
    "Vocabulary",
    "SUPPORTED_SCHEMA_VERSION",
    "ENGINE_LIMITS",
    # statuses & vocabularies
    "STATUSES",
    "PASS",
    "WARN",
    "FAIL",
    "NOT_APPLICABLE",
    "worst_status",
    "CHECK_TYPES",
    "CHECK_SCOPES",
    "RESULT_UNITS",
    "CUSTOM_FNS",
    "BOUND_WHEN_FIELDS",
    "OPERATORS",
    "ZONES",
    "COMPASS8",
    "ZoneGrid",
    "zone_grid_for",
    "facing_of",
    "scope_of",
    "result_unit_of",
    "run_check",
    # helpers
    "render_message",
    "format_length_mm",
    "format_area_mm2",
    "format_ratio",
    "polygon_area_mm2",
    "polygon_centroid_mm",
    "polygon_least_width_mm",
    "PERFORMANCE_BUDGET_MS",
    # errors
    "GarhRulesError",
    "PackLoadError",
    "SchemaValidationError",
    "SchemaFeatureError",
    "ContextError",
    "EvaluationError",
]
