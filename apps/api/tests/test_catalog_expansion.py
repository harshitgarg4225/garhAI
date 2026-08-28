"""The parametric catalogue expander and the committed catalogue must agree.

``scripts/expand_catalog.py`` declares furniture and material *families* — one design
across a series of sizes, shades or finishes — and appends the ones the committed
catalogue does not already carry. Two properties make that safe to run in CI rather
than only by hand, and both are asserted here:

1. **Idempotence.** Running the expander against the committed catalogue adds nothing.
   If it ever wants to add a row, the family list and the catalogue have drifted and
   somebody needs to run it with ``--write`` and commit the result.
2. **Append-only.** It never rewrites an existing row. The 229 hand-authored furniture
   entries and 97 hand-authored materials predate the families and carry judgement the
   generator does not have; silently regenerating them would throw that away.

Both are negative-tested: a deliberately shrunken catalogue must make the expander
want to add rows again, and a tampered row must be detected as a rewrite.
"""

from __future__ import annotations

import importlib.util
import json
import os

import pytest


def _repo_root() -> str:
    """Walk up until the repository's own marker files are both present.

    Counted ``dirname`` calls get this wrong the moment a file moves, and the
    failure is a confusing FileNotFoundError two frames away from the cause.
    """
    here = os.path.dirname(os.path.abspath(__file__))
    while True:
        if os.path.isdir(os.path.join(here, "fixtures")) and os.path.isdir(
            os.path.join(here, "scripts")
        ):
            return here
        parent = os.path.dirname(here)
        if parent == here:  # pragma: no cover - only on a broken checkout
            raise RuntimeError("no repository root above %s" % os.path.abspath(__file__))
        here = parent


_ROOT = _repo_root()
_SCRIPT = os.path.join(_ROOT, "scripts", "expand_catalog.py")


def _expander():
    spec = importlib.util.spec_from_file_location("expand_catalog", _SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load(name: str) -> list[dict]:
    with open(os.path.join(_ROOT, "fixtures", "catalog", name), encoding="utf-8") as handle:
        data = json.load(handle)
    assert isinstance(data, list)
    return data


@pytest.fixture(scope="module")
def expander():
    return _expander()


# ===========================================================================
# Idempotence
# ===========================================================================
def test_the_expander_has_nothing_left_to_add(expander) -> None:
    """The committed catalogue already carries every declared family size."""
    _, added = expander.build(_load("furniture.json"))
    assert added == [], "furniture families drifted — run scripts/expand_catalog.py --write"


def test_the_material_expander_has_nothing_left_to_add(expander) -> None:
    _, added = expander.build_materials(_load("materials.json"))
    assert added == [], "material families drifted — run scripts/expand_catalog.py --write"


def test_negative_control_a_short_catalogue_wants_rows_back(expander) -> None:
    """Prove the drift gate can fail: drop rows and the expander must want them back."""
    full = _load("furniture.json")
    _, added = expander.build(full[:50])
    assert len(added) > 100, "the expander did not notice a catalogue missing 400 rows"


# ===========================================================================
# Append-only
# ===========================================================================
def test_existing_rows_are_never_rewritten(expander) -> None:
    """Every row the expander returns for an input is that input's row, unchanged."""
    for name, builder in (
        ("furniture.json", expander.build),
        ("materials.json", expander.build_materials),
    ):
        existing = _load(name)
        merged, _ = builder(existing)
        assert merged[: len(existing)] == existing, "%s rows were rewritten" % name


def test_negative_control_a_tampered_row_is_visible(expander) -> None:
    """Prove the append-only assertion discriminates rather than always passing."""
    existing = _load("furniture.json")
    tampered = [dict(existing[0], widthMm=existing[0]["widthMm"] + 1), *existing[1:]]
    merged, _ = expander.build(tampered)
    assert merged[: len(existing)] != existing


# ===========================================================================
# What the families produce has to be usable, not just numerous
# ===========================================================================
def test_generated_rows_pass_the_seed_validators(expander) -> None:
    """A row the seeder would refuse is worse than no row."""
    from garh_api.seed.catalog import validate_furniture, validate_materials
    from garh_model.model import ROOM_TYPES

    assert len(validate_furniture(_load("furniture.json"), room_types=ROOM_TYPES)) == 469
    assert len(validate_materials(_load("materials.json"))) == 184


def test_every_dimension_is_plausible_for_the_thing_it_names(expander) -> None:
    """A catalogue's value is its dimensions; a 4 m wardrobe is worse than none."""
    for row in _load("furniture.json"):
        for key in ("widthMm", "depthMm", "heightMm"):
            value = row[key]
            assert 50 <= value <= 6000, "%s.%s = %d is not a real object" % (
                row["id"],
                key,
                value,
            )
        assert 0 <= row["clearanceMm"] <= 2000, row["id"]


def test_categories_stay_within_the_ones_the_browser_knows(expander) -> None:
    """A new category with one item in it is a filter nobody will ever click."""
    known = {
        "bed",
        "storage",
        "table",
        "seating",
        "kitchen",
        "appliance",
        "sanitary",
        "vehicle",
        "service",
    }
    found = {row["category"] for row in _load("furniture.json")}
    assert found <= known, "unexpected furniture categories: %s" % (found - known)


def test_ids_are_unique_and_slug_shaped(expander) -> None:
    import re

    pattern = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
    for name in ("furniture.json", "materials.json"):
        rows = _load(name)
        ids = [row["id"] for row in rows]
        assert len(ids) == len(set(ids)), "duplicate ids in %s" % name
        for item_id in ids:
            assert pattern.match(item_id), "%s: %r is not a slug" % (name, item_id)
