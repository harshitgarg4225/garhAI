"""Procedural PBR textures for the material catalogue (playbook §9).

Why procedural rather than photographs
--------------------------------------
``fixtures/catalog/materials.json`` carries 184 materials and, until this module,
one flat ``colorHex`` each — so teak, Kota stone and a vitrified tile all painted
as the same plastic. The obvious fix is a bitmap library, and it is unavailable:
ambientCG and Poly Haven are network-blocked from the build sandbox, and every
other free set worth having is attribution- or share-alike-licensed, which the
licence gate (``references/market-research-and-oss-licenses.md``) refuses. An
Indian palette is in any case mostly stone, tile, plaster and timber — families
better described by a generator (grain direction, grout width, chip density) than
by a photograph we would have to licence, host and ship.

So each material names a *recipe* (``materials.json``'s ``texture`` field) and this
module paints it from the material's own base colour. Ten recipes cover the whole
catalogue: tile, stone, speckle, vein, concrete, wood, plaster, brick, glass, metal.

The two properties that make the output usable
----------------------------------------------
**Seamless.** Every primitive here wraps: noise is interpolated from a lattice that is
genuinely periodic, blurs run on a wrap-padded copy, drawn features are painted at all
nine torus offsets *in integer coordinates* (so the nine copies are exact translations
of each other), and gradients are taken with ``ImageChops.offset``, which rolls. Grid
recipes additionally sit their courses half a cell off the origin, so the wrap falls in
the middle of a face rather than down the centre of a joint — a joint split across the
wrap doubles up against its own neighbour when the tile repeats. A texture that does
not wrap is wallpaper with a visible grid, so ``tests/test_textures.py`` measures the
wrap numerically rather than trusting this paragraph.

**Deterministic.** The PRNG is seeded from a BLAKE2b digest of the recipe, colour and
size — never ``hash()``, which is salted per process — so the same material yields
byte-identical PNGs on every machine and every run. Goldens depend on that.

Pillow only. No ML dependency; importable on the ``PROVIDER_RENDER=mock`` path.
"""

from __future__ import annotations

import hashlib
import math
import random
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

from PIL import Image, ImageChops, ImageDraw, ImageFilter

from services.render.imaging import encode_png

#: Default edge length. 256 keeps a whole catalogue (184 materials x 3 maps) inside a
#: dozen seconds of pure Python; 512 is there for a hero surface and costs ~4x.
DEFAULT_SIZE = 256

#: The recipes ``materials.json`` may name. Adding a name here without a painter in
#: :data:`_PAINTERS` raises rather than silently painting a default — a recipe that
#: quietly aliases another is exactly the "83 rules went inert" failure this repo has had.
RECIPES: tuple[str, ...] = (
    "tile",
    "stone",
    "speckle",
    "vein",
    "concrete",
    "wood",
    "plaster",
    "brick",
    "glass",
    "metal",
)

Rgb = tuple[int, int, int]
#: An integer rectangle ``(x0, y0, x1, y1)``; see :func:`_wrapped_draw` on why integers.
Box = tuple[int, int, int, int]


class TextureError(ValueError):
    """A material named a recipe this module cannot paint, or an unpaintable colour."""


def _round_half_away(value: float) -> int:
    """Half-away-from-zero, the repo-wide rounding rule (CLAUDE.md).

    Local rather than imported from ``garh_model.units``: ``services/render`` ships in
    the workers' distribution and must import without the API package present.
    """
    return int(math.floor(value + 0.5)) if value >= 0 else -int(math.floor(-value + 0.5))


# ---------------------------------------------------------------------------
# Colour
# ---------------------------------------------------------------------------
def _parse_hex(color_hex: str) -> Rgb:
    text = color_hex.strip().lstrip("#")
    try:
        value = int(text, 16)
    except ValueError as exc:
        raise TextureError("colorHex must look like '#RRGGBB', got %r" % (color_hex,)) from exc
    if len(text) != 6:
        raise TextureError("colorHex must look like '#RRGGBB', got %r" % (color_hex,))
    return ((value >> 16) & 255, (value >> 8) & 255, value & 255)


def _clamp8(value: int) -> int:
    return 0 if value < 0 else (255 if value > 255 else value)


def _scale(rgb: Rgb, factor: float) -> Rgb:
    """Brighten or darken. Multiplicative, so an Absolute Black granite stays black."""
    return (
        _clamp8(_round_half_away(rgb[0] * factor)),
        _clamp8(_round_half_away(rgb[1] * factor)),
        _clamp8(_round_half_away(rgb[2] * factor)),
    )


