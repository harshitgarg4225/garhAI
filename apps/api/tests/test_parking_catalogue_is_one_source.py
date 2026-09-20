"""The bay the solver places, the rule measures and the sheet draws is ONE entry.

Three pieces of code want the size of a car parking bay:

* ``services.solver.parking.bay_size_mm`` — the rectangle the solver puts on the plan;
* ``garh_api.parking_geometry.catalog_bay_size_mm`` — what the drawings service asks
  for when it draws the bay on A-01;
* ``garh_api.compliance._measured_parking_spaces`` — the rectangle the ``parking_min``
  rule measures, via ``bay_footprint_mm`` over ``garh_api.catalog_data``.

If two of them read different files, a plan can draw a car the compliance tab then
refuses, and the architect is told their drawing is wrong about its own drawing.
That is the failure this file exists to prevent, and it was reachable: the solver
resolved a hardcoded ``<repo>/fixtures/catalog/furniture.json`` while the API honoured
``GARH_CATALOG_DIR``, which docker-compose sets. So the override is the test.

The negative control is :func:`test_the_check_would_catch_a_reader_that_ignored_the_override`,
which points one reader at the repo default on purpose and requires this file's own
comparison to fail. Without it, a version of these tests that read nothing at all
would pass.
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterator
from typing import Any

import pytest

API_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPO_ROOT = os.path.abspath(os.path.join(API_ROOT, "..", ".."))

#: Deliberately not the shipped 2500 × 5000: a reader that fell back to the built-in
#: table, or to the repo's own file, would return that instead and be caught.
OVERRIDE_BAY = {"widthMm": 2700, "depthMm": 5400}


@pytest.fixture()
def overridden_catalogue(tmp_path: Any) -> Iterator[str]:
    """A catalogue directory with one differently-sized bay, via GARH_CATALOG_DIR."""
    from garh_api import catalog_data

    directory = tmp_path / "catalog"
    directory.mkdir()
    _source, shipped = catalog_data.load_catalog("furniture")
    items = [dict(item) for item in shipped if isinstance(item, dict)]
    for item in items:
        if str(item.get("id")) == "parking-bay":
            item.update(OVERRIDE_BAY)
            break
    else:  # pragma: no cover - the shipped catalogue is asserted to carry a bay below
        raise AssertionError("the shipped catalogue has no parking-bay to override")
    (directory / "furniture.json").write_text(json.dumps({"items": items}), encoding="utf-8")

    previous = os.environ.get("GARH_CATALOG_DIR")
    os.environ["GARH_CATALOG_DIR"] = str(directory)
    catalog_data.reset_catalog_cache()
    try:
        yield str(directory)
    finally:
        if previous is None:
            os.environ.pop("GARH_CATALOG_DIR", None)
        else:
            os.environ["GARH_CATALOG_DIR"] = previous
        catalog_data.reset_catalog_cache()


def _three_readers() -> dict[str, tuple[int, int] | None]:
    from garh_api.catalog_data import load_catalog
    from garh_api.parking_geometry import bay_footprint_mm, catalog_bay_size_mm

    from services.solver.parking import bay_size_mm

    _source, catalog = load_catalog("furniture")
    return {
        "the rule measures": bay_footprint_mm([item for item in catalog if isinstance(item, dict)]),
        "the sheet draws": catalog_bay_size_mm(),
        "the solver places": bay_size_mm(),
    }


def test_the_shipped_catalogue_carries_exactly_one_bay_and_all_three_agree() -> None:
    sizes = _three_readers()
    assert all(size is not None for size in sizes.values()), sizes
    assert len(set(sizes.values())) == 1, (
        "the three readers of the parking bay disagree: %s" % sizes
    )


def test_an_overridden_catalogue_moves_all_three_together(overridden_catalogue: str) -> None:
    expected = (OVERRIDE_BAY["widthMm"], OVERRIDE_BAY["depthMm"])
    sizes = _three_readers()
    assert len(set(sizes.values())) == 1, (
        "GARH_CATALOG_DIR moved some readers and not others: %s" % sizes
    )
    assert sizes["the solver places"] == expected, (
        "a reader ignored GARH_CATALOG_DIR and answered %s instead of %s — the solver "
        "would place a bay the rule does not measure" % (sizes, expected)
    )


def test_the_check_would_catch_a_reader_that_ignored_the_override(
    overridden_catalogue: str,
) -> None:
    """Negative control: a reader pinned to the repo file must break the comparison."""
    from services.solver.parking import bay_size_mm

    repo_default = os.path.join(REPO_ROOT, "fixtures", "catalog", "furniture.json")
    stubborn = bay_size_mm(repo_default)
    honest = bay_size_mm()
    assert (
        stubborn != honest
    ), "the override did not change what the solver reads, so this file proves nothing"
    assert honest == (OVERRIDE_BAY["widthMm"], OVERRIDE_BAY["depthMm"])
