"""DXF boundary import — the crash-safe parse behind ``drawings.import_dxf`` (§13, F1).

The handler (``services/drawings/handler.py``) has already enforced the size cap and
the content sniff by the time bytes reach this module. What happens here is the part
§13 calls out explicitly: *"parsed in worker with 10s timeout + memory cap (malicious
DXF = crash-safe)"*.

Crash-safety boundary
---------------------

``ezdxf`` never runs in the worker process. :func:`parse_dxf_bytes` writes the upload
to a temp file and spawns a **separate interpreter** (``multiprocessing`` spawn
context) that applies an address-space rlimit, parses with ``ezdxf.recover`` (the
loader built for files from unreliable sources), and writes its result as JSON. A
hostile file can therefore do exactly three things, all survivable:

* hang — the parent kills the child at the timeout and fails the job with
  :class:`DxfParseTimeoutError`;
* eat memory — the rlimit turns that into ``MemoryError``/a dead child, reported as
  :class:`DxfTooComplexError` / :class:`DxfUnreadableError`;
* crash the interpreter — the child's exit code is not 0, reported as
  :class:`DxfUnreadableError`.

Every error here is a :class:`~services.common.errors.PermanentError`: the same file
parses the same way on every attempt, so retrying only spends the architect's patience.

Unit detection rule (documented per the playbook)
-------------------------------------------------

The ``$INSUNITS`` header names the drawing unit. Supported values and their exact
mm-per-unit factors are in :data:`MM_PER_INSUNIT`. ``0`` (unitless) and any code not
in the table fall back to **millimetres**, and the result marks ``assumed: true`` so
the UI can render the assumption as an editable chip instead of a silent guess.
Every coordinate is converted ``value * factor`` and rounded **half away from zero**
(:func:`round_half_away_from_zero`) to an integer millimetre — no float ever leaves
this module.

Output shape (the job result, published verbatim in the ``succeeded`` event)::

    {
      "layers": [
        {"name": "PLOT",
         "polylines": [
           {"points": [{"x": 0, "y": 0}, ...],   # closed ring, CCW, int mm,
                                                  # translated so bbox min == (0, 0),
                                                  # first point NOT repeated
            "closedArea": 111483648}              # mm², shoelace on the int-mm ring
         ]}
      ],
      "units": {"insunits": 4, "mmPerUnit": "1", "assumed": false},
      "skipped": {"openPolylines": 1, "overVertexCap": 0, "degenerate": 0,
                  "unsupported": 0, "polylinesOverCap": 0, "layersOverCap": 0}
    }

Rings are normalised for the client: CCW orientation (what ``plot.set_boundary``
requires), rotated to start at the lexicographically smallest ``(x, y)`` vertex so the
same drawing always produces byte-identical JSON (golden rule 10), and translated to
plot-local coordinates (bbox minimum at the origin). The client turns the chosen ring
into ``plot.set_boundary {polygon, source: "dxf"}`` — this module never touches the
op log.
"""

from __future__ import annotations

import json
import math
import multiprocessing
import os
import shutil
import tempfile
from typing import Any

from services.common.errors import PermanentError

#: Everything past these caps is dropped (and counted in ``skipped``), never fatal —
#: a survey drawing with 400 hatch outlines still yields its plot boundary.
MAX_LAYERS = 64
MAX_POLYLINES_PER_LAYER = 32
MAX_VERTICES_PER_POLYLINE = 4_096

#: Interpreter start-up + ezdxf import are not the hostile file's fault, so the child
#: gets this on top of the configured parse budget before it is declared hung.
SPAWN_GRACE_SECONDS = 5

#: Two segment ends this close (in mm, after unit conversion) are the same corner.
#: A Total-Station export writes 1 mm precision; a hand-drafted survey can miss by a
#: centimetre or two. 25 mm is the editor's fine snap — a gap wider than that is
#: reported by position, never silently bridged.
CHAIN_TOLERANCE_MM = 25

