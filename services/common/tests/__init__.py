"""Tests for the shared worker runtime (``services/common``).

Collected by ``pytest -c apps/api/pyproject.toml services`` — the same invocation
``make test-py`` and the CI unit job use. Kept as a package (rather than bare test
files) so ``services`` stays importable as a namespace from the repo root without
``conftest.py`` path surgery.
"""
