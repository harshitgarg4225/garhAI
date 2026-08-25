"""Tests for the §7 drawings service.

Every module here bootstraps its own ``sys.path`` and installs the ``dev_stubs`` worker
dependency stand-ins at import time, so each file runs two ways: under pytest in CI, and
under ``python3 services/drawings/tests/test_render.py`` on a machine with no packages
installed. The second matters for this package in particular — the SVG renderer, the
dimension arithmetic and the area statement are pure integer code specifically so they
can be proven anywhere, and a test that needed a toolchain would give that away.
"""
