"""units.py — the integer-millimetre boundary. Mirror of ``packages/model/src/units.ts``.

GOLDEN RULE 6 ("mm in, pretty out"): every user-supplied length is parsed to
integer millimetres here, and every displayed length is formatted here. No other
module in the product is allowed to know about feet, inches, gaj or rupees.

ROUNDING POLICY (a cross-language contract, restated because it is load-bearing):
    All mm rounding is ROUND-HALF-AWAY-FROM-ZERO ("commercial rounding"):
        0.5 -> 1,  1.5 -> 2,  2.5 -> 3,  -0.5 -> -1,  -2.5 -> -3
    Banker's / half-to-even is explicitly NOT wanted — architects expect 2.5mm to
    become 3mm, and a dimension chain that rounds half-to-even sums differently
    depending on the parity of its parts. Python's builtin ``round()`` is
    half-to-even and JavaScript's ``Math.round`` is half-UP (so -0.5 -> -0);
    BOTH are wrong. Both languages implement exactly:
        x >= 0 ? floor(x + 0.5) : -floor(-x + 0.5)
    on IEEE-754 doubles, which makes TS and Python agree bit-for-bit.

EXACTNESS: imperial conversion factors are exact decimals (1in = 25.4mm), so the
only inexactness is the final rounding to whole mm, which is the point.

CROSS-LANGUAGE GOLDEN TABLE: ``fixtures/model/golden-units.json`` is the single
source of truth for the parser's behaviour and is read by BOTH languages (see
:func:`load_golden_units`). Never re-type those literals into a test file.
"""

from __future__ import annotations

import datetime as _dt
import json
import math
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Pattern, Sequence, Tuple, Union

try:  # pragma: no cover - typing only
    from typing import Literal
except ImportError:  # pragma: no cover
    from typing_extensions import Literal  # type: ignore[assignment]

__all__ = [
    "MM_PER_INCH",
    "MM_PER_FOOT",
    "MM_PER_YARD",
    "MM_PER_METRE",
    "MM_PER_CM",
    "MM2_PER_SQFT",
    "MM2_PER_SQM",
    "MM2_PER_GAJ",
    "MAX_SAFE_INTEGER",
    "UnitsDisplay",
    "DefaultLengthUnit",
    "DefaultAreaUnit",
    "UnitParseError",
    "FtInOptions",
    "LengthParseResult",
    "GoldenUnits",
    "round_half_away_from_zero",
    "round_mm",
    "is_int_mm",
    "assert_int_mm",
    "normalise_length_input",
    "parse_length_mm",
    "try_parse_length_mm",
    "parse_area_mm2",
    "format_ft_in",
    "format_metres",
    "format_mm",
    "format_length",
    "to_sqft",
    "to_sqm",
    "to_gaj",
    "from_sqft",
    "from_sqm",
    "from_gaj",
    "format_fixed",
    "format_sqft",
    "format_sqm",
    "format_gaj",
    "format_area",
    "format_plot_area",
    "format_indian_number",
    "format_rupees",
    "format_rupees_compact",
    "format_indian_date",
    "golden_units_path",
    "load_golden_units",
]

# ---------------------------------------------------------------------------
# Exact conversion factors
# ---------------------------------------------------------------------------

#: Exact: 1 inch = 25.4 mm.
MM_PER_INCH = 25.4
#: Exact: 1 foot = 304.8 mm.
MM_PER_FOOT = 304.8
#: Exact: 1 yard = 914.4 mm.
MM_PER_YARD = 914.4
#: Exact: 1 metre = 1000 mm.
MM_PER_METRE = 1000
#: Exact: 1 centimetre = 10 mm.
MM_PER_CM = 10
#: Exact: 1 sq ft = 92_903.04 mm^2 (304.8 squared).
MM2_PER_SQFT = MM_PER_FOOT * MM_PER_FOOT
#: Exact: 1 sq m = 1_000_000 mm^2.
MM2_PER_SQM = 1_000_000
#: 1 gaj = 1 square yard = 9 sq ft = 836_127.36 mm^2.
MM2_PER_GAJ = MM_PER_YARD * MM_PER_YARD

