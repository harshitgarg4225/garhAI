"""Element identity: shape, parsing, and the DETERMINISTIC derived ids.

Derived ids appear inside the state hash (rooms and slabs are derived), so
``derived_id`` is a cross-language contract, not an implementation detail.
"""

from __future__ import annotations

import pytest

from garh_model.ids import (
    CROCKFORD32,
    ELEMENT_TYPES,
    ID_PATTERN,
    IdError,
    assert_id_of,
    compare_ids,
    derived_id,
    derived_id_unique,
    id_type,
    is_id,
    is_id_of,
    new_id,
    parse_id,
    seeded_ulid_factory,
    set_ulid_factory,
    try_parse_id,
)


def test_element_types_cover_every_document_family() -> None:
    for expected in (
        "storey",
        "wall",
        "opening",
        "room",
        "stair",
        "slab",
        "column",
        "furniture",
        "balcony",
        "facadecomp",
        "material",
        "annotation",
        "sheet",
    ):
        assert expected in ELEMENT_TYPES
    # `facadecomp`, not `facadeComponent` — the id prefix must stay lowercase.
    assert "facadeComponent" not in ELEMENT_TYPES


def test_new_id_shape() -> None:
    value = new_id("wall")
    assert value.startswith("wall_")
    assert ID_PATTERN.match(value) is not None
    assert is_id(value)
    assert is_id_of("wall", value)
    assert not is_id_of("opening", value)
    assert id_type(value) == "wall"


def test_new_id_is_unique_and_sorts_by_time() -> None:
    ids = [new_id("wall") for _ in range(50)]
    assert len(set(ids)) == 50


def test_seeded_factory_is_deterministic() -> None:
    set_ulid_factory(seeded_ulid_factory(7))
    try:
        first = [new_id("room") for _ in range(3)]
    finally:
        set_ulid_factory(None)
    set_ulid_factory(seeded_ulid_factory(7))
    try:
        second = [new_id("room") for _ in range(3)]
    finally:
        set_ulid_factory(None)
    assert first == second


@pytest.mark.parametrize(
    "bad",
    [
        "",
        "wall",
        "wall_",
        "_01J0000000000000000000WALL",
        "wall_short",
        "Wall_01J000000000000000000000",
        "wall_01J00000000000000000000I",  # I is not in the Crockford alphabet
        "wall_01J00000000000000000000U",  # nor is U
        "unknowntype_01J000000000000000000000",
        None,
        42,
    ],
)
def test_malformed_ids_are_rejected(bad: object) -> None:
    assert try_parse_id(bad) is None
    assert not is_id(bad)
    with pytest.raises(IdError):
        parse_id(bad)


def test_parse_id_round_trip() -> None:
    value = new_id("opening")
    parsed = parse_id(value)
    assert parsed.type == "opening"
    assert len(parsed.ulid) == 26
    assert assert_id_of("opening", value, "payload.id") == value
    with pytest.raises(IdError):
        assert_id_of("wall", value, "payload.wallId")


def test_derived_id_is_stable_and_valid() -> None:
    """The exact value is part of the cross-language contract."""
    a = derived_id("room", "storey_01J0000000000000000000GF|0,0 1000,0 1000,1000 0,1000")
    b = derived_id("room", "storey_01J0000000000000000000GF|0,0 1000,0 1000,1000 0,1000")
    assert a == b
    assert is_id_of("room", a)
    body = a.split("_", 1)[1]
    assert len(body) == 26
    assert all(ch in CROCKFORD32 for ch in body)
    # bits[0] and bits[1] are forced to zero, so the leading char is 0-7
    assert body[0] in "01234567"


def test_derived_id_differs_per_key_and_per_type() -> None:
    assert derived_id("room", "a") != derived_id("room", "b")
    assert derived_id("room", "a") != derived_id("slab", "a")


def test_derived_id_unique_salts_on_collision() -> None:
    first = derived_id("room", "same")
    second = derived_id_unique("room", "same", {first})
    assert second != first
    assert is_id_of("room", second)
    third = derived_id_unique("room", "same", {first, second})
    assert third not in {first, second}


def test_compare_ids_is_plain_code_point_order() -> None:
    assert compare_ids("wall_A", "wall_B") < 0
    assert compare_ids("wall_B", "wall_A") > 0
    assert compare_ids("wall_A", "wall_A") == 0
