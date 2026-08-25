from __future__ import annotations

"""The loader's job is to REFUSE, loudly. This module is that promise, tested.

Playbook §6: "rejects unknown ``when`` fields and unknown check types LOUDLY (a
silently-ignored rule is a compliance lie)". Every test below is one way a pack
can name something the engine cannot evaluate, and every one of them must raise
:class:`~garh_rules.errors.PackLoadError` — never load with the rule quietly
dropped, and never load with the rule quietly passing.

The shipped packs are also asserted here (they load, they resolve root-first,
every value is marked ``seed``), because "the packs still load" is the cheapest
regression test in the repo and the one that breaks most often while authoring.
"""

import json
import os
from typing import Any, Dict, List

import pytest

from garh_rules import PackLoader, clear_pack_cache, load_pack_set
from garh_rules.errors import PackLoadError, SchemaValidationError
from garh_rules.packs import (
    DEFAULT_COUNT_KINDS,
    ENGINE_LIMITS,
    SUPPORTED_EXTRA_FLOOR_KINDS,
    SUPPORTED_SCHEMA_VERSION,
    PackSet,
)

from .conftest import (
    PACK_IDS,
    RULEPACK_DIR,
    copy_real_pack,
    minimal_pack,
    minimal_rule,
    read_json,
    write_pack_dir,
)


def load_one(pack: Dict[str, Any], *more: Dict[str, Any], request: str = "") -> PackSet:
    """Write pack(s) to a temp dir and load the first (or ``request``)."""
    root = write_pack_dir(pack, *more)
    return PackLoader(root).load([request or pack["pack"]])


# ---------------------------------------------------------------------------
# The shipped packs
# ---------------------------------------------------------------------------


class TestShippedPacks:
    def test_every_pack_loads_with_its_parents(self) -> None:
        for pack_id in PACK_IDS:
            pack_set = load_pack_set([pack_id], root=RULEPACK_DIR)
            assert pack_set.rules, pack_id
            assert pack_id in pack_set.load_order

    def test_the_whole_set_resolves_root_first(self) -> None:
        pack_set = load_pack_set(PACK_IDS, root=RULEPACK_DIR)
        assert pack_set.load_order[0] == "nbc-core"
        assert len(pack_set.rules) == 118
        # A child's rules always come after its parent's, which is the order a
        # compliance annexure reads in.
        seen: List[str] = []
        for rule in pack_set.rules:
            if rule.pack_id not in seen:
                seen.append(rule.pack_id)
        assert seen == list(pack_set.load_order)

    def test_city_packs_extend_nbc_core(self) -> None:
        loader = PackLoader(RULEPACK_DIR)
        for pack_id in ("blr", "ncr", "hyd"):
            assert loader.chain(pack_id) == ("nbc-core", pack_id)
        assert loader.chain("nbc-core") == ("nbc-core",)

    def test_every_shipped_value_is_marked_seed(self) -> None:
        """Nothing in this repo has been checked against a primary document yet, and
        the UI has to be able to say so next to every number (§6)."""
        pack_set = load_pack_set(PACK_IDS, root=RULEPACK_DIR)
        assert {rule.confidence for rule in pack_set.rules} == {"seed"}
        for pack_id in pack_set.load_order:
            assert pack_set.packs[pack_id].review_status == "unreviewed"

    def test_pack_versions_and_disclaimers_are_exposed(self) -> None:
        pack_set = load_pack_set(["blr"], root=RULEPACK_DIR)
        assert pack_set.pack_versions == {
            "nbc-core": read_json(os.path.join(RULEPACK_DIR, "nbc-core.json"))["version"],
            "blr": read_json(os.path.join(RULEPACK_DIR, "blr.json"))["version"],
        }
        disclaimers = dict(pack_set.disclaimers())
        assert set(disclaimers) == {"nbc-core", "blr"}
        assert all(len(text) >= 20 for text in disclaimers.values())

    def test_citations_are_fully_qualified(self) -> None:
        pack_set = load_pack_set(["blr"], root=RULEPACK_DIR)
        rule = pack_set.require_rule("blr.far.road.9-18m")
        assert rule.cite_full.startswith("BBMP Building Bye-laws 2020")
        assert rule.cite_full.endswith(rule.cite)

    def test_vocabulary_comes_from_the_packs_not_the_code(self) -> None:
        pack_set = load_pack_set(["blr"], root=RULEPACK_DIR)
        vocabulary = pack_set.vocabulary
        assert "bedroom" in vocabulary.habitable_room_types
        assert "bath" not in vocabulary.habitable_room_types
        assert vocabulary.sources["habitableRoomTypes"] in pack_set.load_order

    def test_index_json_lists_exactly_the_pack_files(self) -> None:
        index = read_json(os.path.join(RULEPACK_DIR, "index.json"))
        listed = {entry["pack"] for entry in index["packs"]}
        assert listed == set(PACK_IDS)

    def test_cache_returns_the_same_object_until_cleared(self) -> None:
        first = load_pack_set(["nbc-core"], root=RULEPACK_DIR)
        assert load_pack_set(["nbc-core"], root=RULEPACK_DIR) is first
        clear_pack_cache()
        assert load_pack_set(["nbc-core"], root=RULEPACK_DIR) is not first

    def test_engine_limits_are_documented(self) -> None:
        assert SUPPORTED_SCHEMA_VERSION == 1
        assert ENGINE_LIMITS and all(isinstance(note, str) for note in ENGINE_LIMITS)


