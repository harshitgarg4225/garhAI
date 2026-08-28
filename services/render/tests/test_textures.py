"""Procedural texture tests: the wrap, the determinism, and the catalogue wiring.

Every assertion here is negative-tested in the same file. The pattern this repo has been
burned by is a green check that cannot go red (CLAUDE.md), and a seamlessness test is an
easy place to write one — pick a loose enough tolerance and every image on earth passes.

The wrap is checked at two levels, because one level is not enough:

* **Exactly**, per primitive. ``_noise``, ``_wrap_blur``, ``_wrapped_draw`` and
  ``_gradient`` are the only four places wrapping can be got wrong, and each has an
  independently computable reference it must match *byte for byte* — a wider lattice
  tiling, a generously padded blur, the torus symmetry of a disc on the origin, and a
  rolled neighbour. No tolerance, so no room to hide.
* **Statistically**, per shipped texture. Every one of the catalogue's 184 materials is
  painted and every map is measured at its wrap. This is the coarse net: it catches a
  recipe doing something non-wrapping *outside* those four primitives.

It is worth being exact about what the statistical net does and does not see, measured
by deliberately de-wrapping each primitive and re-running the whole catalogue:

===================== ====================================================
de-wrapped primitive  worst overrun of the seam budget (fires above 1.0)
===================== ====================================================
``_gradient``         56x — every recipe, deafening
``_wrapped_draw``     117x on ``tile``; ignored by the recipes that draw
                      nothing, which is correct
``_noise``            2.7x-10.6x on eight of ten recipes
``_wrap_blur``        1.41x, and on only 2 of the 184 shipped materials
===================== ====================================================

An unwrapped blur is the thin one. The whole-catalogue sweep does go red on it — two
marbles cross the line — but 1.41x is far too little margin to build a per-recipe control
on, and no tolerance tightening would fix that honestly: for most recipes the wrap lands
in a locally flat part of the height field, where a clamped blur and a wrapping one agree
to the byte. That primitive is pinned by its exact test instead, which is the shape of
the argument throughout — the statistical gate is the backstop, the four exact tests are
the proof.
"""

from __future__ import annotations

import array
import importlib.util
import json
import math
import os
import subprocess
import sys
from collections.abc import Iterator
from typing import Any

import pytest
from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageStat

from services.render import textures as tx
from services.render.textures import RECIPES, TextureError, generate, material_texture_set


def _repo_root() -> str:
    """Walk up to the checkout's own markers rather than counting ``dirname`` calls."""
    here = os.path.dirname(os.path.abspath(__file__))
    while True:
        if os.path.isdir(os.path.join(here, "fixtures")) and os.path.isdir(
            os.path.join(here, "scripts")
        ):
            return here
        parent = os.path.dirname(here)
        if parent == here:  # pragma: no cover - only on a broken checkout
            raise RuntimeError("no repository root above %s" % os.path.abspath(__file__))
        here = parent


_ROOT = _repo_root()
MATERIALS_JSON = os.path.join(_ROOT, "fixtures", "catalog", "materials.json")


