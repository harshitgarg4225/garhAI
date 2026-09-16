"""Regenerate the survey-style DXF fixtures (LINE / ARC boundaries).

    /home/user/garhAI/.venv/bin/python fixtures/dxf/_tools/make_survey_fixtures.py

Three files, each the way a real drawing arrives rather than the way our exporter
writes one:

* ``plot_survey_lines.dxf`` — a Total-Station export: LINE entities in METRES at
  3-decimal precision on ``PLOT_BOUNDARY``, one line drawn in the opposite direction,
  one end 3 mm short of its corner (inside the 25 mm chaining tolerance), station
  POINTs and TEXT labels on ``STATIONS``, and a SPLINE contour on ``CONTOURS`` that
  the reader must COUNT as unsupported rather than swallow.
* ``plot_rounded_corner.dxf`` — a 10 × 8 m plot in mm whose north-east corner is an
  R 2 m ARC, so the ring is four lines plus a tessellated arc.
* ``plot_survey_open.dxf`` — the negative control: the same survey with the last
  line stopping 340 mm short of the first corner. Must FAIL, naming the gap.

Written with ezdxf (Apache-2.0-compatible MIT, already a worker dependency). The
expected rings in ``apps/api/tests/test_dxf_import.py`` were derived from the
coordinates below by hand, not read back from the parser.
"""

from __future__ import annotations

from pathlib import Path

import ezdxf

HERE = Path(__file__).resolve().parent.parent

# Corners in metres. Not a rectangle: the north side is 8 m against a 12 m south side.
A = (100.000, 200.000)
B = (112.000, 200.000)
C = (110.000, 209.000)
D = (102.000, 209.000)


def survey_lines() -> None:
    doc = ezdxf.new("R2000")
    doc.header["$INSUNITS"] = 6  # metres
    doc.layers.add("PLOT_BOUNDARY")
    doc.layers.add("STATIONS")
    doc.layers.add("CONTOURS")
    msp = doc.modelspace()
    layer = {"layer": "PLOT_BOUNDARY"}
    msp.add_line(A, B, dxfattribs=layer)
    msp.add_line(C, B, dxfattribs=layer)  # drawn the other way round
    msp.add_line(C, (102.003, 209.000), dxfattribs=layer)  # stops 3 mm short of D
    msp.add_line(D, A, dxfattribs=layer)
    for name, p in (("ST1", A), ("ST2", B), ("ST3", C), ("ST4", D)):
        msp.add_point(p, dxfattribs={"layer": "STATIONS"})
        text = msp.add_text(name, dxfattribs={"layer": "STATIONS", "height": 0.3})
        text.set_placement((p[0] + 0.2, p[1] + 0.2))
    msp.add_spline([(99, 199), (105, 203), (111, 199)], dxfattribs={"layer": "CONTOURS"})
    doc.saveas(HERE / "plot_survey_lines.dxf")


def rounded_corner() -> None:
    doc = ezdxf.new("R2000")
    doc.header["$INSUNITS"] = 4  # millimetres
    doc.layers.add("PLOT")
    msp = doc.modelspace()
    layer = {"layer": "PLOT"}
    msp.add_line((0, 0), (10000, 0), dxfattribs=layer)
    msp.add_line((10000, 0), (10000, 6000), dxfattribs=layer)
    # Quarter circle centred (8000, 6000): from (10000, 6000) to (8000, 8000).
    msp.add_arc((8000, 6000), 2000, 0, 90, dxfattribs=layer)
    msp.add_line((8000, 8000), (0, 8000), dxfattribs=layer)
    msp.add_line((0, 8000), (0, 0), dxfattribs=layer)
    doc.saveas(HERE / "plot_rounded_corner.dxf")


def survey_open() -> None:
    doc = ezdxf.new("R2000")
    doc.header["$INSUNITS"] = 6
    doc.layers.add("PLOT_BOUNDARY")
    msp = doc.modelspace()
    layer = {"layer": "PLOT_BOUNDARY"}
    msp.add_line(A, B, dxfattribs=layer)
    msp.add_line(B, C, dxfattribs=layer)
    msp.add_line(C, D, dxfattribs=layer)
    msp.add_line(D, (100.000, 200.340), dxfattribs=layer)  # 340 mm short of A
    doc.saveas(HERE / "plot_survey_open.dxf")


if __name__ == "__main__":
    survey_lines()
    rounded_corner()
    survey_open()
    print("wrote plot_survey_lines.dxf, plot_rounded_corner.dxf, plot_survey_open.dxf")
