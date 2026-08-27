"""The client pack — §9's one-click batch: 6 exteriors + living + kitchen.

This module is the single source of truth for the pack's composition. The API
router (``apps/api/garh_api/routers/renders.py``) and the web feature
(``apps/web/src/features/renders/presets.ts``) each keep a **byte-identical
mirror** of :data:`CLIENT_PACK_SHOTS` — the same convention ``garh_api.queue``
uses for the envelope — because the API deliberately does not import
``services.*`` at runtime and the browser cannot. Change the pack here first,
then update both mirrors in the same commit.

Composition rationale (spec F6, §15 "Share on WhatsApp"):

* Six exterior shots — every exterior preset in Precise (the geometry-locked
  contract a client signs off on), plus the two hero angles again in Explore so
  the WhatsApp pack also carries mood.
* Living + kitchen in Explore — interiors are Explore-only at MVP (spec F6),
  and these are the two rooms an Indian residential client always asks for.

Determinism: every shot's seed is derived from the pack's base seed by
:func:`shot_seed`, so re-running a pack with the same base seed reproduces the
same eight images on the mock provider — which is what makes the pack testable.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from services.render.types import PRESETS, RenderMode


@dataclass(frozen=True)
class PackShot:
    """One image in the client pack."""

    #: Stable slug used in filenames inside the zip (`01-exterior-street-day…`).
    slug: str
    preset: str
    mode: RenderMode
    #: Which room type the client camera should aim at, or None for exteriors.
    room_type: str | None = None


#: The §9 client pack: 6 exteriors + living + kitchen, in zip order.
CLIENT_PACK_SHOTS: tuple[PackShot, ...] = (
    PackShot(slug="exterior-street-day", preset="exterior-street-day", mode="precise"),
    PackShot(slug="exterior-34-day", preset="exterior-34-day", mode="precise"),
    PackShot(slug="exterior-34-dusk", preset="exterior-34-dusk", mode="precise"),
    PackShot(slug="exterior-night", preset="exterior-night", mode="precise"),
    PackShot(slug="exterior-street-day-explore", preset="exterior-street-day", mode="explore"),
    PackShot(slug="exterior-34-dusk-explore", preset="exterior-34-dusk", mode="explore"),
    PackShot(slug="interior-living", preset="interior-living", mode="explore", room_type="living"),
    PackShot(
        slug="interior-kitchen", preset="interior-kitchen", mode="explore", room_type="kitchen"
    ),
)

#: Exactly the spec's arithmetic, asserted so a careless edit cannot ship.
assert sum(1 for s in CLIENT_PACK_SHOTS if PRESETS[s.preset].scene == "exterior") == 6
assert sum(1 for s in CLIENT_PACK_SHOTS if PRESETS[s.preset].scene == "interior") == 2
for _shot in CLIENT_PACK_SHOTS:
    assert PRESETS[_shot.preset].allows(_shot.mode), (
        "pack shot %r requests mode %r which preset %r does not allow"
        % (_shot.slug, _shot.mode, _shot.preset)
    )
del _shot


def shot_seed(base_seed: int, index: int) -> int:
    """Deterministic per-shot seed from the pack's base seed.

    ``base + index`` and nothing cleverer: it is stable, obvious in a filename,
    and two shots of the same preset in different modes still get distinct
    grades on the mock provider.
    """
    return int(base_seed) + int(index)


def pack_filenames(shots: Iterable[PackShot] = CLIENT_PACK_SHOTS) -> list[str]:
    """Zip member names, ordered: ``01-exterior-street-day.png`` …"""
    return ["%02d-%s.png" % (index + 1, shot.slug) for index, shot in enumerate(shots)]


__all__ = ["CLIENT_PACK_SHOTS", "PackShot", "pack_filenames", "shot_seed"]
