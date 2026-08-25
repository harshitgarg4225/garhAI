"""Build the default sheet set, compose a floor plan, and print it. **Executable.**

    python "services/drawings/sheets/smoke.py"

Run it as a **file** for the same reason as ``projection/smoke.py``: importing
``services.drawings`` pulls in ``services.common`` → ``structlog``, which is a real worker
dependency and absent on a bare machine, so the bootstrap installs the established
stand-ins before the first repo import.

What this proves, and it is the thing §7 warns hardest about: the mm-to-paper transform is
right. A 8.0m building at 1:100 must land in 80mm of paper, the border must sit at the
frame margins, and a 2.5mm letter must come back out of the pipeline 2.5mm tall. Those are
the numbers printed below — if the scale were inverted or squared, every one of them would
be visibly absurd instead of subtly wrong.
"""

from __future__ import annotations

import os
import sys
from typing import Tuple

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.abspath(os.path.join(_HERE, "..", "..", ".."))
_APPS_API = os.path.join(_REPO_ROOT, "apps", "api")


def bootstrap() -> Tuple[str, ...]:
    for path in (_REPO_ROOT, _APPS_API):
        if path not in sys.path:
            sys.path.insert(0, path)
    from services.dev_stubs import install_worker_dep_stubs

    return install_worker_dep_stubs()


def main() -> int:
    stubbed = bootstrap()
    from services.drawings.projection import Text, count_by_layer, validate_primitives
    from services.drawings.projection.smoke import demo_doc
    from services.drawings.sheets import (
        PAPER_UM_PER_MM,
        TitleBlock,
        build_sheet_set,
        compose_plan_sheet,
        plan_options_for,
    )

    print("=" * 78)
    print("§7 sheet set smoke — services/drawings/sheets")
    print("stubbed worker deps: %s" % (", ".join(stubbed) or "none (real packages present)"))

    doc = demo_doc()
    title_block = TitleBlock(
        firm_name="Studio Demo",
        project_name="Demo Residence — 30x40 Bengaluru",
        client_name="R. Kumar",
        date="21-08-2026",
        drawn_by="HG",
        checked_by="—",
        notes="ADVISORY — NOT AN APPROVAL",
    )
    sheets = build_sheet_set(doc.house, title_block=title_block)
    print("")
    print("%-6s %-22s %-9s %s" % ("NO", "TITLE", "SCALE", "VIEWPORT"))
    for sheet in sheets:
        print(
            "%-6s %-22s %-9s %s"
            % (sheet.number, sheet.title, sheet.scale.label, sheet.viewport.to_json())
        )

    options = plan_options_for(sheets, north_deg=doc.plot.north_deg)
    plan_sheet = next(sheet for sheet in sheets if sheet.kind == "floor-plan")
    composed = compose_plan_sheet(plan_sheet, doc.house, plan_options=options)
    validate_primitives(composed.primitives)

    paper = plan_sheet.frame.paper
    print("")
    print("-" * 78)
    print("composed %s (%s) on %s %dx%dmm" % (plan_sheet.number, plan_sheet.title, paper.name, paper.width_mm, paper.height_mm))
    print("transform: %s" % composed.transform.to_json())
    print("fits: %s (needs %.1f x %.1fmm, has %.1f x %.1fmm)"
          % (
              composed.fit.fits,
              composed.fit.required_paper_mm[0],
              composed.fit.required_paper_mm[1],
              composed.fit.available_paper_mm[0],
              composed.fit.available_paper_mm[1],
          ))
    for warning in composed.warnings:
        print("warning: %s" % warning)
    print("primitives: %d" % len(composed.primitives))
    for layer, count in count_by_layer(composed.primitives).items():
        print("  %-12s %4d" % (layer, count))

    xs = []
    ys = []
    for item in composed.primitives:
        from services.drawings.projection import bbox_of

        box = bbox_of([item])
        if box is not None:
            xs.extend((box[0], box[2]))
            ys.extend((box[1], box[3]))
    print(
        "paper extent: x %.1f..%.1fmm, y %.1f..%.1fmm"
        % (
            min(xs) / PAPER_UM_PER_MM,
            max(xs) / PAPER_UM_PER_MM,
            min(ys) / PAPER_UM_PER_MM,
            max(ys) / PAPER_UM_PER_MM,
        )
    )
    heights = sorted(
        {item.height_mm / PAPER_UM_PER_MM for item in composed.primitives if isinstance(item, Text)}
    )
    print("text heights on paper (mm): %s" % ", ".join("%.2f" % h for h in heights))
    print("")
    print("OK — sheet set built and one plan composed, with no ezdxf and no I/O.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