def _mix(a: Rgb, b: Rgb, t: float) -> Rgb:
    return (
        _clamp8(_round_half_away(a[0] + (b[0] - a[0]) * t)),
        _clamp8(_round_half_away(a[1] + (b[1] - a[1]) * t)),
        _clamp8(_round_half_away(a[2] + (b[2] - a[2]) * t)),
    )


def _desaturate(rgb: Rgb, amount: float) -> Rgb:
    """Toward the row's own luminance — grout and mortar read as neutral, not tinted."""
    grey = _round_half_away(0.299 * rgb[0] + 0.587 * rgb[1] + 0.114 * rgb[2])
    return _mix(rgb, (grey, grey, grey), amount)


# ---------------------------------------------------------------------------
# Seeded randomness
# ---------------------------------------------------------------------------
def _rng(*parts: object) -> random.Random:
    """A PRNG keyed on the recipe, colour and size.

    BLAKE2b rather than ``hash()``: ``hash()`` of a str is salted per interpreter, so a
    generator keyed on it would paint a different texture in every process — a defect a
    single-process test can never see. ``tests/test_textures.py`` re-runs the generator
    in a subprocess under a different ``PYTHONHASHSEED`` for exactly that reason.
    """
    key = "|".join(str(part) for part in parts).encode("utf-8")
    return random.Random(int.from_bytes(hashlib.blake2b(key, digest_size=8).digest(), "big"))


def _randbyte(rng: random.Random, low: int = 0, high: int = 255) -> int:
    span = high - low + 1
    return low + min(span - 1, int(rng.random() * span))


def _randint(rng: random.Random, low: int, high: int) -> int:
    """Inclusive integer draw. Only ``Random.random`` is used anywhere in this module,
    so the stream depends on nothing but the Mersenne Twister itself."""
    return low + min(high - low, int(rng.random() * (high - low + 1)))


# ---------------------------------------------------------------------------
# Wrap-safe primitives. Everything below is exactly periodic in both axes.
# ---------------------------------------------------------------------------
def _noise(rng: random.Random, size: int, cells_x: int, cells_y: int) -> Image.Image:
    """Smooth value noise on a ``cells_x`` x ``cells_y`` lattice, periodic by construction.

    The lattice is tiled 3x3 before the bicubic upsample and the *centre* tile is kept.
    A bicubic kernel reaches two source pixels, so every destination pixel in the centre
    tile is interpolated from real periodic neighbours rather than from a clamped edge —
    which is what makes the result wrap exactly rather than nearly.
    """
    cells_x = max(3, cells_x)
    cells_y = max(3, cells_y)
    lattice = Image.new("L", (cells_x, cells_y))
    lattice.putdata([_randbyte(rng) for _ in range(cells_x * cells_y)])
    tiled = Image.new("L", (cells_x * 3, cells_y * 3))
    for row in range(3):
        for col in range(3):
            tiled.paste(lattice, (col * cells_x, row * cells_y))
    big = tiled.resize((size * 3, size * 3), Image.Resampling.BICUBIC)
    return big.crop((size, size, size * 2, size * 2))


def _fbm(
    rng: random.Random, size: int, cells: int, octaves: int, *, aspect: float = 1.0
) -> Image.Image:
    """Fractional Brownian motion: octaves of :func:`_noise` at halving amplitude.

    ``aspect`` > 1 stretches the lattice along x, which is how bedding planes in stone,
    fibre in timber and brush marks in metal get their direction.
    """
    acc: Image.Image | None = None
    total = 0.0
    for octave in range(octaves):
        step = 1 << octave
        layer = _noise(
            rng, size, max(3, _round_half_away(cells * step / aspect)), max(3, cells * step)
        )
        amplitude = 0.5**octave
        if acc is None:
            acc, total = layer, amplitude
        else:
            total += amplitude
            acc = Image.blend(acc, layer, amplitude / total)
    if acc is None:  # pragma: no cover - every call site passes octaves >= 1
        raise TextureError("fbm needs at least one octave")
    return acc


def _wrap_pad(image: Image.Image, pad: int) -> Image.Image:
    width, height = image.size
    out = Image.new(image.mode, (width + pad * 2, height + pad * 2))
    for offset_y in (-height, 0, height):
        for offset_x in (-width, 0, width):
            out.paste(image, (pad + offset_x, pad + offset_y))
    return out


