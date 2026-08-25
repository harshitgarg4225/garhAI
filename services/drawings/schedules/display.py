"""The display boundary: integers in, Indian-formatted strings out. Nothing else.

Golden rule 6 — "mm in, pretty out" — and §15's Indian defaults land here and *only*
here. Every number above this line is an integer of millimetres or square millimetres;
every string below it is what an architect reads on a sheet:

* **areas**: m² to two decimals *and* sq ft to one decimal, because a municipal
  reviewer reads m² and an Indian client reads sq ft, and a set that shows only one of
  them gets a phone call;
* **plot area**: the same, plus **gaj** (= sq yard = 9 sq ft), which is how plots are
  actually quoted in north India;
* **ratios** (FAR, coverage): formatted by ``garh_rules.formatting.format_ratio`` — the
  *same function* the compliance chip calls, so the sheet and the chip cannot print
  "1.82" and "1.83" for one number;
* **lengths on a table**: plain millimetres. §7: "all dim text in mm on drawings
  regardless of the project's display units". A setback column that switches between
  "750 mm" and "3.10 m" down its own rows is worse than either.

The m² conversion is done in **integer arithmetic** (``mm² → hundredths of m²`` with
half-up rounding), not by dividing floats: these strings are byte-diffed goldens, and
1 mm² of float drift in the last decimal is a failed build for no reason. Sq ft and
gaj reuse ``garh_model.units``, the golden-tested TS/Python pair — this module must
never grow its own foot conversion.
"""

from __future__ import annotations

import sys
from fractions import Fraction
from pathlib import Path
from typing import Optional

__all__ = [
    "DASH",
    "area_cell",
    "count_cell",
    "gaj_text",
    "mm_cell",
    "percent_cell",
    "plot_area_cell",
    "ratio_cell",
    "sqft_text",
    "sqm_text",
    "storey_row_label",
]

#: What an absent number prints as. Never "0" — "no FAR rule applied" and "FAR of zero"
#: are different facts, and a municipal sheet must not conflate them.
DASH = "-"


def _ensure_apps_api_on_path() -> None:
    """Make ``garh_model`` / ``garh_rules`` importable from a repo checkout.

    Mirrors ``services/solver/program.py``: in the worker image
    ``PYTHONPATH=/app:/app/apps/api`` already covers it.
    """
    try:
        import garh_model  # noqa: F401

        return
    except ImportError:
        pass
    root = Path(__file__).resolve().parents[3]
    candidate = root / "apps" / "api"
    if candidate.is_dir() and str(candidate) not in sys.path:
        sys.path.append(str(candidate))


def sqm_text(area_mm2: int, decimals: int = 2) -> str:
    """``111484000 -> '111.48'`` — exact integer rounding, half away from zero.

    No unit suffix: the column header carries it, so the numbers stay right-aligned on
    the decimal point.
    """
    if not isinstance(area_mm2, int) or isinstance(area_mm2, bool):
        raise TypeError(
            "sqm_text takes an integer of square millimetres, got %r (%s)."
            % (area_mm2, type(area_mm2).__name__)
        )
    if decimals < 0:
        raise ValueError("decimals must be >= 0, got %d" % decimals)
    scale = 10 ** decimals  # 10^2 hundredths of a m2
    per_unit = 1_000_000  # mm2 per m2
    sign = "-" if area_mm2 < 0 else ""
    magnitude = abs(area_mm2)
    # half away from zero, in integers: (a*scale + per/2) // per
    units = (magnitude * scale + per_unit // 2) // per_unit
    if decimals == 0:
        return "%s%d" % (sign, units)
    return "%s%d.%0*d" % (sign, units // scale, decimals, units % scale)


def sqft_text(area_mm2: int, decimals: int = 1) -> str:
    """``111484000 -> '1,200.0 sq ft'`` — via the shared, golden-tested units module."""
    _ensure_apps_api_on_path()
    from garh_model.units import format_sqft

    return format_sqft(area_mm2, decimals)


def gaj_text(area_mm2: int, decimals: int = 0) -> str:
    """``111484000 -> '133 gaj'``."""
    _ensure_apps_api_on_path()
    from garh_model.units import format_gaj

    return format_gaj(area_mm2, decimals)


def area_cell(area_mm2: Optional[int]) -> str:
    """``'111.48 m2 · 1,200.0 sq ft'`` — the standard area cell."""
    if area_mm2 is None:
        return DASH
    return "%s m2 · %s" % (sqm_text(area_mm2), sqft_text(area_mm2))


def plot_area_cell(area_mm2: Optional[int]) -> str:
    """``'111.48 m2 · 1,200.0 sq ft · 133 gaj'`` — plot area only (§15)."""
    if area_mm2 is None:
        return DASH
    return "%s · %s" % (area_cell(area_mm2), gaj_text(area_mm2))


def mm_cell(value: Optional[int]) -> str:
    """A length on a table: plain millimetres, no grouping (CAD convention)."""
    if value is None:
        return DASH
    return "%d" % value


def count_cell(value: Optional[int]) -> str:
    if value is None:
        return DASH
    return "%d" % value


def ratio_cell(value: Optional[Fraction], decimals: int = 2) -> str:
    """A FAR / coverage ratio, formatted by the rules engine's own formatter."""
    if value is None:
        return DASH
    _ensure_apps_api_on_path()
    from garh_rules.formatting import format_ratio

    return format_ratio(value, decimals)


def percent_cell(value: Optional[Fraction], decimals: int = 1) -> str:
    if value is None:
        return DASH
    _ensure_apps_api_on_path()
    from garh_rules.formatting import format_percent

    return format_percent(value, decimals)


def storey_row_label(name: str) -> str:
    """``'Ground Floor' -> 'Ground floor'`` — sheet copy is sentence case (§15 tone)."""
    if not name:
        return name
    return name[0].upper() + name[1:].lower()