#: JavaScript's ``Number.MAX_SAFE_INTEGER``. The document may hold no integer
#: outside +/- this value, because the TypeScript side could not represent it.
MAX_SAFE_INTEGER = 2**53 - 1

#: Display unit system for a project. Mirrors ``projects.units`` in the DB.
UnitsDisplay = Literal["ft-in", "m"]

#: Unit assumed for a bare number when parsing a length.
DefaultLengthUnit = Literal["mm", "ft-in", "m"]

#: Unit assumed for a bare number when parsing an area.
DefaultAreaUnit = Literal["sqft", "sqm", "gaj"]


class UnitParseError(ValueError):
    """Raised by :func:`parse_length_mm` when the input cannot be understood.

    Mirror of the TypeScript ``UnitParseError``: same ``code``, same message
    shape, so the UI's error-copy map keys off one string in both languages.
    """

    code = "UNIT_PARSE_FAILED"

    def __init__(self, raw_input: object, reason: str) -> None:
        super().__init__(f'Cannot read "{raw_input}" as a length: {reason}')
        self.input = raw_input
        self.reason = reason


# ---------------------------------------------------------------------------
# Rounding
# ---------------------------------------------------------------------------


def round_half_away_from_zero(x: float) -> int:
    """Round half away from zero. THE only rounding function allowed on lengths.

    ``x >= 0 ? floor(x + 0.5) : -floor(-x + 0.5)`` — see the module docstring for
    why neither builtin will do.
    """
    if isinstance(x, bool) or not isinstance(x, (int, float)):
        raise TypeError(f"round_half_away_from_zero: not a number ({x!r})")
    if isinstance(x, int):
        return x
    if not math.isfinite(x):
        raise ValueError(f"round_half_away_from_zero: not a finite number ({x})")
    return math.floor(x + 0.5) if x >= 0 else -math.floor(-x + 0.5)


#: Alias used at call sites where "this value becomes integer mm" is the point.
round_mm = round_half_away_from_zero


def is_int_mm(v: object) -> bool:
    """True when ``v`` is a value we are willing to store as a length/coordinate.

    Mirrors ``Number.isSafeInteger``: a Python ``bool`` is rejected even though
    it is an ``int`` subclass, and a ``float`` is rejected outright (the model
    holds no floats). Values outside the JS safe-integer range are rejected so a
    document can never be written that TypeScript cannot read back.
    """
    return isinstance(v, int) and not isinstance(v, bool) and -MAX_SAFE_INTEGER <= v <= MAX_SAFE_INTEGER


def assert_int_mm(v: object, field: str) -> int:
    """Assert integer mm, naming the field (used by :mod:`garh_model.validate`)."""
    if not is_int_mm(v):
        raise ValueError(f"{field} must be an integer number of millimetres, got {v!r}")
    return int(v)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

# NOTE ON REGEXES: every `\d` from the TypeScript source is written `[0-9]` here.
# Python's `\d` is Unicode-aware and would happily match DEVANAGARI DIGIT THREE;
# JavaScript's is ASCII-only. Silently accepting "३८००" as 3800 is exactly the
# class of surprise this module exists to prevent.

