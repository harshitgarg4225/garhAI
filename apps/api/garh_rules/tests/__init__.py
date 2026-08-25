"""Tests for the rules engine.

``fixtures/rules/index.json`` is the gate (``test_fixtures.py``): every rule in
every pack must have a passing and a failing fixture, and both must produce the
expected row. A rule with a missing fixture is a test failure, not a skip.
"""