#: Chord deviation for ARC entities (a rounded plot corner), in mm. The stored ring
#: is the tessellation; 5 mm is well inside what a municipal site plan can show.
ARC_SAGITTA_MM = 5.0

#: How many open chains the result describes (largest first) for the dialog.
MAX_OPEN_CHAINS_REPORTED = 8

#: Entity types that carry no geometry a boundary could live on. Counted as
#: ``annotations`` so the skipped summary can say "40 labels" rather than "40
#: unsupported entities" — both honest, one useful.
ANNOTATION_ENTITY_TYPES = frozenset(
    {
        "TEXT",
        "MTEXT",
        "DIMENSION",
        "ARC_DIMENSION",
        "LARGE_RADIAL_DIMENSION",
        "LEADER",
        "MLEADER",
        "MULTILEADER",
        "POINT",
        "ATTDEF",
        "ATTRIB",
        "TOLERANCE",
        "VIEWPORT",
        "IMAGE",
        "WIPEOUT",
        "XLINE",
        "RAY",
        "TABLE",
        "ACAD_TABLE",
    }
)

#: ``$INSUNITS`` → exact millimetres per drawing unit. Only units that plausibly
#: appear in an Indian architectural survey are mapped; anything else assumes mm.
#: (4=mm is the value our own exporter writes — see ``services/drawings/dxf.py``.)
MM_PER_INSUNIT: dict[int, float] = {
    1: 25.4,  # inches
    2: 304.8,  # feet
    4: 1.0,  # millimetres
    5: 10.0,  # centimetres
    6: 1_000.0,  # metres
    10: 914.4,  # yards
    13: 0.001,  # microns
    14: 100.0,  # decimetres
}


# ---------------------------------------------------------------------------
# Error taxonomy — all permanent, all with user-facing copy (golden rule 9)
# ---------------------------------------------------------------------------


class DxfImportError(PermanentError):
    """Base class for every way a DXF import can fail deterministically."""

    code = "dxf_import_failed"


class DxfUnreadableError(DxfImportError):
    """ezdxf could not produce a document from the bytes (or the child died)."""

    code = "dxf_unreadable"

    def __init__(self, detail: str | None = None) -> None:
        super().__init__(
            "We couldn't read that DXF drawing.",
            action="Re-export it from your CAD software (R12 or newer) and try again.",
            detail=detail,
        )


class DxfParseTimeoutError(DxfImportError):
    """The parse blew its wall-clock budget. Same file, same hang — never retried."""

    code = "dxf_parse_timeout"

    def __init__(self, timeout_seconds: int) -> None:
        super().__init__(
            "That DXF is too complex to read in time.",
            action="Export just the plot boundary layer and upload that instead.",
            detail="child parser exceeded %ds (+%ds spawn grace)"
            % (timeout_seconds, SPAWN_GRACE_SECONDS),
        )


class DxfTooComplexError(DxfImportError):
    """The parse hit the memory cap."""

    code = "dxf_too_complex"

    def __init__(self, memory_limit_mb: int) -> None:
        super().__init__(
            "That DXF needs more memory to read than we allow.",
            action="Export just the plot boundary layer and upload that instead.",
            detail="child parser exceeded the %dMB address-space cap" % memory_limit_mb,
        )


class DxfNoBoundaryError(DxfImportError):
    """Parsed fine, but no closed ring anywhere — nothing to offer the picker."""

    code = "dxf_no_boundary"

    def __init__(self, layer_names: list[str] | None = None) -> None:
        super().__init__(
            "We couldn't find a closed boundary in that drawing.",
            action="In your CAD software, close the plot boundary polyline "
            "(join its ends) and export again.",
            detail="no closed LWPOLYLINE/POLYLINE and no closed LINE/ARC chain in modelspace",
            context={"layers": list(layer_names or [])[:20]},
        )