# U+2032 PRIME, U+2019 RIGHT SINGLE QUOTE, U+02B9 MODIFIER PRIME, U+00B4 ACUTE, backtick
_PRIMES = re.compile("[\u2032\u2019\u02b9\u00b4`]")
# U+2033 DOUBLE PRIME, U+201D RIGHT DOUBLE QUOTE, U+02BA, U+3003, U+FF02
_DOUBLE_PRIMES = re.compile('[\u2033\u201d\u02ba\u3003\uff02]')
# NBSP, en/em/thin spaces, narrow NBSP, medium math space, ideographic space
_UNICODE_SPACES = re.compile("[\u00a0\u2000-\u200a\u202f\u205f\u3000]")
# U+2212 MINUS SIGN, U+2013 EN DASH, U+2014 EM DASH
_UNICODE_MINUS = re.compile("[\u2212\u2013\u2014]")
# thousands separators, only when a digit follows
# Mirror of the TS regex: comma preceded by digit-or-comma, followed by a
# digit — see packages/model/src/units.ts normaliseLengthInput.
_THOUSANDS = re.compile("(?<=[0-9,]),(?=[0-9])")
_WHITESPACE_RUN = re.compile(r"\s+")


def normalise_length_input(raw: str) -> str:
    """Normalise the many ways an Indian architect types a length.

    - unicode primes/quotes -> ASCII ``'`` and ``"``
    - unicode spaces (NBSP, thin, narrow-NBSP) -> ASCII space
    - unicode minus/en-dash used as a minus or as a feet-inch separator -> ``-``
    - thousands commas between digits are dropped ("1,200" -> "1200")
    - collapsed whitespace, lower-cased
    """
    s = _PRIMES.sub("'", raw)
    s = _DOUBLE_PRIMES.sub('"', s)
    s = _UNICODE_SPACES.sub(" ", s)
    s = _UNICODE_MINUS.sub("-", s)
    s = _THOUSANDS.sub("", s)
    return _WHITESPACE_RUN.sub(" ", s.strip()).lower()


_UNIT_ALIASES: Sequence[Tuple[Pattern[str], float]] = (
    (re.compile(r"^(?:mm|millimetre|millimetres|millimeter|millimeters)$"), 1.0),
    (re.compile(r"^(?:cm|centimetre|centimetres|centimeter|centimeters)$"), float(MM_PER_CM)),
    (re.compile(r"^(?:m|mt|mtr|metre|metres|meter|meters)$"), float(MM_PER_METRE)),
    (re.compile(r"^(?:ft|foot|feet)$"), MM_PER_FOOT),
    (re.compile(r"^(?:in|inch|inches)$"), MM_PER_INCH),
    (re.compile(r"^(?:yd|yard|yards)$"), MM_PER_YARD),
)


def _unit_factor(token: str) -> Optional[float]:
    for pattern, factor in _UNIT_ALIASES:
        if pattern.match(token):
            return factor
    return None


_MIXED_WHOLE_FRACTION = re.compile(r"^([0-9]+)\s*[-\s]\s*([0-9]+)\s*/\s*([0-9]+)$")
_PLAIN_FRACTION = re.compile(r"^([0-9]+)\s*/\s*([0-9]+)$")
_PLAIN_DECIMAL = re.compile(r"^[0-9]+(?:\.[0-9]+)?$")
_LEADING_DOT_DECIMAL = re.compile(r"^\.[0-9]+$")


def _parse_mixed_number(text: str) -> Optional[float]:
    """``"6 1/2"`` / ``"6-1/2"`` / ``"1/2"`` / ``"6.5"`` -> 6.5 (a plain count)."""
    s = text.strip()
    if s == "":
        return None
    m = _MIXED_WHOLE_FRACTION.match(s)
    if m:
        den = int(m.group(3))
        if den == 0:
            return None
        return float(m.group(1)) + float(m.group(2)) / den
    m = _PLAIN_FRACTION.match(s)
    if m:
        den = int(m.group(2))
        if den == 0:
            return None
        return float(m.group(1)) / den
    if _PLAIN_DECIMAL.match(s) or _LEADING_DOT_DECIMAL.match(s):
        return float(s)
    return None


