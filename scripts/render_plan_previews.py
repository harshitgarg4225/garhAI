"""Render fixtures/plans/<id>.svg for every recipe, through the sheets' own primitives.

    PYTHONPATH=.:apps/api python scripts/render_plan_previews.py

Deterministic: the same recipe produces the same bytes, which is what the golden test
in apps/api/tests/test_plan_library.py checks.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "apps" / "api")]

from garh_api.template_preview import preview_svg  # noqa: E402


def main() -> int:
    plans = sorted((ROOT / "fixtures" / "plans").glob("*.json"))
    if not plans:
        print("no recipes in fixtures/plans")
        return 1
    for path in plans:
        record = json.loads(path.read_text())
        svg = preview_svg(record["ops"])
        path.with_suffix(".svg").write_text(svg)
        print("  %-24s %6d bytes" % (path.stem, len(svg)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
