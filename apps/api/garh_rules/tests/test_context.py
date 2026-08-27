"""Carried Phase-2 finding (i): the ``buildingUse`` default drift.

``garh_api.compliance.build_evaluation_context`` used to default
``profile.buildingUse`` to ``"residential"`` — a value that is **not** in the packs'
own enum (``rulepack.schema.json``: ``dwelling-single | dwelling-two | row-house |
apartment | other``) and that the client mirror
(``apps/web/src/features/plot/rules.ts`` ``defaultRegFacts``) never used. Every
city-pack rule gated on ``when.buildingUse`` — all 83 of them across blr/ncr/hyd:
every setback, FAR, coverage and height band — therefore reported
``not_applicable`` for a default-context house. Silently. That is the worst kind of
bug this engine can have: a compliance panel that looks clean because the rules
never ran.

The fix is ``garh_api.compliance.DEFAULT_BUILDING_USE = "dwelling-single"``. These
tests pin it from four directions:

1. the constant is a member of the packs' enum (the root cause was that it wasn't);
2. every ``when.buildingUse`` clause in every shipped pack names only enum values,
   and every city-pack ``in`` list includes the default — so a default-context house
   can never fall outside the residential bands again;
3. the projection + engine, end to end: a default-context Bengaluru house **binds**
   a blr setback rule (fail, with a real actual and limit) where the old default
   produced ``not_applicable`` across the board — both directions asserted;
4. the TS mirror agrees, so the panel's instant numbers and the server's report
   cannot band differently.

IMPORT NOTE ------------------------------------------------------------------
``garh_api.compliance`` transitively imports ``structlog``, ``pydantic`` and
``pydantic_settings`` (via ``garh_api.logging`` → ``garh_api.config``). Those are
pinned, real dependencies in CI and in the images — but the local authoring
machine has only the stdlib (see DECISIONS.md, toolchain rows). So this module
installs **minimal import-shims into ``sys.modules`` only when the real package is
absent**. The shims satisfy module-scope imports only; no shimmed behaviour is
asserted anywhere. In CI the real packages import first and the shims are dead
code.
"""

from __future__ import annotations

import json
import os
import sys
import types
from collections.abc import Mapping
from typing import Any

from .conftest import REPO_ROOT, RULEPACK_DIR

PACK_IDS = ("nbc-core", "blr", "ncr", "hyd", "vastu")
CITY_PACK_IDS = ("blr", "ncr", "hyd")


# ---------------------------------------------------------------------------
# import shims (no-ops when the real dependencies are installed)
# ---------------------------------------------------------------------------


def _shim(name: str, **attrs: Any) -> None:
    if name in sys.modules:
        return
    try:
        __import__(name)
        return  # the real package exists; never shadow it
    except ImportError:
        pass
    module = types.ModuleType(name)
    for key, value in attrs.items():
        setattr(module, key, value)
    sys.modules[name] = module


class _NoopLogger:
    def _log(self, *args: Any, **kwargs: Any) -> None:
        return None

    def bind(self, *args: Any, **kwargs: Any) -> _NoopLogger:
        return self

    debug = info = warning = error = critical = exception = _log


class _Namespace:
    def __getattr__(self, name: str) -> Any:
        def _fn(*args: Any, **kwargs: Any) -> Any:
            return None

        return _fn


def _field(default: Any = None, **kwargs: Any) -> Any:
    if default is ... and "default_factory" in kwargs:
        return kwargs["default_factory"]()
    return default


def _decorator_factory(*args: Any, **kwargs: Any) -> Any:
    def decorator(fn: Any) -> Any:
        return fn

    return decorator


_shim(
    "structlog",
    get_logger=lambda *a, **k: _NoopLogger(),
    configure=lambda *a, **k: None,
    contextvars=_Namespace(),
    stdlib=_Namespace(),
    processors=_Namespace(),
)
_shim(
    "pydantic",
    Field=_field,
    field_validator=_decorator_factory,
    model_validator=_decorator_factory,
    AliasChoices=type("AliasChoices", (), {"__init__": lambda self, *c: None}),
    BaseModel=object,
    ValidationError=type("ValidationError", (Exception,), {}),
)
_shim(
    "pydantic_settings",
    BaseSettings=object,
    SettingsConfigDict=lambda **kwargs: dict(**kwargs),
)

from garh_api.compliance import (  # noqa: E402  (shims must land first)
    DEFAULT_BUILDING_USE,
    build_evaluation_context,
    packs_for,
)

from garh_rules import evaluate  # noqa: E402

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _read_json(path: str) -> dict[str, Any]:
    with open(path, encoding="utf-8") as handle:
        data: dict[str, Any] = json.load(handle)
    return data