def _wrap_blur(image: Image.Image, radius: float) -> Image.Image:
    """Gaussian blur that wraps.

    Pillow's blur clamps at the frame edge, which would smear the seam differently from
    the interior and break the wrap. Padding with the image's own opposite edge first
    puts the clamp out of reach. The pad is four sigma — Pillow approximates the
    Gaussian with three box passes of finite support, comfortably inside that.
    """
    if radius <= 0:
        return image
    width, height = image.size
    pad = min(max(4, _round_half_away(radius * 4) + 2), min(width, height))
    padded = _wrap_pad(image, pad).filter(ImageFilter.GaussianBlur(radius))
    return padded.crop((pad, pad, pad + width, pad + height))


def _wrapped_draw(
    image: Image.Image, paint: Callable[[ImageDraw.ImageDraw, int, int], None]
) -> None:
    """Run ``paint`` at all nine torus offsets, so a feature crossing an edge reappears.

    Painting all nine unconditionally (rather than only for features near an edge) makes
    the result an exact translation of itself by one period, which is the property the
    seam test measures. Callers MUST hand integer coordinates: Pillow truncates float
    coordinates toward zero, so a shape at x = 0.5 and its copy at x = -255.5 would
    rasterise one pixel apart and put a hairline down the seam.
    """
    draw = ImageDraw.Draw(image)
    width, height = image.size
    for offset_y in (-height, 0, height):
        for offset_x in (-width, 0, width):
            paint(draw, offset_x, offset_y)


def _levels(image: Image.Image, low: int, high: int) -> Image.Image:
    """Remap 0..255 into ``low..high``. The one lever every recipe uses for contrast."""
    span = high - low
    return image.point(
        [_clamp8(low + _round_half_away(value * span / 255)) for value in range(256)]
    )


def _contrast(image: Image.Image, gamma: float) -> Image.Image:
    """Gamma about the midpoint: > 1 pushes toward the extremes, < 1 flattens."""
    table = []
    for value in range(256):
        signed = (value - 127.5) / 127.5
        curved = math.copysign(abs(signed) ** gamma, signed)
        table.append(_clamp8(_round_half_away(127.5 + curved * 127.5)))
    return image.point(table)


def _solid(size: int, rgb: Rgb) -> Image.Image:
    return Image.new("RGB", (size, size), rgb)


def _shade(size: int, base: Rgb, mask: Image.Image, dark: float, light: float) -> Image.Image:
    """Base colour modulated between two multiples of itself by ``mask``.

    Variation as a multiple of the row's own colour, never a blend toward a fixed grey:
    Kashmir White and Absolute Black must both come out still recognisably themselves.
    """
    return Image.composite(
        _solid(size, _scale(base, light)), _solid(size, _scale(base, dark)), mask
    )


def _overlay(target: Image.Image, rgb: Rgb, mask: Image.Image) -> Image.Image:
    """Composite a flat colour into an RGB map through ``mask`` (255 = fully the colour)."""
    return Image.composite(_solid(target.size[0], rgb), target, mask)


def _overlay_grey(target: Image.Image, value: int, mask: Image.Image) -> Image.Image:
    """The single-channel twin of :func:`_overlay`, for height and roughness maps."""
    return Image.composite(Image.new("L", target.size, value), target, mask)


# ---------------------------------------------------------------------------
# Normal maps
# ---------------------------------------------------------------------------
#: OpenGL tangent-space convention: +X right, +Y **up**, +Z out of the surface. Stated
#: because a flipped green channel is invisible in a thumbnail and wrong in every render.
NORMAL_CONVENTION = "opengl"


@lru_cache(maxsize=16)
def _normal_table(strength_milli: int) -> tuple[bytes, ...]:
    """(dx, dy) byte pair -> normalised RGB triple, built once per bump strength.

    A 65 536-entry table beats 65 536 square roots per material: both gradient channels
    are already 8-bit, so their product is a small, exhaustively enumerable domain.
    ``strength`` arrives in thousandths so the cache key is an int and materials sharing
    a recipe share the table.
    """
    strength = strength_milli / 1000.0
    table: list[bytes] = []
    for dx_byte in range(256):
        # `_gradient` halves the difference to stay inside a byte; undo that here.
        gradient_x = (dx_byte - 128) * 2.0 / 255.0 * strength
        for dy_byte in range(256):
            gradient_y = (dy_byte - 128) * 2.0 / 255.0 * strength
            # Image rows run downward, so d/d(row) is the negative of d/dv.
            nx, ny = -gradient_x, gradient_y
            length = math.sqrt(nx * nx + ny * ny + 1.0)
            table.append(
                bytes(
                    (
                        _clamp8(_round_half_away((nx / length) * 127.5 + 127.5)),
                        _clamp8(_round_half_away((ny / length) * 127.5 + 127.5)),
                        _clamp8(_round_half_away((1.0 / length) * 127.5 + 127.5)),
                    )
                )
            )
    return tuple(table)


