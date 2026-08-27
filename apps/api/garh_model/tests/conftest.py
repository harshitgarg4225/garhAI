"""Shared fixtures and path wiring for the model-core tests.

CI NOTE: ``apps/api/pyproject.toml`` sets ``testpaths = ["tests"]``, so a bare
``pytest`` from ``apps/api`` will NOT collect this directory. Run it explicitly::

    pytest apps/api/garh_model/tests

or add ``garh_model/tests`` to ``testpaths`` (that file is owned by the API
agent, so this mirror does not edit it).

The repo-root fixtures directory is located relative to this file so the tests
work from any working directory; ``GARH_MODEL_FIXTURES_DIR`` overrides it for
containers that mount ``fixtures/`` elsewhere.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest

from garh_model.model import ProjectDoc
from garh_model.testing import make_two_room_plan, make_two_room_plan_with_openings

#: ``<repo>/apps/api/garh_model/tests/conftest.py`` -> ``<repo>``
REPO_ROOT = Path(__file__).resolve().parents[4]


def fixtures_dir() -> Path:
    """``fixtures/`` at the repo root, or ``$GARH_MODEL_FIXTURES_DIR``."""
    override = os.environ.get("GARH_MODEL_FIXTURES_DIR")
    if override:
        return Path(override)
    return REPO_ROOT / "fixtures"


def load_fixture(*parts: str) -> Any:
    """Read and parse a JSON fixture, e.g. ``load_fixture('model', 'golden-states.json')``."""
    import json

    path = fixtures_dir().joinpath(*parts)
    with path.open(encoding="utf-8") as fh:
        return json.load(fh)


@pytest.fixture()
def two_room_plan() -> ProjectDoc:
    """The canonical 6000x4000 two-room ground floor."""
    return make_two_room_plan()


@pytest.fixture()
def two_room_plan_with_openings() -> ProjectDoc:
    """The two-room plan plus a main door and a west window."""
    return make_two_room_plan_with_openings()


@pytest.fixture(scope="session")
def golden_states() -> list[dict[str, Any]]:
    """Rows of ``fixtures/model/golden-states.json`` (the cross-language sync check)."""
    data = load_fixture("model", "golden-states.json")
    return list(data["cases"])
