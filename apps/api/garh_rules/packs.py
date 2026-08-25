from __future__ import annotations

"""Pack loader: read, validate, resolve ``extends``, apply overrides — loudly.

Playbook §6 and ``rulepacks/README.md`` both say the same thing in different
words: *a rule the engine cannot evaluate must be a load error, never a silent
pass*. Everything in this module exists to honour that. It rejects, with a named
rule id and a reason:

* a ``schemaVersion`` it does not implement;
* a pack that fails ``rulepacks/schema/rulepack.schema.json``;
* an unknown check type, ``when`` field, operator or ``custom.fn``;
* a ``when`` field the *schema* declares but this engine cannot bind (adding a
  field to the schema without teaching :mod:`garh_rules.predicates` is a bug we
  catch here rather than in production);
* a check parameter the engine cannot honour from the EvaluationContext —
  ``floors_max.counts: ["mezzanine"]`` and a non-default
  ``ventilation_ratio_min.countKinds`` are the two real cases (see
  :data:`ENGINE_LIMITS`);
* an ``extends`` cycle, a duplicate rule id, an id whose prefix disagrees with
  its pack, a scoring rule with no weight or an undeclared group.

Loading is I/O, so it happens **once**: :func:`load_pack_set` memoises on
``(root, pack ids)`` and the evaluator never touches the filesystem. Call
:func:`clear_pack_cache` after editing a pack in dev (``catalog.py`` exposes the
same escape hatch for the same reason).
"""

import importlib
import json
import os
from dataclasses import dataclass, field, replace
from typing import Any, Dict, FrozenSet, Iterable, List, Mapping, Optional, Sequence, Tuple

from .errors import PackLoadError, SchemaValidationError
from .jsonschema_min import SchemaValidator
from .predicates import BOUND_WHEN_FIELDS, OPERATORS
from .ratio import Ratio, require_int

__all__ = [
    "AutoFix",
    "Check",
    "Rule",
    "Pack",
    "PackSet",
    "PackLoader",
    "Vocabulary",
    "ScoringMode",
    "Scoring",
    "SUPPORTED_SCHEMA_VERSION",
    "DEFAULT_COUNT_KINDS",
    "SUPPORTED_EXTRA_FLOOR_KINDS",
    "CUSTOM_FN_SCOPES",
    "ENGINE_LIMITS",
    "load_pack_set",
    "clear_pack_cache",
    "rulepack_dir",
]

#: The only DSL version this engine implements. A pack declaring anything else is
#: rejected: a bumped schemaVersion means check types or context fields changed.
SUPPORTED_SCHEMA_VERSION = 1

#: ``ventilation_ratio_min.countKinds`` default, per the schema. The context gives
#: one pre-summed ``room.ventilationOpeningAreaMm2`` with no per-kind breakdown, so
#: the engine can only verify a rule that counts this exact set.
DEFAULT_COUNT_KINDS: Tuple[str, ...] = ("window", "ventilator")

#: ``floors_max.counts`` entries the engine can actually count. ``mezzanine`` and
#: ``terrace-mumty`` are in the schema's enum but absent from the EvaluationContext,
#: so a pack naming them would produce a floor count that is quietly too low.
SUPPORTED_EXTRA_FLOOR_KINDS: Tuple[str, ...] = ("stilt", "basement")

#: Declared scope of each registered ``custom.fn`` (``x-garh-check-meta.customFns``).
CUSTOM_FN_SCOPES: Mapping[str, str] = {"rwh_required": "project", "brahmasthan_open": "project"}

#: Documented, enforced limits — each one is a *loud* rejection at load, and each
#: one is a note for whoever reviews the packs. Kept as data so the API can serve
#: it next to ``GET /rulepacks``.
ENGINE_LIMITS: Tuple[str, ...] = (
    "floors_max.counts accepts only %s; mezzanine and terrace-mumty are not in the "
    "EvaluationContext, so counting them is impossible rather than approximate."
    % (" / ".join(SUPPORTED_EXTRA_FLOOR_KINDS),),
    "ventilation_ratio_min.countKinds must be %s (the schema default): the context "
    "supplies one pre-summed openable area per room, which cannot be re-partitioned "
    "by opening kind." % (" + ".join(DEFAULT_COUNT_KINDS),),
    "zone_check mode=facing requires target.kind == 'opening': outwardNormalDeg is the "
    "only facing the model projection carries.",
    "height_max.excludes entries missing from model.heightComponentsMm subtract 0 — a "
    "component the building does not have has no height.",
    "far_max.premium is reported as a note and never added to the limit; buying premium "
    "FAR is the architect's decision, not the engine's.",
    "setback_min.measure='to-projection' measures to the outermost projection on that "
    "edge (provided setback minus the deepest projection); no seed pack uses it, so it "
    "is covered by unit tests only.",
    "autofix.opType and when.roomType are vetted against garh_model (op catalogue / "
    "ROOM_TYPES) at load. A mismatch is a NOTE on the pack set, not a load error: the "
    "pack is usually right and the model core behind, and taking a city pack offline "
    "over a Fix-it hint would be worse. Every offender is named in PackSet.notes, which "
    "surfaces in report.warnings.",
)