def _building_use_enum() -> list[str]:
    schema = _read_json(os.path.join(RULEPACK_DIR, "schema", "rulepack.schema.json"))
    text = json.dumps(schema)

    # Resolve through $defs so this survives schema refactors: find the property.
    def find(node: Any) -> Any:
        if isinstance(node, dict):
            if "buildingUse" in node and isinstance(node["buildingUse"], dict):
                found = node["buildingUse"].get("enum")
                if found:
                    return found
            for value in node.values():
                result = find(value)
                if result:
                    return result
        elif isinstance(node, list):
            for value in node:
                result = find(value)
                if result:
                    return result
        return None

    enum = find(schema)
    assert enum, "rulepack.schema.json no longer declares a buildingUse enum: %s" % text[:80]
    return list(enum)


def _clause_values(clause: Any) -> list[str]:
    """Every literal a when.buildingUse predicate can compare against."""
    values: list[str] = []
    if isinstance(clause, Mapping):
        for op in ("eq", "neq"):
            if isinstance(clause.get(op), str):
                values.append(clause[op])
        for op in ("in", "nin"):
            entries = clause.get(op)
            if isinstance(entries, list | tuple):
                values.extend(str(v) for v in entries)
    elif isinstance(clause, str):
        values.append(clause)
    return values


def _default_context_document() -> dict[str, Any]:
    """A house an architect has *just started*: plot drawn, road set, nothing else
    answered — exactly the state in which the default buildingUse governs."""
    return {
        "plot": {
            "boundary": [
                {"x": 0, "y": 0},
                {"x": 9144, "y": 0},
                {"x": 9144, "y": 12192},
                {"x": 0, "y": 12192},
            ],
            "northDeg": 0,
            "roads": [{"edgeIndex": 0, "widthMm": 9000}],
            "regProfile": {"cityPack": "blr"},
        },
        "brief": {"vastuMode": "off", "data": {}},
        "house": {
            "storeys": [{"id": "s0", "heightMm": 3000, "level": {"slabThicknessMm": 150}}],
            "walls": [],
            "openings": [],
            "rooms": [],
            "stairs": [],
            "slabs": [],
            "balconies": [],
            "levels": {"plinthMm": 600, "parapetMm": 1000},
        },
    }


# ---------------------------------------------------------------------------
# 1 + 2: the constant and the packs agree with the schema's own vocabulary
# ---------------------------------------------------------------------------


class TestBuildingUseVocabulary:
    def test_the_default_is_a_member_of_the_packs_enum(self) -> None:
        enum = _building_use_enum()
        assert DEFAULT_BUILDING_USE in enum, (
            "DEFAULT_BUILDING_USE=%r is outside the packs' enum %r — this is the "
            "exact drift the Phase-2 review caught" % (DEFAULT_BUILDING_USE, enum)
        )
        assert DEFAULT_BUILDING_USE == "dwelling-single"
        assert "residential" not in enum, (
            "if 'residential' ever joins the buildingUse enum, re-audit this default: "
            "the old bug becomes representable again"
        )

    def test_every_when_clause_names_only_enum_values(self) -> None:
        enum = set(_building_use_enum())
        audited = 0
        for pack_id in PACK_IDS:
            pack = _read_json(os.path.join(RULEPACK_DIR, "%s.json" % pack_id))
            for rule in pack.get("rules", ()):
                clause = (rule.get("when") or {}).get("buildingUse")
                if clause is None:
                    continue
                audited += 1
                for value in _clause_values(clause):
                    assert value in enum, "%s: when.buildingUse names %r, not in %r" % (
                        rule["id"],
                        value,
                        sorted(enum),
                    )
        assert audited >= 80, (
            "expected the ~83 buildingUse-gated city rules; found %d — if packs "
            "changed shape, re-verify the audit still reaches them" % audited
        )

    def test_every_city_pack_gate_admits_the_default(self) -> None:
        """The point of the fix: a default-context house must fall INSIDE the
        residential bands of every city pack, not outside every band."""
        for pack_id in CITY_PACK_IDS:
            pack = _read_json(os.path.join(RULEPACK_DIR, "%s.json" % pack_id))
            for rule in pack.get("rules", ()):
                clause = (rule.get("when") or {}).get("buildingUse")
                if clause is None:
                    continue
                allowed = clause.get("in") if isinstance(clause, Mapping) else None
                if allowed is not None:
                    assert DEFAULT_BUILDING_USE in allowed, (
                        "%s gates on buildingUse in %r, which excludes the default "
                        "%r — a fresh project would silently skip it"
                        % (rule["id"], allowed, DEFAULT_BUILDING_USE)
                    )

    def test_the_ts_mirror_uses_the_same_default(self) -> None:
        """The client mirror computes the panel's instant numbers; if its default
        drifts from the server constant, the two band differently. String-level
        pin — the mirror is data, not importable from Python."""
        mirror_path = os.path.join(REPO_ROOT, "apps", "web", "src", "features", "plot", "rules.ts")
        with open(mirror_path, encoding="utf-8") as handle:
            source = handle.read()
        assert "'%s'" % DEFAULT_BUILDING_USE in source or '"%s"' % DEFAULT_BUILDING_USE in source, (
            "apps/web/src/features/plot/rules.ts no longer contains the default "
            "buildingUse %r — update the mirror (defaultRegFacts) and this pin "
            "together" % DEFAULT_BUILDING_USE
        )


