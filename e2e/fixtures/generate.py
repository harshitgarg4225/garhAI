#!/usr/bin/env python3
"""Generate ``e2e/fixtures/base-plan.ops.json`` from the model core's own fixture.

Why this file exists
--------------------
The Phase-6 copilot spec needs a project whose document contains the FIXED
element ids ``garh_model.testing`` mints (``wall_01J…WSP``,
``room_3STWE7…``). That is not an accident of the test: the mock LLM provider's
corpus (``services/llm/fixtures/copilot-commands.json``) proposes ops against
exactly those ids, and the API's dry-run fold correctly refuses ops that name
elements the document does not have. A plan drawn by the spec's own wall tool
would have fresh ULIDs, every mock response would fail the fold, and the spec
would be asserting the refusal path while claiming to test the happy one.

The Playwright project cannot import the fixture directly: ``@garh/e2e`` depends
on Playwright, Node types and TypeScript, and nothing else — no
``@garh/model`` build step, no Python. So the op log is materialised here, once,
from the REAL source, and the spec reads the JSON.

Run:      python3 e2e/fixtures/generate.py
Check:    python3 e2e/fixtures/generate.py --check      (exit 1 on drift)

The check mode is the drift guard: if ``two_room_plan_ops()`` or ``opening_ops()``
ever changes, this exits non-zero and the file is regenerated deliberately
rather than discovered stale by a red spec three phases later. Wire it into CI
next to ``fixtures/llm/copilot-commands/_tools/generate.py --check``, which is
the same pattern for the same reason.
"""

from __future__ import annotations

import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.abspath(os.path.join(_HERE, "..", ".."))
for _path in (_ROOT, os.path.join(_ROOT, "apps", "api")):
    if _path not in sys.path:
        sys.path.insert(0, _path)

OUTPUT = os.path.join(_HERE, "base-plan.ops.json")


def build() -> dict:
    from garh_model.testing import FIXTURE_IDS, opening_ops, ops_to_json, two_room_plan_ops

    ops = ops_to_json(list(two_room_plan_ops()) + list(opening_ops()))
    return {
        "$comment": (
            "DERIVED — do not edit. Regenerate with `python3 e2e/fixtures/generate.py`. "
            "The two-room plan with openings from garh_model.testing, as wire ops. The "
            "copilot mock corpus proposes edits against these exact ids, so the e2e "
            "project must be built from this log and not by drawing."
        ),
        "source": "garh_model.testing.two_room_plan_ops() + opening_ops()",
        "ids": {str(k): str(v) for k, v in FIXTURE_IDS.items()},
        "ops": ops,
    }


def main(argv: list[str]) -> int:
    payload = build()
    text = json.dumps(payload, indent=2, sort_keys=False) + "\n"

    if "--check" in argv:
        if not os.path.exists(OUTPUT):
            print("MISSING %s — run `python3 e2e/fixtures/generate.py`." % OUTPUT)
            return 1
        with open(OUTPUT, "r", encoding="utf-8") as handle:
            current = handle.read()
        if current != text:
            print(
                "DRIFT: %s no longer matches garh_model.testing.\n"
                "The copilot e2e spec builds its project from this log and the mock\n"
                "corpus targets its ids. Regenerate with:\n"
                "  python3 e2e/fixtures/generate.py" % OUTPUT
            )
            return 1
        print("ok — base-plan.ops.json matches garh_model.testing (%d ops)." % len(payload["ops"]))
        return 0

    with open(OUTPUT, "w", encoding="utf-8") as handle:
        handle.write(text)
    print("wrote %s (%d ops)" % (OUTPUT, len(payload["ops"])))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