@pytest.fixture(scope="module")
def expander() -> Any:
    """``scripts/expand_catalog.py`` loaded as a module — it is a script, not a package."""
    path = os.path.join(_ROOT, "scripts", "expand_catalog.py")
    spec = importlib.util.spec_from_file_location("expand_catalog", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def materials() -> list[dict[str, Any]]:
    with open(MATERIALS_JSON, encoding="utf-8") as handle:
        rows = json.load(handle)
    assert isinstance(rows, list) and rows
    return rows


@pytest.fixture(scope="module")
def painted(materials: list[dict[str, Any]]) -> list[tuple[dict[str, Any], tx.TextureSet]]:
    """Every shipped material, painted once. ~4 s; shared by the suite's sweeps."""
    return [(row, material_texture_set(row)) for row in materials]


# ===========================================================================
# The seam statistic
# ===========================================================================
def junction_profile(image: Image.Image, axis: int) -> list[float]:
    """Mean absolute difference between each line and the next, wrapping at the end.

    Index ``n - 1`` is the wrap: line ``n - 1`` against line ``0``. Computed with
    ``ImageChops`` and a box-filter reduction rather than a Python loop, so measuring
    all 1 104 maps in the catalogue costs about a second rather than four minutes.
    """
    width, height = image.size
    count = width if axis == 0 else height
    step = (-1, 0) if axis == 0 else (0, -1)
    bands = image.split()
    totals = [0.0] * count
    for band in bands:
        delta = ImageChops.difference(band, ImageChops.offset(band, *step)).convert("F")
        line = delta.resize((count, 1) if axis == 0 else (1, count), Image.Resampling.BOX)
        totals = [a + b for a, b in zip(totals, array.array("f", line.tobytes()), strict=True)]
    return [total / len(bands) for total in totals]


def _percentile(values: list[float], quantile: float) -> float:
    ordered = sorted(values)
    position = (len(ordered) - 1) * quantile
    low = int(position)
    high = min(low + 1, len(ordered) - 1)
    return ordered[low] + (ordered[high] - ordered[low]) * (position - low)


def seam_overrun(image: Image.Image, axis: int) -> float:
    """How far the wrap junction exceeds its budget. ``<= 1.0`` is seamless.

    The budget is the 95th percentile of the image's *own* interior junctions, so the
    test scales with the content instead of pinning an absolute number that a busy
    texture would fail and a flat one would pass with a visible seam. The 1.6x and the
    constant are set from measurement, not taste: across all 1 104 shipped maps the
    worst wrap sits at 0.73 of budget (``granite-forest-green``), while the de-wrapped
    controls below land between 8.6x and 117x.
    """
    profile = junction_profile(image, axis)
    return profile[-1] / (1.6 * _percentile(profile, 0.95) + 0.75)


def worst_overrun(texture: tx.TextureSet) -> tuple[float, str]:
    worst, where = 0.0, ""
    for name, image in texture.maps().items():
        for axis in (0, 1):
            overrun = seam_overrun(image, axis)
            if overrun > worst:
                worst, where = overrun, "%s/%s" % (name, "x" if axis == 0 else "y")
    return worst, where


@pytest.fixture()
def restore_textures_module() -> Iterator[None]:
    """Undo the deliberate breakages the negative controls install."""
    saved = {
        name: getattr(tx, name) for name in ("_noise", "_wrap_blur", "_wrapped_draw", "_gradient")
    }
    try:
        yield
    finally:
        for name, value in saved.items():
            setattr(tx, name, value)


# ===========================================================================
# Level 1 — the four wrap primitives, checked exactly
# ===========================================================================
def _lattice_upsample(
    seed: str, size: int, cells_x: int, cells_y: int, repeats: int
) -> Image.Image:
    """The reference ``_noise`` is meant to be: an N-times tiled lattice, centre kept."""
    rng = tx._rng(seed)
    lattice = Image.new("L", (cells_x, cells_y))
    lattice.putdata([tx._randbyte(rng) for _ in range(cells_x * cells_y)])
    tiled = Image.new("L", (cells_x * repeats, cells_y * repeats))
    for row in range(repeats):
        for col in range(repeats):
            tiled.paste(lattice, (col * cells_x, row * cells_y))
    big = tiled.resize((size * repeats, size * repeats), Image.Resampling.BICUBIC)
    origin = (repeats // 2) * size
    return big.crop((origin, origin, origin + size, origin + size))


LATTICES = [(3, 3), (4, 4), (8, 8), (22, 22), (40, 40), (3, 80)]


@pytest.mark.parametrize(("cells_x", "cells_y"), LATTICES)
def test_noise_tiles_its_lattice_far_enough(cells_x: int, cells_y: int) -> None:
    """``_noise`` itself must equal a five-times-tiled reference, byte for byte.

    Note what is on the left of the ``==``: the production function, not a copy of it.
    An earlier draft of this test compared two locally built references and stayed green
    while ``_noise`` was deliberately de-tiled — a check that could not go red, which is
    the exact failure CLAUDE.md's third bug describes.

    Both sides draw from ``_rng("noise")``, and ``_noise`` consumes exactly
    ``cells_x * cells_y`` values, so the two lattices are identical and the only thing
    under test is how far the lattice was tiled before the upsample.
    """
    produced = tx._noise(tx._rng("noise"), 256, cells_x, cells_y)
    assert produced.tobytes() == _lattice_upsample("noise", 256, cells_x, cells_y, 5).tobytes()


@pytest.mark.parametrize(("cells_x", "cells_y"), LATTICES)
def test_negative_control_an_untiled_lattice_does_not_match(cells_x: int, cells_y: int) -> None:
    """Prove the comparison above discriminates: resizing the bare lattice must differ."""
    one = _lattice_upsample("noise", 256, cells_x, cells_y, 1)
    five = _lattice_upsample("noise", 256, cells_x, cells_y, 5)
    assert one.tobytes() != five.tobytes()


def test_the_lattice_reference_is_periodic_where_it_is_cropped() -> None:
    """Why the reference above is a valid one: two interior strips a period apart match.

    The *outer* tiles of a 3x or 5x resize are clamped at the image border and would not
    match anything — which is why both ``_noise`` and the reference keep the centre one.
    """
    size, cells = 256, 8
    rng = tx._rng("period")
    lattice = Image.new("L", (cells, cells))
    lattice.putdata([tx._randbyte(rng) for _ in range(cells * cells)])
    tiled = Image.new("L", (cells * 3, cells * 3))
    for row in range(3):
        for col in range(3):
            tiled.paste(lattice, (col * cells, row * cells))
    big = tiled.resize((size * 3, size * 3), Image.Resampling.BICUBIC)
    half = size // 2
    left = big.crop((half, size, half + size, size * 2))
    right = big.crop((half + size, size, half + size * 2, size * 2))
    assert left.tobytes() == right.tobytes()


@pytest.mark.parametrize("radius", [0.4, 0.5, 0.6, 0.8, 1.0, 1.2, 1.5, 3.0])
def test_wrap_blur_equals_a_generously_padded_blur(radius: float) -> None:
    """``_wrap_blur``'s four-sigma pad must give the same bytes as a half-image pad."""
    size = 64
    field = tx._noise(tx._rng("blur"), size, 8, 8)
    padded = tx._wrap_pad(field, size // 2).filter(ImageFilter.GaussianBlur(radius))
    reference = padded.crop((size // 2, size // 2, size // 2 + size, size // 2 + size))
    assert tx._wrap_blur(field, radius).tobytes() == reference.tobytes()


@pytest.mark.parametrize("radius", [0.4, 0.5, 0.6, 0.8, 1.0, 1.2, 1.5, 3.0])
def test_negative_control_pillows_own_blur_clamps_at_the_edge(radius: float) -> None:
    """Prove the check above can fail: the unpadded blur must NOT match the reference."""
    size = 64
    field = tx._noise(tx._rng("blur"), size, 8, 8)
    padded = tx._wrap_pad(field, size // 2).filter(ImageFilter.GaussianBlur(radius))
    reference = padded.crop((size // 2, size // 2, size // 2 + size, size // 2 + size))
    assert field.filter(ImageFilter.GaussianBlur(radius)).tobytes() != reference.tobytes()


def _disc_on_the_origin(paint_all_nine: bool) -> Image.Image:
    size = 64
    image = Image.new("L", (size, size), 0)

    def paint(draw: ImageDraw.ImageDraw, offset_x: int, offset_y: int) -> None:
        draw.ellipse((offset_x - 11, offset_y - 11, offset_x + 11, offset_y + 11), fill=255)

    if paint_all_nine:
        tx._wrapped_draw(image, paint)
    else:
        paint(ImageDraw.Draw(image), 0, 0)
    return image


def test_wrapped_draw_paints_the_whole_torus() -> None:
    """A disc centred on the origin is symmetric under x -> -x and y -> -y on a torus.

    Flip the image and roll it one pixel and it must come back byte-identical — which is
    only true if the shape reappears in all four corners.
    """
    image = _disc_on_the_origin(paint_all_nine=True)
    flipped_x = ImageChops.offset(image.transpose(Image.Transpose.FLIP_LEFT_RIGHT), 1, 0)
    flipped_y = ImageChops.offset(image.transpose(Image.Transpose.FLIP_TOP_BOTTOM), 0, 1)
    assert flipped_x.tobytes() == image.tobytes()
    assert flipped_y.tobytes() == image.tobytes()
    corners = image.size[0] - 1
    assert {image.getpixel((0, 0)), image.getpixel((corners, corners))} == {255}


def test_negative_control_a_single_pass_draw_breaks_the_symmetry() -> None:
    """Prove it: painting only the centre copy leaves three corners black."""
    image = _disc_on_the_origin(paint_all_nine=False)
    flipped_x = ImageChops.offset(image.transpose(Image.Transpose.FLIP_LEFT_RIGHT), 1, 0)
    assert flipped_x.tobytes() != image.tobytes()
    assert image.getpixel((image.size[0] - 1, image.size[1] - 1)) == 0


def _step_height(size: int = 16) -> Image.Image:
    """A single bright column at x = 0, so the gradient at the wrap is unmissable."""
    height = Image.new("L", (size, size), 0)
    ImageDraw.Draw(height).rectangle((0, 0, 0, size - 1), fill=255)
    return height


def test_gradient_rolls_to_the_opposite_edge() -> None:
    """The x-gradient at the last column must see the bright column at x = 0."""
    height = _step_height()
    gradient = tx._gradient(height, 0)
    size = height.size[0]
    # column size-1's forward neighbour is column 0 (bright) and its back neighbour is
    # size-2 (dark), so (ahead - behind)/2 + 128 saturates upward.
    assert gradient.getpixel((size - 1, 0)) == 255
    assert gradient.getpixel((1, 0)) == 0  # ahead dark, behind bright


def test_negative_control_a_clamped_gradient_misses_the_wrap() -> None:
    """Prove it: cropping instead of rolling reads the wrong neighbour at the edge."""
    height = _step_height()
    size = height.size[0]
    ahead = height.crop((1, 0, size + 1, size))
    behind = height.crop((-1, 0, size - 1, size))
    clamped = ImageChops.subtract(ahead, behind, 2, 128)
    assert clamped.getpixel((size - 1, 0)) != 255


# ===========================================================================
# Level 2 — every shipped texture, measured at its wrap
# ===========================================================================
def test_every_catalogue_material_wraps(
    painted: list[tuple[dict[str, Any], tx.TextureSet]],
) -> None:
    """All 184 materials x 3 maps x 2 axes. A tile that does not wrap is wallpaper."""
    offenders = []
    for row, texture in painted:
        overrun, where = worst_overrun(texture)
        if overrun > 1.0:
            offenders.append(
                "%s (%s) %s at %.2fx budget" % (row["id"], row["texture"], where, overrun)
            )
    assert not offenders, "seam(s) found: %s" % "; ".join(offenders[:10])


def test_negative_control_a_ramp_is_not_seamless() -> None:
    """The simplest non-wrapping image there is: smooth everywhere, a cliff at the wrap."""
    size = 256
    ramp = Image.new("L", (size, size))
    ramp.putdata([x for _ in range(size) for x in range(size)])
    assert seam_overrun(ramp, 0) > 10.0
    assert seam_overrun(ramp, 1) <= 1.0  # ...and it is seamless the other way, as it should be


@pytest.mark.parametrize(
    ("break_name", "recipe", "least_overrun"),
    [
        # A primitive deliberately de-wrapped, a recipe the break is loud in, and a floor
        # comfortably under what it measured (8.6x, 56x, 117x). ``_wrap_blur`` is absent
        # on purpose: its loudest effect anywhere in the catalogue is 1.41x, which is too
        # near the 1.0 line to assert without flaking. See the module docstring.
        ("noise", "stone", 4.0),
        ("gradient", "metal", 10.0),
        ("draw", "tile", 20.0),
    ],
)
def test_negative_control_breaking_a_wrap_primitive_is_caught(
    break_name: str,
    recipe: str,
    least_overrun: float,
    restore_textures_module: None,
) -> None:
    """Break the wrap for real and confirm the gate goes red rather than shrugging."""
    if break_name == "noise":

        def untiled(rng: Any, size: int, cells_x: int, cells_y: int) -> Image.Image:
            cells_x, cells_y = max(3, cells_x), max(3, cells_y)
            lattice = Image.new("L", (cells_x, cells_y))
            lattice.putdata([tx._randbyte(rng) for _ in range(cells_x * cells_y)])
            return lattice.resize((size, size), Image.Resampling.BICUBIC)

        tx._noise = untiled
    elif break_name == "gradient":

        def clamped(height: Image.Image, axis: int) -> Image.Image:
            step = (1, 0) if axis == 0 else (0, 1)
            width, tall = height.size
            ahead = height.crop((step[0], step[1], width + step[0], tall + step[1]))
            behind = height.crop((-step[0], -step[1], width - step[0], tall - step[1]))
            return ImageChops.subtract(ahead, behind, 2, 128)

        tx._gradient = clamped
    elif break_name == "draw":
        tx._wrapped_draw = lambda image, paint: paint(ImageDraw.Draw(image), 0, 0)
    else:  # pragma: no cover - a typo in the parametrisation must not pass vacuously
        raise AssertionError("unknown breakage %r — nothing was de-wrapped" % break_name)

    worst = max(worst_overrun(generate(recipe, colour))[0] for colour in ("#9C5137", "#5B6B66"))
    assert worst >= least_overrun, (
        "de-wrapping %s only moved %s to %.2fx budget — the seam gate has gone blunt"
        % (break_name, recipe, worst)
    )


def test_the_seam_gate_is_green_again_after_the_controls(
    restore_textures_module: None,
) -> None:
    """Guards the fixture that undoes the breakages above; a leak would poison the suite."""
    assert worst_overrun(generate("tile", "#9C5137"))[0] <= 1.0


# ===========================================================================
# Determinism
# ===========================================================================
def test_the_same_material_paints_the_same_bytes() -> None:
    first = generate("stone", "#5B6B66").to_png()
    second = generate("stone", "#5B6B66").to_png()
    assert first == second


def test_negative_control_a_different_colour_or_recipe_paints_different_bytes() -> None:
    """Prove the equality above is not comparing two constants."""
    base = generate("stone", "#5B6B66").to_png()
    assert generate("stone", "#7A6A52").to_png() != base
    assert generate("speckle", "#5B6B66").to_png() != base


DETERMINISM_SNIPPET = """
import hashlib, sys
sys.path.insert(0, %r)
from services.render.textures import generate
payload = generate("wood", "#8F6238").to_png()
print(hashlib.sha256(b"".join(payload[k] for k in sorted(payload))).hexdigest())
"""


def _digest_in_subprocess(hash_seed: str) -> str:
    env = dict(os.environ, PYTHONHASHSEED=hash_seed)
    result = subprocess.run(
        [sys.executable, "-c", DETERMINISM_SNIPPET % _ROOT],
        capture_output=True,
        text=True,
        check=True,
        env=env,
        cwd=_ROOT,
    )
    return result.stdout.strip()


def test_determinism_survives_a_different_hash_seed() -> None:
    """Two fresh interpreters with different ``PYTHONHASHSEED`` must agree byte for byte.

    Seeding a generator from ``hash()`` of a string is the classic way to ship something
    that is reproducible in every test and different on every deploy; nothing inside one
    process can see it. This test can.
    """
    assert _digest_in_subprocess("1") == _digest_in_subprocess("987654321")


def test_negative_control_the_subprocess_probe_would_notice_a_change() -> None:
    """The probe compares real work, not an empty string."""
    digest = _digest_in_subprocess("1")
    assert len(digest) == 64 and digest != "0" * 64


# ===========================================================================
# The maps carry information — no recipe is quietly an alias for another
# ===========================================================================
def test_every_recipe_paints_something_different() -> None:
    """Same colour, ten recipes, ten distinct images.

    Necessary but nowhere near sufficient: because the PRNG is seeded on the recipe
    *name*, two recipes wired to the same painter still produce different pixels. The
    signature tests below are what actually catch that.
    """
    by_recipe = {recipe: generate(recipe, "#B58A5C").albedo.tobytes() for recipe in RECIPES}
    assert len(set(by_recipe.values())) == len(RECIPES)


#: ``recipe -> (roughness mean band, normal-map relief band)``, from the shipped
#: catalogue with roughly 15% margin. Their real job is not to pin numbers, it is to give
#: each recipe a **signature**: wire a recipe to the wrong painter and the seed still
#: changes with the name, so the pixels differ and a bytes-differ test shrugs — but the
#: character of the surface moves and lands outside the band. That is the "83 rules
#: reported not_applicable while the report stayed green" failure, in texture form.
RECIPE_SIGNATURES: dict[str, tuple[tuple[float, float], tuple[int, int]]] = {
    "tile": ((70, 92), (140, 200)),
    "stone": ((165, 192), (18, 50)),
    "speckle": ((56, 74), (55, 85)),
    "vein": ((35, 53), (40, 75)),
    "concrete": ((185, 212), (145, 185)),
    "wood": ((98, 118), (165, 200)),
    "plaster": ((204, 226), (20, 46)),
    "brick": ((210, 230), (200, 235)),
    "glass": ((14, 28), (3, 14)),
    "metal": ((74, 96), (18, 42)),
}


def _signature(texture: tx.TextureSet) -> tuple[float, int]:
    return ImageStat.Stat(texture.roughness).mean[0], _channel_range(texture.normal)


def _matches_signature(recipe: str, texture: tx.TextureSet) -> bool:
    (rough_lo, rough_hi), (relief_lo, relief_hi) = RECIPE_SIGNATURES[recipe]
    roughness, relief = _signature(texture)
    return rough_lo <= roughness <= rough_hi and relief_lo <= relief <= relief_hi


def test_the_signatures_can_tell_the_recipes_apart() -> None:
    """Every pair must be separated on at least one axis, or the gate below is theatre.

    This is the negative control for the signature idea itself: if two recipes' bands
    both overlapped, swapping their painters would slip through and nothing would say so.
    """
    assert set(RECIPE_SIGNATURES) == set(RECIPES)
    for first in RECIPES:
        for second in RECIPES:
            if first >= second:
                continue
            bands_a, bands_b = RECIPE_SIGNATURES[first], RECIPE_SIGNATURES[second]
            separated = any(
                a[1] < b[0] or b[1] < a[0] for a, b in zip(bands_a, bands_b, strict=True)
            )
            assert separated, (
                "%s and %s share both bands: either could stand in for "
                "the other unnoticed" % (first, second)
            )


def test_every_shipped_material_matches_its_recipe_signature(
    painted: list[tuple[dict[str, Any], tx.TextureSet]],
) -> None:
    strays = [
        "%s (%s) -> roughness %.1f relief %d" % (row["id"], row["texture"], *_signature(texture))
        for row, texture in painted
        if not _matches_signature(row["texture"], texture)
    ]
    assert not strays, "material(s) outside their recipe's signature: %s" % strays[:5]


def test_negative_control_a_recipe_wired_to_the_wrong_painter_is_caught() -> None:
    """Prove it: point ``metal`` at the plaster painter and the signature must reject it.

    A plain "do the ten recipes produce ten different images" check passes this — the
    seed is keyed on the name, so the pixels genuinely differ. Only the signature sees it.
    """
    saved = dict(tx._PAINTERS)
    try:
        tx._PAINTERS["metal"] = tx._PAINTERS["plaster"]
        assert (
            generate("metal", "#B58A5C").albedo.tobytes()
            != generate("plaster", "#B58A5C").albedo.tobytes()
        ), "the weaker check would have been fooled here"
        assert not _matches_signature("metal", generate("metal", "#B58A5C"))
    finally:
        tx._PAINTERS.clear()
        tx._PAINTERS.update(saved)
    assert _matches_signature("metal", generate("metal", "#B58A5C"))


def _channel_range(image: Image.Image) -> int:
    extrema = image.getextrema()
    pairs = extrema if image.mode == "RGB" else (extrema,)
    return max(high - low for low, high in pairs)  # type: ignore[misc]


def test_no_shipped_map_is_a_flat_fill(
    painted: list[tuple[dict[str, Any], tx.TextureSet]],
) -> None:
    """Every map of every material must carry something, and one of them a lot.

    A flat fill passes every wrap test ever written, which is what makes this the
    companion the seam gate needs. The floors are deliberately low because the tight
    cases are legitimate: a charcoal emulsion shaded multiplicatively by +/-4% spans
    only 3 levels (min albedo range in the catalogue), and float glass is nearly
    featureless by design (normal 5, roughness 18). The ``>= 16`` clause is what stops
    all three maps being simultaneously near-flat.
    """
    for row, texture in painted:
        spans = {name: _channel_range(image) for name, image in texture.maps().items()}
        assert min(spans.values()) >= 2, "%s has a near-constant map: %s" % (row["id"], spans)
        assert max(spans.values()) >= 16, "%s is flat all round: %s" % (row["id"], spans)


def test_negative_control_a_solid_fill_fails_the_flatness_check() -> None:
    """Prove the floors above are not below the thing they are meant to exclude."""
    assert _channel_range(Image.new("RGB", (32, 32), (140, 120, 90))) == 0
    assert _channel_range(Image.new("L", (32, 32), 128)) == 0


def test_every_recipe_gives_its_normal_map_something_to_say() -> None:
    """Regression: glass's bump was once shallow enough to quantise to one constant byte.

    A constant normal map is not "flat glass", it is a wasted file — the flat default
    would light identically and cost nothing. Every recipe must move the surface.
    """
    for recipe in RECIPES:
        span = _channel_range(generate(recipe, "#B58A5C").normal)
        assert span >= 4, "%s paints a constant normal map" % recipe


@pytest.mark.parametrize("colour", ["#9C5137", "#4A5F4A", "#D6E4E8", "#141416"])
@pytest.mark.parametrize("recipe", RECIPES)
def test_albedo_keeps_the_material_colour(recipe: str, colour: str) -> None:
    """The catalogue's ``colorHex`` must survive the recipe.

    Materials are chosen by colour in the UI; a generator that washes Udaipur green into
    a generic grey silently unpicks that choice.
    """
    base = tx._parse_hex(colour)
    mean = ImageStat.Stat(generate(recipe, colour).albedo).mean
    drift = max(abs(channel - reference) for channel, reference in zip(mean, base, strict=True))
    assert drift <= 36, "%s at %s drifted %.0f levels from its base colour" % (
        recipe,
        colour,
        drift,
    )


def test_negative_control_a_grey_stand_in_fails_the_colour_check() -> None:
    """Prove the tolerance above is not wide enough to swallow a colour being ignored."""
    base = tx._parse_hex("#4A5F4A")
    grey = ImageStat.Stat(Image.new("RGB", (64, 64), (128, 128, 128))).mean
    assert max(abs(c - r) for c, r in zip(grey, base, strict=True)) > 36


#: Roughness is the whole point of the exercise — flat plastic is a *uniform* roughness.
#: These are the physical relations that must hold, with the measured means in brackets:
#: glass [21] < vein [45] < speckle [64] < tile [80] < wood [108] < stone [176] <
#: concrete [200] < plaster [215] < brick [220].
ROUGHNESS_ORDER = ("glass", "vein", "speckle", "tile", "wood", "stone", "concrete", "plaster")


def test_roughness_ranks_polished_below_rough() -> None:
    means = {
        recipe: ImageStat.Stat(generate(recipe, "#9C5137").roughness).mean[0]
        for recipe in ROUGHNESS_ORDER
    }
    ordered = [means[recipe] for recipe in ROUGHNESS_ORDER]
    assert ordered == sorted(ordered), "roughness ordering broke: %s" % means
    assert means["plaster"] - means["glass"] > 150, "the maps barely differ: %s" % means


def test_normal_maps_are_unit_length_and_face_out() -> None:
    """A normal map that is not normalised lights wrong, and one with z < 0 lights inside out."""
    for recipe in RECIPES:
        payload = generate(recipe, "#9C5137").normal.tobytes()
        worst = 0.0
        for index in range(0, len(payload) - 2, 3 * 331):
            vector = [(payload[index + axis] - 127.5) / 127.5 for axis in range(3)]
            worst = max(worst, abs(math.sqrt(sum(v * v for v in vector)) - 1.0))
            assert vector[2] > 0.0, "%s has an inward-facing normal" % recipe
        assert worst < 0.02, "%s normals are off unit length by %.3f" % (recipe, worst)


def test_negative_control_a_flat_grey_would_fail_the_normal_check() -> None:
    """(128, 128, 128) is the sloppy "no bump" fill; it is not a unit normal and must fail."""
    vector = [(128 - 127.5) / 127.5] * 3
    assert abs(math.sqrt(sum(v * v for v in vector)) - 1.0) >= 0.02


# ===========================================================================
# Catalogue wiring — the field is written, and it is read
# ===========================================================================
def test_the_two_recipe_lists_agree(expander: Any) -> None:
    """``scripts/expand_catalog.py`` cannot import this module (it must run without
    Pillow, for ``make bare``), so its copy of the recipe names is gated here instead."""
    assert tuple(expander.TEXTURE_RECIPES) == RECIPES


def test_negative_control_the_recipe_lists_would_notice_a_new_name(expander: Any) -> None:
    assert (*expander.TEXTURE_RECIPES, "hologram") != RECIPES


def test_every_material_declares_a_known_recipe(materials: list[dict[str, Any]]) -> None:
    unknown = sorted({row.get("texture") for row in materials} - set(RECIPES))
    assert not unknown, "materials.json names recipe(s) this module cannot paint: %s" % unknown


def test_every_recipe_is_actually_used_by_the_catalogue(materials: list[dict[str, Any]]) -> None:
    """A recipe nothing selects is dead code that will rot without anyone noticing."""
    unused = sorted(set(RECIPES) - {row["texture"] for row in materials})
    assert not unused, "recipes no material uses: %s" % unused


def test_every_material_paints(painted: list[tuple[dict[str, Any], tx.TextureSet]]) -> None:
    """The whole catalogue goes through ``material_texture_set`` — the live consumer of
    the ``texture`` field — and produces three maps of the right size and mode."""
    for row, texture in painted:
        assert texture.recipe == row["texture"]
        assert texture.albedo.size == (tx.DEFAULT_SIZE, tx.DEFAULT_SIZE)
        assert texture.albedo.mode == "RGB" and texture.normal.mode == "RGB"
        assert texture.roughness.mode == "L"


def test_a_material_without_a_recipe_is_refused() -> None:
    with pytest.raises(TextureError, match="no 'texture' recipe"):
        material_texture_set({"id": "x", "colorHex": "#FFFFFF"})


def test_an_unknown_recipe_is_refused_rather_than_defaulted() -> None:
    """Falling back to a default here is how 83 rules once went quietly inert."""
    with pytest.raises(TextureError, match="unknown texture recipe"):
        generate("marbre", "#FFFFFF")


def test_a_bad_colour_is_refused() -> None:
    with pytest.raises(TextureError):
        generate("tile", "#ZZZ")


def test_committed_textures_match_the_generator(
    expander: Any, materials: list[dict[str, Any]]
) -> None:
    """Drift gate: the file must equal what ``--write`` would produce, row for row."""
    family_textures = expander._family_textures()
    drifted = [
        "%s: file=%s script=%s" % (row["id"], row.get("texture"), expected)
        for row in materials
        if row.get("texture") != (expected := expander.texture_for(row, family_textures))
    ]
    assert not drifted, "run scripts/expand_catalog.py --write: %s" % drifted[:5]


def test_negative_control_a_hand_edited_texture_is_caught(
    expander: Any, materials: list[dict[str, Any]]
) -> None:
    """Prove the drift gate discriminates: tamper with one row and it must be seen."""
    tampered = dict(materials[0], texture="glass" if materials[0]["texture"] != "glass" else "wood")
    assert expander.texture_for(tampered, expander._family_textures()) != tampered["texture"]


def test_the_backfill_touches_only_the_texture_field(
    expander: Any, materials: list[dict[str, Any]]
) -> None:
    """The derived field is regenerated every run; the authored ones never are.

    ``build_materials`` stays strictly append-only (``apps/api/tests/test_catalog_expansion``
    asserts that). ``backfill_textures`` is the separate pass that owns ``texture``, and
    what it must never do is touch anything else — including row order and ids.
    """
    stripped = [{k: v for k, v in row.items() if k != "texture"} for row in materials]
    rebuilt, changed = expander.backfill_textures(stripped)
    assert changed == len(materials), "the backfill did not fill every stripped row"
    assert [row["id"] for row in rebuilt] == [row["id"] for row in materials]
    for before, after in zip(materials, rebuilt, strict=True):
        assert {k: v for k, v in after.items() if k != "texture"} == {
            k: v for k, v in before.items() if k != "texture"
        }
        assert after["texture"] == before["texture"]


def test_the_backfill_is_idempotent(expander: Any, materials: list[dict[str, Any]]) -> None:
    once, changed = expander.backfill_textures(materials)
    assert changed == 0, "the committed file is not what the script would write"
    twice, changed_again = expander.backfill_textures(once)
    assert changed_again == 0 and twice == once


def test_negative_control_the_backfill_reports_the_rows_it_fills(
    expander: Any, materials: list[dict[str, Any]]
) -> None:
    """Prove the ``changed == 0`` assertion above can go non-zero."""
    stripped = [{k: v for k, v in row.items() if k != "texture"} for row in materials[:5]]
    _, changed = expander.backfill_textures(stripped)
    assert changed == 5