# ---------------------------------------------------------------------------
# 3: the regression, end to end through the real projection + real engine
# ---------------------------------------------------------------------------


class TestDefaultContextBindsCityRules:
    def test_projection_carries_the_new_default(self) -> None:
        document = _default_context_document()
        context = build_evaluation_context(document, packs=packs_for(document))
        assert context["profile"]["buildingUse"] == DEFAULT_BUILDING_USE

    def test_a_blr_setback_rule_binds_for_a_default_context_house(self) -> None:
        """The headline regression: with no walls drawn yet, the provided front
        setback is 0, so the ≤120 m² front-setback rule must FAIL with real
        numbers — the one thing the old default made impossible."""
        document = _default_context_document()
        context = build_evaluation_context(document, packs=packs_for(document))
        report = evaluate(context, root=RULEPACK_DIR)
        row = report.rule("blr.setback.front.plot.le120")
        assert row is not None
        assert row.status == "fail", (
            "expected the front-setback rule to BIND (fail on 0 provided); got %r "
            "(notApplicable reason: %r / field %r)"
            % (row.status, row.not_applicable_reason, row.not_applicable_field)
        )
        assert row.actual == 0
        assert isinstance(row.limit, int) and row.limit > 0

    def test_at_least_one_rule_per_city_pack_binds_by_default(self) -> None:
        for city in CITY_PACK_IDS:
            document = _default_context_document()
            document["plot"]["regProfile"]["cityPack"] = city
            context = build_evaluation_context(document, packs=packs_for(document))
            report = evaluate(context, root=RULEPACK_DIR)
            bound = [
                r for r in report.results if r.pack_id == city and r.status != "not_applicable"
            ]
            assert bound, (
                "%s: no rule bound for a default-context house — the buildingUse "
                "default has drifted out of the pack's bands again" % city
            )

    def test_the_old_default_silently_skipped_every_gated_rule(self) -> None:
        """Keeps the failure mode visible: pass the OLD value explicitly and every
        buildingUse-gated blr rule drops to not_applicable. If this test ever
        fails, the packs stopped gating on buildingUse and the default stopped
        mattering — re-audit either way."""
        document = _default_context_document()
        packs = packs_for(document)
        pack = _read_json(os.path.join(RULEPACK_DIR, "blr.json"))
        gated_ids = {
            rule["id"]
            for rule in pack.get("rules", ())
            if (rule.get("when") or {}).get("buildingUse") is not None
        }
        assert gated_ids, "blr no longer gates anything on buildingUse?"

        old = build_evaluation_context(document, packs=packs, building_use="residential")
        report_old = evaluate(old, root=RULEPACK_DIR)
        for rule_id in sorted(gated_ids):
            row = report_old.rule(rule_id)
            assert row is not None and row.status == "not_applicable", (
                "%s evaluated under buildingUse='residential' — the audit premise "
                "changed" % rule_id
            )

        new = build_evaluation_context(document, packs=packs)
        report_new = evaluate(new, root=RULEPACK_DIR)
        recovered = [
            rule_id
            for rule_id in sorted(gated_ids)
            if report_new.rule(rule_id) is not None
            and report_new.rule(rule_id).status != "not_applicable"
        ]
        assert recovered, (
            "the new default recovered no blr rule at all — the fix is not doing "
            "what this file claims"
        )


# ---------------------------------------------------------------------------
# bare-python runner (pytest is not installed on the build machine)
# ---------------------------------------------------------------------------

if __name__ == "__main__":  # pragma: no cover
    import traceback

    failures = 0
    for cls_name, cls in sorted(globals().items()):
        if not (isinstance(cls, type) and cls_name.startswith("Test")):
            continue
        instance = cls()
        for name in sorted(dir(instance)):
            if not name.startswith("test_"):
                continue
            try:
                getattr(instance, name)()
                print("PASS %s.%s" % (cls_name, name))
            except Exception:
                failures += 1
                print("FAIL %s.%s" % (cls_name, name))
                traceback.print_exc()
    sys.exit(1 if failures else 0)