# ---------------------------------------------------------------------------
# Value objects
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AutoFix:
    """Advisory hint behind the "Fix it" button (§15). Never applied by the engine."""

    op_type: str
    strategy: str
    computable: bool = True

    @classmethod
    def from_json(cls, data: Mapping[str, Any]) -> "AutoFix":
        return cls(
            op_type=str(data["opType"]),
            strategy=str(data["strategy"]),
            computable=bool(data.get("computable", True)),
        )

    def to_json(self) -> Dict[str, Any]:
        return {"opType": self.op_type, "strategy": self.strategy, "computable": self.computable}


@dataclass(frozen=True)
class Check:
    """One typed measurement, verbatim from the pack plus typed accessors."""

    type: str
    params: Mapping[str, Any]

    @classmethod
    def from_json(cls, data: Mapping[str, Any]) -> "Check":
        return cls(type=str(data["type"]), params=dict(data))

    def to_json(self) -> Dict[str, Any]:
        return dict(self.params)

    # -- typed accessors ---------------------------------------------------
    def int_param(self, name: str) -> int:
        return require_int(self.params.get(name), "check.%s" % name)

    def opt_int_param(self, name: str, default: int = 0) -> int:
        value = self.params.get(name)
        if value is None:
            return default
        return require_int(value, "check.%s" % name)

    def str_param(self, name: str, default: Optional[str] = None) -> str:
        value = self.params.get(name, default)
        if not isinstance(value, str):
            raise PackLoadError("check.%s must be a string, got %r" % (name, value))
        return value

    def ratio_param(self, name: str) -> Ratio:
        value = self.params.get(name)
        if not isinstance(value, Mapping):
            raise PackLoadError("check.%s must be a {num, den} ratio" % name)
        return Ratio.from_json(value, "check.%s" % name)

    def opt_ratio_param(self, name: str) -> Optional[Ratio]:
        value = self.params.get(name)
        if value is None:
            return None
        if not isinstance(value, Mapping):
            raise PackLoadError("check.%s must be a {num, den} ratio" % name)
        return Ratio.from_json(value, "check.%s" % name)

    def list_param(self, name: str, default: Sequence[str] = ()) -> Tuple[str, ...]:
        value = self.params.get(name)
        if value is None:
            return tuple(default)
        if not isinstance(value, (list, tuple)):
            raise PackLoadError("check.%s must be an array" % name)
        return tuple(str(v) for v in value)

    def bool_param(self, name: str, default: bool = False) -> bool:
        value = self.params.get(name, default)
        return bool(value)

    def mapping_param(self, name: str) -> Mapping[str, Any]:
        value = self.params.get(name) or {}
        if not isinstance(value, Mapping):
            raise PackLoadError("check.%s must be an object" % name)
        return value


@dataclass(frozen=True)
class Rule:
    """A resolved rule: pack metadata folded in, overrides applied."""

    id: str
    pack_id: str
    severity: str
    title: str
    message: str
    check: Check
    cite: str  # clause reference as written in the pack
    cite_full: str  # citations_base + " " + cite, which is what the UI shows
    fix: str
    confidence: str
    when: Mapping[str, Mapping[str, Any]] = field(default_factory=dict)
    cite_url: Optional[str] = None
    autofix: Optional[AutoFix] = None
    weight: Optional[int] = None
    group: Optional[str] = None
    hard: bool = False
    tags: Tuple[str, ...] = ()
    notes: Optional[str] = None
    #: ``relax-to-warn`` came from a child pack's ``overrides``.
    relaxed_to_warn: bool = False
    #: Position in the resolved load order — the engine's deterministic sort key.
    order: int = 0
    #: True when the owning pack declares ``scoring`` (Vastu today).
    scoring: bool = False


@dataclass(frozen=True)
class ScoringMode:
    enforce: bool
    severity_ceiling: str
    score: bool


@dataclass(frozen=True)
class Scoring:
    """A scoring pack's aggregate contract (Vastu). Weights live on the rules."""

    mode_field: str
    scale_min: int
    scale_max: int
    aggregate: str
    rounding: str
    modes: Mapping[str, ScoringMode]
    groups: Tuple[Tuple[str, str, str], ...] = ()  # (id, label, description)

    @classmethod
    def from_json(cls, data: Mapping[str, Any], pack_id: str) -> "Scoring":
        if data.get("aggregate") != "weighted-mean":
            raise PackLoadError(
                "scoring.aggregate %r is not implemented (only weighted-mean)"
                % (data.get("aggregate"),),
                pack_id=pack_id,
            )
        if data.get("rounding") != "half-up":
            raise PackLoadError(
                "scoring.rounding %r is not implemented (only half-up)" % (data.get("rounding"),),
                pack_id=pack_id,
            )
        modes = {
            name: ScoringMode(
                enforce=bool(spec["enforce"]),
                severity_ceiling=str(spec["severityCeiling"]),
                score=bool(spec["score"]),
            )
            for name, spec in (data.get("modes") or {}).items()
        }
        groups = tuple(
            (str(g["id"]), str(g["label"]), str(g.get("description", "")))
            for g in (data.get("groups") or ())
        )
        scale = data.get("scale") or {}
        return cls(
            mode_field=str(data.get("modeField")),
            scale_min=require_int(scale.get("min"), "scoring.scale.min"),
            scale_max=require_int(scale.get("max"), "scoring.scale.max"),
            aggregate="weighted-mean",
            rounding="half-up",
            modes=modes,
            groups=groups,
        )

    def group_ids(self) -> FrozenSet[str]:
        return frozenset(g[0] for g in self.groups)