def _gradient(height: Image.Image, axis: int) -> Image.Image:
    """Central difference along ``axis``, wrapped, as a byte image with 128 = flat.

    ``ImageChops.offset`` rolls rather than clamps, so the gradient at column 0 is taken
    against column ``width - 1`` — the neighbour the tiled surface actually has. The
    scale of 2 keeps the worst case inside a byte, so a sharp grout line is not silently
    clipped flat.
    """
    step = (1, 0) if axis == 0 else (0, 1)
    ahead = ImageChops.offset(height, -step[0], -step[1])
    behind = ImageChops.offset(height, step[0], step[1])
    return ImageChops.subtract(ahead, behind, 2, 128)


def _normal_map(height: Image.Image, strength: float) -> Image.Image:
    table = _normal_table(_round_half_away(strength * 1000))
    dx_bytes = _gradient(height, 0).tobytes()
    dy_bytes = _gradient(height, 1).tobytes()
    packed = b"".join([table[(dx << 8) | dy] for dx, dy in zip(dx_bytes, dy_bytes, strict=True)])
    return Image.frombytes("RGB", height.size, packed)


# ---------------------------------------------------------------------------
# Recipe building blocks
# ---------------------------------------------------------------------------
def _bond_faces(size: int, cols: int, rows: int, stagger: bool) -> list[Box]:
    """Integer face rectangles for a stack (``stagger=False``) or running bond.

    The bond is set off the origin so the texture's wrap runs through the middle of a
    face. A joint centred on the wrap meets its own other half when the tile repeats,
    which reads as a double-width joint on one grid line out of four — the commonest way
    a hand-made tileable texture goes wrong. Half a cell does it for a stack bond; a
    *running* bond needs a quarter, because the alternate courses' half-cell stagger
    would otherwise cancel the half-cell offset and put every odd course's head joint
    back on the wrap.
    """
    cell_w = size / cols
    cell_h = size / rows
    origin_x = cell_w * (0.25 if stagger else 0.5)
    origin_y = cell_h * 0.5
    boxes: list[Box] = []
    for row in range(rows):
        top = _round_half_away(origin_y + row * cell_h)
        bottom = _round_half_away(origin_y + (row + 1) * cell_h)
        shift = cell_w / 2.0 if (stagger and row % 2) else 0.0
        for col in range(cols):
            left = _round_half_away(origin_x + col * cell_w + shift)
            right = _round_half_away(origin_x + (col + 1) * cell_w + shift)
            boxes.append((left, top, right, bottom))
    return boxes


def _bond(
    rng: random.Random, size: int, cols: int, rows: int, joint: int, *, stagger: bool, spread: int
) -> tuple[Image.Image, Image.Image]:
    """``(faces, tone)`` for a tiled or bonded surface.

    ``faces`` is 255 on a unit and 0 in the joint; ``tone`` is one flat random grey per
    unit, which is what gives a tile floor its shade-to-shade variation and a brick wall
    its liveliness. Both come off the *same* rectangle list, so the tone can never drift
    out of register with the joints.
    """
    faces = Image.new("L", (size, size), 0)
    tone = Image.new("L", (size, size), 128)
    boxes = _bond_faces(size, cols, rows, stagger)
    greys = [_randbyte(rng, 128 - spread, 128 + spread) for _ in boxes]
    inset = joint // 2

    def paint_faces(draw: ImageDraw.ImageDraw, offset_x: int, offset_y: int) -> None:
        for x0, y0, x1, y1 in boxes:
            draw.rectangle(
                (
                    x0 + offset_x + inset,
                    y0 + offset_y + inset,
                    x1 + offset_x - inset - 1,
                    y1 + offset_y - inset - 1,
                ),
                fill=255,
            )

    def paint_tone(draw: ImageDraw.ImageDraw, offset_x: int, offset_y: int) -> None:
        for (x0, y0, x1, y1), grey in zip(boxes, greys, strict=True):
            draw.rectangle(
                (x0 + offset_x, y0 + offset_y, x1 + offset_x - 1, y1 + offset_y - 1), fill=grey
            )

    _wrapped_draw(faces, paint_faces)
    _wrapped_draw(tone, paint_tone)
    return faces, tone


