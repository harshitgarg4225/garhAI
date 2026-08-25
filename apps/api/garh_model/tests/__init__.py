"""Tests for :mod:`garh_model` — the Python side of the model core.

These live inside the package (rather than ``apps/api/tests``) because the model
core is the one component that ships as a library to the API AND the workers,
and its tests are part of the contract it ships. Run them with::

    pytest apps/api/garh_model/tests

``pyproject.toml``'s ``testpaths`` points at ``apps/api/tests``, so CI must name
this directory explicitly (or add it to ``testpaths``) — see the note in
``apps/api/garh_model/tests/conftest.py``.
"""