@dataclass(frozen=True)
class Vocabulary:
    """Merged pack vocabulary — the regulatory judgements the code must not hard-code."""

    habitable_room_types: FrozenSet[str] = frozenset()
    wet_room_types: FrozenSet[str] = frozenset()
    open_room_types: FrozenSet[str] = frozenset()
    far_exclusions: Tuple[str, ...] = ()
    coverage_inclusions: Tuple[str, ...] = ()
    #: vocabulary key -> the pack that supplied the winning value, for the report.
    sources: Mapping[str, str] = field(default_factory=dict)

    def to_json(self) -> Dict[str, Any]:
        return {
            "habitableRoomTypes": sorted(self.habitable_room_types),
            "wetRoomTypes": sorted(self.wet_room_types),
            "openRoomTypes": sorted(self.open_room_types),
            "farExclusions": list(self.far_exclusions),
            "coverageInclusions": list(self.coverage_inclusions),
            "sources": dict(sorted(self.sources.items())),
        }


@dataclass(frozen=True)
class Pack:
    """One pack file, validated, with its own rules unresolved."""

    id: str
    id_prefix: str
    version: str
    title: str
    authority: str
    citations_base: str
    confidence_default: str
    disclaimer: str
    extends: Optional[str]
    review_status: str
    jurisdiction: Mapping[str, Any]
    raw: Mapping[str, Any]
    scoring: Optional[Scoring] = None

    @property
    def is_scoring(self) -> bool:
        return self.scoring is not None


# ---------------------------------------------------------------------------
# Resolved pack set
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PackSet:
    """The immutable, fully resolved rule set the evaluator runs against."""

    load_order: Tuple[str, ...]
    packs: Mapping[str, Pack]
    rules: Tuple[Rule, ...]
    vocabulary: Vocabulary
    room_types: FrozenSet[str]
    disabled: Mapping[str, str] = field(default_factory=dict)  # ruleId -> reason
    notes: Tuple[str, ...] = ()

    @property
    def pack_versions(self) -> Dict[str, str]:
        """``{packId: version}`` — pinned into every compliance report so an old
        report can still be re-explained by the exact rules that produced it."""
        return {pid: self.packs[pid].version for pid in self.load_order}

    def rule(self, rule_id: str) -> Optional[Rule]:
        for candidate in self.rules:
            if candidate.id == rule_id:
                return candidate
        return None

    def require_rule(self, rule_id: str) -> Rule:
        found = self.rule(rule_id)
        if found is None:
            raise PackLoadError("no rule %r in the loaded pack set" % rule_id, rule_id=rule_id)
        return found

    def scoring_packs(self) -> Tuple[Pack, ...]:
        return tuple(self.packs[pid] for pid in self.load_order if self.packs[pid].is_scoring)

    def disclaimers(self) -> Tuple[Tuple[str, str], ...]:
        """``(packId, disclaimer)`` pairs the UI and every export must show verbatim."""
        return tuple((pid, self.packs[pid].disclaimer) for pid in self.load_order)


# ---------------------------------------------------------------------------
# Loader
# ---------------------------------------------------------------------------


def rulepack_dir(root: Optional[str] = None) -> str:
    """Where the packs live.

    Honours ``GARH_RULEPACK_DIR``, then ``RULEPACK_DIR``, then ``GARH_ROOT``.

    ``RULEPACK_DIR`` is the name ``docker-compose.yml`` and ``.env.example``
    actually set; ``GARH_RULEPACK_DIR`` is the name the API's catalog router and
    the seed script read. Honouring both means the compose-supplied value is not
    silently ignored — before this, a container pointed at a non-default pack
    directory fell through to the filesystem walk below and happened to land on
    ``/app/rulepacks`` by luck. ``garh_api.seed.catalog`` uses the same order.
    """
    if root:
        return root
    override = os.environ.get("GARH_RULEPACK_DIR") or os.environ.get("RULEPACK_DIR")
    if override:
        return override
    base = os.environ.get("GARH_ROOT")
    if not base:
        here = os.path.abspath(os.path.dirname(__file__))
        # garh_rules -> apps/api -> apps -> <repo root>
        base = os.path.abspath(os.path.join(here, "..", "..", ".."))
    return os.path.join(base, "rulepacks")