_FT_IN = re.compile(r"""^(?:([0-9]+(?:\.[0-9]+)?)\s*')?\s*(?:[-\s]\s*)?(?:([0-9 /.]+?)\s*"?)?$""")
_FT_IN_WORDS = re.compile(r"^([0-9.]+)\s*(?:ft|foot|feet)\s*([0-9 /.]+)?\s*(?:in|inch|inches)?$")
_NUMBER_WITH_UNIT = re.compile(r"^([0-9]*\.?[0-9]+(?:\s+[0-9]+/[0-9]+)?)\s*([a-z]+)$")
_DASH_SHORTHAND = re.compile(r"^([0-9]+)\s*-\s*([0-9]+(?:\s+[0-9]+/[0-9]+)?|[0-9]+/[0-9]+)$")


def parse_length_mm(raw: str, default_unit: str = "mm") -> int:
    """Parse ANY length form an Indian architect types into integer millimetres.

    Accepted (case-insensitive, whitespace-tolerant)::

        bare number      "3800"                -> 3800 (bare == mm; see default_unit)
        explicit metric  "3800mm" "380cm" "3.8m" "3.8 metres"
        explicit imperial "12ft" "12 feet" "150in" "4 yd"
        feet+inches      "12'6\\"" "12' 6\\"" "12'-6\\"" "12'6" "12'" "6\\""
        dash shorthand   "12-6"                -> 12'-6" (drafting shorthand)
        inch fractions   "6 1/2\\"" "12'-6 1/2\\"" "1/2\\""
        decimals         "12.5'" "6.25in" "0.5mm"
        signed           "-12'6\\"" "-3800"
        unicode primes   "12'6″"

    :param raw: user text
    :param default_unit: unit assumed for a bare number. Default ``'mm'`` — the
        model is mm, so a bare number in an mm field means mm. UI code that owns
        a ft-in field passes ``'ft-in'`` so "12" means 12 feet.
    :raises UnitParseError: when the input cannot be understood.
    """
    if not isinstance(raw, str):
        raise UnitParseError(raw, "not a string")
    s0 = normalise_length_input(raw)
    if s0 == "":
        raise UnitParseError(raw, "empty")

    sign = 1
    s = s0
    if s.startswith("-"):
        sign = -1
        s = s[1:].strip()
    elif s.startswith("+"):
        s = s[1:].strip()
    if s == "":
        raise UnitParseError(raw, "sign with no number")

    # --- 1. feet-and-inches with explicit marks: 12'6", 12' 6 1/2", 12', 6"
    ft_in = _FT_IN.match(s)
    if "'" in s and ft_in is not None:
        feet = 0.0 if ft_in.group(1) is None else float(ft_in.group(1))
        inch_text = (ft_in.group(2) or "").strip()
        inches: Optional[float] = 0.0 if inch_text == "" else _parse_mixed_number(inch_text)
        if inches is None:
            raise UnitParseError(raw, f'cannot read inches part "{inch_text}"')
        return sign * round_mm((feet * 12 + inches) * MM_PER_INCH)

    # --- 2. inches only with the " mark: 6", 6 1/2"
    if s.endswith('"'):
        inch_text = s[:-1].strip()
        inches = _parse_mixed_number(inch_text)
        if inches is None:
            raise UnitParseError(raw, f'cannot read inches "{inch_text}"')
        return sign * round_mm(inches * MM_PER_INCH)

    # --- 3. "12 ft 6 in" / "12 feet 6 inches"
    words = _FT_IN_WORDS.match(s)
    if words is not None and words.group(2) is not None and words.group(2).strip() != "":
        feet = float(words.group(1))
        inches = _parse_mixed_number(words.group(2))
        if inches is None:
            raise UnitParseError(raw, f'cannot read inches "{words.group(2)}"')
        return sign * round_mm((feet * 12 + inches) * MM_PER_INCH)

    # --- 4. number + unit word: 3.8m, 3800mm, 12 ft, 4 yd, 150 in
    with_unit = _NUMBER_WITH_UNIT.match(s)
    if with_unit is not None:
        factor = _unit_factor(with_unit.group(2))
        if factor is None:
            raise UnitParseError(raw, f'unknown unit "{with_unit.group(2)}"')
        n = _parse_mixed_number(with_unit.group(1))
        if n is None:
            raise UnitParseError(raw, f'cannot read number "{with_unit.group(1)}"')
        return sign * round_mm(n * factor)

    # --- 5. dash shorthand "12-6" == 12'-6" (integers only, no unit marks)
    dash = _DASH_SHORTHAND.match(s)
    if dash is not None:
        feet = float(dash.group(1))
        inches = _parse_mixed_number(dash.group(2))
        if inches is None:
            raise UnitParseError(raw, f'cannot read inches "{dash.group(2)}"')
        return sign * round_mm((feet * 12 + inches) * MM_PER_INCH)

    # --- 6. bare number / bare fraction -> default_unit
    bare = _parse_mixed_number(s)
    if bare is not None:
        if default_unit == "mm":
            return sign * round_mm(bare)
        if default_unit == "m":
            return sign * round_mm(bare * MM_PER_METRE)
        return sign * round_mm(bare * MM_PER_FOOT)  # 'ft-in': a bare number is feet

    raise UnitParseError(raw, "unrecognised format")


