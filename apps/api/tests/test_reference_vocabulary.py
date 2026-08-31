"""The board's vocabulary is defined twice. It must never mean two things.

``garh_api.models`` owns the storage and its CHECK constraints; ``services.render.
references`` owns what each value does to a prompt. Two lists of the same strings in two
trees is exactly the shape that produced five silent defects in the brief (see
``docs/first-run-verification.md``) — a value written under one vocabulary and read
under another, failing with no error at all.

Here it would fail worse than silently: a scope the database accepts but the render side
does not recognise becomes a picture the architect annotated, saved, and which then
contributes nothing to any render, forever, with no message.
"""

from __future__ import annotations

from garh_api.models import REFERENCE_INTENTS, REFERENCE_SCOPES


def _render_side() -> tuple[tuple[str, ...], tuple[str, ...]]:
    import sys
    from pathlib import Path

    root = Path(__file__).resolve().parents[3]
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    from services.render.references import INTENTS, SCOPES

    return SCOPES, INTENTS


def test_the_scopes_are_the_same_list_in_both_trees() -> None:
    scopes, _ = _render_side()
    assert tuple(REFERENCE_SCOPES) == tuple(scopes)


def test_the_intents_are_the_same_list_in_both_trees() -> None:
    _, intents = _render_side()
    assert tuple(REFERENCE_INTENTS) == tuple(intents)


def test_the_database_constraint_lists_exactly_those_scopes() -> None:
    """The CHECK is written out in the migration by hand. A scope added to the model but
    not the constraint is a save that 500s; one added to the constraint but not the
    model is a value nothing can produce."""
    from pathlib import Path

    migration = (
        Path(__file__).resolve().parents[1] / "migrations/versions/0008_project_references.py"
    ).read_text(encoding="utf-8")
    for scope in REFERENCE_SCOPES:
        assert '"%s"' % scope in migration, scope
    for intent in REFERENCE_INTENTS:
        assert '"%s"' % intent in migration, intent
