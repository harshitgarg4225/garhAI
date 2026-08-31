"""One brief, two vocabularies — and the gate that stops a third appearing.

The web app and the API grew separate names for the same brief fields. The Brief form,
the completeness meter, the free-text chips and the LLM parser all write
``parkingCount``; compliance, the solver enqueue, the seed and the project templates all
read ``carParking``. Nothing translated.

The consequence was not a missing chip. It was that EVERY plan a real user generated —
form-filled or parsed — was rejected by ``blr.parking.plot.le240`` ("0 car spaces are
shown, this plot needs at least 1") after the solver had already found it. The seed
passed because the seed hard-codes the API's spelling, and its own comment says why:

    "carParking": 1,  # declared, or blr.parking.plot.le240 rejects every candidate

That was the fifth field in this area written under one name and read under another, so
the last test in this file is the one that matters most: it asserts the alias table
still covers what the web actually writes, so a sixth is caught here rather than in a
rejected plan nobody can explain.
"""

from __future__ import annotations

import re
from pathlib import Path

from garh_api.brief_aliases import BRIEF_ALIASES, canonical_brief_data

REPO_ROOT = Path(__file__).resolve().parents[3]


def test_the_parsers_spelling_reaches_the_readers_name() -> None:
    """The defect, as an assertion. `carParking` was absent, so parking read as 0."""
    assert canonical_brief_data({"parkingCount": 1})["carParking"] == 1


def test_an_explicit_canonical_value_wins() -> None:
    """A brief carrying both is a client that already knows the API's spelling, and
    that answer is the more deliberate one."""
    assert canonical_brief_data({"parkingCount": 1, "carParking": 2})["carParking"] == 2


def test_the_alias_is_kept_as_well_as_translated() -> None:
    """Additive, never destructive: the web app reads its own field names back, and a
    round trip through this must not lose one."""
    out = canonical_brief_data({"parkingCount": 1})
    assert out["parkingCount"] == 1


def test_a_brief_with_neither_gains_nothing() -> None:
    """Negative control: it must not invent a declaration. Declaring parking nobody
    asked for would pass a rule on a drawing that shows no parking."""
    assert "carParking" not in canonical_brief_data({"bedrooms": 3})


def test_a_null_alias_is_not_a_declaration() -> None:
    assert "carParking" not in canonical_brief_data({"parkingCount": None})


def test_zero_is_a_real_answer_and_survives() -> None:
    """ "No parking" is a statement, not a missing value — and it is the one that makes
    the rule fail honestly rather than by accident."""
    assert canonical_brief_data({"parkingCount": 0, "carParking": 0})["carParking"] == 0


def test_the_input_is_not_mutated() -> None:
    original = {"parkingCount": 1}
    canonical_brief_data(original)
    assert original == {"parkingCount": 1}


def test_storeys_is_deliberately_not_an_alias() -> None:
    """`floorsAboveGround` and `storeys` mean different numbers for the same house —
    G+1 is one floor above ground and two storeys — so they need arithmetic, not a
    rename. `solver_enqueue._resolve_storeys` does that conversion explicitly, and
    listing them here would silently equate 1 with 2."""
    assert "floorsAboveGround" not in BRIEF_ALIASES
    assert "storeys" not in BRIEF_ALIASES


# ===========================================================================
# The gate: the two vocabularies must stay reconciled
# ===========================================================================
def _web_brief_fields() -> set[str]:
    """Field names the web app's BriefData declares."""
    source = (REPO_ROOT / "apps/web/src/features/brief/types.ts").read_text(encoding="utf-8")
    block = source[source.index("interface BriefData") :]
    block = block[: block.index("\n}")]
    return set(re.findall(r"^\s*readonly (\w+)\??:", block, re.MULTILINE))


def _api_read_fields() -> set[str]:
    """Brief keys the API actually reads out of `brief_data`."""
    found: set[str] = set()
    for path in (REPO_ROOT / "apps/api/garh_api").rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        found.update(re.findall(r"brief_data\.get\(\s*[\"'](\w+)[\"']", text))
        found.update(re.findall(r"brief_data\[\s*[\"'](\w+)[\"']\s*\]", text))
    return found


def test_every_field_the_api_reads_is_one_the_web_can_write() -> None:
    """The gate that would have caught all five.

    A key the API reads that no web field produces is a value that is always absent —
    which is exactly how "0 car spaces are shown" happened, and how it stayed hidden:
    nothing errored, the plan was simply always rejected.

    An alias closes the gap; so does renaming one side. What must not happen is a sixth
    silent pair.
    """
    web = _web_brief_fields()
    assert web, "could not read BriefData — has types.ts moved?"
    reachable = web | set(BRIEF_ALIASES.values())
    # Values the API DERIVES rather than asks for. `dwellingUnits` is 1 for the single
    # house this product plans, and `floorsAboveGround` is computed from `storeys`; a
    # form field for either would be a question with one possible answer.
    derived = {"floorsAboveGround", "rooms", "adjacency", "dwellingUnits"}
    orphans = {key for key in _api_read_fields() if key not in reachable and key not in derived}
    assert not orphans, (
        "the API reads brief fields the web app never writes, so they are always "
        "absent: %s. Add an alias in BRIEF_ALIASES, or rename one side." % sorted(orphans)
    )