@dataclass(frozen=True)
class LengthParseResult:
    """Non-throwing parse outcome. Mirrors the TS discriminated union."""

    ok: bool
    mm: Optional[int] = None
    error: Optional[str] = None


def try_parse_length_mm(raw: str, default_unit: str = "mm") -> LengthParseResult:
    """Non-throwing variant of :func:`parse_length_mm`, for form fields."""
    try:
        return LengthParseResult(ok=True, mm=parse_length_mm(raw, default_unit))
    except UnitParseError as exc:
        return LengthParseResult(ok=False, error=str(exc))


_AREA_RECT = re.compile(r"^([0-9.]+)\s*[x×*]\s*([0-9.]+)\s*([a-z]+)?$")
_AREA_NUMBER = re.compile(r"^([0-9]*\.?[0-9]+)\s*(.*)$")
_AREA_STRIP = re.compile(r"[\s.]")

_SQFT_KEYS = frozenset({"sqft", "sqfeet", "ft2", "sft", "squarefeet", "squarefoot"})
_SQM_KEYS = frozenset(
    {"sqm", "m2", "sqmt", "squaremetre", "squaremetres", "squaremeter", "squaremeters"}
)
_GAJ_KEYS = frozenset({"gaj", "sqyd", "yd2", "squareyard", "squareyards"})


def parse_area_mm2(raw: str, default_unit: str = "sqft") -> int:
    """Parse an area into integer mm^2.

    Accepts ``"1200 sqft"``, ``"1,200 sq ft"``, ``"133 gaj"``, ``"111 sqm"``,
    ``"111 m2"``, ``"30x40 ft"`` (a rectangle!); a bare number means sq ft by
    default.
    """
    s = normalise_length_input(raw)
    if s == "":
        raise UnitParseError(raw, "empty")

    rect = _AREA_RECT.match(s)
    if rect is not None:
        unit = rect.group(3) if rect.group(3) is not None else ("m" if default_unit == "sqm" else "ft")
        factor = _unit_factor(unit)
        if factor is None:
            raise UnitParseError(raw, f'unknown unit "{unit}"')
        a = round_mm(float(rect.group(1)) * factor)
        b = round_mm(float(rect.group(2)) * factor)
        return a * b

    m = _AREA_NUMBER.match(s)
    if m is None:
        raise UnitParseError(raw, "unrecognised area")
    n = float(m.group(1))
    unit = _AREA_STRIP.sub("", m.group(2) or "")
    key = default_unit if unit == "" else unit
    if key in _SQFT_KEYS:
        return round_mm(n * MM2_PER_SQFT)
    if key in _SQM_KEYS:
        return round_mm(n * MM2_PER_SQM)
    if key in _GAJ_KEYS:
        return round_mm(n * MM2_PER_GAJ)
    raise UnitParseError(raw, f'unknown area unit "{unit}"')