# ---------------------------------------------------------------------------
# Rejections — one per way a pack can lie
# ---------------------------------------------------------------------------


class TestRejectsUnevaluableRules:
    def test_unknown_check_type(self) -> None:
        pack = minimal_pack(rules=[minimal_rule(check={"type": "sunlight_hours_min", "valueMm": 1})])
        with pytest.raises(PackLoadError) as excinfo:
            load_one(pack)
        assert "sunlight_hours_min" in str(excinfo.value)

    def test_unknown_when_field(self) -> None:
        """The one the schema comment calls load-bearing: ``roadWidthmm`` would
        otherwise make the rule apply to every plot or to none."""
        pack = minimal_pack(rules=[minimal_rule(when={"roadWidthmm": {"lt": 9000}})])
        with pytest.raises(PackLoadError) as excinfo:
            load_one(pack)
        assert "roadWidthmm" in str(excinfo.value)

    def test_unknown_operator(self) -> None:
        pack = minimal_pack(rules=[minimal_rule(when={"roadWidthMm": {"between": [1, 2]}})])
        with pytest.raises(PackLoadError):
            load_one(pack)

    def test_unsupported_schema_version(self) -> None:
        pack = minimal_pack(schemaVersion=2)
        with pytest.raises(PackLoadError) as excinfo:
            load_one(pack)
        assert "schemaVersion" in str(excinfo.value)

    def test_a_float_anywhere_is_a_schema_failure(self) -> None:
        """The schema's promise: there is not one floating-point number in a valid pack."""
        pack = minimal_pack(rules=[minimal_rule(check={"type": "stair_riser_max", "valueMm": 190.5})])
        with pytest.raises(SchemaValidationError):
            load_one(pack)

    def test_floors_max_counts_the_context_cannot_supply(self) -> None:
        pack = minimal_pack(
            rules=[
                minimal_rule(
                    check={"type": "floors_max", "value": 3, "counts": ["mezzanine"]}
                )
            ]
        )
        with pytest.raises(PackLoadError) as excinfo:
            load_one(pack)
        assert "mezzanine" in str(excinfo.value)
        assert all(kind in str(excinfo.value) for kind in SUPPORTED_EXTRA_FLOOR_KINDS)

    def test_ventilation_count_kinds_it_cannot_re_partition(self) -> None:
        pack = minimal_pack(
            rules=[
                minimal_rule(
                    check={
                        "type": "ventilation_ratio_min",
                        "ratio": {"num": 1, "den": 10},
                        "countKinds": ["window"],
                    }
                )
            ]
        )
        with pytest.raises(PackLoadError) as excinfo:
            load_one(pack)
        assert "countKinds" in str(excinfo.value)

    def test_the_default_count_kinds_are_accepted(self) -> None:
        pack = minimal_pack(
            rules=[
                minimal_rule(
                    check={
                        "type": "ventilation_ratio_min",
                        "ratio": {"num": 1, "den": 10},
                        "countKinds": list(reversed(DEFAULT_COUNT_KINDS)),
                    }
                )
            ]
        )
        assert load_one(pack).rules  # order does not matter, membership does

    def test_ventilation_with_neither_parameter(self) -> None:
        pack = minimal_pack(rules=[minimal_rule(check={"type": "ventilation_ratio_min"})])
        with pytest.raises(PackLoadError):
            load_one(pack)

    def test_zone_check_facing_needs_an_opening_target(self) -> None:
        pack = minimal_pack(
            rules=[
                minimal_rule(
                    check={
                        "type": "zone_check",
                        "mode": "facing",
                        "target": {"kind": "room", "roomTypes": ["pooja"]},
                        "allow": ["NE"],
                    }
                )
            ]
        )
        with pytest.raises(PackLoadError) as excinfo:
            load_one(pack)
        assert "outwardNormalDeg" in str(excinfo.value)

    def test_zone_check_facing_cannot_name_the_centre_cell(self) -> None:
        pack = minimal_pack(
            rules=[
                minimal_rule(
                    check={
                        "type": "zone_check",
                        "mode": "facing",
                        "target": {"kind": "opening", "roles": ["main-entrance"]},
                        "allow": ["N", "C"],
                    }
                )
            ]
        )
        with pytest.raises(PackLoadError):
            load_one(pack)

    def test_zone_check_with_neither_allow_nor_deny(self) -> None:
        pack = minimal_pack(
            rules=[
                minimal_rule(
                    check={"type": "zone_check", "mode": "zone", "target": {"kind": "stair"}}
                )
            ]
        )
        with pytest.raises(PackLoadError):
            load_one(pack)

    def test_unknown_custom_fn(self) -> None:
        pack = minimal_pack(
            rules=[
                minimal_rule(
                    check={"type": "custom", "fn": "feng_shui_score", "scope": "project"}
                )
            ]
        )
        with pytest.raises(PackLoadError):
            load_one(pack)

    def test_custom_scope_must_match_the_registered_function(self) -> None:
        pack = minimal_pack(
            rules=[
                minimal_rule(
                    check={
                        "type": "custom",
                        "fn": "rwh_required",
                        "scope": "room",
                        "args": {"flag": "rwhDeclared"},
                    }
                )
            ]
        )
        with pytest.raises(PackLoadError) as excinfo:
            load_one(pack)
        assert "project-scope" in str(excinfo.value)

    def test_rwh_flag_must_name_a_field_the_profile_exposes(self) -> None:
        pack = minimal_pack(
            rules=[
                minimal_rule(
                    check={
                        "type": "custom",
                        "fn": "rwh_required",
                        "scope": "project",
                        "args": {"flag": "hasRainwaterTank"},
                    }
                )
            ]
        )
        with pytest.raises(PackLoadError) as excinfo:
            load_one(pack)
        assert "rwhDeclared" in str(excinfo.value)

    def test_brahmasthan_needs_its_ratio(self) -> None:
        pack = minimal_pack(
            rules=[
                minimal_rule(
                    check={
                        "type": "custom",
                        "fn": "brahmasthan_open",
                        "scope": "project",
                        "args": {},
                    }
                )
            ]
        )
        with pytest.raises(PackLoadError) as excinfo:
            load_one(pack)
        assert "maxEnclosedRatio" in str(excinfo.value)

    def test_unknown_setback_measure(self) -> None:
        pack = minimal_pack(
            rules=[
                minimal_rule(
                    check={
                        "type": "setback_min",
                        "edge": "front",
                        "valueMm": 1500,
                        "measure": "to-plot-line",
                    }
                )
            ]
        )
        with pytest.raises(PackLoadError):
            load_one(pack)

    def test_unknown_parking_basis(self) -> None:
        pack = minimal_pack(
            rules=[
                minimal_rule(
                    check={
                        "type": "parking_min",
                        "basis": "per-bedroom",
                        "rate": {"num": 1, "den": 1},
                    }
                )
            ]
        )
        with pytest.raises(PackLoadError):
            load_one(pack)