class DxfOpenBoundaryError(DxfImportError):
    """Parsed fine, and the lines nearly make a boundary — but the chain does not
    close. The message NAMES the gap, because "no closed boundary" would send the
    architect hunting through a survey drawing for a 340 mm hole."""

    code = "dxf_open_boundary"

    def __init__(self, chain: dict[str, Any]) -> None:
        # Positions in the DRAWING's units when the child supplied them (what the
        # architect sees in CAD), else in mm.
        start = chain.get("fromUnits") or chain.get("from") or {}
        end = chain.get("toUnits") or chain.get("to") or {}
        in_units = "fromUnits" in chain
        gap = int(chain.get("gapMm") or 0)
        segments = int(chain.get("segments") or 0)
        layer = str(chain.get("layer") or "0")
        super().__init__(
            "The boundary on layer %s is drawn as %d line%s that do not meet: "
            "a %d mm gap between (%s, %s) and (%s, %s)%s."
            % (
                layer,
                segments,
                "" if segments == 1 else "s",
                gap,
                start.get("x", 0),
                start.get("y", 0),
                end.get("x", 0),
                end.get("y", 0),
                " in the drawing's units" if in_units else " mm",
            ),
            action="Join those two ends in your CAD software (ends within %d mm are joined "
            "automatically) and export again." % CHAIN_TOLERANCE_MM,
            detail="largest open chain on %s: gap %d mm, %d segments" % (layer, gap, segments),
            context={"openChain": dict(chain)},
        )


# ---------------------------------------------------------------------------
# Pure geometry helpers (unit-tested directly; no ezdxf needed)
# ---------------------------------------------------------------------------


def round_half_away_from_zero(value: float) -> int:
    """The documented rounding rule for unit conversion: 0.5 → 1, -0.5 → -1.

    Python's ``round`` is banker's rounding (0.5 → 0), which would make two mirrored
    plots convert asymmetrically. Half-away-from-zero is what a surveyor expects.
    """
    if value >= 0:
        return int(math.floor(value + 0.5))
    return int(math.ceil(value - 0.5))


def mm_per_insunit(insunits: int) -> tuple[float, bool]:
    """``(mm_per_unit, assumed)`` for an ``$INSUNITS`` code.

    ``assumed`` is True when the code is 0/unknown and we fell back to millimetres —
    the UI shows that as an editable assumption chip (golden rule 4).
    """
    factor = MM_PER_INSUNIT.get(int(insunits))
    if factor is None:
        return 1.0, True
    return factor, False


def shoelace_twice_area(points: list[tuple[int, int]]) -> int:
    """Twice the signed area of an integer ring. Positive = counter-clockwise."""
    total = 0
    count = len(points)
    for index in range(count):
        x1, y1 = points[index]
        x2, y2 = points[(index + 1) % count]
        total += x1 * y2 - x2 * y1
    return total


def normalise_ring(points: list[tuple[int, int]]) -> dict[str, Any] | None:
    """Turn a raw closed ring into the canonical candidate dict, or ``None`` if degenerate.

    Canonical means: consecutive duplicates removed, CCW, rotated to start at the
    lexicographically smallest vertex, translated so the bounding-box minimum is the
    origin (plot-local coordinates, §3). Deterministic by construction so the same
    file always yields byte-identical results.
    """
    deduped: list[tuple[int, int]] = []
    for point in points:
        if not deduped or point != deduped[-1]:
            deduped.append(point)
    if len(deduped) > 1 and deduped[0] == deduped[-1]:
        deduped.pop()
    if len(deduped) < 3:
        return None
    twice_area = shoelace_twice_area(deduped)
    if twice_area == 0:
        return None
    if twice_area < 0:
        deduped.reverse()
        twice_area = -twice_area
    start = deduped.index(min(deduped))
    ring = deduped[start:] + deduped[:start]
    min_x = min(x for x, _ in ring)
    min_y = min(y for _, y in ring)
    return {
        "points": [{"x": x - min_x, "y": y - min_y} for x, y in ring],
        # mm². abs(shoelace)/2 rounded half up; exact for every even shoelace sum.
        "closedArea": (twice_area + 1) // 2,
    }