# ---------------------------------------------------------------------------
# Formatting — lengths
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FtInOptions:
    """Options for :func:`format_ft_in`."""

    #: Inch fraction resolution: 1 = whole inches (default), 2 = half, 4, 8.
    fraction: int = 1
    #: Include the trailing ``"`` on inches.
    inch_mark: bool = True
    #: Omit ``0"`` for a whole number of feet. Municipal drawings want 12'-0".
    drop_zero_inches: bool = False


_FRACTION_GLYPHS: Dict[str, str] = {
    "1/2": "½",
    "1/4": "¼",
    "3/4": "¾",
    "1/8": "⅛",
    "3/8": "⅜",
    "5/8": "⅝",
    "7/8": "⅞",
}


def _gcd(a: int, b: int) -> int:
    x, y = abs(a), abs(b)
    while y != 0:
        x, y = y, x % y
    return 1 if x == 0 else x


def format_ft_in(mm: int, opts: Optional[FtInOptions] = None) -> str:
    """3810 -> ``12'-6"``. Negative lengths keep the sign outside: ``-12'-6"``."""
    assert_int_mm(mm, "mm")
    o = opts if opts is not None else FtInOptions()
    fraction = o.fraction
    sign = "-" if mm < 0 else ""
    abs_mm = abs(mm)

    # Work in fraction-units of an inch to keep the carry logic integral.
    units = round_half_away_from_zero((abs_mm / MM_PER_INCH) * fraction)
    units_per_foot = 12 * fraction
    feet = units // units_per_foot
    rem = units - feet * units_per_foot
    inches = rem // fraction
    num = rem - inches * fraction

    if inches == 12:
        feet += 1
        inches = 0

    inch_text = str(inches)
    if num > 0:
        g = _gcd(num, fraction)
        key = f"{num // g}/{fraction // g}"
        glyph = _FRACTION_GLYPHS.get(key, key)
        inch_text = glyph if inches == 0 else f"{inches}{glyph}"

    if o.drop_zero_inches and inches == 0 and num == 0:
        return f"{sign}{feet}'"
    return f"{sign}{feet}'-{inch_text}" + ('"' if o.inch_mark else "")


def format_metres(mm: int, decimals: int = 2, with_unit: bool = True) -> str:
    """3800 -> ``3.80 m``."""
    assert_int_mm(mm, "mm")
    sign = "-" if mm < 0 else ""
    abs_mm = abs(mm)
    scale = 10**decimals
    scaled = round_half_away_from_zero((abs_mm * scale) / MM_PER_METRE)
    whole = scaled // scale
    frac = scaled - whole * scale
    body = str(whole) if decimals == 0 else f"{whole}.{str(frac).rjust(decimals, '0')}"
    return f"{sign}{body}" + (" m" if with_unit else "")


def format_mm(mm: int, with_unit: bool = True) -> str:
    """3800 -> ``3800 mm``. Drawings always dimension in mm (playbook section 7)."""
    assert_int_mm(mm, "mm")
    return f"{mm} mm" if with_unit else str(mm)


def format_length(mm: int, display: str, opts: Optional[FtInOptions] = None) -> str:
    """Format per project display units."""
    return format_ft_in(mm, opts) if display == "ft-in" else format_metres(mm)


# ---------------------------------------------------------------------------
# Formatting — areas
# ---------------------------------------------------------------------------


def to_sqft(mm2: int) -> float:
    """mm^2 -> sq ft (float; DISPLAY ONLY — never feed this back into geometry)."""
    return mm2 / MM2_PER_SQFT


def to_sqm(mm2: int) -> float:
    """mm^2 -> sq m (float; display only)."""
    return mm2 / MM2_PER_SQM


def to_gaj(mm2: int) -> float:
    """mm^2 -> gaj (= sq yard = 9 sq ft) (float; display only)."""
    return mm2 / MM2_PER_GAJ


def from_sqft(sqft: float) -> int:
    """sq ft -> mm^2 (integer)."""
    return round_mm(sqft * MM2_PER_SQFT)


