"""Shared fixtures: the repo root, the real packs, and a minimal context builder.

The context builder is deliberately small and *not* a plausible house — same
choice the fixture corpus makes. A test for ``room_area_min`` wants one room whose
area is one millimetre short and nothing else in the way; a realistic plan would
make a red test ambiguous.
"""

from __future__ import annotations

import atexit
import copy
import json
import os
import shutil
import tempfile
from collections.abc import Mapping, Sequence
from typing import Any

import pytest

from garh_rules import EvaluationContext, PackLoader, clear_pack_cache, load_pack_set
from garh_rules.packs import PackSet

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", ".."))
RULEPACK_DIR = os.path.join(REPO_ROOT, "rulepacks")
FIXTURE_DIR = os.path.join(REPO_ROOT, "fixtures", "rules")

PACK_IDS = ("nbc-core", "blr", "ncr", "hyd", "vastu")


@pytest.fixture(scope="session", autouse=True)
def _pin_rulepack_dir() -> None:
    """Point the loader at this checkout's packs, whatever the cwd is."""
    os.environ["GARH_RULEPACK_DIR"] = RULEPACK_DIR
    clear_pack_cache()


@pytest.fixture(scope="session")
def loader() -> PackLoader:
    return PackLoader(RULEPACK_DIR)


@pytest.fixture(scope="session")
def nbc() -> PackSet:
    return load_pack_set(["nbc-core"], root=RULEPACK_DIR)


@pytest.fixture(scope="session")
def blr() -> PackSet:
    return load_pack_set(["blr"], root=RULEPACK_DIR)


@pytest.fixture(scope="session")
def vastu() -> PackSet:
    return load_pack_set(["vastu"], root=RULEPACK_DIR)


def read_json(path: str) -> dict[str, Any]:
    with open(path, encoding="utf-8") as handle:
        data: dict[str, Any] = json.load(handle)
    return data


def fixture_index() -> dict[str, Any]:
    return read_json(os.path.join(FIXTURE_DIR, "index.json"))


def load_fixture(relative_path: str) -> dict[str, Any]:
    return read_json(os.path.join(FIXTURE_DIR, relative_path))


# ---------------------------------------------------------------------------
# Synthetic packs, for the loader's rejection paths
# ---------------------------------------------------------------------------
#
# The loader's whole job is to refuse a pack it cannot evaluate, so most of
# ``test_packs.py`` needs packs that are deliberately wrong — which the real
# ``rulepacks/`` directory must never contain. These helpers write throwaway
# packs into a temp directory alongside a symlink/copy of the real schema, so the
# tests exercise the same schema the product loads.

_TEMP_DIRS: list[str] = []


def _cleanup_temp_dirs() -> None:  # pragma: no cover - process exit
    for path in _TEMP_DIRS:
        shutil.rmtree(path, ignore_errors=True)


atexit.register(_cleanup_temp_dirs)


def minimal_check() -> dict[str, Any]:
    """A valid, boring check — ``stair_riser_max`` needs only one parameter."""
    return {"type": "stair_riser_max", "valueMm": 190}


def minimal_rule(rule_id: str = "tpack.stair.riser", **overrides: Any) -> dict[str, Any]:
    rule: dict[str, Any] = {
        "id": rule_id,
        "severity": "fail",
        "title": "Riser height",
        "message": "{element} has a {actual} riser; {limit} is the maximum ({cite}).",
        "check": minimal_check(),
        "cite": "Test clause 1",
        "fix": "Reduce the riser.",
        "confidence": "seed",
    }
    rule.update(overrides)
    return rule


def minimal_pack(
    pack_id: str = "tpack",
    *,
    id_prefix: str | None = None,
    extends: str | None = None,
    rules: list[dict[str, Any]] | None = None,
    **overrides: Any,
) -> dict[str, Any]:
    """The smallest pack that satisfies ``rulepack.schema.json``."""
    pack: dict[str, Any] = {
        "schemaVersion": 1,
        "pack": pack_id,
        "idPrefix": id_prefix if id_prefix is not None else pack_id.replace("-", ""),
        "version": "2026.07",
        "title": "Test pack %s" % pack_id,
        "authority": "Garh AI test suite",
        "jurisdiction": {"country": "IN", "scope": "national"},
        "citations_base": "TEST",
        "extends": extends,
        "confidenceDefault": "seed",
        "review": {"status": "unreviewed", "reviewers": [], "lastReviewedAt": None},
        "disclaimer": "Test pack. Advisory only, never an approval, and never shipped.",
        "rules": rules if rules is not None else [minimal_rule()],
    }
    pack.update(overrides)
    return pack


def write_pack_dir(*packs: Mapping[str, Any]) -> str:
    """Write packs into a fresh temp rulepack directory and return its path.

    The real ``schema/`` is copied in, not stubbed: a loader test that passed
    against a hand-simplified schema would prove nothing about the product.
    """
    root = tempfile.mkdtemp(prefix="garh-rulepack-")
    _TEMP_DIRS.append(root)
    shutil.copytree(os.path.join(RULEPACK_DIR, "schema"), os.path.join(root, "schema"))
    for pack in packs:
        with open(os.path.join(root, "%s.json" % pack["pack"]), "w", encoding="utf-8") as handle:
            json.dump(pack, handle, indent=2)
    clear_pack_cache()
    return root


