"""Solver test fixtures.

The one job this file must do before anything else: install stand-ins for the
worker dependencies that are absent on a bare machine, *before* pytest imports
any test module. ``services.solver`` re-exports through ``services.common``,
which imports structlog and pydantic at module scope, so without this the
ortools-free solver modules cannot be collected at all — even though they were
written specifically to be runnable without a full environment.

A real installed package always wins (see ``services.common.dev_stubs``), so on
CI this is a no-op.
"""

from __future__ import annotations

import os
import sys

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

# apps/api is where garh_rules and garh_model live; the critic calls the rules engine.
_APPS_API = os.path.join(_REPO_ROOT, "apps", "api")
if _APPS_API not in sys.path:
    sys.path.insert(0, _APPS_API)

from services.dev_stubs import install_worker_dep_stubs  # noqa: E402

STUBBED = install_worker_dep_stubs()

import pytest  # noqa: E402


@pytest.fixture(scope="session")
def stubbed_dependencies() -> tuple[str, ...]:
    """Which worker dependencies were faked for this run (empty on CI).

    Tests that assert on real logging or real pydantic validation should skip
    when their dependency is in here, rather than asserting against a stub.
    """
    return STUBBED


@pytest.fixture(scope="session")
def repo_root() -> str:
    return _REPO_ROOT


@pytest.fixture(scope="session")
def rulepack_dir(repo_root: str) -> str:
    return os.path.join(repo_root, "rulepacks")


@pytest.fixture(scope="session")
def furniture_catalog_path(repo_root: str) -> str:
    return os.path.join(repo_root, "fixtures", "catalog", "furniture.json")
