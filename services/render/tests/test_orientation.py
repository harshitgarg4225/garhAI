"""Elevation renders must know which way the building actually faces.

The seven original presets were orientation-blind: "three-quarter view, dusk" is a
mood, not a facade. An architect asks for the *north elevation in morning light*,
because that is the drawing they are checking — and "north elevation" is a
different physical face on every plot, because north is a property of the project
(``plot.north``), not of the preset.

That makes two things testable, and both of them are the kind of defect that looks
completely plausible in the output:

1. **The camera stands opposite the facade.** Get it backwards and the render shows
   the far side of the house, convincingly.
2. **The light is computed, not written.** A render that paints warm afternoon sun
   onto a north wall is a photograph of a building that does not exist, and it is
   the architect who has to explain it to the client.

Every assertion here has a negative control, because "the prompt mentions the sun"
is exactly the shape of a test that cannot fail.
"""

from __future__ import annotations

import pytest

from services.render.orientation import (
    CARDINALS,
    TIMES_OF_DAY,
    camera_azimuth_deg,
    describe_light,
    facade_azimuth_deg,
    normalise_deg,
)
from services.render.prompts import assert_templates_cover_presets, build_prompt
from services.render.types import PRESETS, RenderRequest


def _request(preset: str, north_deg: float = 0.0) -> RenderRequest:
    return RenderRequest(
        viewport_png=b"viewport",
        mode="precise",
        preset=preset,
        seed=1,
        size=(512, 512),
        depth_png=b"depth",
        north_deg=north_deg,
    )


# ===========================================================================
# Where the camera stands
# ===========================================================================
def test_the_camera_stands_opposite_the_facade_it_photographs() -> None:
    """To see the north face you stand north of it and look south."""
    for face in CARDINALS:
        facade = facade_azimuth_deg(face)
        camera = camera_azimuth_deg(face)
        assert normalise_deg(camera - facade) == 180, face


def test_negative_control_standing_on_the_facades_own_bearing_is_the_far_side() -> None:
    """Prove the assertion above discriminates: the wrong answer is 0, not 180."""
    for face in CARDINALS:
        assert normalise_deg(facade_azimuth_deg(face) - facade_azimuth_deg(face)) == 0


@pytest.mark.parametrize("north_deg", [0, 30, 90, 180, 270, 359])
def test_the_project_north_rotates_the_camera_with_it(north_deg: int) -> None:
    """A plot rotated by N turns every elevation's camera by exactly N.

    Without this the sheet's "north elevation" and the render's would be different
    faces of the same house on any plot that is not axis-aligned — which is most of
    them.
    """
    for face in CARDINALS:
        rotated = camera_azimuth_deg(face, north_deg)
        assert rotated == normalise_deg(camera_azimuth_deg(face) + north_deg), face


# ===========================================================================
# What the light does
# ===========================================================================
def test_a_north_facade_is_in_shade_through_the_working_day() -> None:
    """The sun crosses the SOUTHERN sky, so the north face is lit by sky, not sun.

    Morning, midday and afternoon only — and the exclusion of evening is a real
    result rather than a convenience. This assertion originally covered all four
    hours and failed, correctly: at sunset the sun sits north of west (here, a
    bearing of 285), so a north wall genuinely does catch low raking light from the
    north-west. That is true in India in summer, it is exactly the condition an
    architect checks a north facade for, and a test that had asserted it away would
    have forced the renderer to draw a shaded wall that is not shaded.
    """
    for hour in ("morning", "midday", "afternoon"):
        light = describe_light("north", hour)
        assert not light.lit, hour
        assert light.incidence == "shaded"


def test_a_north_facade_does_catch_low_north_west_light_at_sunset() -> None:
    """The other half of the result above, asserted so it cannot be "fixed" away."""
    evening = describe_light("north", "evening")
    assert evening.lit
    assert evening.incidence == "raking"
    assert "very low sun" in evening.phrase


def test_a_south_facade_is_frontally_lit_at_midday() -> None:
    light = describe_light("south", "midday")
    assert light.lit
    assert light.incidence == "frontal"


def test_a_west_facade_is_shaded_in_the_morning_and_frontal_in_the_afternoon() -> None:
    """The classic Indian west-facade problem, which is why an architect checks it."""
    assert not describe_light("west", "morning").lit
    assert describe_light("west", "afternoon").incidence == "frontal"


def test_rotating_the_plot_moves_the_light_onto_the_other_face() -> None:
    """The whole point, stated as one assertion.

    Rotate north by 90 and the face LABELLED north physically points east — so it
    must gain morning sun and lose the afternoon. If this passes with the rotation
    ignored, the presets are orientation-blind again.
    """
    assert not describe_light("north", "morning", north_deg=0).lit
    assert describe_light("north", "morning", north_deg=90).lit
    assert not describe_light("north", "afternoon", north_deg=90).lit


def test_negative_control_ignoring_the_rotation_collapses_the_distinction() -> None:
    """Prove the test above can fail: with north_deg dropped, both answers are equal."""
    unrotated = describe_light("north", "morning").lit
    assert unrotated == describe_light("north", "morning", north_deg=0).lit


# ===========================================================================
# The preset catalogue and the prompt it produces
# ===========================================================================
def test_every_face_and_hour_has_a_preset_and_they_are_precise_only() -> None:
    """An elevation render is a drawing-check, not a mood board.

    Explore mode is allowed to reinterpret geometry, which would defeat the reason
    the architect named a face in the first place.
    """
    elevations = {key: preset for key, preset in PRESETS.items() if preset.is_elevation}
    assert len(elevations) == len(CARDINALS) * len(TIMES_OF_DAY)
    for face in CARDINALS:
        for hour in TIMES_OF_DAY:
            preset = elevations["elevation-%s-%s" % (face, hour)]
            assert preset.modes == ("precise",), preset.id
            assert preset.scene == "exterior"


def test_the_prompt_changes_when_the_plot_is_rotated() -> None:
    """The same preset, two plots, two different descriptions of the light."""
    shaded = build_prompt(_request("elevation-north-morning", north_deg=0)).positive
    sunlit = build_prompt(_request("elevation-north-morning", north_deg=90)).positive

    assert shaded != sunlit
    assert "no direct sun on this face" in shaded
    assert "no direct sun on this face" not in sunlit
    # Both still name the face the architect asked for — the LABEL does not move,
    # only the light on it.
    assert "north elevation" in shaded
    assert "north elevation" in sunlit


def test_negative_control_a_free_camera_preset_ignores_north() -> None:
    """The rotation must reach the elevation branch and ONLY the elevation branch.

    If this ever starts differing, the computed light has leaked into the fixed
    templates and every existing render has silently changed.
    """
    a = build_prompt(_request("exterior-street-day", north_deg=0)).positive
    b = build_prompt(_request("exterior-street-day", north_deg=90)).positive
    assert a == b


def test_the_template_coverage_gate_still_holds_with_computed_presets() -> None:
    """The gate must cover the fixed templates without failing on the computed ones."""
    assert_templates_cover_presets()
