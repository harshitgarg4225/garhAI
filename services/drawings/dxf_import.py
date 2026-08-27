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
    """Parsed fine, but no closed polyline anywhere — nothing to offer the picker."""

    code = "dxf_no_boundary"

    def __init__(self, layer_names: list[str] | None = None) -> None:
        super().__init__(
            "We couldn't find a closed boundary in that drawing.",
            action="In your CAD software, close the plot boundary polyline "
            "(join its ends) and export again.",
            detail="no closed LWPOLYLINE/POLYLINE in modelspace",
            context={"layers": list(layer_names or [])[:20]},
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


def _extract_layers(doc: Any) -> dict[str, Any]:
    """Walk modelspace once; group closed LWPOLYLINE/POLYLINE rings by layer."""
    insunits = 0
    try:
        insunits = int(doc.header.get("$INSUNITS", 0) or 0)
    except (TypeError, ValueError):
        insunits = 0
    factor, assumed = mm_per_insunit(insunits)

    skipped = {
        "openPolylines": 0,
        "overVertexCap": 0,
        "degenerate": 0,
        "unsupported": 0,
        "polylinesOverCap": 0,
        "layersOverCap": 0,
    }
    per_layer: dict[str, list[dict[str, Any]]] = {}

    for entity in doc.modelspace():
        kind = entity.dxftype()
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
        else:
            continue

        layer = str(entity.dxf.layer or "0")
        per_layer.setdefault(layer, [])

        if len(raw_points) > MAX_VERTICES_PER_POLYLINE:
            skipped["overVertexCap"] += 1
            continue
        mm_points = [
            (round_half_away_from_zero(x * factor), round_half_away_from_zero(y * factor))
            for x, y in raw_points
        ]
        # A ring whose exported first point repeats as its last is closed in intent
        # even when the closed flag was not set — a very common CAD export.
        if not closed and not (len(mm_points) > 3 and mm_points[0] == mm_points[-1]):
            skipped["openPolylines"] += 1
            continue
        candidate = normalise_ring(mm_points)
        if candidate is None:
            skipped["degenerate"] += 1
            continue
        per_layer[layer].append(candidate)

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
            raise DxfNoBoundaryError([str(layer.get("name")) for layer in result["layers"]])
        return result
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


def candidate_count(result: dict[str, Any]) -> tuple[int, int]:
    """``(polylines, layers_with_candidates)`` for progress copy."""
    layers = [layer for layer in result.get("layers", []) if layer.get("polylines")]
    return sum(len(layer["polylines"]) for layer in layers), len(layers)


__all__ = [
    "MAX_LAYERS",
    "MAX_POLYLINES_PER_LAYER",
    "MAX_VERTICES_PER_POLYLINE",
    "MM_PER_INSUNIT",
    "SPAWN_GRACE_SECONDS",
    "DxfImportError",
    "DxfNoBoundaryError",
    "DxfParseTimeoutError",
    "DxfTooComplexError",
    "DxfUnreadableError",
    "candidate_count",
    "mm_per_insunit",
    "normalise_ring",
    "parse_dxf_bytes",
    "round_half_away_from_zero",
    "shoelace_twice_area",
]