class TestRejectsOnARealPack:
    """The same guards, fired on a shipped pack with exactly one field broken.

    A toy pack can pass a guard for the wrong reason (nothing else in it is real).
    These start from ``blr.json`` as it ships, change one thing, and assert the load
    stops and names the rule.
    """

    def _blr_with(self, mutate: Any) -> PackLoadError:
        blr = copy_real_pack("blr")
        mutate(blr)
        root = write_pack_dir(blr, copy_real_pack("nbc-core"))
        with pytest.raises(PackLoadError) as excinfo:
            PackLoader(root).load(["blr"])
        return excinfo.value

    def test_unchanged_it_still_loads(self) -> None:
        root = write_pack_dir(copy_real_pack("blr"), copy_real_pack("nbc-core"))
        assert len(PackLoader(root).load(["blr"]).rules) == 23 + 33

    def test_a_typo_in_one_when_field(self) -> None:
        """Caught twice over: the schema's ``additionalProperties: false`` on the
        predicate fires first, and :meth:`PackLoader._validate_when` would catch it
        even if the schema were relaxed. Both are checked, because this is the typo
        that would otherwise make a rule apply to every plot or to none."""

        def mutate(pack: Dict[str, Any]) -> None:
            rule = next(r for r in pack["rules"] if r["id"] == "blr.far.road.9-18m")
            rule["when"]["roadwidthMm"] = rule["when"].pop("roadWidthMm")

        error = self._blr_with(mutate)
        assert isinstance(error, SchemaValidationError)
        assert "roadwidthMm" in str(error)

        loader = PackLoader(RULEPACK_DIR)
        with pytest.raises(PackLoadError) as excinfo:
            loader._validate_when({"roadwidthMm": {"lt": 9000}}, "blr", "blr.far.road.9-18m")
        assert excinfo.value.rule_id == "blr.far.road.9-18m"
        assert "closed context field set" in str(excinfo.value)

    def test_a_check_type_renamed_by_a_bad_merge(self) -> None:
        def mutate(pack: Dict[str, Any]) -> None:
            next(r for r in pack["rules"] if r["id"] == "blr.far.road.9-18m")["check"]["type"] = "fsi_max"

        error = self._blr_with(mutate)
        assert "fsi_max" in str(error)

    def test_a_ratio_written_as_a_decimal(self) -> None:
        def mutate(pack: Dict[str, Any]) -> None:
            next(r for r in pack["rules"] if r["id"] == "blr.far.road.9-18m")["check"]["ratio"] = 2.25

        assert isinstance(self._blr_with(mutate), SchemaValidationError)


