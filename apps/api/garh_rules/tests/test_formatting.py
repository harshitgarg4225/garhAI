"""Numbers and labels on a chip. Small module, disproportionate user impact.

Two decisions are pinned here because both are easy to "clean up" into a bug:

1. **Exactness beats a fixed two decimals.** A 1 mm2 shortfall rendered at two
   decimals reads "has 9.50 m2, needs at least 9.50 m2", which looks like the
   engine is broken. So a value keeps as many decimals as it takes to stay exact
   (3 for a length, 6 for an area — which is what millimetres are), with trailing
   zeros trimmed back to two.
2. **Under a metre stays in millimetres.** NBC quotes door and riser minima as
   "at least 750 mm"; "0.75 m" reads like a different rule.

Everything is ASCII (``m2``, never ``m²``) to match the packs' own ``fix`` strings —
a chip that mixes the two in one sentence looks like an encoding bug.
"""

from __future__ import annotations

from fractions import Fraction

from garh_rules.context import OpeningSummary, PlotEdge, RoomSummary, StoreySummary
from garh_rules.formatting import (
    edge_label,
    format_area_mm2,
    format_count,
    format_length_mm,
    format_limit,
    format_percent,
    format_ratio,
    format_value,
    join_labels,
    opening_label,
    render_message,
    room_label,
    stair_label,
    storey_label,
)

from .conftest import make_context, make_room


def room(name: str | None = None, room_type: str = "bedroom") -> RoomSummary:
    return RoomSummary.from_json(make_room("r1", room_type, name=name), "room")


class TestLengths:
    def test_metres_with_two_decimals_when_exact(self) -> None:
        assert format_length_mm(2400) == "2.40 m"
        assert format_length_mm(3000) == "3.00 m"
        assert format_length_mm(15_000) == "15.00 m"

    def test_extra_decimals_only_when_needed(self) -> None:
        assert format_length_mm(2749) == "2.749 m"
        assert format_length_mm(2740) == "2.74 m"

    def test_under_a_metre_stays_in_millimetres(self) -> None:
        assert format_length_mm(750) == "750 mm"
        assert format_length_mm(999) == "999 mm"
        assert format_length_mm(1000) == "1.00 m"

    def test_negative_lengths_keep_their_sign(self) -> None:
        """A projection past the plot line reports a negative clear distance."""
        assert format_length_mm(-500) == "-500 mm"
        assert format_length_mm(-1500) == "-1.50 m"

    def test_zero(self) -> None:
        assert format_length_mm(0) == "0 mm"


class TestAreas:
    def test_square_metres_with_two_decimals_when_exact(self) -> None:
        assert format_area_mm2(9_500_000) == "9.50 m2"
        assert format_area_mm2(300_000_000) == "300.00 m2"

    def test_a_millimetre_of_shortfall_is_still_visible(self) -> None:
        assert format_area_mm2(9_499_999) == "9.499999 m2"
        assert format_area_mm2(9_496_960) == "9.49696 m2"

    def test_small_areas(self) -> None:
        assert format_area_mm2(300_000) == "0.30 m2"
        assert format_area_mm2(0) == "0.00 m2"


class TestRatios:
    def test_far_style_two_decimals_half_up_on_the_exact_rational(self) -> None:
        assert format_ratio(Fraction(225, 100)) == "2.25"
        assert format_ratio(Fraction(1, 3)) == "0.33"
        assert format_ratio(Fraction(2, 3)) == "0.67"
        assert format_ratio(Fraction(1, 8)) == "0.13"  # 0.125 rounds half up

    def test_zero_decimals(self) -> None:
        assert format_ratio(Fraction(3, 2), 0) == "2"

    def test_percentages(self) -> None:
        assert format_percent(Fraction(60, 100)) == "60.0%"
        assert format_percent(Fraction(1, 3)) == "33.3%"


