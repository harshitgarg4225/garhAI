"""Tests for the §10 LLM layer (``services/llm``).

Collected by ``pytest -c apps/api/pyproject.toml services`` — the same invocation
``make test-py`` and the CI unit job use. Kept as a package (rather than bare test
files) so ``services`` stays importable as a namespace from the repo root without
``conftest.py`` path surgery.

Nothing here needs a database, a network or an API key: the provider is either the
real fixture-driven mock or a scripted double from :mod:`services.llm.tests.doubles`.
"""