class PackLoader:
    """Reads and resolves packs. One instance per pack directory; reusable."""

    def __init__(self, root: Optional[str] = None) -> None:
        self.dir = rulepack_dir(root)
        self.schema_path = os.path.join(self.dir, "schema", "rulepack.schema.json")
        self._schema: Optional[Mapping[str, Any]] = None
        self._validator: Optional[SchemaValidator] = None
        self._raw: Dict[str, Mapping[str, Any]] = {}

    # -- schema ------------------------------------------------------------
    @property
    def schema(self) -> Mapping[str, Any]:
        if self._schema is None:
            self._schema = self._read_json(self.schema_path, "rule pack schema")
            self._check_engine_covers_schema(self._schema)
        return self._schema

    @property
    def validator(self) -> SchemaValidator:
        if self._validator is None:
            self._validator = SchemaValidator(self.schema)
        return self._validator

    def schema_enum(self, def_name: str) -> Tuple[str, ...]:
        node = (self.schema.get("$defs") or {}).get(def_name) or {}
        values = node.get("enum")
        if not values:
            raise PackLoadError("schema $defs.%s has no enum" % def_name)
        return tuple(str(v) for v in values)

    def _check_engine_covers_schema(self, schema: Mapping[str, Any]) -> None:
        """Fail if the schema declares something the engine cannot bind or run.

        This is the guard that makes "add a field, bump schemaVersion, teach the
        engine" enforceable rather than aspirational: a new ``when`` field or check
        type in the schema breaks the load until the engine grows to match.
        """
        defs = schema.get("$defs") or {}
        declared_fields = frozenset((defs.get("predicate") or {}).get("properties") or {})
        unbindable = sorted(declared_fields - BOUND_WHEN_FIELDS)
        if unbindable:
            raise PackLoadError(
                "the pack schema declares `when` field(s) %s that this engine cannot bind — "
                "teach garh_rules.predicates before shipping the schema change"
                % ", ".join(unbindable)
            )
        from .checks import CHECK_TYPES  # local import: checks imports packs' Check

        declared_checks = frozenset(
            ((defs.get("check") or {}).get("properties") or {}).get("type", {}).get("enum") or ()
        )
        missing = sorted(declared_checks - frozenset(CHECK_TYPES))
        if missing:
            raise PackLoadError(
                "the pack schema declares check type(s) %s that this engine does not "
                "implement" % ", ".join(missing)
            )
        declared_fns = frozenset(
            ((defs.get("check_custom") or {}).get("properties") or {}).get("fn", {}).get("enum")
            or ()
        )
        missing_fns = sorted(declared_fns - frozenset(CUSTOM_FN_SCOPES))
        if missing_fns:
            raise PackLoadError(
                "the pack schema declares custom fn(s) %s with no registered "
                "implementation" % ", ".join(missing_fns)
            )

    # -- files -------------------------------------------------------------
    def _read_json(self, path: str, what: str) -> Mapping[str, Any]:
        try:
            with open(path, "r", encoding="utf-8") as handle:
                data = json.load(handle)
        except FileNotFoundError as exc:
            raise PackLoadError("%s not found at %s" % (what, path)) from exc
        except ValueError as exc:
            raise PackLoadError("%s at %s is not valid JSON: %s" % (what, path, exc)) from exc
        if not isinstance(data, dict):
            raise PackLoadError("%s at %s must be a JSON object" % (what, path))
        return data

    def read_pack(self, pack_id: str) -> Mapping[str, Any]:
        """Raw pack JSON, schema-validated. Cached per loader instance."""
        cached = self._raw.get(pack_id)
        if cached is not None:
            return cached
        if not _SAFE_PACK_ID(pack_id):
            raise PackLoadError("unsafe pack id %r" % pack_id, pack_id=pack_id)
        data = self._read_json(os.path.join(self.dir, "%s.json" % pack_id), "rule pack %r" % pack_id)
        declared = data.get("schemaVersion")
        if declared != SUPPORTED_SCHEMA_VERSION:
            raise PackLoadError(
                "pack %r declares schemaVersion %r; this engine implements %d only"
                % (pack_id, declared, SUPPORTED_SCHEMA_VERSION),
                pack_id=pack_id,
            )
        errors = self.validator.validate(data, path=pack_id)
        if errors:
            raise SchemaValidationError(
                "pack %r does not satisfy rulepack.schema.json (%d problem(s))"
                % (pack_id, len(errors)),
                pack_id=pack_id,
                errors=errors[:20],
            )
        if data.get("pack") != pack_id:
            raise PackLoadError(
                "pack field %r does not match file name %r" % (data.get("pack"), pack_id),
                pack_id=pack_id,
            )
        self._raw[pack_id] = data
        return data

    # -- resolution --------------------------------------------------------
    def chain(self, pack_id: str) -> Tuple[str, ...]:
        """The ``extends`` chain, root first. A cycle is a load error."""
        order: List[str] = []
        seen: List[str] = []
        cursor: Optional[str] = pack_id
        while cursor is not None:
            if cursor in seen:
                raise PackLoadError(
                    "extends cycle: %s" % " -> ".join(seen + [cursor]), pack_id=pack_id
                )
            seen.append(cursor)
            order.insert(0, cursor)
            parent = self.read_pack(cursor).get("extends")
            cursor = str(parent) if parent is not None else None
        return tuple(order)

    def load(self, pack_ids: Sequence[str]) -> PackSet:
        """Resolve ``pack_ids`` (each expanded through its parents) into one set."""
        if not pack_ids:
            raise PackLoadError("no packs requested — a compliance run needs at least one pack")
        order: List[str] = []
        for pack_id in pack_ids:
            for member in self.chain(pack_id):
                if member not in order:
                    order.append(member)

        packs: Dict[str, Pack] = {}
        for pack_id in order:
            packs[pack_id] = self._build_pack(pack_id)

        rules: List[Rule] = []
        owner: Dict[str, str] = {}
        for pack_id in order:
            pack = packs[pack_id]
            for raw_rule in pack.raw["rules"]:
                rule_id = str(raw_rule["id"])
                if rule_id in owner:
                    raise PackLoadError(
                        "rule id %s is defined in both %s and %s — ids are globally unique so a "
                        "six-month-old compliance report still means one thing"
                        % (rule_id, owner[rule_id], pack_id),
                        pack_id=pack_id,
                        rule_id=rule_id,
                    )
                owner[rule_id] = pack_id
                rules.append(self._build_rule(pack, raw_rule, len(rules)))

        rules, autofix_notes = self._vet_autofix(rules)
        autofix_notes.extend(self._vet_room_type_reachability(rules))
        rules_by_id = {r.id: r for r in rules}
        disabled: Dict[str, str] = {}
        relaxed: Dict[str, str] = {}
        for pack_id in order:
            for override in packs[pack_id].raw.get("overrides") or ():
                target = str(override["ruleId"])
                action = str(override["action"])
                reason = str(override["reason"])
                if target not in rules_by_id:
                    raise PackLoadError(
                        "pack %s overrides rule %s, which is not in the loaded chain"
                        % (pack_id, target),
                        pack_id=pack_id,
                        rule_id=target,
                    )
                if action == "replace":
                    replacement = str(override.get("replacedBy") or "")
                    if replacement not in rules_by_id:
                        raise PackLoadError(
                            "pack %s replaces %s with %r, which does not exist"
                            % (pack_id, target, replacement),
                            pack_id=pack_id,
                            rule_id=target,
                        )
                    disabled[target] = "replaced by %s: %s" % (replacement, reason)
                elif action == "disable":
                    disabled[target] = reason
                elif action == "relax-to-warn":
                    relaxed[target] = reason
                else:  # pragma: no cover - schema-constrained
                    raise PackLoadError(
                        "unknown override action %r" % action, pack_id=pack_id, rule_id=target
                    )

        resolved: List[Rule] = []
        for rule in rules:
            if rule.id in disabled:
                continue
            if rule.id in relaxed and rule.severity != "warn":
                rule = replace(rule, severity="warn", relaxed_to_warn=True)
            resolved.append(replace(rule, order=len(resolved)))

        return PackSet(
            load_order=tuple(order),
            packs=packs,
            rules=tuple(resolved),
            vocabulary=self._merge_vocabulary(order, packs),
            room_types=frozenset(self.schema_enum("roomType")),
            disabled=disabled,
            notes=tuple(autofix_notes),
        )

    # -- builders ----------------------------------------------------------
    def _build_pack(self, pack_id: str) -> Pack:
        raw = self.read_pack(pack_id)
        scoring_raw = raw.get("scoring")
        scoring = Scoring.from_json(scoring_raw, pack_id) if scoring_raw else None
        if scoring is not None and scoring.mode_field != "vastuMode":
            raise PackLoadError(
                "scoring.modeField %r is not implemented (only vastuMode)" % scoring.mode_field,
                pack_id=pack_id,
            )
        return Pack(
            id=pack_id,
            id_prefix=str(raw["idPrefix"]),
            version=str(raw["version"]),
            title=str(raw["title"]),
            authority=str(raw["authority"]),
            citations_base=str(raw["citations_base"]),
            confidence_default=str(raw["confidenceDefault"]),
            disclaimer=str(raw["disclaimer"]),
            extends=str(raw["extends"]) if raw.get("extends") is not None else None,
            review_status=str((raw.get("review") or {}).get("status", "unreviewed")),
            jurisdiction=dict(raw.get("jurisdiction") or {}),
            raw=raw,
            scoring=scoring,
        )

    def _build_rule(self, pack: Pack, raw: Mapping[str, Any], order: int) -> Rule:
        rule_id = str(raw["id"])
        if rule_id.split(".")[0] != pack.id_prefix:
            raise PackLoadError(
                "rule id %s does not start with the pack's idPrefix %r" % (rule_id, pack.id_prefix),
                pack_id=pack.id,
                rule_id=rule_id,
            )
        when = self._validate_when(raw.get("when") or {}, pack.id, rule_id)
        check = Check.from_json(raw["check"])
        self._validate_check(check, pack, rule_id)

        weight = raw.get("weight")
        group = raw.get("group")
        if pack.is_scoring:
            if weight is None:
                raise PackLoadError(
                    "scoring pack rule has no weight", pack_id=pack.id, rule_id=rule_id
                )
            assert pack.scoring is not None
            if group is None or str(group) not in pack.scoring.group_ids():
                raise PackLoadError(
                    "rule group %r is not declared in scoring.groups" % (group,),
                    pack_id=pack.id,
                    rule_id=rule_id,
                )
        cite = str(raw["cite"])
        return Rule(
            id=rule_id,
            pack_id=pack.id,
            severity=str(raw["severity"]),
            title=str(raw["title"]),
            message=str(raw["message"]),
            check=check,
            cite=cite,
            cite_full=("%s %s" % (pack.citations_base, cite)).strip(),
            fix=str(raw["fix"]),
            confidence=str(raw.get("confidence") or pack.confidence_default),
            when=when,
            cite_url=str(raw["citeUrl"]) if raw.get("citeUrl") else None,
            autofix=AutoFix.from_json(raw["autofix"]) if raw.get("autofix") else None,
            weight=require_int(weight, "rule.weight") if weight is not None else None,
            group=str(group) if group is not None else None,
            hard=bool(raw.get("hard", False)),
            tags=tuple(str(t) for t in (raw.get("tags") or ())),
            notes=str(raw["notes"]) if raw.get("notes") else None,
            order=order,
            scoring=pack.is_scoring,
        )

    def _validate_when(
        self, when: Mapping[str, Any], pack_id: str, rule_id: str
    ) -> Mapping[str, Mapping[str, Any]]:
        out: Dict[str, Mapping[str, Any]] = {}
        for field_name, predicate in when.items():
            if field_name not in BOUND_WHEN_FIELDS:
                raise PackLoadError(
                    "`when` field %r is not in the engine's closed context field set — a typo "
                    "here would make the rule apply to every plot or to none"
                    % field_name,
                    pack_id=pack_id,
                    rule_id=rule_id,
                )
            if not isinstance(predicate, Mapping) or not predicate:
                raise PackLoadError(
                    "`when.%s` must be a non-empty predicate object" % field_name,
                    pack_id=pack_id,
                    rule_id=rule_id,
                )
            for operator in predicate:
                if operator not in OPERATORS:
                    raise PackLoadError(
                        "`when.%s` uses unknown operator %r (the six are %s)"
                        % (field_name, operator, ", ".join(sorted(OPERATORS))),
                        pack_id=pack_id,
                        rule_id=rule_id,
                    )
            out[field_name] = dict(predicate)
        return out

    def _validate_check(self, check: Check, pack: Pack, rule_id: str) -> None:
        from .checks import CHECK_TYPES

        if check.type not in CHECK_TYPES:
            raise PackLoadError(
                "unknown check type %r — the engine implements %s"
                % (check.type, ", ".join(sorted(CHECK_TYPES))),
                pack_id=pack.id,
                rule_id=rule_id,
            )
        if check.type == "custom":
            fn = check.str_param("fn")
            if fn not in CUSTOM_FN_SCOPES:
                raise PackLoadError(
                    "unknown custom fn %r — registered: %s"
                    % (fn, ", ".join(sorted(CUSTOM_FN_SCOPES))),
                    pack_id=pack.id,
                    rule_id=rule_id,
                )
            declared_scope = check.str_param("scope")
            if declared_scope != CUSTOM_FN_SCOPES[fn]:
                raise PackLoadError(
                    "custom fn %r is a %s-scope function; the rule declares scope %r"
                    % (fn, CUSTOM_FN_SCOPES[fn], declared_scope),
                    pack_id=pack.id,
                    rule_id=rule_id,
                )
            if fn == "rwh_required":
                flag = str(check.mapping_param("args").get("flag") or "")
                if flag != "rwhDeclared":
                    raise PackLoadError(
                        "rwh_required args.flag must name a boolean profile field; %r is not one "
                        "(the profile exposes rwhDeclared)" % flag,
                        pack_id=pack.id,
                        rule_id=rule_id,
                    )
            if fn == "brahmasthan_open":
                args = check.mapping_param("args")
                if not isinstance(args.get("maxEnclosedRatio"), Mapping):
                    raise PackLoadError(
                        "brahmasthan_open needs args.maxEnclosedRatio as a {num, den} ratio",
                        pack_id=pack.id,
                        rule_id=rule_id,
                    )
                Ratio.from_json(args["maxEnclosedRatio"], "args.maxEnclosedRatio")
        elif check.type == "floors_max":
            unsupported = sorted(
                set(check.list_param("counts")) - set(SUPPORTED_EXTRA_FLOOR_KINDS)
            )
            if unsupported:
                raise PackLoadError(
                    "floors_max.counts %s cannot be counted: the EvaluationContext carries no "
                    "such level, so the floor count would be silently too low. Counting is "
                    "limited to %s."
                    % (", ".join(unsupported), " / ".join(SUPPORTED_EXTRA_FLOOR_KINDS)),
                    pack_id=pack.id,
                    rule_id=rule_id,
                )
        elif check.type == "ventilation_ratio_min":
            kinds = check.list_param("countKinds", DEFAULT_COUNT_KINDS)
            if tuple(sorted(kinds)) != tuple(sorted(DEFAULT_COUNT_KINDS)):
                raise PackLoadError(
                    "ventilation_ratio_min.countKinds %s cannot be verified: the context supplies "
                    "one pre-summed room.ventilationOpeningAreaMm2, which cannot be split by "
                    "opening kind. Only %s is supported."
                    % (", ".join(kinds), " + ".join(DEFAULT_COUNT_KINDS)),
                    pack_id=pack.id,
                    rule_id=rule_id,
                )
            if check.params.get("ratio") is None and check.params.get("minAreaMm2") is None:
                raise PackLoadError(
                    "ventilation_ratio_min needs a ratio, a minAreaMm2, or both",
                    pack_id=pack.id,
                    rule_id=rule_id,
                )
        elif check.type == "zone_check":
            mode = check.str_param("mode")
            target_kind = str(check.mapping_param("target").get("kind"))
            if mode == "facing" and target_kind != "opening":
                raise PackLoadError(
                    "zone_check mode=facing needs target.kind 'opening': outwardNormalDeg is the "
                    "only facing the model projection carries, so a %r target has no direction"
                    % target_kind,
                    pack_id=pack.id,
                    rule_id=rule_id,
                )
            if not check.params.get("allow") and not check.params.get("deny"):
                raise PackLoadError(
                    "zone_check needs `allow`, `deny`, or both", pack_id=pack.id, rule_id=rule_id
                )
            if mode == "facing":
                for key in ("allow", "deny"):
                    if "C" in check.list_param(key):
                        raise PackLoadError(
                            "zone_check mode=facing cannot use the centre cell 'C' in %s" % key,
                            pack_id=pack.id,
                            rule_id=rule_id,
                        )
        elif check.type == "setback_min":
            measure = check.str_param("measure", "to-building-line")
            if measure not in ("to-building-line", "to-projection"):
                raise PackLoadError(
                    "setback_min.measure %r is not implemented" % measure,
                    pack_id=pack.id,
                    rule_id=rule_id,
                )
        elif check.type == "parking_min":
            basis = check.str_param("basis")
            if basis not in ("dwelling", "built-up-area"):
                raise PackLoadError(
                    "parking_min.basis %r is not implemented" % basis,
                    pack_id=pack.id,
                    rule_id=rule_id,
                )

    def _vet_autofix(self, rules: Sequence[Rule]) -> Tuple[List[Rule], List[str]]:
        """Validate ``autofix.opType`` against the generated op catalogue.

        The schema asks for this check "at pack load". Two judgement calls:

        * ``garh_model`` owns the catalogue and is importable in both the API and
          worker images, but this package must not *require* it — the solver critic
          can be exercised without it. A missing catalogue records a note saying the
          check did not run; it is never reported as having passed.
        * An unknown op type does **not** fail the load. ``autofix`` is advisory
          (the client computes the payload and always shows a reversible diff first),
          so a bad op type cannot produce a wrong verdict — only a button that
          cannot be built. Failing the load would take a whole city pack offline over
          a hint. Instead the rule keeps its hint text and is forced to
          ``computable: false``, so the button never appears, and every offender is
          named in :attr:`PackSet.notes`, which surfaces in ``report.warnings``.
        """
        op_types = _op_catalogue()
        if op_types is None:
            return (
                list(rules),
                [
                    "autofix.opType was NOT validated against the op catalogue: garh_model is not "
                    "importable from here. Fix the PYTHONPATH to restore the check."
                ],
            )
        out: List[Rule] = []
        broken: Dict[str, List[str]] = {}
        for rule in rules:
            fix = rule.autofix
            if fix is not None and fix.op_type not in op_types:
                broken.setdefault(fix.op_type, []).append(rule.id)
                if fix.computable:
                    rule = replace(rule, autofix=replace(fix, computable=False))
            out.append(rule)
        notes: List[str] = []
        for op_type, rule_ids in sorted(broken.items()):
            notes.append(
                "autofix.opType %r is not in the op catalogue, so the Fix-it button is disabled "
                "for %d rule(s) (%s%s). The checks themselves are unaffected."
                % (
                    op_type,
                    len(rule_ids),
                    ", ".join(sorted(rule_ids)[:3]),
                    ", ..." if len(rule_ids) > 3 else "",
                )
            )
        return out, notes

    def _vet_room_type_reachability(self, rules: Sequence[Rule]) -> List[str]:
        """Name every rule keyed on a room type the model core cannot emit.

        The other half of "no rule runs unseen". A rule whose ``when.roomType`` (or
        whose ``zone_check`` target) selects a type that never appears on a real room
        can only ever report ``not_applicable`` — it looks like a checked rule in the
        pack and is dead code in practice.

        A **note**, not a load error, and the direction of the fix is why:
        ``kitchen_dining`` is a genuine NBC category, so the pack is right and the
        model core is behind. Failing the load would take the whole city pack offline
        over a room type nobody has drawn yet. Silence, though, would let NBC's
        combined kitchen-dining minimum sit in the pack looking enforced forever.

        Skipped without comment when ``garh_model`` is not importable —
        :meth:`_vet_autofix` already reports that in the same run.
        """
        model_types = _model_room_types()
        if model_types is None:
            return []
        unreachable: Dict[str, List[str]] = {}
        dead: List[str] = []
        for rule in rules:
            referenced = _room_types_referenced(rule)
            if not referenced:
                continue
            missing = sorted(t for t in referenced if t not in model_types)
            for room_type in missing:
                unreachable.setdefault(room_type, []).append(rule.id)
            if len(missing) == len(referenced):
                dead.append(rule.id)
        notes: List[str] = []
        for room_type, rule_ids in sorted(unreachable.items()):
            notes.append(
                "room type %r is selected by %d rule(s) (%s) but no model room can carry it: "
                "garh_model.ROOM_TYPES has no such type and garh_rules.context."
                "ROOM_TYPE_ALIASES maps nothing onto it."
                % (room_type, len(rule_ids), ", ".join(sorted(rule_ids)))
            )
        if dead:
            notes.append(
                "rule(s) %s can never be evaluated on a model from garh_model: every room type "
                "they select is unreachable. Either the model core needs the type or the rule "
                "needs retiring — it is not being checked today."
                % ", ".join(sorted(dead))
            )
        return notes

    def _merge_vocabulary(self, order: Sequence[str], packs: Mapping[str, Pack]) -> Vocabulary:
        """Merge key by key: a child key replaces the parent key wholesale.

        Never element-wise. "Is a study habitable" is one editorial decision, and a
        child pack that answers it answers all of it — a union would quietly
        re-admit a type the child deliberately dropped.
        """
        keys = ("habitableRoomTypes", "wetRoomTypes", "openRoomTypes", "farExclusions", "coverageInclusions")
        winning: Dict[str, Any] = {}
        sources: Dict[str, str] = {}
        for pack_id in order:
            vocabulary = packs[pack_id].raw.get("vocabulary") or {}
            for key in keys:
                if key in vocabulary:
                    winning[key] = vocabulary[key]
                    sources[key] = pack_id
        return Vocabulary(
            habitable_room_types=frozenset(
                str(v) for v in winning.get("habitableRoomTypes", ())
            ),
            wet_room_types=frozenset(str(v) for v in winning.get("wetRoomTypes", ())),
            open_room_types=frozenset(str(v) for v in winning.get("openRoomTypes", ())),
            far_exclusions=tuple(str(v) for v in winning.get("farExclusions", ())),
            coverage_inclusions=tuple(str(v) for v in winning.get("coverageInclusions", ())),
            sources=sources,
        )