class TestValueDispatch:
    def test_each_result_unit_gets_its_own_rendering(self) -> None:
        assert format_value(2400, "mm") == "2.40 m"
        assert format_value(9_500_000, "mm2") == "9.50 m2"
        assert format_value(3, "count") == "3"
        assert format_value(5000, "bp10000") == "50.00%"
        assert format_value(True, "boolean") == "yes"
        assert format_value(False, "boolean") == "no"
        assert format_value(["N", "NE"], "zone") == "N or NE"

    def test_a_missing_measurement_says_so(self) -> None:
        assert format_value(None, "mm") == "not measured"

    def test_a_zone_limit_prefers_the_allowed_directions(self) -> None:
        assert format_limit({"allow": ["NE"], "fallback": {"allow": ["N"]}}, "zone") == "NE"

    def test_a_deny_only_zone_limit_names_what_to_avoid(self) -> None:
        assert format_limit({"deny": ["NE"]}, "zone") == "NE"

    def test_counts(self) -> None:
        assert format_count(0) == "0"


class TestRenderMessage:
    TEMPLATE = "{element} is {actual} - the minimum is {limit} ({cite})."

    def test_all_four_placeholders(self) -> None:
        rendered = render_message(
            self.TEMPLATE,
            element="Bedroom 2",
            actual=8_900_000,
            limit=9_500_000,
            unit="mm2",
            cite="NBC 2016 Cl. 4.2.1",
        )
        assert rendered == "Bedroom 2 is 8.90 m2 - the minimum is 9.50 m2 (NBC 2016 Cl. 4.2.1)."

    def test_an_unknown_brace_is_left_alone_not_raised_on(self) -> None:
        """A pack is data written by an architect; a stray brace must not crash a run."""
        rendered = render_message(
            "{element} needs {something}",
            element="The stair",
            actual=1,
            limit=2,
            unit="count",
            cite="x",
        )
        assert rendered == "The stair needs {something}"


class TestLabels:
    def test_a_named_room_wins_over_its_type(self) -> None:
        assert room_label(room(name="Bedroom 2")) == "Bedroom 2"

    def test_an_unnamed_room_reads_as_its_type(self) -> None:
        summary = RoomSummary.from_json(
            {**make_room("r1", "master_bedroom"), "name": "master_bedroom"}, "room"
        )
        assert room_label(summary) == "Master Bedroom"

    def test_storeys_read_as_floors(self) -> None:
        assert storey_label(StoreySummary(id="s", index=0, height_mm=3000)) == "Ground floor"
        assert storey_label(StoreySummary(id="s", index=1, height_mm=3000)) == "First floor"
        assert storey_label(StoreySummary(id="s", index=9, height_mm=3000)) == "Floor 9"

    def test_edges_read_as_setbacks(self) -> None:
        edge = PlotEdge(index=0, role="front", road_width_mm=9000, setback_provided_mm=3000)
        assert edge_label(edge) == "The front setback"
        side = PlotEdge(index=1, role="side-a", road_width_mm=None, setback_provided_mm=1200)
        assert edge_label(side) == "The left side setback"

    def test_an_opening_is_named_by_the_room_it_serves(self) -> None:
        context = make_context(rooms=[make_room("r1", "bedroom", name="Bedroom 2")])
        opening = OpeningSummary.from_json(
            {
                "id": "d1",
                "storeyId": "storey_g",
                "kind": "door",
                "role": "internal",
                "widthMm": 800,
                "heightMm": 2100,
                "roomIds": ["r1"],
            },
            "opening",
        )
        assert opening_label(opening, context) == "The Bedroom 2 door"

    def test_an_opening_with_no_room_falls_back_to_its_role(self) -> None:
        opening = OpeningSummary.from_json(
            {
                "id": "d1",
                "storeyId": "storey_g",
                "kind": "door",
                "role": "main-entrance",
                "widthMm": 900,
                "heightMm": 2100,
            },
            "opening",
        )
        assert opening_label(opening) == "The main door"

    def test_stairs_and_joins(self) -> None:
        from garh_rules.context import StairSummary

        stair = StairSummary(
            id="s1",
            storey_id="storey_g",
            riser_mm=165,
            tread_mm=280,
            width_mm=1050,
            headroom_mm=2200,
        )
        assert stair_label(stair) == "The staircase"
        assert join_labels(["Bedroom 1"]) == "Bedroom 1"
        assert join_labels(["Bedroom 1", "Bedroom 2"]) == "Bedroom 1 and Bedroom 2"
        assert join_labels([]) == ""