class TestRejectsStructuralMistakes:
    def test_pack_field_must_match_the_file_name(self) -> None:
        pack = minimal_pack("tpack")
        root = write_pack_dir(pack)
        os.rename(os.path.join(root, "tpack.json"), os.path.join(root, "otherpack.json"))
        with pytest.raises(PackLoadError) as excinfo:
            PackLoader(root).load(["otherpack"])
        assert "does not match file name" in str(excinfo.value)

    def test_rule_id_prefix_must_match_the_pack(self) -> None:
        pack = minimal_pack("tpack", rules=[minimal_rule("other.stair.riser")])
        with pytest.raises(PackLoadError) as excinfo:
            load_one(pack)
        assert "idPrefix" in str(excinfo.value)

    def test_duplicate_rule_ids_across_a_chain(self) -> None:
        """Ids are globally unique so a six-month-old report still means one thing."""
        parent = minimal_pack("tparent", rules=[minimal_rule("tparent.stair.riser")])
        child = minimal_pack(
            "tchild",
            id_prefix="tparent",
            extends="tparent",
            rules=[minimal_rule("tparent.stair.riser", severity="warn")],
        )
        with pytest.raises(PackLoadError) as excinfo:
            load_one(child, parent, request="tchild")
        assert "globally unique" in str(excinfo.value)

    def test_extends_cycle(self) -> None:
        first = minimal_pack("tone", extends="ttwo", rules=[minimal_rule("tone.stair.riser")])
        second = minimal_pack("ttwo", extends="tone", rules=[minimal_rule("ttwo.stair.riser")])
        with pytest.raises(PackLoadError) as excinfo:
            load_one(first, second, request="tone")
        assert "cycle" in str(excinfo.value)

    def test_missing_parent_file(self) -> None:
        pack = minimal_pack("tpack", extends="nowhere")
        with pytest.raises(PackLoadError) as excinfo:
            load_one(pack)
        assert "not found" in str(excinfo.value)

    def test_no_packs_requested(self) -> None:
        root = write_pack_dir(minimal_pack())
        with pytest.raises(PackLoadError):
            PackLoader(root).load([])

    def test_unsafe_pack_id_is_refused_before_any_file_access(self) -> None:
        root = write_pack_dir(minimal_pack())
        with pytest.raises(PackLoadError) as excinfo:
            PackLoader(root).load(["../../etc/passwd"])
        assert "unsafe pack id" in str(excinfo.value)

    def test_override_of_a_rule_outside_the_chain(self) -> None:
        pack = minimal_pack(
            overrides=[
                {"ruleId": "nbc.stair.riser.max", "action": "disable", "reason": "not in the chain"}
            ]
        )
        with pytest.raises(PackLoadError) as excinfo:
            load_one(pack)
        assert "not in the loaded chain" in str(excinfo.value)

    def test_replace_needs_an_existing_replacement(self) -> None:
        parent = minimal_pack("tparent", rules=[minimal_rule("tparent.stair.riser")])
        child = minimal_pack(
            "tchild",
            extends="tparent",
            rules=[minimal_rule("tchild.stair.riser")],
            overrides=[
                {
                    "ruleId": "tparent.stair.riser",
                    "action": "replace",
                    "replacedBy": "tchild.does.not.exist",
                    "reason": "a replacement that is not there",
                }
            ],
        )
        with pytest.raises(PackLoadError) as excinfo:
            load_one(child, parent, request="tchild")
        assert "does not exist" in str(excinfo.value)


