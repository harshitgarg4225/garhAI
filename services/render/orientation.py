"""Which way a facade faces, and what the light does to it at a given hour.

An architect does not ask for "three-quarter view, dusk". They ask for the *north
elevation in morning light*, because that is the drawing they are checking and
that is the hour the client will see it. Serving that means the render has to know
two things the seven original presets could not know:

* **Where north actually is.** It is a property of the project — ``plot.north`` —
  not of the preset. The same "north elevation" preset points the camera in a
  different world direction on two different plots, and a preset catalogue with a
  hard-coded azimuth would silently render the wrong face of the building.
* **Where the sun is at that hour, in India.** A north facade in Bengaluru is in
  shade essentially all day; a west facade at 16:00 is in hard, low, warm light
  that an architect specifically wants to see because it is the one that drives
  the shading decision. Describing "warm afternoon light" on a north elevation
  would be a lie the render then draws convincingly.

This module owns that arithmetic and nothing else. It emits a
:class:`LightDescription` — plain words — which
:mod:`services.render.prompts` interpolates and which the web app uses to place
the camera. Keeping it here rather than in the prompt templates means the two
cannot disagree about which way the building is facing.

Deliberately simple, and deliberately honest about it: this is a solar *heuristic*
for prompt text and camera placement, not an analysis engine. It does not know the
date, the latitude, or the surrounding buildings. §9's sun-path work owns real
solar geometry; this owns "is this face lit, and how".
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

__all__ = [
    "CARDINALS",
    "Cardinal",
    "LightDescription",
    "TIMES_OF_DAY",
    "TimeOfDay",
    "camera_azimuth_deg",
    "describe_light",
    "facade_azimuth_deg",
    "normalise_deg",
]

#: The four faces an elevation sheet is drawn for. §7 names exactly these.
Cardinal = Literal["north", "east", "south", "west"]
CARDINALS: tuple[Cardinal, ...] = ("north", "east", "south", "west")

#: The hours an architect actually asks to see. Not a clock — three lighting
#: conditions with names a person uses.
TimeOfDay = Literal["morning", "midday", "afternoon", "evening"]
TIMES_OF_DAY: tuple[TimeOfDay, ...] = ("morning", "midday", "afternoon", "evening")

#: Compass bearing of each face, measured clockwise from north in MODEL space
#: before the project's own north rotation is applied.
_FACADE_BEARING: dict[Cardinal, int] = {"north": 0, "east": 90, "south": 180, "west": 270}

#: Roughly where the sun sits, as a bearing, at each named hour. Northern-hemisphere
#: India: the sun crosses the southern sky, so midday is due south rather than
#: overhead-neutral. Whole degrees because this feeds prose, not a shadow study.
_SUN_BEARING: dict[TimeOfDay, int] = {
    "morning": 100,
    "midday": 180,
    "afternoon": 250,
    "evening": 285,
}

#: How high the sun is at each hour, in words. Drives the shadow language.
_SUN_HEIGHT: dict[TimeOfDay, str] = {
    "morning": "low",
    "midday": "high",
    "afternoon": "lowering",
    "evening": "very low",
}


def normalise_deg(deg: float) -> int:
    """Fold any bearing into ``[0, 360)`` as a whole number of degrees.

    Half-away-from-zero, like every other rounding in this repository, so a
    bearing that lands on x.5 does not depend on the interpreter's banker's
    rounding.
    """
    value = float(deg) % 360.0
    whole = int(value)
    return (whole + 1) % 360 if value - whole >= 0.5 else whole


def facade_azimuth_deg(face: Cardinal, north_deg: float = 0.0) -> int:
    """The world bearing the given facade looks OUT along.

    ``north_deg`` is the project's north rotation — the same value the plot
    carries. A plot whose north is rotated 30 degrees turns its "north elevation"
    into a facade pointing at bearing 30, and the camera must follow it or the
    sheet and the render show different faces of the same house.
    """
    return normalise_deg(_FACADE_BEARING[face] + north_deg)


def camera_azimuth_deg(face: Cardinal, north_deg: float = 0.0) -> int:
    """Where the CAMERA stands to photograph that facade.

    Opposite the facade's own bearing: to see the north face you stand to its
    north and look south. Getting this backwards renders the far side of the
    building and looks, at a glance, entirely plausible — which is exactly why it
    is one named function with one test rather than an inline ``+ 180``.
    """
    return normalise_deg(facade_azimuth_deg(face, north_deg) + 180)


@dataclass(frozen=True)
class LightDescription:
    """What the light is doing on one facade at one hour, in words a prompt can use."""

    #: True when the sun is on this side of the building at all.
    lit: bool
    #: "raking", "frontal", "grazing" — how the light meets the face.
    incidence: str
    #: A whole clause, ready to drop into a prompt template.
    phrase: str
    #: Bearing between the sun and the facade, 0 = sun straight onto the face.
    relative_deg: int


def describe_light(
    face: Cardinal, time_of_day: TimeOfDay, north_deg: float = 0.0
) -> LightDescription:
    """Describe the light on ``face`` at ``time_of_day`` for a plot rotated ``north_deg``.

    The rule is one angle: how far the sun's bearing is from the direction the
    facade looks out along.

    * within 45 degrees — the sun is square on the face: frontal, bright, short shadows;
    * 45 to 90 — raking across it, which is the condition that shows relief and is
      what an architect asks for when checking a facade;
    * beyond 90 — the face is in its own shade, and the honest answer is diffuse
      skylight rather than an invented sunbeam. A north facade in India is in this
      state nearly always, and saying so is the point: a render that puts warm
      afternoon sun on a north wall is a drawing of a building that does not exist.
    """
    facade = facade_azimuth_deg(face, north_deg)
    relative = normalise_deg(_SUN_BEARING[time_of_day] - facade)
    signed = relative if relative <= 180 else 360 - relative
    height = _SUN_HEIGHT[time_of_day]

    if signed <= 45:
        return LightDescription(
            lit=True,
            incidence="frontal",
            phrase="%s sun square on the facade, bright, short crisp shadows" % height,
            relative_deg=signed,
        )
    if signed <= 90:
        return LightDescription(
            lit=True,
            incidence="raking",
            phrase="%s sun raking across the facade, long shadows picking out every "
            "projection and reveal" % height,
            relative_deg=signed,
        )
    return LightDescription(
        lit=False,
        incidence="shaded",
        phrase="facade in its own shade under open sky, soft even diffuse light, no "
        "direct sun on this face",
        relative_deg=signed,
    )
