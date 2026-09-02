"""The worker image must carry the catalogue the solver opens — and say so if it doesn't.

Execution find, first trial architect's first Generate (2026-09-02): ``services/Dockerfile``
copied ``services``, ``apps/api``, ``packages/model`` and ``rulepacks`` into the worker
image but not ``fixtures/``. ``furniture_fit.load_catalog`` opens
``fixtures/catalog/furniture.json`` from the repo root after stage B, so every job
raised ``FileNotFoundError``, the runtime retried it four times, and the architect
lost two of ten free generations to "something went wrong on our side".

Two gates, so this cannot recur silently:

1. The Dockerfile's prod stage copies the directory the catalogue lives in. A path
   check against the real Dockerfile text — CI's compose e2e runs the dev stage with
   a bind mount, so it never executed the prod stage's COPY list.
2. A missing catalogue is a PERMANENT, path-free worker error, not a bare
   ``FileNotFoundError``: one attempt, honest copy, no retry budget burned.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

import pytest

from services.common.errors import is_retryable, user_facing
from services.solver.furniture_fit import (
    CatalogUnavailableError,
    default_catalog_path,
    load_catalog,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
DOCKERFILE = REPO_ROOT / "services" / "Dockerfile"


def _prod_stage_copy_sources(text: str) -> list[str]:
    """Every COPY source in the stage named ``prod`` (the one Railway builds)."""
    stage = text.split("FROM base AS prod", 1)
    assert len(stage) == 2, "services/Dockerfile no longer has a `prod` stage"
    sources: list[str] = []
    for match in re.finditer(r'^COPY \["([^"]+)",\s*"[^"]+"\]', stage[1], re.M):
        sources.append(match.group(1))
    return sources


def test_the_prod_stage_copies_the_directory_the_catalogue_lives_in() -> None:
    catalogue = Path(default_catalog_path())
    relative = catalogue.relative_to(REPO_ROOT).as_posix()  # fixtures/catalog/furniture.json
    sources = _prod_stage_copy_sources(DOCKERFILE.read_text(encoding="utf-8"))
    covered = [
        src for src in sources if relative == src or relative.startswith(src.rstrip("/") + "/")
    ]
    assert covered, (
        f"services/Dockerfile's prod stage copies {sources} but nothing that contains "
        f"{relative} — every deployed generate job will die in furniture fit"
    )


def test_the_catalogue_the_dockerfile_copies_actually_exists() -> None:
    """Guards the guard: a COPY of a directory that is not in the repo is a build error."""
    assert os.path.isfile(default_catalog_path())


def test_a_missing_catalogue_is_permanent_and_names_no_path(tmp_path: Path) -> None:
    missing = tmp_path / "not-there" / "furniture.json"
    with pytest.raises(CatalogUnavailableError) as excinfo:
        load_catalog(str(missing))
    exc = excinfo.value
    assert is_retryable(exc) is False, "a missing data file will not appear on attempt 2"
    problem = user_facing(exc)
    assert problem["code"] == "catalog_unavailable"
    assert str(missing) not in problem["message"] and "not-there" not in str(problem)
    assert "fault on our side" in problem["action"]
