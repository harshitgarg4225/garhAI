#!/usr/bin/env python3
"""Regenerate (or --check) the brief-parse eval corpus.

The corpus is DERIVED: each fixture's ``expected`` is the exact output of the
deterministic mock parser (``services/llm/brief_mock.py``) for its ``text``. That is
the point — the corpus pins the parser, and the same files are the contract shape the
real Anthropic provider is held to (``apps/api/tests/test_brief_parse.py`` validates
every ``expected`` against ``BRIEF_PARSE_SCHEMA``).

House rules (fixtures/README.md):

* ``--check`` exits non-zero if any file differs from what the parser produces now —
  CI runs this, so the parser and the corpus cannot drift apart silently.
* Regenerating is a deliberate act: run without flags, read the diff, commit both the
  parser change and the corpus change together with a note.
* Adding a brief: add a ``(id, text)`` row to ``TEXTS`` below and regenerate.

Stdlib-only, runs on any Python >= 3.9: modules are loaded by file path so neither
pydantic nor the ``services`` package import chain is needed.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
CORPUS_DIR = HERE.parent
REPO_ROOT = CORPUS_DIR.parents[2]  # fixtures/llm/brief-parse → fixtures/llm → fixtures → root

COMMENT = (
    "Brief-parse eval fixture (playbook §10). `text` is a real-world Indian client "
    "brief; `expected` is the exact output of the deterministic mock parser "
    "(services/llm/brief_mock.py) AND the contract shape the real provider is held "
    "to. Regenerate deliberately with fixtures/llm/brief-parse/_tools/generate.py "
    "and read the diff."
)

#: The corpus. Ordered; ids are stable and match the filenames.
TEXTS: tuple = (
    ("brief-parse-01-hinglish-3bhk-pooja",
     "3BHK, pooja room chahiye, budget 60 lakh, plot 30x40 north facing"),
    ("brief-parse-02-4bhk-duplex-strict-vastu",
     "4 BHK duplex with home theatre, strict vastu, 2 car parking, G+2, budget 1.5 crore"),
    ("brief-parse-03-2bhk-elderly-single-storey",
     "2BHK single storey for elderly parents, ground floor only, budget 35 lakhs, no stairs please"),
    ("brief-parse-04-family5-study-stilt-basement",
     "Family of 5, 3 bedrooms, study for wfh, servant room, stilt parking, basement for storage"),
    ("brief-parse-05-hinglish-kamre-rasoi",
     "Ghar chahiye 30x50 ke plot pe, 3 kamre, badi rasoi, pooja ghar aur parking 2 gaadi"),
    ("brief-parse-06-5bhk-villa-lift",
     "Modern 5 bedroom villa on 50x80, G+1, home office, 3 bathrooms, lift, budget ₹2.5 cr"),
    ("brief-parse-07-1bhk-rental",
     "Simple 1BHK for rental, single floor, budget 18 lakh"),
    ("brief-parse-08-3bhk-compact-l-budget",
     "3BHK G+1, vastu compliant, east facing plot, guest room, 2 balconies, ₹75L budget"),
    ("brief-parse-09-everything-assumed",
     "Make me a house"),
    ("brief-parse-10-joint-family-g3",
     "Joint family 8 members, 6 bedrooms, 2 kitchens, G+3, 3 cars, pooja room, budget 3 crore"),
    ("brief-parse-11-compact-20x30",
     "Compact home on 20x30 site, 2 bed rooms, 1 bathroom, scooter parking only, budget 25 lacs"),
    ("brief-parse-12-no-vastu-bungalow",
     "Retirement bungalow, no vastu, big verandah, garden, single storey, 2 crore budget"),
)


def _load(name: str, relative: str):
    spec = importlib.util.spec_from_file_location(name, str(REPO_ROOT / relative))
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def main() -> int:
    check = "--check" in sys.argv[1:]
    brief_mock = _load("garh_brief_mock", "services/llm/brief_mock.py")
    schemas = _load("garh_llm_schemas", "services/llm/schemas.py")
    jsl = _load("garh_jsonschema_lite", "services/common/jsonschema_lite.py")
    validator = jsl.SchemaValidator(schemas.BRIEF_PARSE_SCHEMA)

    problems = []
    expected_files = set()
    for fixture_id, text in TEXTS:
        expected = brief_mock.synthesize_brief_parse(text)
        if expected != brief_mock.synthesize_brief_parse(text):
            problems.append("%s: parser is not deterministic" % fixture_id)
            continue
        failures = validator.validate(expected)
        if failures:
            problems.append(
                "%s: parser output violates BRIEF_PARSE_SCHEMA:\n%s"
                % (fixture_id, jsl.format_errors(failures))
            )
            continue
        document = {"$comment": COMMENT, "id": fixture_id, "text": text, "expected": expected}
        rendered = json.dumps(document, indent=2, ensure_ascii=False) + "\n"
        path = CORPUS_DIR / ("%s.json" % fixture_id)
        expected_files.add(path.name)
        if check:
            if not path.is_file():
                problems.append("%s: missing — run generate.py" % path.name)
            elif path.read_text(encoding="utf-8") != rendered:
                problems.append("%s: stale — parser output changed; regenerate" % path.name)
        else:
            path.write_text(rendered, encoding="utf-8")

    strays = [
        p.name for p in sorted(CORPUS_DIR.glob("brief-parse-*.json"))
        if p.name not in expected_files
    ]
    if strays:
        problems.append("stray fixture(s) not in TEXTS: %s" % ", ".join(strays))

    if problems:
        print("brief-parse corpus %s FAILED:" % ("check" if check else "generate"))
        for problem in problems:
            print("  - %s" % problem)
        return 1
    print(
        "brief-parse corpus %s: %d fixtures OK"
        % ("check" if check else "regenerated", len(TEXTS))
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