def _SAFE_PACK_ID(pack_id: str) -> bool:
    return bool(pack_id) and all(c.isalnum() or c == "-" for c in pack_id) and pack_id[0].isalpha()


def _room_types_referenced(rule: Rule) -> FrozenSet[str]:
    """Every room type a rule selects on — ``when.roomType`` plus a zone target."""
    found: List[str] = []
    predicate = rule.when.get("roomType")
    if predicate:
        if "eq" in predicate:
            found.append(str(predicate["eq"]))
        found.extend(str(v) for v in (predicate.get("in") or ()))
    if rule.check.type == "zone_check":
        target = rule.check.params.get("target")
        if isinstance(target, Mapping):
            found.extend(str(v) for v in (target.get("roomTypes") or ()))
    return frozenset(found)


def _garh_model_attr(name: str) -> Optional[FrozenSet[str]]:
    """A string tuple exported by ``garh_model``, or ``None`` when it is not importable.

    ``garh_model`` ships in the same distribution as this package but is not a
    dependency of it: the solver critic and the fixture suite must be able to run
    the engine without it. So the import is optional, never raises, and a caller
    that needs it reports that the check did not run rather than that it passed.
    """
    try:
        module = importlib.import_module("garh_model")
    except Exception:  # pragma: no cover - depends on PYTHONPATH
        return None
    values = getattr(module, name, None)
    if not values:  # pragma: no cover - garh_model would have to change shape
        return None
    return frozenset(str(v) for v in values)