class TestScoringPackValidation:
    def _scoring_pack(self, **rule_overrides: Any) -> Dict[str, Any]:
        rule = minimal_rule(
            "tscore.stair.zone",
            check={"type": "zone_check", "mode": "zone", "target": {"kind": "stair"}, "allow": ["S"]},
            weight=50,
            group="circulation",
        )
        rule.update(rule_overrides)
        return minimal_pack(
            "tscore",
            rules=[rule],
            scoring={
                "enabled": True,
                "modeField": "vastuMode",
                "scale": {"min": 0, "max": 100},
                "aggregate": "weighted-mean",
                "rounding": "half-up",
                "modes": {
                    "advisory": {"enforce": False, "severityCeiling": "warn", "score": True},
                    "strict": {"enforce": True, "severityCeiling": "fail", "score": True},
                },
                "groups": [{"id": "circulation", "label": "Circulation"}],
            },
        )

    def test_a_valid_scoring_pack_loads(self) -> None:
        pack_set = load_one(self._scoring_pack())
        rule = pack_set.require_rule("tscore.stair.zone")
        assert rule.scoring and rule.weight == 50 and rule.group == "circulation"

    def test_missing_weight_is_a_schema_failure(self) -> None:
        pack = self._scoring_pack()
        del pack["rules"][0]["weight"]
        with pytest.raises(PackLoadError):
            load_one(pack)

    def test_group_must_be_declared(self) -> None:
        with pytest.raises(PackLoadError) as excinfo:
            load_one(self._scoring_pack(group="nowhere"))
        assert "scoring.groups" in str(excinfo.value)

    def test_unimplemented_aggregate(self) -> None:
        pack = self._scoring_pack()
        pack["scoring"]["aggregate"] = "sum"
        with pytest.raises(PackLoadError):
            load_one(pack)

    def test_unimplemented_rounding(self) -> None:
        pack = self._scoring_pack()
        pack["scoring"]["rounding"] = "bankers"
        with pytest.raises(PackLoadError):
            load_one(pack)


# ---------------------------------------------------------------------------
# Overrides and vocabulary merging
# ---------------------------------------------------------------------------