def _scatter(rng: random.Random, size: int, count: int, min_r: int, max_r: int) -> Image.Image:
    """Random ellipses on black — granite's feldspar, terrazzo's chips, concrete's pores."""
    mask = Image.new("L", (size, size), 0)
    chips = [
        (
            _randint(rng, 0, size - 1),
            _randint(rng, 0, size - 1),
            _randint(rng, min_r, max_r),
            _randint(rng, min_r, max_r),
        )
        for _ in range(count)
    ]

    def paint(draw: ImageDraw.ImageDraw, offset_x: int, offset_y: int) -> None:
        for cx, cy, rx, ry in chips:
            draw.ellipse(
                (
                    cx - rx + offset_x,
                    cy - ry + offset_y,
                    cx + rx + offset_x,
                    cy + ry + offset_y,
                ),
                fill=255,
            )

    _wrapped_draw(mask, paint)
    return mask


def _veins(rng: random.Random, size: int, count: int, width: int) -> Image.Image:
    """Wandering polylines — marble's veining.

    Each vein is a random walk that leaves the frame; painting it at all nine torus
    offsets makes the part that exits one edge re-enter the opposite one, so the field is
    periodic without any walk having to close on itself.
    """
    mask = Image.new("L", (size, size), 0)
    walks: list[tuple[list[tuple[int, int]], int]] = []
    for _ in range(count):
        x = rng.random() * size
        y = rng.random() * size
        heading = rng.random() * math.tau
        points = [(_round_half_away(x), _round_half_away(y))]
        for _ in range(48):
            heading += (rng.random() - 0.5) * 1.1
            x += math.cos(heading) * size / 24.0
            y += math.sin(heading) * size / 24.0
            points.append((_round_half_away(x), _round_half_away(y)))
        walks.append((points, max(1, _round_half_away(width * (0.5 + rng.random())))))

    def paint(draw: ImageDraw.ImageDraw, offset_x: int, offset_y: int) -> None:
        for points, thickness in walks:
            draw.line(
                [(px + offset_x, py + offset_y) for px, py in points],
                fill=255,
                width=thickness,
                joint="curve",
            )

    _wrapped_draw(mask, paint)
    return mask


def _planks(
    rng: random.Random, size: int, rows: int, low: int, high: int
) -> tuple[Image.Image, Image.Image]:
    """``(tone, grooves)`` for horizontal planks with staggered end joints.

    Courses sit half a plank off the origin for the same reason the bond does. Each
    course carries exactly **two** end joints, both kept inside the middle of the width:
    on a torus a course is a circle, so *n* cuts make *n* planks, and the plank between
    the second cut and the first is the one that runs across the wrap. It therefore gets
    a single tone painted as two rectangles — give the two halves independent tones (the
    obvious way to write this) and the wrap becomes a colour step down the seam with no
    groove to explain it.
    """
    tone = Image.new("L", (size, size), 128)
    grooves = Image.new("L", (size, size), 255)
    cell_h = size / rows
    courses: list[tuple[int, int, int, int, int, int]] = []
    for row in range(rows):
        top = _round_half_away(cell_h * 0.5 + row * cell_h)
        bottom = _round_half_away(cell_h * 0.5 + (row + 1) * cell_h)
        first = _randint(rng, _round_half_away(size * 0.12), _round_half_away(size * 0.40))
        second = _randint(rng, _round_half_away(size * 0.58), _round_half_away(size * 0.88))
        courses.append(
            (top, bottom, first, second, _randbyte(rng, low, high), _randbyte(rng, low, high))
        )

    def paint_tone(draw: ImageDraw.ImageDraw, offset_x: int, offset_y: int) -> None:
        for top, bottom, first, second, inner_grey, outer_grey in courses:
            y0, y1 = top + offset_y, bottom + offset_y - 1
            draw.rectangle((first + offset_x, y0, second + offset_x - 1, y1), fill=inner_grey)
            # The plank that spans the wrap: one plank, one tone, two rectangles.
            draw.rectangle((second + offset_x, y0, size + offset_x - 1, y1), fill=outer_grey)
            draw.rectangle((offset_x, y0, first + offset_x - 1, y1), fill=outer_grey)

    def paint_grooves(draw: ImageDraw.ImageDraw, offset_x: int, offset_y: int) -> None:
        for top, bottom, first, second, _inner, _outer in courses:
            y0, y1 = top + offset_y, bottom + offset_y - 1
            draw.rectangle((offset_x - 1, y0 - 1, offset_x + size, y0), fill=0)
            for end in (first, second):
                draw.rectangle((end + offset_x - 1, y0, end + offset_x, y1), fill=0)

    _wrapped_draw(tone, paint_tone)
    _wrapped_draw(grooves, paint_grooves)
    return tone, grooves


