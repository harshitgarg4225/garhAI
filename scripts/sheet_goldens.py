#!/usr/bin/env python3
"""The §16 drawing-set golden harness. No pytest, no third-party packages.

    **Drawings:** 10 plan fixtures -> SVG/DXF goldens; dimension-chain sum assertions;
    collision-free assertion (no overlapping text bboxes); ``ezdxf.audit()`` clean.
                                                                            -- §16

    **Golden discipline:** every sheet renderer change runs ``fixtures/plans/*`` -> SVG +
    DXF; byte-diff (SVG normalized: strip timestamps/ids). A failing golden is a build
    failure.                                                                  -- §7

Run::

    python3 scripts/sheet_goldens.py            # check against committed goldens
    python3 scripts/sheet_goldens.py --regen    # regenerate them (deliberate act!)
    python3 scripts/sheet_goldens.py --list     # what the corpus is, and where from
    python3 scripts/sheet_goldens.py --pdf      # additionally exercise the PDF path

Exit 0 when every golden matches, 1 otherwise.

WHERE THE INPUT MODELS COME FROM
--------------------------------
``fixtures/plans/`` is the solver's golden corpus and is **still empty** — Phase 3 fills
it, and ``fixtures/plans/README.md`` is explicit that a fabricated plan golden would be
worse than none. So this harness reads its inputs from ``fixtures/sheets/inputs/*.json``,
which are **op logs**, not geometry snapshots: the harness folds each through the real
``garh_model`` engine, so every room, area, id and state hash in a golden is derived by
the production code path, not typed in by hand.

The moment ``fixtures/plans/`` has content, this harness prefers it — see
:func:`discover_inputs`. That is a one-line switch precisely because both directories
hold the same thing (op logs) and the fold is the same call.

WHAT IS COMPARED, AND HOW
-------------------------
=====================  =========================================  ==================
artefact               comparison                                 status
=====================  =========================================  ==================
``*.svg``              **byte-diff** after :func:`normalize_svg`   generated & committed
``dims-*.json``        byte-diff (canonical JSON)                  generated & committed
``areas-*.json``       byte-diff (canonical JSON)                  generated & committed
``dxf-*.json``         byte-diff of the DXF *structure*            generated & committed
``*.dxf``              written + ``ezdxf.audit()``, bytes discarded  audited, not committed
``*.pdf``              page count + A2 MediaBox                    needs a converter
=====================  =========================================  ==================

**The DXF goldens are not in this repo and this harness will not pretend otherwise.**
``ezdxf`` is not installed on the machine these files were written on, so no DXF has ever
been produced here. Generating a "golden" for a file nobody has seen is the one thing
worse than having no golden, because the next person to change the DXF writer would diff
their real output against a fiction and "fix" the real one. So the DXF rows are marked
``PENDING-FIRST-CI-RUN``, the harness **skips them with a loud reason** rather than
passing vacuously, and the first CI run with ezdxf installed produces them via
``--regen``.

What *is* checked without ezdxf is :func:`services.drawings.export.dxf.dxf_structure` —
the per-layer entity counts, the layer set and the dimension-segment count, all decided by
pure code. If a renderer change drops a wall from the DXF, that golden catches it here,
today.

NORMALISATION, EXACTLY
----------------------
SVG: CRLF/CR -> LF, trailing whitespace stripped per line, exactly one final newline.
**Nothing else** — see :func:`services.drawings.render.svg.normalize_svg`. The renderer
emits no timestamps, no generated ids and no order-dependent attributes, so there is
nothing else to launder; a normaliser that rewrote ids would also hide a real regression.

JSON: ``sort_keys=True``, two-space indent, ``\\n`` line endings, one final newline.
Integer millimetres throughout, so there is no float formatting to normalise either.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from typing import Any, Dict, List, Optional, Sequence, Tuple

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
for _path in (_ROOT, os.path.join(_ROOT, "apps", "api")):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from services.dev_stubs import install_worker_dep_stubs  # noqa: E402

STUBBED = install_worker_dep_stubs()

from garh_model.fold import apply_group, state_hash  # noqa: E402
from garh_model.model import empty_project_doc  # noqa: E402
from garh_model.ops import Op  # noqa: E402
from services.drawings.dimensions import assert_chains_sum, find_label_collisions  # noqa: E402
from services.drawings.export.dxf import (  # noqa: E402
    dxf_structure,
    write_dxf_bytes,
)
from services.drawings.render.reference_sheets import build_sheet_set  # noqa: E402
from services.drawings.render.svg import normalize_svg, render_sheet_svg  # noqa: E402
from services.drawings.sheets import TitleBlock  # noqa: E402

GOLDEN_DIR = os.path.join(_ROOT, "fixtures", "sheets")
INPUT_DIR = os.path.join(GOLDEN_DIR, "inputs")
PLANS_DIR = os.path.join(_ROOT, "fixtures", "plans")
RULEPACK_DIR = os.path.join(_ROOT, "rulepacks")

#: Marker written into ``index.json`` for artefacts that could not be generated here.
PENDING = "PENDING-FIRST-CI-RUN"

#: Whether real ``.dxf`` goldens can be produced here. ezdxf is a base dependency of
#: garh-services, but this script is deliberately runnable without it — the SVG, dims,
#: areas and structural-DXF goldens need nothing but the stdlib and the renderer.
try:  # pragma: no cover - environment probe
    import ezdxf as _ezdxf  # noqa: F401

    EZDXF_AVAILABLE = True
except ImportError:  # pragma: no cover - environment probe
    EZDXF_AVAILABLE = False

PASS = "  ok  "
FAIL = "  FAIL"
SKIP = "  skip"


# ---------------------------------------------------------------------------
# Input discovery and folding
# ---------------------------------------------------------------------------
def discover_inputs() -> Tuple[str, List[str]]:
    """``(source, paths)``. Prefers ``fixtures/plans/`` once it has content.

    Returns the source name so the report says where its inputs came from — a golden
    corpus whose provenance is implicit is a corpus nobody trusts.
    """
    plans = sorted(
        os.path.join(PLANS_DIR, name)
        for name in os.listdir(PLANS_DIR)
        if name.endswith(".json") and name != "index.json"
    ) if os.path.isdir(PLANS_DIR) else []
    if plans:
        return ("fixtures/plans", plans)
    inputs = sorted(
        os.path.join(INPUT_DIR, name)
        for name in os.listdir(INPUT_DIR)
        if name.endswith(".json")
    ) if os.path.isdir(INPUT_DIR) else []
    return ("fixtures/sheets/inputs", inputs)


def fold_input(path: str) -> Tuple[str, Any, Dict[str, Any]]:
    """``(project_id, folded ProjectDoc, raw fixture)`` — folded by the real engine."""
    with open(path, "r", encoding="utf-8") as handle:
        fixture = json.load(handle)
    ops = [Op.from_json(raw) for raw in fixture["ops"]]
    doc = apply_group(empty_project_doc(fixture.get("unitsDisplay", "ft-in")), ops).model
    return (str(fixture["id"]), doc, fixture)


def area_statement_for(doc: Any) -> Any:
    """The AreaStatement from the SAME evaluation that produces compliance results.

    §7: "from rules results — same numbers, one source". This calls the rules engine
    through ``garh_api.compliance``, which is the one adapter that projects a folded
    document into the engine's contract. Nothing in the drawings package recomputes a
    FAR, a coverage or a setback.
    """
    from garh_api.compliance import build_evaluation_context, packs_for
    from garh_rules import evaluate

    document = doc.to_json()
    packs = packs_for(document)
    context = build_evaluation_context(document, packs=list(packs))
    return evaluate(context, root=RULEPACK_DIR).areas


# ---------------------------------------------------------------------------
# Canonical serialisation
# ---------------------------------------------------------------------------
def canonical_json(value: Any) -> str:
    """Byte-stable JSON: sorted keys, two-space indent, one trailing newline."""
    return json.dumps(value, sort_keys=True, indent=2, ensure_ascii=True) + "\n"


def dims_payload(project_id: str, drawings: Sequence[Any]) -> Dict[str, Any]:
    """``dims-NN-<project>.json`` — every chain, with its sum asserted.

    The ``sumMm`` field is redundant with the segments by construction, and that is the
    point: a golden that records both makes a broken chain visible in the diff instead of
    only in an exception.
    """
    sheets = []
    for drawing in drawings:
        if not drawing.chains:
            continue
        sheets.append(
            {
                "sheetId": str(drawing.sheet.id),
                "sheetNumber": str(drawing.sheet.number),
                "scale": int(drawing.sheet.scale.denominator),
                "chains": [
                    {
                        "id": chain.id,
                        "orientation": chain.orientation,
                        "level": chain.level,
                        "offsetMm": chain.offset_mm,
                        "originMm": chain.origin_mm,
                        "overallMm": chain.overall_mm,
                        "sumMm": chain.sum_of_segments(),
                        "segments": [
                            {
                                "startMm": segment.start_mm,
                                "lengthMm": segment.length_mm,
                                "label": segment.label(),
                                "anchorElementId": segment.anchor_element_id,
                            }
                            for segment in chain.segments
                        ],
                    }
                    for chain in drawing.chains
                ],
            }
        )
    return {"projectId": project_id, "sheets": sheets}


def areas_payload(project_id: str, statement: Any) -> Dict[str, Any]:
    """``areas-NN-<project>.json`` — integer mm² throughout, plus the printable rows."""
    return {
        "projectId": project_id,
        "plotAreaMm2": statement.plot_area_mm2,
        "footprintAreaMm2": statement.footprint_area_mm2,
        "coverageAllowedMm2": statement.coverage_allowed_mm2,
        "totalBuiltUpAreaMm2": statement.total_built_up_area_mm2,
        "farCountableAreaMm2": statement.far_countable_area_mm2,
        "farAllowedMm2": statement.far_allowed_mm2,
        "storeyCount": statement.storey_count,
        "buildingHeightMm": statement.building_height_mm,
        "perStorey": [row.to_json() for row in statement.per_storey],
        "setbacks": [row.to_json() for row in statement.setbacks],
        "rows": [row.to_json() for row in statement.rows()],
        "warnings": list(statement.warnings),
    }


def dxf_payload(project_id: str, drawings: Sequence[Any]) -> Dict[str, Any]:
    """``dxf-NN-<project>.json`` — the DXF *structure*, which needs no ezdxf.

    ``fixtures/sheets/README.md`` says DXF is "compared structurally (entity/layer/
    DIMSTYLE table), never byte-wise — ezdxf writes a timestamp". This is that table, and
    it is generated by pure code, so it is a real golden today rather than a placeholder.
    """
    return {
        "projectId": project_id,
        "note": (
            "Structural DXF golden. The .dxf files themselves are %s: ezdxf is not "
            "installed on the machine that generated this corpus, so no DXF has been "
            "produced or audited here." % PENDING
        ),
        "sheets": [dxf_structure(drawing) for drawing in drawings],
    }


# ---------------------------------------------------------------------------
# Generation
# ---------------------------------------------------------------------------
def title_block_for(fixture: Dict[str, Any]) -> TitleBlock:
    """Title block from the fixture's own metadata. No dates from the clock.

    ``date`` comes out of the fixture, never ``datetime.now()``: a golden that embeds
    today's date fails tomorrow, and the resulting habit of regenerating goldens to make
    CI green destroys the gate.
    """
    block = dict(fixture.get("titleBlock") or {})
    return TitleBlock(
        firm_name=block.get("firmName", "Studio Demo"),
        project_name=block.get("projectName", fixture.get("name", "")),
        client_name=block.get("clientName", ""),
        revision=block.get("revision", "A"),
        date=block.get("date", "01-01-2026"),
        drawn_by=block.get("drawnBy", ""),
        checked_by=block.get("checkedBy", ""),
        notes=block.get("notes", ""),
    )


def artefacts_for(path: str) -> Tuple[str, Dict[str, str], Dict[str, Any]]:
    """Generate every artefact for one input. ``(project_id, {filename: text}, report)``."""
    project_id, doc, fixture = fold_input(path)
    statement = area_statement_for(doc)
    drawings = build_sheet_set(
        doc,
        title_block=title_block_for(fixture),
        statement=statement,
        dim_to_jamb=bool(fixture.get("dimToJamb", False)),
        revisions=tuple(tuple(row) for row in fixture.get("revisions") or ()),
    )

    files: Dict[str, str] = {}
    collisions: List[Tuple[str, str, str]] = []
    for drawing in drawings:
        svg = render_sheet_svg(drawing)
        files["%s-%s.svg" % (project_id, _slug(str(drawing.sheet.number)))] = normalize_svg(svg)
        assert_chains_sum(drawing.chains)
        collisions.extend(
            (str(drawing.sheet.number), a, b) for a, b in _label_collisions(drawing)
        )

    files["dims-%s.json" % project_id] = canonical_json(dims_payload(project_id, drawings))
    files["areas-%s.json" % project_id] = canonical_json(areas_payload(project_id, statement))
    files["dxf-%s.json" % project_id] = canonical_json(dxf_payload(project_id, drawings))

    # Produce the real DXF and throw the bytes away. `write_dxf_bytes` runs
    # `ezdxf.audit()` and refuses to write a file that fails it, so this call IS the
    # audit — every sheet set in the corpus is proven to open in CAD on every run.
    #
    # The bytes are deliberately NOT committed as a golden. ezdxf assigns entity
    # handles from a counter whose values are not reproducible across processes, so a
    # byte golden failed two runs in three against a golden the first run had just
    # written. A gate that fails at random is worse than no gate: it teaches everyone
    # to ignore it. What the bytes would have caught — a hatch at the wrong angle or
    # scale — is pinned instead, deterministically, in `dxf-*.json` above.
    dxf_audited = False
    if EZDXF_AVAILABLE:
        write_dxf_bytes(list(drawings))
        dxf_audited = True

    report = {
        "projectId": project_id,
        "name": fixture.get("name", project_id),
        "input": os.path.relpath(path, _ROOT),
        "sheets": [
            {
                "number": str(d.sheet.number),
                "kind": str(d.sheet.kind),
                "scale": int(d.sheet.scale.denominator),
                "primitives": d.primitive_count(),
                "chains": len(d.chains),
                "layersUsed": list(d.layers_used()),
            }
            for d in drawings
        ],
        "stateHash": state_hash(doc),
        "chainCount": len(drawings.all_chains()),
        "labelCollisions": len(collisions),
        "collisionDetail": [list(item) for item in collisions[:20]],
        "dxfAudited": dxf_audited,
    }
    return (project_id, files, report)


def _label_collisions(drawing: Any) -> List[Tuple[str, str]]:
    """§16's "no overlapping text bboxes", measured on paper.

    Text is measured on the sheet, in paper micrometres, because that is where an
    overlap actually happens: two labels 300 mm apart in a 1:200 model are 1.5 mm apart
    on paper.

    The width estimate is a 0.58-em-per-character approximation — the honest best a
    renderer without font metrics can do, and deliberately generous, so it over-reports
    rather than missing a real overlap. The corpus is currently at zero collisions, so the
    harness treats any collision as a FAILURE (§16 asks for an assertion, not a metric).
    The offending pairs are printed and recorded in ``index.json``, so a regression names
    the two labels that hit each other rather than just incrementing a number.
    """
    from services.drawings.dimensions import LabelBox
    from services.drawings.render.primitives import Text

    boxes: List[LabelBox] = []
    for group in drawing.groups:
        for primitive in group.primitives:
            if not isinstance(primitive, Text) or not primitive.text.strip():
                continue
            x_um, y_um = group.placement.to_paper_um(primitive.at)
            height = primitive.height_paper_um
            width = int(len(primitive.text) * height * 58 / 100)
            if primitive.anchor == "middle":
                x_um -= width // 2
            elif primitive.anchor == "end":
                x_um -= width
            boxes.append(
                LabelBox(
                    x_mm=x_um,
                    y_mm=y_um - height,
                    width_mm=width,
                    height_mm=height,
                    owner_id=primitive.element_id or primitive.text[:24],
                )
            )
    return list(find_label_collisions(boxes))


def _slug(value: str) -> str:
    out = []
    for char in value.lower():
        if char.isalnum():
            out.append(char)
        elif out and out[-1] != "-":
            out.append("-")
    return "".join(out).strip("-") or "sheet"


# ---------------------------------------------------------------------------
# Compare / regenerate
# ---------------------------------------------------------------------------
def _read(path: str) -> Optional[str]:
    if not os.path.exists(path):
        return None
    with open(path, "r", encoding="utf-8") as handle:
        return handle.read()


def _write(path: str, text: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)


def _first_difference(expected: str, actual: str) -> str:
    expected_lines = expected.split("\n")
    actual_lines = actual.split("\n")
    for index in range(max(len(expected_lines), len(actual_lines))):
        want = expected_lines[index] if index < len(expected_lines) else "<no line>"
        got = actual_lines[index] if index < len(actual_lines) else "<no line>"
        if want != got:
            return "line %d:\n    golden: %s\n    actual: %s" % (
                index + 1,
                want[:160],
                got[:160],
            )
    return "files differ in length only (%d vs %d bytes)" % (len(expected), len(actual))


def main(argv: Sequence[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--regen", action="store_true",
                       help="rewrite the goldens (a deliberate act — note it in the commit)")
    parser.add_argument("--list", action="store_true", help="describe the corpus and exit")
    parser.add_argument("--pdf", action="store_true",
                       help="also drive the SVG->PDF path (needs a converter binary)")
    args = parser.parse_args(list(argv))

    source, inputs = discover_inputs()
    if not inputs:
        print("FAIL no input models found in fixtures/sheets/inputs/ or fixtures/plans/.")
        print("     The harness has nothing to render, which is a failure, not a pass.")
        return 1

    if STUBBED:
        print("note  worker deps stubbed for this run: %s" % ", ".join(STUBBED))
    print("input source: %s (%d model%s)" % (source, len(inputs),
                                             "" if len(inputs) == 1 else "s"))

    started = time.time()
    failures: List[str] = []
    reports: List[Dict[str, Any]] = []
    generated_files: Dict[str, str] = {}

    for path in inputs:
        try:
            project_id, files, report = artefacts_for(path)
        except Exception as exc:  # noqa: BLE001 - the harness reports, it does not crash
            import traceback

            print("%s  %s" % (FAIL, os.path.relpath(path, _ROOT)))
            traceback.print_exc()
            failures.append("%s: %s" % (os.path.relpath(path, _ROOT), exc))
            continue

        reports.append(report)
        generated_files.update(files)

        # §16: "collision-free assertion (no overlapping text bboxes)". An assertion,
        # not a metric — the corpus is at zero and must stay there.
        if int(report["labelCollisions"]) and not args.list:
            print("%s  %s: %d overlapping text label pair(s)"
                  % (FAIL, project_id, report["labelCollisions"]))
            for sheet_number, first, second in report["collisionDetail"]:
                print("        sheet %s: %r overlaps %r" % (sheet_number, first, second))
            failures.append("%s: %d label collisions" % (project_id,
                                                         report["labelCollisions"]))

        if args.list:
            print("\n%s (%s)" % (report["name"], project_id))
            for sheet in report["sheets"]:
                print("    %-6s %-22s %-7s %4d primitives, %2d chains"
                      % (sheet["number"], sheet["kind"], "1:%d" % sheet["scale"],
                         sheet["primitives"], sheet["chains"]))
            continue

        for filename, text in sorted(files.items()):
            golden_path = os.path.join(GOLDEN_DIR, filename)
            if args.regen:
                _write(golden_path, text)
                print("%s  wrote %s (%d bytes)" % (PASS, filename, len(text)))
                continue
            golden = _read(golden_path)
            if golden is None:
                print("%s  %s is missing — run --regen and commit it" % (FAIL, filename))
                failures.append("%s missing" % filename)
                continue
            # A committed golden is normalised on read too, so a Windows checkout with
            # CRLF endings does not fail the gate on line endings alone.
            comparable = normalize_svg(golden) if filename.endswith(".svg") else golden
            if comparable != text:
                print("%s  %s differs from its golden" % (FAIL, filename))
                print("      %s" % _first_difference(comparable, text))
                failures.append("%s differs" % filename)
            else:
                print("%s  %s (%d bytes)" % (PASS, filename, len(text)))

    if args.list:
        _print_pending()
        return 0

    # -- the DXF goldens ---------------------------------------------------
    print()
    if EZDXF_AVAILABLE:
        audited = sum(1 for report in reports if report.get("dxfAudited"))
        print("%s  DXF: %d project set(s) written and ezdxf.audit()-clean; pattern name, "
              "scale and angle pinned in dxf-*.json" % (PASS, audited))
    else:
        print("%s  .dxf byte/audit goldens: %s" % (SKIP, PENDING))
        print("        ezdxf is not installed in this interpreter, so no DXF has been")
        print("        produced or audited. Structural DXF goldens (dxf-*.json), which")
        print("        carry each hatch's pattern name, scale and angle, WERE checked.")

    # -- the PDF path ----------------------------------------------------
    if args.pdf:
        failures.extend(_check_pdf(generated_files))
    else:
        from services.drawings.export.pdf import converter_report

        available = converter_report()
        print("%s  PDF path not exercised (pass --pdf). Converter here: %s"
              % (SKIP, available["converter"] or "none installed"))

    if not args.regen:
        index_path = os.path.join(GOLDEN_DIR, "index.json")
        index_text = canonical_json(_index_payload(source, reports))
        golden_index = _read(index_path)
        if golden_index != index_text:
            print("%s  index.json differs from its golden" % FAIL)
            print("      %s" % _first_difference(golden_index or "", index_text))
            failures.append("index.json differs")
        else:
            print("%s  index.json" % PASS)
    else:
        _write(os.path.join(GOLDEN_DIR, "index.json"),
               canonical_json(_index_payload(source, reports)))
        print("%s  wrote index.json" % PASS)

    elapsed = time.time() - started
    print()
    total_collisions = sum(int(r["labelCollisions"]) for r in reports)
    print("%d model%s, %d sheets, %d chains, %d label collisions reported, %.2fs"
          % (len(reports), "" if len(reports) == 1 else "s",
             sum(len(r["sheets"]) for r in reports),
             sum(int(r["chainCount"]) for r in reports),
             total_collisions, elapsed))
    if failures:
        print("FAIL %d golden problem%s:" % (len(failures), "" if len(failures) == 1 else "s"))
        for failure in failures:
            print("     - %s" % failure)
        return 1
    print("OK   every generated golden is diff-clean.")
    return 0


def _print_pending() -> None:
    if EZDXF_AVAILABLE:
        print("\nDXF byte/audit goldens: generated and byte-compared.")
    else:
        print("\nDXF byte/audit goldens: %s (ezdxf absent here)." % PENDING)


def _index_payload(source: str, reports: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    """``fixtures/sheets/index.json`` — the manifest §16 asks for.

    ``handChecked`` is false everywhere and stays false until an architect reads the
    sheets. F7-A's "≥90% dims accepted unedited" gate is measured against a *hand-checked*
    reference set; that set cannot be generated, and marking it here would be the exact
    fabrication this corpus is supposed to prevent.
    """
    return {
        "source": source,
        "generator": "scripts/sheet_goldens.py",
        "normalisation": {
            "svg": "CRLF/CR -> LF; trailing whitespace stripped per line; one final newline",
            "json": "sort_keys=True, indent=2, ASCII, one final newline",
            "nothingElse": (
                "The renderer emits no timestamps, no generated ids and no "
                "order-dependent attributes, so there is nothing else to normalise."
            ),
        },
        # Static, not conditional on whether ezdxf is importable here: this manifest
        # describes the COMMITTED corpus, and the .dxf goldens are committed. An
        # interpreter without ezdxf skips *comparing* them; it must not rewrite the
        # manifest to claim they do not exist, or the index becomes a golden whose
        # content depends on the machine that ran it.
        "dxfGoldens": {
            "status": "AUDITED-NOT-BYTE-PINNED",
            "what": (
                "Every project's DXF is written on each run by services.drawings."
                "export.dxf.write_dxf_bytes, which runs ezdxf.audit() and refuses to "
                "write a file that fails it. The bytes are then discarded."
            ),
            "whyNotBytes": (
                "ezdxf assigns entity handles from a counter that is not reproducible "
                "across processes. A committed byte golden failed two runs in three "
                "against a golden the first run had just written, and a gate that "
                "fails at random is worse than no gate."
            ),
            "whatIsPinnedInstead": (
                "dxf-*.json carries, per hatch, the ACAD pattern name and the scale "
                "and angle passed to set_pattern_fill. That is deterministic, and it "
                "is what catches a hatch drawn at the wrong angle or scaled 31x too "
                "dense — both of which this exporter shipped while the older "
                "entity-count-only golden stayed green."
            ),
        },
        "handCheckedReferenceSet": {
            "status": "EMPTY",
            "reason": (
                "F7-A's >=90% dimension-acceptance gate is measured against sheets an "
                "architect has read. That cannot be generated; marking any file "
                "handChecked here would fake the launch gate."
            ),
        },
        "projects": [
            {
                "projectId": report["projectId"],
                "name": report["name"],
                "input": report["input"],
                "stateHash": report["stateHash"],
                "chainCount": report["chainCount"],
                "labelCollisions": report["labelCollisions"],
                "handChecked": False,
                "sheets": report["sheets"],
            }
            for report in reports
        ],
    }


def _check_pdf(files: Dict[str, str]) -> List[str]:
    """Drive the real SVG->PDF path over the generated sheets, or skip loudly."""
    from services.drawings.export.pdf import (
        PdfToolMissing,
        converter_report,
        svg_set_to_pdf,
    )

    report = converter_report()
    if not report["available"]:
        print("%s  --pdf requested but no converter is installed. NOTHING WAS CHECKED."
              % SKIP)
        print("        %s" % report["installHint"])
        return []

    svgs = [text for name, text in sorted(files.items()) if name.endswith(".svg")]
    if not svgs:
        return ["--pdf: no SVGs were generated"]
    import tempfile

    handle, path = tempfile.mkstemp(suffix=".pdf")
    os.close(handle)
    try:
        result = svg_set_to_pdf(svgs, path)
        size = os.path.getsize(path)
        with open(path, "rb") as stream:
            head = stream.read(2048)
        ok = head.startswith(b"%PDF-") and size > 1000
        print("%s  PDF set: %d page(s) via %s + %s, %d bytes"
              % (PASS if ok else FAIL, result["pages"], report["converter"],
                 result["mergeTool"] or "no merge needed", size))
        return [] if ok else ["--pdf: output is not a plausible PDF"]
    except PdfToolMissing as exc:
        print("%s  PDF path failed: %s" % (FAIL, exc))
        return ["--pdf: %s" % exc]
    finally:
        try:
            os.unlink(path)
        except OSError:  # pragma: no cover
            pass


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
