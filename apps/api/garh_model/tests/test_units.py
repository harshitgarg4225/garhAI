"""Unit-conversion goldens — the mm-in / pretty-out boundary (Golden Rule 6).

The table lives in ``fixtures/model/golden-units.json`` and is read by BOTH
languages. If a row here disagrees with the TypeScript, the canvas and the
drawing set disagree about a length; that is a build failure, never a rounding
opinion.
"""

from __future__ import annotations

import pytest

from garh_model.units import (
    MM_PER_FOOT,
    MM_PER_INCH,
    MM_PER_METRE,
    UnitParseError,
    format_area,
    format_ft_in,
    format_gaj,
    format_length,
    format_metres,
    format_mm,
    format_plot_area,
    format_rupees,
    format_rupees_compact,
    from_gaj,
    load_golden_units,
    parse_area_mm2,
    parse_length_mm,
    round_half_away_from_zero,
    to_gaj,
    to_sqft,
    try_parse_length_mm,
)

GOLDEN = load_golden_units()


def test_golden_table_is_loaded_from_the_shared_fixture() -> None:
    assert GOLDEN.default_unit == "mm"
    assert len(GOLDEN.pairs) >= 60, "the shared table lost rows"
    assert len(GOLDEN.failures) >= 10


@pytest.mark.parametrize(("text", "expected_mm"), GOLDEN.pairs)
def test_golden_unit_pairs(text: str, expected_mm: int) -> None:
    """Every row of the cross-language table parses to the same integer mm."""
    actual = parse_length_mm(text, default_unit=GOLDEN.default_unit)
    assert actual == expected_mm, f"{text!r} -> {actual} (expected {expected_mm})"
    assert isinstance(actual, int) and not isinstance(actual, bool)


@pytest.mark.parametrize("text", GOLDEN.failures)
def test_golden_unit_failures(text: str) -> None:
    """Every must-fail input raises rather than guessing."""
    with pytest.raises(UnitParseError):
        parse_length_mm(text, default_unit=GOLDEN.default_unit)
    result = try_parse_length_mm(text, default_unit=GOLDEN.default_unit)
    assert result.ok is False
    assert result.mm is None
    assert result.error, "a failed parse must say why (Golden Rule 9)"


def test_try_parse_reports_success_without_raising() -> None:
    result = try_parse_length_mm("12'6\"", default_unit="mm")
    assert result.ok is True
    assert result.mm == 3810
    assert result.error is None


def test_conversion_constants() -> None:
    assert MM_PER_INCH == 25.4
    assert pytest.approx(12 * MM_PER_INCH, rel=1e-12) == MM_PER_FOOT
    assert MM_PER_METRE == 1000


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (0.5, 1),
        (1.5, 2),
        (2.5, 3),  # NOT banker's rounding — 2, which Python's round() would give
        (-0.5, -1),
        (-1.5, -2),
        (-2.5, -3),
        (0.49999, 0),
        (3810.0, 3810),
    ],
)
def test_round_half_away_from_zero(value: float, expected: int) -> None:
    assert round_half_away_from_zero(value) == expected


def test_python_round_would_disagree() -> None:
    """Documents WHY we hand-roll the rounding: builtins round half to even."""
    assert round(2.5) == 2
    assert round_half_away_from_zero(2.5) == 3


@pytest.mark.parametrize(
    ("mm", "expected"),
    [
        (3810, "12'-6\""),
        (9144, "30'-0\""),
        (305, "1'-0\""),
        (0, "0'-0\""),
    ],
)
def test_format_ft_in(mm: int, expected: str) -> None:
    assert format_ft_in(mm) == expected


def test_format_metres_and_length_dispatch() -> None:
    assert format_metres(3810) == "3.81 m"
    assert format_length(3810, "ft-in") == "12'-6\""
    assert format_length(3810, "m") == "3.81 m"
    assert format_mm(3810) == "3810 mm"


def test_area_helpers_are_exact_on_the_demo_plot() -> None:
    plot = 9144 * 12192  # 30 x 40 ft, the seeded demo plot
    assert to_sqft(plot) == pytest.approx(1200.0, abs=0.01)
    gaj = to_gaj(plot)
    assert from_gaj(gaj) == pytest.approx(plot, rel=1e-9)
    # 1200 sq ft in whole mm^2 is exact: (30*304.8) * (40*304.8)
    assert parse_area_mm2("1200 sqft") == plot


def test_indian_formatting() -> None:
    """Golden Rule 12: Indian grouping (lakh/crore), ft-in and gaj."""
    assert format_rupees(9500000) == "₹95,00,000"
    assert format_rupees_compact(9500000) == "₹95.0 L"
    assert format_area(9144 * 12192, "ft-in") == "1,200.0 sq ft"
    assert format_area(9144 * 12192, "m") == "111.48 m²"
    assert format_gaj(9144 * 12192) == "133 gaj"
    assert format_plot_area(9144 * 12192) == "1,200.0 sq ft · 133 gaj"