def from_sqm(sqm: float) -> int:
    """sq m -> mm^2 (integer)."""
    return round_mm(sqm * MM2_PER_SQM)


def from_gaj(gaj: float) -> int:
    """gaj -> mm^2 (integer)."""
    return round_mm(gaj * MM2_PER_GAJ)


def format_fixed(value: float, decimals: int) -> str:
    """Fixed-decimal formatting with round-half-away-from-zero (never ``%.2f``)."""
    sign = "-" if value < 0 else ""
    abs_value = abs(value)
    scale = 10**decimals
    scaled = round_half_away_from_zero(abs_value * scale)
    whole = scaled // scale
    frac = scaled - whole * scale
    if decimals == 0:
        return f"{sign}{format_indian_number(whole)}"
    return f"{sign}{format_indian_number(whole)}.{str(frac).rjust(decimals, '0')}"


def format_sqft(mm2: int, decimals: int = 1) -> str:
    """``1,200.5 sq ft`` — one decimal is the municipal-drawing convention."""
    return f"{format_fixed(to_sqft(mm2), decimals)} sq ft"


def format_sqm(mm2: int, decimals: int = 2) -> str:
    """``111.48 m2`` (with a superscript two)."""
    return f"{format_fixed(to_sqm(mm2), decimals)} m²"


def format_gaj(mm2: int, decimals: int = 0) -> str:
    """``133 gaj`` — plot sizes in north India are quoted in gaj."""
    return f"{format_fixed(to_gaj(mm2), decimals)} gaj"


def format_area(mm2: int, display: str) -> str:
    """Area per project display units."""
    return format_sqft(mm2) if display == "ft-in" else format_sqm(mm2)


def format_plot_area(mm2: int, display: str = "ft-in") -> str:
    """``1,200.0 sq ft · 133 gaj`` — the plot-header string."""
    return f"{format_area(mm2, display)} · {format_gaj(mm2)}"


# ---------------------------------------------------------------------------
# Indian number / currency formatting
# ---------------------------------------------------------------------------


def format_indian_number(n: float) -> str:
    """Indian digit grouping (lakh/crore): last 3 digits, then groups of 2.

    ``1245000 -> "12,45,000"``; ``999 -> "999"``; ``-1234567 -> "-12,34,567"``.
    """
    if isinstance(n, float) and not math.isfinite(n):
        raise ValueError(f"format_indian_number: {n}")
    neg = n < 0
    whole = math.floor(abs(n))
    digits = str(whole)
    if len(digits) <= 3:
        out = digits
    else:
        head = digits[: len(digits) - 3]
        tail = digits[len(digits) - 3 :]
        groups: List[str] = []
        i = len(head)
        while i > 2:
            groups.insert(0, head[i - 2 : i])
            i -= 2
        if i > 0:
            groups.insert(0, head[:i])
        out = f"{','.join(groups)},{tail}"
    return f"-{out}" if neg else out


def format_rupees(rupees: float, decimals: int = 0) -> str:
    """``₹12,45,000``. Rupees only — this is a budget field, not a ledger."""
    neg = rupees < 0
    body = format_fixed(abs(rupees), decimals)
    return f"{'-' if neg else ''}₹{body}"


def format_rupees_compact(rupees: float) -> str:
    """``₹1.25 Cr`` / ``₹45.0 L`` / ``₹85,000`` — compact budget bands."""
    abs_value = abs(rupees)
    sign = "-" if rupees < 0 else ""
    if abs_value >= 10_000_000:
        return f"{sign}₹{format_fixed(abs_value / 10_000_000, 2)} Cr"
    if abs_value >= 100_000:
        return f"{sign}₹{format_fixed(abs_value / 100_000, 1)} L"
    return format_rupees(rupees)