def _dist_sq(a: tuple[int, int], b: tuple[int, int]) -> int:
    return (a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2


def chain_segments(
    segments: list[list[tuple[int, int]]], tolerance_mm: int = CHAIN_TOLERANCE_MM
) -> tuple[list[list[tuple[int, int]]], list[dict[str, Any]]]:
    """Join open segments (LINE = 2 points, ARC = its tessellation, an open polyline)
    end-to-end into closed rings by endpoint proximity.

    Returns ``(rings, open_chains)``:

    * ``rings`` — every chain whose two free ends met within ``tolerance_mm``, as a
      point list with the closing duplicate dropped (ready for :func:`normalise_ring`).
      A joined pair of ends keeps the FIRST end's coordinates; the mm or two of
      Total-Station rounding is absorbed there rather than averaged into a new point
      that appears on no survey.
    * ``open_chains`` — every chain that did not close, largest total length first:
      ``{"gapMm", "from": {x, y}, "to": {x, y}, "segments", "lengthMm"}``. The
      gap is the straight-line distance between its two free ends — what the
      architect has to close in CAD.

    Deterministic: segments are consumed in the order given (modelspace order), and
    the nearest free end within tolerance wins, so the same file chains the same
    way every time. Pure integer arithmetic; nothing here rounds.
    """
    pool: list[list[tuple[int, int]]] = [
        [(int(x), int(y)) for x, y in seg] for seg in segments if len(seg) >= 2
    ]
    # Drop zero-length segments and consecutive duplicate points up front.
    cleaned: list[list[tuple[int, int]]] = []
    for seg in pool:
        deduped: list[tuple[int, int]] = []
        for point in seg:
            if not deduped or point != deduped[-1]:
                deduped.append(point)
        if len(deduped) >= 2:
            cleaned.append(deduped)
    pool = cleaned

    tol_sq = int(tolerance_mm) * int(tolerance_mm)
    rings: list[list[tuple[int, int]]] = []
    open_chains: list[dict[str, Any]] = []
    used = [False] * len(pool)

    def chain_length(chain: list[tuple[int, int]]) -> int:
        return sum(
            round_half_away_from_zero(math.sqrt(_dist_sq(chain[i], chain[i + 1])))
            for i in range(len(chain) - 1)
        )

    def is_closed(chain: list[tuple[int, int]]) -> bool:
        # A closed ring: head and tail within tolerance and at least 3 corners.
        return len(chain) >= 4 and _dist_sq(chain[0], chain[-1]) <= tol_sq

    def nearest_continuation(tail: tuple[int, int]) -> tuple[int, bool] | None:
        """``(index, reversed)`` of the unused segment whose nearer end is closest
        to ``tail`` within tolerance, or ``None``."""
        best: tuple[int, int, bool] | None = None  # (dist_sq, index, reversed)
        for j, seg in enumerate(pool):
            if used[j]:
                continue
            d_head = _dist_sq(tail, seg[0])
            d_tail = _dist_sq(tail, seg[-1])
            if d_head <= tol_sq and (best is None or d_head < best[0]):
                best = (d_head, j, False)
            if d_tail <= tol_sq and (best is None or d_tail < best[0]):
                best = (d_tail, j, True)
        return None if best is None else (best[1], best[2])

    for start in range(len(pool)):
        if used[start]:
            continue
        used[start] = True
        chain = list(pool[start])
        count = 1
        # Grow from the tail; when nothing continues it, turn the chain around ONCE
        # and grow from what was the head. Two passes cover every free end.
        for _pass in range(2):
            while not is_closed(chain):
                found = nearest_continuation(chain[-1])
                if found is None:
                    break
                j, rev = found
                used[j] = True
                piece = list(reversed(pool[j])) if rev else list(pool[j])
                # The shared corner keeps the chain's own coordinates.
                chain.extend(piece[1:])
                count += 1
            if is_closed(chain):
                break
            chain.reverse()
        if is_closed(chain):
            rings.append(chain[:-1])
        else:
            gap = round_half_away_from_zero(math.sqrt(_dist_sq(chain[0], chain[-1])))
            open_chains.append(
                {
                    "gapMm": gap,
                    "from": {"x": chain[0][0], "y": chain[0][1]},
                    "to": {"x": chain[-1][0], "y": chain[-1][1]},
                    "segments": count,
                    "lengthMm": chain_length(chain),
                }
            )
    open_chains.sort(key=lambda c: (-int(c["lengthMm"]), int(c["gapMm"])))
    return rings, open_chains


# ---------------------------------------------------------------------------
# The child process (the only place ezdxf runs)
# ---------------------------------------------------------------------------


def _apply_memory_cap(memory_limit_mb: int) -> None:
    """Best-effort address-space cap. On platforms without RLIMIT_AS (or where it is
    advisory, e.g. macOS) the timeout remains the backstop."""
    try:
        import resource
    except ImportError:  # pragma: no cover - non-POSIX
        return
    limit = int(memory_limit_mb) * 1024 * 1024
    for name in ("RLIMIT_AS", "RLIMIT_DATA"):
        rlimit = getattr(resource, name, None)
        if rlimit is None:
            continue
        try:
            resource.setrlimit(rlimit, (limit, limit))
        except (ValueError, OSError):  # pragma: no cover - platform quirks
            continue


def _in_drawing_units(point: dict[str, Any], factor: float) -> dict[str, str]:
    """A mm point back in the file's units, as decimal STRINGS (never a float on the
    wire): ``{"x": "100.000", "y": "200.340"}`` for a metres drawing."""
    if factor <= 0 or factor == 1.0:
        return {"x": str(int(point["x"])), "y": str(int(point["y"]))}
    return {
        "x": ("%.3f" % (int(point["x"]) / factor)).rstrip("0").rstrip("."),
        "y": ("%.3f" % (int(point["y"]) / factor)).rstrip("0").rstrip("."),
    }


def _arc_points(entity: Any, factor: float) -> list[tuple[float, float]]:
    """Tessellate an ARC to ``ARC_SAGITTA_MM`` chord deviation, in drawing units."""
    sagitta_units = ARC_SAGITTA_MM / factor if factor > 0 else ARC_SAGITTA_MM
    try:
        return [(float(v.x), float(v.y)) for v in entity.flattening(sagitta_units)]
    except Exception:
        # Old ezdxf without flattening(): a coarse fixed tessellation is still a ring.
        cx, cy = float(entity.dxf.center.x), float(entity.dxf.center.y)
        r = float(entity.dxf.radius)
        a0 = float(entity.dxf.start_angle)
        a1 = float(entity.dxf.end_angle)
        while a1 <= a0:
            a1 += 360.0
        steps = max(4, int((a1 - a0) / 5.0))
        return [
            (
                cx + r * math.cos(math.radians(a0 + (a1 - a0) * k / steps)),
                cy + r * math.sin(math.radians(a0 + (a1 - a0) * k / steps)),
            )
            for k in range(steps + 1)
        ]


def _extract_layers(doc: Any) -> dict[str, Any]:
    """Walk modelspace once; group closed rings by layer.

    Closed LWPOLYLINE/POLYLINE entities are rings directly. LINE and ARC entities —
    how a Total-Station export and most hand-drafted surveys draw a boundary — and
    OPEN polylines are pooled per layer and chained end-to-end by
    :func:`chain_segments`; a chain that closes is a ring like any other, one that
    does not is reported with its gap. Everything else is counted, never silently
    dropped: ``skipped.unsupported`` for geometry this reader does not follow
    (SPLINE, ELLIPSE, CIRCLE, INSERT, HATCH, 3D polylines…), ``skipped.annotations``
    for text, dimensions and the like.
    """
    insunits = 0
    try:
        insunits = int(doc.header.get("$INSUNITS", 0) or 0)
    except (TypeError, ValueError):
        insunits = 0
    factor, assumed = mm_per_insunit(insunits)

    skipped = {
        "openPolylines": 0,
        "openChains": 0,
        "overVertexCap": 0,
        "degenerate": 0,
        "unsupported": 0,
        "annotations": 0,
        "polylinesOverCap": 0,
        "layersOverCap": 0,
    }
    assembled = {"lines": 0, "arcs": 0, "openPolylines": 0, "rings": 0}
    per_layer: dict[str, list[dict[str, Any]]] = {}
    pools: dict[str, list[list[tuple[int, int]]]] = {}

    def to_mm(raw_points: list[tuple[float, float]]) -> list[tuple[int, int]]:
        return [
            (round_half_away_from_zero(x * factor), round_half_away_from_zero(y * factor))
            for x, y in raw_points
        ]

    for entity in doc.modelspace():
        kind = entity.dxftype()
        layer = str(entity.dxf.layer or "0")
        raw_points: list[tuple[float, float]]
        if kind == "LWPOLYLINE":
            raw_points = [(float(p[0]), float(p[1])) for p in entity.get_points("xy")]
            closed = bool(entity.closed)
        elif kind == "POLYLINE":
            if not entity.is_2d_polyline:
                skipped["unsupported"] += 1
                continue
            raw_points = [
                (float(v.dxf.location.x), float(v.dxf.location.y)) for v in entity.vertices
            ]
            closed = bool(entity.is_closed)
        elif kind == "LINE":
            per_layer.setdefault(layer, [])
            pools.setdefault(layer, []).append(
                to_mm(
                    [
                        (float(entity.dxf.start.x), float(entity.dxf.start.y)),
                        (float(entity.dxf.end.x), float(entity.dxf.end.y)),
                    ]
                )
            )
            assembled["lines"] += 1
            continue
        elif kind == "ARC":
            per_layer.setdefault(layer, [])
            points = _arc_points(entity, factor)
            if len(points) > MAX_VERTICES_PER_POLYLINE:
                skipped["overVertexCap"] += 1
                continue
            pools.setdefault(layer, []).append(to_mm(points))
            assembled["arcs"] += 1
            continue
        elif kind in ANNOTATION_ENTITY_TYPES:
            skipped["annotations"] += 1
            continue
        else:
            skipped["unsupported"] += 1
            continue

        per_layer.setdefault(layer, [])

        if len(raw_points) > MAX_VERTICES_PER_POLYLINE:
            skipped["overVertexCap"] += 1
            continue
        mm_points = to_mm(raw_points)
        # A ring whose exported first point repeats as its last is closed in intent
        # even when the closed flag was not set — a very common CAD export.
        if not closed and not (len(mm_points) > 3 and mm_points[0] == mm_points[-1]):
            # Not a ring on its own — but it may be one side of a boundary drawn as
            # several polylines, so it joins the layer's chain pool.
            skipped["openPolylines"] += 1
            assembled["openPolylines"] += 1
            pools.setdefault(layer, []).append(mm_points)
            continue
        candidate = normalise_ring(mm_points)
        if candidate is None:
            skipped["degenerate"] += 1
            continue
        per_layer[layer].append(candidate)

    # Chain the per-layer pools. Rings become candidates; open chains are reported.
    open_chains: list[dict[str, Any]] = []
    for layer in sorted(pools):
        rings, layer_open = chain_segments(pools[layer], CHAIN_TOLERANCE_MM)
        for ring in rings:
            if len(ring) > MAX_VERTICES_PER_POLYLINE:
                skipped["overVertexCap"] += 1
                continue
            candidate = normalise_ring(ring)
            if candidate is None:
                skipped["degenerate"] += 1
                continue
            per_layer.setdefault(layer, []).append(candidate)
            assembled["rings"] += 1
        skipped["openChains"] += len(layer_open)
        for chain in layer_open:
            # The free ends in the DRAWING's own units too, so the message can say
            # "(100.000, 200.340)" to someone looking at a drawing in metres.
            open_chains.append(
                dict(
                    chain,
                    layer=layer,
                    fromUnits=_in_drawing_units(chain["from"], factor),
                    toUnits=_in_drawing_units(chain["to"], factor),
                )
            )
    open_chains.sort(key=lambda c: (-int(c["lengthMm"]), int(c["gapMm"]), str(c["layer"])))
    del open_chains[MAX_OPEN_CHAINS_REPORTED:]

    # Every declared layer appears in the picker, even with no candidates — seeing
    # "ROADS (nothing closed here)" teaches more than the layer silently missing.
    try:
        for layer_record in doc.layers:
            per_layer.setdefault(str(layer_record.dxf.name), [])
    except Exception:
        pass

    for candidates in per_layer.values():
        candidates.sort(key=lambda c: (-int(c["closedArea"]), json.dumps(c["points"])))
        if len(candidates) > MAX_POLYLINES_PER_LAYER:
            skipped["polylinesOverCap"] += len(candidates) - MAX_POLYLINES_PER_LAYER
            del candidates[MAX_POLYLINES_PER_LAYER:]

    def layer_sort_key(name: str) -> tuple[int, int, str]:
        candidates = per_layer[name]
        largest = int(candidates[0]["closedArea"]) if candidates else 0
        return (0 if candidates else 1, -largest, name)

    ordered = sorted(per_layer, key=layer_sort_key)
    if len(ordered) > MAX_LAYERS:
        skipped["layersOverCap"] += len(ordered) - MAX_LAYERS
        ordered = ordered[:MAX_LAYERS]

    factor_text = (
        ("%f" % factor).rstrip("0").rstrip(".") if factor != int(factor) else str(int(factor))
    )
    return {
        "layers": [{"name": name, "polylines": per_layer[name]} for name in ordered],
        "units": {"insunits": insunits, "mmPerUnit": factor_text, "assumed": assumed},
        "skipped": skipped,
        # How many LINE/ARC/open-polyline entities were chained, and into how many
        # rings — so the picker can say "assembled from 5 lines and 1 arc".
        "assembled": assembled,
        # The chains that did NOT close, largest first, each with its gap and the two
        # free ends in mm — the dialog names the gap instead of saying "not closed".
        "openChains": open_chains,
    }


def _child_main(dxf_path: str, result_path: str, memory_limit_mb: int) -> None:
    """Entry point of the sandboxed parser. Must never raise — the result file is the
    whole conversation with the parent."""
    _apply_memory_cap(memory_limit_mb)
    payload: dict[str, Any]
    try:
        from ezdxf import recover  # the tolerant loader, built for hostile files

        doc, _auditor = recover.readfile(dxf_path)
        payload = {"ok": True, "result": _extract_layers(doc)}
    except MemoryError:
        payload = {"ok": False, "error": {"code": "dxf_too_complex"}}
    except BaseException as exc:
        payload = {
            "ok": False,
            "error": {
                "code": "dxf_unreadable",
                "detail": "%s: %s" % (type(exc).__name__, str(exc)[:500]),
            },
        }
    tmp_path = result_path + ".tmp"
    try:
        with open(tmp_path, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, separators=(",", ":"), ensure_ascii=False)
        os.replace(tmp_path, result_path)
    except OSError:  # pragma: no cover - parent treats a missing file as unreadable
        pass


# ---------------------------------------------------------------------------
# The worker-facing API
# ---------------------------------------------------------------------------


def parse_dxf_bytes(
    data: bytes,
    *,
    timeout_seconds: int = 10,
    memory_limit_mb: int = 512,
) -> dict[str, Any]:
    """Parse an (already size-capped, already sniffed) DXF into the layer/candidate shape.

    Blocking — the handler wraps it in ``asyncio.to_thread``. Raises a
    :class:`DxfImportError` subclass on every failure; never lets an ezdxf exception
    escape, because ezdxf never runs in this process.
    """
    workdir = tempfile.mkdtemp(prefix="garh-dxf-import-")
    try:
        dxf_path = os.path.join(workdir, "upload.dxf")
        result_path = os.path.join(workdir, "result.json")
        with open(dxf_path, "wb") as handle:
            handle.write(data)

        # spawn, not fork: a clean interpreter with no inherited Redis sockets or
        # event loop, identical on Linux (compose) and macOS (dev).
        context = multiprocessing.get_context("spawn")
        child = context.Process(
            target=_child_main,
            args=(dxf_path, result_path, int(memory_limit_mb)),
            daemon=True,
        )
        child.start()
        child.join(max(1, int(timeout_seconds)) + SPAWN_GRACE_SECONDS)
        if child.is_alive():
            child.terminate()
            child.join(2)
            if child.is_alive():  # pragma: no cover - SIGTERM ignored
                child.kill()
                child.join(2)
            raise DxfParseTimeoutError(timeout_seconds)

        if not os.path.exists(result_path):
            raise DxfUnreadableError(
                detail="child parser died without a result (exit code %r)" % child.exitcode
            )
        try:
            with open(result_path, encoding="utf-8") as handle:
                payload = json.load(handle)
        except (OSError, ValueError) as exc:
            raise DxfUnreadableError(detail="unreadable child result: %s" % exc) from exc

        if not isinstance(payload, dict) or not payload.get("ok"):
            error = payload.get("error") if isinstance(payload, dict) else None
            code = str((error or {}).get("code") or "dxf_unreadable")
            if code == "dxf_too_complex":
                raise DxfTooComplexError(memory_limit_mb)
            raise DxfUnreadableError(detail=str((error or {}).get("detail") or "no detail"))

        result = payload.get("result")
        if not isinstance(result, dict) or not isinstance(result.get("layers"), list):
            raise DxfUnreadableError(detail="child result missing layers")
        if not any(layer.get("polylines") for layer in result["layers"]):
            # Nothing closed. If the lines NEARLY made a boundary, say where it
            # fails to meet — the largest chain is the one that was meant to close.
            open_chains = result.get("openChains") or []
            if isinstance(open_chains, list) and open_chains:
                raise DxfOpenBoundaryError(dict(open_chains[0]))
            raise DxfNoBoundaryError([str(layer.get("name")) for layer in result["layers"]])
        return result
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


def candidate_count(result: dict[str, Any]) -> tuple[int, int]:
    """``(polylines, layers_with_candidates)`` for progress copy."""
    layers = [layer for layer in result.get("layers", []) if layer.get("polylines")]
    return sum(len(layer["polylines"]) for layer in layers), len(layers)


__all__ = [
    "ARC_SAGITTA_MM",
    "CHAIN_TOLERANCE_MM",
    "MAX_LAYERS",
    "MAX_OPEN_CHAINS_REPORTED",
    "MAX_POLYLINES_PER_LAYER",
    "MAX_VERTICES_PER_POLYLINE",
    "MM_PER_INSUNIT",
    "SPAWN_GRACE_SECONDS",
    "DxfImportError",
    "DxfNoBoundaryError",
    "DxfOpenBoundaryError",
    "DxfParseTimeoutError",
    "DxfTooComplexError",
    "DxfUnreadableError",
    "candidate_count",
    "chain_segments",
    "mm_per_insunit",
    "normalise_ring",
    "parse_dxf_bytes",
    "round_half_away_from_zero",
    "shoelace_twice_area",
]