def copy_real_pack(pack_id: str) -> dict[str, Any]:
    """A mutable deep copy of a shipped pack, for "break one field" tests."""
    return copy.deepcopy(read_json(os.path.join(RULEPACK_DIR, "%s.json" % pack_id)))


# ---------------------------------------------------------------------------
# Hand-built contexts
# ---------------------------------------------------------------------------

RECT_30x40 = [[0, 0], [9144, 0], [9144, 12192], [0, 12192]]  # the demo plot, 30 x 40 ft


def rect(x0: int, y0: int, width: int, depth: int) -> list[list[int]]:
    return [[x0, y0], [x0 + width, y0], [x0 + width, y0 + depth], [x0, y0 + depth]]


def make_room(
    room_id: str,
    room_type: str,
    *,
    x: int = 0,
    y: int = 0,
    width: int = 3000,
    depth: int = 4000,
    ceiling_mm: int = 2900,
    ventilation_mm2: int = 5_000_000,
    internal: bool = False,
    storey_id: str = "storey_g",
    name: str | None = None,
) -> dict[str, Any]:
    return {
        "id": room_id,
        "storeyId": storey_id,
        "type": room_type,
        "name": name or room_type.replace("_", " ").title(),
        "polygonMm": rect(x, y, width, depth),
        "areaMm2": width * depth,
        "leastWidthMm": min(width, depth),
        "centroidMm": [x + (width + 1) // 2, y + (depth + 1) // 2],
        "clearCeilingHeightMm": ceiling_mm,
        "ventilationOpeningAreaMm2": ventilation_mm2,
        "isInternal": internal,
    }


def make_context(
    *,
    packs: Sequence[str] = ("nbc-core",),
    vastu_mode: str = "off",
    boundary: list[list[int]] | None = None,
    area_mm2: int | None = None,
    north_deg: int = 0,
    edges: list[dict[str, Any]] | None = None,
    rooms: list[dict[str, Any]] | None = None,
    openings: list[dict[str, Any]] | None = None,
    stairs: list[dict[str, Any]] | None = None,
    projections: list[dict[str, Any]] | None = None,
    service_elements: list[dict[str, Any]] | None = None,
    storeys: list[dict[str, Any]] | None = None,
    profile: Mapping[str, Any] | None = None,
    model: Mapping[str, Any] | None = None,
) -> EvaluationContext:
    """A complete, valid, deliberately boring context."""
    ring = boundary if boundary is not None else RECT_30x40
    xs = [p[0] for p in ring]
    ys = [p[1] for p in ring]
    width = max(xs) - min(xs)
    depth = max(ys) - min(ys)
    default_edges = [
        {"index": 0, "role": "front", "roadWidthMm": 9000, "setbackProvidedMm": 3000},
        {"index": 1, "role": "side-a", "roadWidthMm": None, "setbackProvidedMm": 1500},
        {"index": 2, "role": "rear", "roadWidthMm": None, "setbackProvidedMm": 2000},
        {"index": 3, "role": "side-b", "roadWidthMm": None, "setbackProvidedMm": 1500},
    ]
    base_model: dict[str, Any] = {
        "storeyCount": 2,
        "hasStilt": False,
        "hasBasement": False,
        "buildingHeightMm": 7200,
        "heightComponentsMm": {"parapet": 1000, "mumty": 2400, "oht": 1200},
        "footprintAreaMm2": 50_000_000,
        "builtUpAreaMm2": 100_000_000,
        "farCountableAreaMm2": 100_000_000,
        "storeys": storeys
        if storeys is not None
        else [
            {
                "id": "storey_g",
                "index": 0,
                "heightMm": 3000,
                "clearHeightMm": 2850,
                "builtUpAreaMm2": 50_000_000,
            },
            {
                "id": "storey_1",
                "index": 1,
                "heightMm": 3000,
                "clearHeightMm": 2850,
                "builtUpAreaMm2": 50_000_000,
            },
        ],
        "rooms": rooms if rooms is not None else [],
        "openings": openings if openings is not None else [],
        "stairs": stairs if stairs is not None else [],
    }
    if projections is not None:
        base_model["projections"] = projections
    if service_elements is not None:
        base_model["serviceElements"] = service_elements
    if model:
        base_model.update(model)

    base_profile: dict[str, Any] = {
        "cityPack": "nbc-core",
        "zoneCategory": "residential",
        "buildingUse": "dwelling-single",
        "dwellingUnits": 1,
        "parkingSpacesProvided": 2,
        "rwhDeclared": True,
    }
    if profile:
        base_profile.update(profile)

    return EvaluationContext.from_json(
        {
            "packs": list(packs),
            "vastuMode": vastu_mode,
            "plot": {
                "boundaryMm": ring,
                "areaMm2": area_mm2 if area_mm2 is not None else width * depth,
                "northDeg": north_deg,
                "frontageMm": width,
                "depthMm": depth,
                "cornerPlot": False,
                "edges": edges if edges is not None else default_edges,
            },
            "profile": base_profile,
            "model": base_model,
        }
    )
