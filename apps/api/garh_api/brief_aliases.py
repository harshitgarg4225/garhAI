"""One brief, two vocabularies — and the map between them.

The web app and the API grew separate names for the same brief fields. The Brief form,
the completeness meter, the free-text parse chips and the LLM parser all write
``parkingCount``; compliance, the solver enqueue, the seed and the project templates all
read ``carParking``. Nothing translated, so the declaration never arrived: every plan a
real user generated was rejected by ``blr.parking.plot.le240`` — "0 car spaces are shown,
this plot needs at least 1" — while the seed passed, because the seed hard-codes the
API's spelling. Its own comment says so:

    "carParking": 1,  # declared, or blr.parking.plot.le240 rejects every candidate

That is the fifth field in this area found written under one name and read under
another, so this module exists to make the *class* of bug visible rather than patch the
fifth instance: the aliases live in one table, `test_brief_aliases.py` asserts the table
covers what the web actually writes, and a sixth alias has an obvious home.

## Canonical is what the readers read

The API side wins, because it is the side with the rules engine behind it and the side a
drawing is judged by. Normalising happens on the way IN — the stored brief keeps
whatever the client sent, so a form that round-trips its own field names keeps working
and no migration is needed.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

__all__ = ["BRIEF_ALIASES", "canonical_brief_data"]

#: ``alias written by the web/parser`` → ``name the API reads``.
#:
#: Only add a pair here when the two genuinely mean the same thing in the same units.
#: ``floorsAboveGround`` and ``storeys`` are NOT in this table for that reason: G+1 is
#: one floor above ground and two storeys, so they need arithmetic, not a rename, and
#: `solver_enqueue._resolve_storeys` does that conversion explicitly.
BRIEF_ALIASES: Mapping[str, str] = {
    "parkingCount": "carParking",
}


def canonical_brief_data(data: Mapping[str, Any] | None) -> dict[str, Any]:
    """A copy of ``data`` with every alias also present under the name readers use.

    Additive, never destructive: the alias is kept alongside the canonical key so a
    round trip through this function cannot lose a field the web app expects back. An
    explicit canonical value always wins — a brief carrying both is a form that already
    knows the API's spelling, and that answer is the more deliberate one.
    """
    out = dict(data or {})
    for alias, canonical in BRIEF_ALIASES.items():
        if canonical in out and out[canonical] is not None:
            continue
        if alias in out and out[alias] is not None:
            out[canonical] = out[alias]
    return out