def _ring_ramp(size: int, bands: int) -> Image.Image:
    """A sawtooth along y with ``bands`` periods across the tile.

    Integer arithmetic and an integer band count make it exactly periodic: the value at
    y = size is ``(bands * 256) % 256`` = 0, the value at y = 0.
    """
    row_values = [(y * bands * 256 // size) % 256 for y in range(size)]
    ramp = Image.new("L", (size, size))
    ramp.putdata([value for value in row_values for _ in range(size)])
    return ramp


# ---------------------------------------------------------------------------
# The recipes
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class _Painted:
    """What a recipe hands back before the normal map is derived from ``height``."""

    albedo: Image.Image
    height: Image.Image
    roughness: Image.Image
    #: Bump depth for :func:`_normal_map`. Mortar wants far more relief than glass.
    strength: float


def _tile(rng: random.Random, size: int, base: Rgb) -> _Painted:
    faces, tone = _bond(rng, size, 4, 4, max(2, size // 96), stagger=False, spread=16)
    grain = _fbm(rng, size, 6, 3)
    surface = Image.blend(tone, grain, 0.35)
    joints = ImageChops.invert(faces)
    albedo = _shade(size, base, surface, 0.93, 1.07)
    albedo = _overlay(albedo, _scale(_desaturate(base, 0.7), 0.72), joints)
    height = _wrap_blur(_levels(faces, 40, 255), 1.2)
    # Fired tile is the smoothest thing in the catalogue; the grout beside it is not.
    roughness = _overlay_grey(_levels(surface, 55, 90), 205, joints)
    return _Painted(albedo, height, roughness, 2.2)


def _stone(rng: random.Random, size: int, base: Rgb) -> _Painted:
    bedding = _fbm(rng, size, 4, 4, aspect=3.5)
    blotch = _fbm(rng, size, 5, 3)
    field = Image.blend(bedding, blotch, 0.45)
    cleft = _contrast(_fbm(rng, size, 10, 3), 1.6)
    albedo = _shade(size, base, field, 0.78, 1.16)
    albedo = _overlay(albedo, _scale(base, 0.62), _levels(cleft, 0, 70))
    height = _wrap_blur(_contrast(Image.blend(field, cleft, 0.5), 1.3), 0.6)
    roughness = _levels(ImageChops.invert(field), 140, 215)
    return _Painted(albedo, height, roughness, 2.6)


def _speckle(rng: random.Random, size: int, base: Rgb) -> _Painted:
    matrix = _fbm(rng, size, 8, 3)
    light_chips = _scatter(rng, size, 220, 1, max(2, size // 70))
    dark_chips = _scatter(rng, size, 160, 1, max(2, size // 96))
    albedo = _shade(size, base, matrix, 0.94, 1.06)
    albedo = _overlay(albedo, _mix(_scale(base, 1.75), (245, 242, 234), 0.35), light_chips)
    albedo = _overlay(albedo, _scale(base, 0.45), dark_chips)
    height = _wrap_blur(
        ImageChops.lighter(_levels(matrix, 100, 150), _levels(light_chips, 0, 190)), 0.7
    )
    roughness = _overlay_grey(_levels(matrix, 45, 75), 120, light_chips)
    return _Painted(albedo, height, roughness, 1.1)


def _vein(rng: random.Random, size: int, base: Rgb) -> _Painted:
    ground = _fbm(rng, size, 3, 4)
    thick = _wrap_blur(_veins(rng, size, 5, max(1, size // 110)), size / 220.0)
    hair = _wrap_blur(_veins(rng, size, 11, 1), size / 400.0)
    albedo = _shade(size, base, ground, 0.96, 1.04)
    albedo = _overlay(albedo, _scale(_desaturate(base, 0.45), 0.66), _levels(thick, 0, 235))
    albedo = _overlay(albedo, _scale(_desaturate(base, 0.3), 0.82), _levels(hair, 0, 150))
    height = _wrap_blur(
        ImageChops.subtract(_levels(ground, 110, 150), _levels(thick, 0, 90), 1, 0), 0.8
    )
    roughness = _overlay_grey(_levels(ground, 30, 55), 95, _levels(thick, 0, 200))
    return _Painted(albedo, height, roughness, 1.4)


def _concrete(rng: random.Random, size: int, base: Rgb) -> _Painted:
    mottle = _fbm(rng, size, 3, 5)
    pores = _scatter(rng, size, 90, 1, max(2, size // 130))
    albedo = _shade(size, base, mottle, 0.88, 1.09)
    albedo = _overlay(albedo, _scale(base, 0.7), pores)
    height = _wrap_blur(
        ImageChops.subtract(_levels(mottle, 90, 170), _levels(pores, 0, 130), 1, 0), 0.5
    )
    roughness = _levels(ImageChops.invert(mottle), 175, 225)
    return _Painted(albedo, height, roughness, 1.8)


def _wood(rng: random.Random, size: int, base: Rgb) -> _Painted:
    tone, grooves = _planks(rng, size, 4, 112, 150)
    # Ring phase: a periodic sawtooth along the grain, warped by stretched noise so the
    # rings wander the way growth rings do, and offset per plank so no two planks are cut
    # from the same log. add_modulo rolls the phase over; the ring profile below is
    # periodic across that rollover, so it leaves no line where the phase wraps.
    phase = ImageChops.add_modulo(
        ImageChops.add_modulo(
            _ring_ramp(size, 8), _levels(_fbm(rng, size, 3, 4, aspect=6.0), 0, 90)
        ),
        tone,
    )
    rings = phase.point(
        [
            _clamp8(_round_half_away(255 * (0.5 - 0.5 * math.cos(v / 256.0 * math.tau)) ** 1.7))
            for v in range(256)
        ]
    )
    fibre = _fbm(rng, size, 8, 3, aspect=10.0)
    grain = Image.blend(rings, fibre, 0.28)
    albedo = _shade(size, base, ImageChops.invert(Image.blend(grain, tone, 0.35)), 0.72, 1.18)
    albedo = _overlay(albedo, _scale(base, 0.35), ImageChops.invert(grooves))
    height = _wrap_blur(
        ImageChops.multiply(_levels(ImageChops.invert(grain), 120, 255), _levels(grooves, 60, 255)),
        0.6,
    )
    roughness = _overlay_grey(_levels(grain, 85, 130), 190, ImageChops.invert(grooves))
    return _Painted(albedo, height, roughness, 1.6)


def _plaster(rng: random.Random, size: int, base: Rgb) -> _Painted:
    stipple = _fbm(rng, size, 22, 3)
    drift = _fbm(rng, size, 3, 2)
    field = Image.blend(stipple, drift, 0.4)
    albedo = _shade(size, base, field, 0.96, 1.04)
    height = _wrap_blur(_levels(stipple, 90, 175), 0.4)
    roughness = _levels(ImageChops.invert(field), 195, 235)
    return _Painted(albedo, height, roughness, 1.3)


def _brick(rng: random.Random, size: int, base: Rgb) -> _Painted:
    faces, tone = _bond(rng, size, 4, 8, max(3, size // 64), stagger=True, spread=26)
    grit = _fbm(rng, size, 14, 3)
    surface = Image.blend(tone, grit, 0.4)
    joints = ImageChops.invert(faces)
    albedo = _shade(size, base, surface, 0.74, 1.18)
    albedo = _overlay(albedo, _mix(_desaturate(base, 0.85), (222, 219, 212), 0.55), joints)
    height = _wrap_blur(
        ImageChops.multiply(_levels(faces, 25, 255), _levels(surface, 190, 255)), 1.0
    )
    roughness = _overlay_grey(_levels(surface, 195, 235), 240, joints)
    return _Painted(albedo, height, roughness, 3.0)


def _glass(rng: random.Random, size: int, base: Rgb) -> _Painted:
    # Glass is nearly featureless by definition; the payload here is the *low* roughness
    # and the near-flat normal, which is what stops the 3D view painting a window as wall.
    waviness = _fbm(rng, size, 3, 2)
    dust = _fbm(rng, size, 26, 2)
    albedo = _shade(size, base, Image.blend(waviness, dust, 0.2), 0.98, 1.03)
    # Roller-wave: float glass is drawn over rollers and keeps a shallow long-period
    # ripple. Without it the normal map quantises to a single constant value, which is a
    # map that carries nothing — ship the flat default instead and save the bytes.
    height = _wrap_blur(_levels(waviness, 96, 160), 1.5)
    roughness = _levels(dust, 12, 30)
    return _Painted(albedo, height, roughness, 2.4)


def _metal(rng: random.Random, size: int, base: Rgb) -> _Painted:
    # aspect 12 rather than the 28 a brush mark really has: past about 15 the lattice
    # collapses to its 3-cell floor across x, the x-gradient quantises to zero and the
    # normal map's red channel goes dead flat.
    brush = _fbm(rng, size, 40, 2, aspect=12.0)
    sheen = _fbm(rng, size, 3, 2, aspect=3.0)
    field = Image.blend(brush, sheen, 0.35)
    albedo = _shade(size, base, field, 0.86, 1.12)
    height = _wrap_blur(_levels(brush, 108, 148), 0.3)
    roughness = _levels(field, 60, 110)
    return _Painted(albedo, height, roughness, 1.8)


_PAINTERS: dict[str, Callable[[random.Random, int, Rgb], _Painted]] = {
    "tile": _tile,
    "stone": _stone,
    "speckle": _speckle,
    "vein": _vein,
    "concrete": _concrete,
    "wood": _wood,
    "plaster": _plaster,
    "brick": _brick,
    "glass": _glass,
    "metal": _metal,
}


# ---------------------------------------------------------------------------
# Public surface
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class TextureSet:
    """One material's PBR maps: albedo (RGB), normal (RGB), roughness (greyscale)."""

    recipe: str
    color_hex: str
    size: int
    albedo: Image.Image
    normal: Image.Image
    roughness: Image.Image

    def maps(self) -> dict[str, Image.Image]:
        return {"albedo": self.albedo, "normal": self.normal, "roughness": self.roughness}

    def to_png(self) -> dict[str, bytes]:
        """Deterministic PNG bytes per map, through the shared render encoder."""
        return {name: encode_png(image) for name, image in self.maps().items()}


def generate(recipe: str, color_hex: str, *, size: int = DEFAULT_SIZE) -> TextureSet:
    """Paint one seamless, deterministic texture set.

    ``recipe`` must be a member of :data:`RECIPES`; an unknown name raises rather than
    falling back to a default, because a material silently painted as something else is
    indistinguishable from a working one until an architect looks at the render.
    """
    painter = _PAINTERS.get(recipe)
    if painter is None:
        raise TextureError(
            "unknown texture recipe %r; known recipes are %s" % (recipe, ", ".join(RECIPES))
        )
    if size < 32 or size & (size - 1):
        raise TextureError("size must be a power of two >= 32, got %r" % (size,))
    painted = painter(_rng(recipe, color_hex.upper(), size), size, _parse_hex(color_hex))
    return TextureSet(
        recipe=recipe,
        color_hex=color_hex,
        size=size,
        albedo=painted.albedo,
        normal=_normal_map(painted.height, painted.strength),
        roughness=painted.roughness,
    )


def material_texture_set(row: Mapping[str, Any], *, size: int = DEFAULT_SIZE) -> TextureSet:
    """Paint a ``fixtures/catalog/materials.json`` row.

    This is the consumer that makes the catalogue's ``texture`` field live data rather
    than a note in a docstring: ``scripts/expand_catalog.py`` writes it, this reads it,
    and ``tests/test_textures.py`` walks every committed row through here.
    """
    recipe = row.get("texture")
    if not isinstance(recipe, str) or not recipe:
        raise TextureError(
            "material %r has no 'texture' recipe; run scripts/expand_catalog.py --write"
            % (row.get("id"),)
        )
    colour = row.get("colorHex")
    if not isinstance(colour, str):
        raise TextureError("material %r has no colorHex to paint from" % (row.get("id"),))
    return generate(recipe, colour, size=size)


def _main(argv: Sequence[str]) -> int:  # pragma: no cover - developer smoke path
    """``python -m services.render.textures [--size 256] [--out DIR]``.

    Paints every row of the committed catalogue and reports the wall time, so the
    "a whole catalogue in seconds" claim in this module's docstring stays checkable.
    """
    import argparse
    import json
    import os
    import time

    parser = argparse.ArgumentParser(description="Paint the catalogue's procedural textures.")
    parser.add_argument("--size", type=int, default=DEFAULT_SIZE)
    parser.add_argument("--out", default=None, help="write PNGs here instead of timing only")
    parser.add_argument("--limit", type=int, default=0, help="stop after N materials")
    args = parser.parse_args(list(argv))

    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    with open(os.path.join(root, "fixtures", "catalog", "materials.json"), encoding="utf-8") as fh:
        rows = json.load(fh)
    if args.limit:
        rows = rows[: args.limit]
    if args.out:
        os.makedirs(args.out, exist_ok=True)

    started = time.time()
    written = 0
    for row in rows:
        texture = material_texture_set(row, size=args.size)
        if args.out:
            for name, payload in texture.to_png().items():
                with open(os.path.join(args.out, "%s-%s.png" % (row["id"], name)), "wb") as out:
                    out.write(payload)
                written += 1
    elapsed = time.time() - started
    print(
        "%d materials x 3 maps at %dpx in %.2fs (%.0f ms each)%s"
        % (
            len(rows),
            args.size,
            elapsed,
            elapsed * 1000 / max(1, len(rows)),
            "; wrote %d PNGs" % written if written else "",
        )
    )
    return 0


__all__ = [
    "DEFAULT_SIZE",
    "NORMAL_CONVENTION",
    "RECIPES",
    "TextureError",
    "TextureSet",
    "generate",
    "material_texture_set",
]


if __name__ == "__main__":  # pragma: no cover - developer smoke path
    import sys

    sys.exit(_main(sys.argv[1:]))