def format_indian_date(d: Union[_dt.datetime, _dt.date, str]) -> str:
    """DD-MM-YYYY (Indian defaults). Takes an ISO string, ``date`` or ``datetime``.

    Like the TypeScript mirror, the date is read in UTC: a ``datetime`` carrying a
    tzinfo is converted, a naive one is assumed to already be UTC.
    """
    if isinstance(d, str):
        text = d[:-1] + "+00:00" if d.endswith("Z") else d
        parsed: Union[_dt.datetime, _dt.date] = _dt.datetime.fromisoformat(text)
    else:
        parsed = d
    if isinstance(parsed, _dt.datetime):
        if parsed.tzinfo is not None:
            parsed = parsed.astimezone(_dt.timezone.utc)
        return f"{parsed.day:02d}-{parsed.month:02d}-{parsed.year:04d}"
    return f"{parsed.day:02d}-{parsed.month:02d}-{parsed.year:04d}"


# ---------------------------------------------------------------------------
# GOLDEN_UNIT_PAIRS — the cross-language contract, read from a shared file
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class GoldenUnits:
    """Contents of ``fixtures/model/golden-units.json``."""

    #: ``[input, expected_mm]`` rows parsed with ``default_unit='mm'``.
    pairs: Tuple[Tuple[str, int], ...]
    #: Inputs that MUST raise :class:`UnitParseError`.
    failures: Tuple[str, ...]
    default_unit: str
    rounding: str


#: Path of the shared golden table relative to the repository root.
GOLDEN_UNITS_RELPATH = Path("fixtures") / "model" / "golden-units.json"

#: Environment override, so a container that does not ship ``fixtures/`` can point
#: the test suite at wherever the fixtures were mounted.
GOLDEN_UNITS_ENV = "GARH_MODEL_FIXTURES_DIR"


def golden_units_path() -> Path:
    """Locate ``fixtures/model/golden-units.json``.

    Honours ``$GARH_MODEL_FIXTURES_DIR`` (which may point either at the
    ``fixtures/`` directory or at the repository root), else walks up from this
    file looking for the repository root. Raises ``FileNotFoundError`` with an
    actionable message rather than silently skipping the contract test.
    """
    override = os.environ.get(GOLDEN_UNITS_ENV)
    candidates: List[Path] = []
    if override:
        base = Path(override)
        candidates.extend([base / "model" / "golden-units.json", base / GOLDEN_UNITS_RELPATH])
    here = Path(__file__).resolve()
    for parent in here.parents:
        candidates.append(parent / GOLDEN_UNITS_RELPATH)
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    raise FileNotFoundError(
        "Could not find fixtures/model/golden-units.json (searched upwards from "
        f"{here} and $%s). This file is the TS<->Python units contract; the unit "
        "tests cannot run without it." % GOLDEN_UNITS_ENV
    )


_GOLDEN_CACHE: Optional[GoldenUnits] = None


def load_golden_units() -> GoldenUnits:
    """Read (and memoise) the shared golden units table.

    THIS is what the Python unit tests assert against, and what
    ``packages/model/src/units.test.ts`` must be pointed at too — neither side
    may keep its own copy of the literals.
    """
    global _GOLDEN_CACHE
    if _GOLDEN_CACHE is not None:
        return _GOLDEN_CACHE
    raw: Any = json.loads(golden_units_path().read_text(encoding="utf-8"))
    pairs: List[Tuple[str, int]] = []
    for row in raw["pairs"]:
        if not isinstance(row, list) or len(row) != 2:
            raise ValueError(f"golden-units.json: malformed pair {row!r}")
        text, expected = row
        if not isinstance(text, str) or not is_int_mm(expected):
            raise ValueError(f"golden-units.json: pair must be [str, int], got {row!r}")
        pairs.append((text, int(expected)))
    failures = tuple(str(f) for f in raw["failures"])
    _GOLDEN_CACHE = GoldenUnits(
        pairs=tuple(pairs),
        failures=failures,
        default_unit=str(raw.get("defaultUnit", "mm")),
        rounding=str(raw.get("rounding", "")),
    )
    return _GOLDEN_CACHE