class TestOverrideResolution:
    def _chain(self, action: str) -> PackSet:
        parent = minimal_pack(
            "tparent",
            rules=[minimal_rule("tparent.stair.riser"), minimal_rule("tparent.stair.tread")],
        )
        child = minimal_pack(
            "tchild",
            extends="tparent",
            rules=[minimal_rule("tchild.stair.riser", check={"type": "stair_riser_max", "valueMm": 175})],
            overrides=[
                {
                    "ruleId": "tparent.stair.riser",
                    "action": action,
                    "reason": "the city is stricter than the national code",
                    **(
                        {"replacedBy": "tchild.stair.riser"}
                        if action == "replace"
                        else {}
                    ),
                }
            ],
        )
        return load_one(child, parent, request="tchild")

    def test_disable_removes_the_rule_and_records_why(self) -> None:
        pack_set = self._chain("disable")
        assert pack_set.rule("tparent.stair.riser") is None
        assert "stricter" in pack_set.disabled["tparent.stair.riser"]
        assert pack_set.rule("tparent.stair.tread") is not None

    def test_replace_disables_the_parent_and_names_the_replacement(self) -> None:
        pack_set = self._chain("replace")
        assert pack_set.rule("tparent.stair.riser") is None
        assert "tchild.stair.riser" in pack_set.disabled["tparent.stair.riser"]
        assert pack_set.rule("tchild.stair.riser") is not None

    def test_relax_to_warn_keeps_the_check_and_clamps_the_severity(self) -> None:
        pack_set = self._chain("relax-to-warn")
        rule = pack_set.require_rule("tparent.stair.riser")
        assert rule.severity == "warn"
        assert rule.relaxed_to_warn is True

    def test_rule_order_is_contiguous_after_disabling(self) -> None:
        pack_set = self._chain("disable")
        assert [rule.order for rule in pack_set.rules] == list(range(len(pack_set.rules)))


class TestVocabularyMerge:
    def test_a_child_key_replaces_the_parent_key_wholesale(self) -> None:
        """Never element-wise: "is a study habitable" is one editorial decision, and a
        union would quietly re-admit a type the child deliberately dropped."""
        parent = minimal_pack(
            "tparent",
            rules=[minimal_rule("tparent.stair.riser")],
            vocabulary={
                "habitableRoomTypes": ["bedroom", "living", "study"],
                "wetRoomTypes": ["bath", "wc"],
            },
        )
        child = minimal_pack(
            "tchild",
            extends="tparent",
            rules=[minimal_rule("tchild.stair.tread", check={"type": "stair_tread_min", "valueMm": 250})],
            vocabulary={"habitableRoomTypes": ["bedroom", "living"]},
        )
        vocabulary = load_one(child, parent, request="tchild").vocabulary
        assert vocabulary.habitable_room_types == frozenset({"bedroom", "living"})
        assert vocabulary.sources["habitableRoomTypes"] == "tchild"
        # The key the child did not touch still comes from the parent.
        assert vocabulary.wet_room_types == frozenset({"bath", "wc"})
        assert vocabulary.sources["wetRoomTypes"] == "tparent"


# ---------------------------------------------------------------------------
# The schema/engine coverage guard
# ---------------------------------------------------------------------------


class TestSchemaEngineGuard:
    def _mutated_schema_root(self, mutate: Any) -> str:
        root = write_pack_dir(minimal_pack())
        path = os.path.join(root, "schema", "rulepack.schema.json")
        schema = read_json(path)
        mutate(schema)
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(schema, handle)
        return root

    def test_a_when_field_the_engine_cannot_bind_breaks_the_load(self) -> None:
        """"Add a field, bump schemaVersion, teach the engine" has to be enforceable,
        not aspirational."""

        def mutate(schema: Dict[str, Any]) -> None:
            schema["$defs"]["predicate"]["properties"]["solarAccessHours"] = {
                "$ref": "#/$defs/intPredicate"
            }

        with pytest.raises(PackLoadError) as excinfo:
            PackLoader(self._mutated_schema_root(mutate)).load(["tpack"])
        assert "solarAccessHours" in str(excinfo.value)

    def test_a_check_type_the_engine_does_not_implement_breaks_the_load(self) -> None:
        def mutate(schema: Dict[str, Any]) -> None:
            schema["$defs"]["check"]["properties"]["type"]["enum"].append("daylight_factor_min")

        with pytest.raises(PackLoadError) as excinfo:
            PackLoader(self._mutated_schema_root(mutate)).load(["tpack"])
        assert "daylight_factor_min" in str(excinfo.value)

    def test_a_custom_fn_with_no_implementation_breaks_the_load(self) -> None:
        def mutate(schema: Dict[str, Any]) -> None:
            schema["$defs"]["check_custom"]["properties"]["fn"]["enum"].append("shadow_analysis")

        with pytest.raises(PackLoadError) as excinfo:
            PackLoader(self._mutated_schema_root(mutate)).load(["tpack"])
        assert "shadow_analysis" in str(excinfo.value)

    def test_an_unimplemented_schema_keyword_is_not_silently_skipped(self) -> None:
        """A validator that ignores what it does not understand reports "valid" for a
        schema it only partly checked."""
        from garh_rules.errors import SchemaFeatureError

        def mutate(schema: Dict[str, Any]) -> None:
            schema["$defs"]["rule"]["dependentRequired"] = {"weight": ["group"]}

        with pytest.raises(SchemaFeatureError) as excinfo:
            PackLoader(self._mutated_schema_root(mutate)).load(["tpack"])
        assert "dependentRequired" in str(excinfo.value)