def _op_catalogue() -> Optional[FrozenSet[str]]:
    """``garh_model.OP_TYPES`` if importable, else ``None``. Never raises."""
    return _garh_model_attr("OP_TYPES")


def _model_room_types() -> Optional[FrozenSet[str]]:
    """The room types a real model can carry: ``garh_model.ROOM_TYPES`` after the
    alias table in :mod:`garh_rules.context` has been applied (the engine normalises
    every incoming room type through it, so an aliased type *is* reachable)."""
    raw = _garh_model_attr("ROOM_TYPES")
    if raw is None:
        return None
    from .context import ROOM_TYPE_ALIASES

    return frozenset(ROOM_TYPE_ALIASES.get(t, t) for t in raw)


# ---------------------------------------------------------------------------
# Cache — pack loading is the only I/O, and it happens once
# ---------------------------------------------------------------------------

_CACHE: Dict[Tuple[str, Tuple[str, ...]], PackSet] = {}


def load_pack_set(pack_ids: Iterable[str], *, root: Optional[str] = None) -> PackSet:
    """Load (and memoise) a resolved pack set.

    Memoised deliberately and without an mtime check: the evaluator runs debounced
    on every edit and inside the solver critic, so it must never stat a file.
    :func:`clear_pack_cache` is the dev escape hatch.
    """
    ids = tuple(pack_ids)
    key = (rulepack_dir(root), ids)
    hit = _CACHE.get(key)
    if hit is None:
        hit = PackLoader(root).load(ids)
        _CACHE[key] = hit
    return hit


def clear_pack_cache() -> None:
    """Drop every memoised pack set (used by tests and by a dev reload endpoint)."""
    _CACHE.clear()