# ---------------------------------------------------------------------------
# autofix vetting
# ---------------------------------------------------------------------------


class TestAutofixVetting:
    def test_an_unknown_op_type_disables_the_button_and_is_reported(self) -> None:
        """A bad hint must not take a city pack offline — but it must not silently
        produce a Fix-it button that cannot work either."""
        pack = minimal_pack(
            rules=[
                minimal_rule(
                    autofix={"opType": "wall.teleport", "strategy": "none", "computable": True}
                )
            ]
        )
        pack_set = load_one(pack)
        rule = pack_set.require_rule("tpack.stair.riser")
        if not any("NOT validated" in note for note in pack_set.notes):
            assert rule.autofix is not None and rule.autofix.computable is False
            assert any("wall.teleport" in note for note in pack_set.notes)

    def test_a_real_op_type_stays_computable(self) -> None:
        pack = minimal_pack(
            rules=[
                minimal_rule(
                    autofix={"opType": "stair.edit", "strategy": "adjust-stair-parameters"}
                )
            ]
        )
        rule = load_one(pack).require_rule("tpack.stair.riser")
        assert rule.autofix is not None and rule.autofix.computable is True

    def test_shipped_packs_report_their_autofix_drift(self) -> None:
        """The seed packs name two op types the model core does not define
        (``balcony.edit``, ``furniture.place`` — the model has ``balcony.set`` and
        ``furniture.set`` with an ``action`` field). Whichever side is wrong, the run
        has to say so rather than shipping dead buttons.

        A pack may also mark a hint ``computable: false`` on purpose (the fix needs a
        re-solve), so the assertion runs the other way: an op type the catalogue does
        not know must be both disabled *and* named in the notes.
        """
        pack_set = load_pack_set(PACK_IDS, root=RULEPACK_DIR)
        notes = " ".join(pack_set.notes)
        if "NOT validated" in notes:
            return  # garh_model is not importable here; the note already says so
        for drifted in ("balcony.edit", "furniture.place"):
            assert drifted in notes, pack_set.notes
            affected = [
                rule
                for rule in pack_set.rules
                if rule.autofix is not None and rule.autofix.op_type == drifted
            ]
            assert affected, drifted
            assert all(rule.autofix is not None and not rule.autofix.computable for rule in affected)


# ---------------------------------------------------------------------------
# Room-type reachability (the other half of "no rule runs unseen")
# ---------------------------------------------------------------------------


def test_room_types_no_model_room_can_ever_carry_are_reported() -> None:
    """A rule keyed on a room type the model core cannot emit never fires.

    ``nbc.room.kitchen_dining.area.min`` is exactly that today: the packs know
    ``kitchen_dining``, ``garh_model.ROOM_TYPES`` does not, so the combined
    kitchen-dining minimum can never be measured. That is a note, not a load
    error — the pack is right and the model core is behind — but it must be
    visible.
    """
    pack_set = load_pack_set(PACK_IDS, root=RULEPACK_DIR)
    notes = " ".join(pack_set.notes)
    if "NOT validated" in notes:
        return  # garh_model is not importable here; the note already says so
    assert "kitchen_dining" in notes, pack_set.notes
