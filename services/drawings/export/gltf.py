"""Model -> glTF 2.0 / GLB. **Fully implemented, pure stdlib, runs here.**

Phase 8's export list: *"exports: vector PDF (print-true scales), DXF (layer convention,
DIMSTYLE), glTF, PNG/WhatsApp preset"*. The glTF is the bridge to the renderers an
Indian practice already pays for — Lumion, D5, Blender — so its job is to arrive with
correct dimensions, sane material groups and nothing else to clean up.

No dependency, deliberately: glTF is JSON plus a binary blob, and both are things the
standard library does. Writing it by hand rather than pulling in a glTF library means
this module *executes on the build machine*, so the geometry it produces is tested
rather than asserted — the opposite of the DXF path's situation.

DECISIONS THAT MATTER TO A RENDERER
-----------------------------------
* **Units: metres.** glTF's convention is metres, and every downstream tool assumes it.
  The model is integer millimetres, so every coordinate is divided by 1000 exactly once,
  at :func:`_vertex`, and stored as float32. This is the only place in the drawings
  package where a length stops being an integer, and it is at the boundary where the
  format demands it.
* **Axes: Y up.** glTF is Y-up, right-handed; the model is Z-up (X east, Y north, Z
  height). The mapping is ``(x, y, z)_model -> (x, z, -y)_gltf``, applied once in
  :func:`_vertex`. Getting this wrong lands the building on its side in Lumion, which is
  the single most common complaint about hand-rolled glTF exporters.
* **One mesh per element class**, not per element: walls, slabs, stairs, plinth, parapet.
  A renderer artist wants five selectable groups to assign materials to, not four hundred
  wall segments.
* **Openings are cut by span splitting, not CSG.** For each wall, the solid spans around
  its openings are extruded full height, and the openings themselves get a box below the
  sill and another above the head. That is exact for orthogonal walls and rectangular
  openings — the MVP envelope — and needs no boolean library. §8's Manifold CSG is the
  *client's* 3D synthesis; an export does not need to repeat it.

VALIDATION
----------
:func:`validate_gltf` checks the structure this module emits against the parts of the
glTF 2.0 spec that actually break importers: buffer/accessor byte bounds, 4-byte
alignment, component types, ``min``/``max`` on POSITION accessors, and GLB chunk padding.
It runs in the test suite over real output, so "the JSON structure you emit is valid" is
a check rather than a claim.
"""

from __future__ import annotations

import base64
import json
import struct
from typing import Any, Dict, List, Sequence, Tuple

__all__ = [
    "GLB_MAGIC",
    "GLTF_VERSION",
    "GltfValidationError",
    "MeshGroup",
    "build_gltf",
    "validate_gltf",
    "write_glb",
    "write_glb_bytes",
]

GLB_MAGIC = 0x46546C67  # 'glTF'
GLTF_VERSION = 2

#: glTF component type codes.
_FLOAT = 5126
_UNSIGNED_INT = 5125

#: Material colours. Deliberately neutral: a renderer artist replaces them, and a
#: guessed "brick red" is worse than a grey they were going to change anyway.
_MATERIALS: Tuple[Tuple[str, Tuple[float, float, float, float], float, float], ...] = (
    ("Wall", (0.87, 0.86, 0.83, 1.0), 0.0, 0.85),
    ("Slab", (0.78, 0.78, 0.78, 1.0), 0.0, 0.9),
    ("Stair", (0.72, 0.72, 0.70, 1.0), 0.0, 0.85),
    ("Plinth", (0.55, 0.54, 0.52, 1.0), 0.0, 0.95),
    ("Parapet", (0.90, 0.90, 0.88, 1.0), 0.0, 0.85),
)
_MATERIAL_INDEX = {name: index for index, (name, _c, _m, _r) in enumerate(_MATERIALS)}


class GltfValidationError(ValueError):
    """The glTF we produced would not load. Always a bug in this module."""


class MeshGroup:
    """Accumulates triangles for one material. Positions are model mm until flushed."""

    __slots__ = ("name", "material", "positions", "indices")

    def __init__(self, name: str, material: str) -> None:
        self.name = name
        self.material = material
        self.positions: List[Tuple[int, int, int]] = []
        self.indices: List[int] = []

    def add_triangle(
        self,
        a: Tuple[int, int, int],
        b: Tuple[int, int, int],
        c: Tuple[int, int, int],
    ) -> None:
        base = len(self.positions)
        self.positions.extend((a, b, c))
        self.indices.extend((base, base + 1, base + 2))

    def add_quad(
        self,
        a: Tuple[int, int, int],
        b: Tuple[int, int, int],
        c: Tuple[int, int, int],
        d: Tuple[int, int, int],
    ) -> None:
        self.add_triangle(a, b, c)
        self.add_triangle(a, c, d)

    def add_box(
        self,
        min_corner: Tuple[int, int, int],
        max_corner: Tuple[int, int, int],
    ) -> None:
        """An axis-aligned box, six quads, every face wound CCW **seen from outside**.

        Winding matters: a renderer with backface culling on shows an inverted box as a
        hole in the building, and it looks plausible enough in a viewport with culling off
        that it ships. The six orders below were derived by taking each face's cross
        product and checking its sign against the outward direction; every one is asserted
        in ``test_box_faces_wind_outward``, which is how the first version of this method
        — with all six faces inverted — was caught.

        The Y-up conversion in :func:`_vertex` is a rotation, not a mirror, so winding
        authored here in model space stays correct in glTF space.
        """
        x0, y0, z0 = min_corner
        x1, y1, z1 = max_corner
        if x1 <= x0 or y1 <= y0 or z1 <= z0:
            return
        # bottom, outward -Z
        self.add_quad((x0, y0, z0), (x0, y1, z0), (x1, y1, z0), (x1, y0, z0))
        # top, outward +Z
        self.add_quad((x0, y0, z1), (x1, y0, z1), (x1, y1, z1), (x0, y1, z1))
        # outward -Y
        self.add_quad((x0, y0, z0), (x1, y0, z0), (x1, y0, z1), (x0, y0, z1))
        # outward +Y
        self.add_quad((x1, y1, z0), (x0, y1, z0), (x0, y1, z1), (x1, y1, z1))
        # outward -X
        self.add_quad((x0, y0, z0), (x0, y0, z1), (x0, y1, z1), (x0, y1, z0))
        # outward +X
        self.add_quad((x1, y0, z0), (x1, y1, z0), (x1, y1, z1), (x1, y0, z1))

    def add_prism(
        self, ring: Sequence[Tuple[int, int]], z0: int, z1: int
    ) -> None:
        """Extrude a closed 2D ring between two heights (slabs, plinth, parapet)."""
        if len(ring) < 3 or z1 <= z0:
            return
        from garh_model.geometry import Pt, ensure_ccw, triangulate

        points = ensure_ccw([Pt(x, y) for x, y in ring])
        flat = [(p.x, p.y) for p in points]
        for triangle in triangulate(tuple(points)):
            a, b, c = triangle
            self.add_triangle((a.x, a.y, z1), (b.x, b.y, z1), (c.x, c.y, z1))
            self.add_triangle((c.x, c.y, z0), (b.x, b.y, z0), (a.x, a.y, z0))
        count = len(flat)
        for index in range(count):
            x0, y0 = flat[index]
            x1, y1 = flat[(index + 1) % count]
            self.add_quad((x0, y0, z0), (x1, y1, z0), (x1, y1, z1), (x0, y0, z1))

    def is_empty(self) -> bool:
        return not self.indices


def _vertex(point: Tuple[int, int, int]) -> Tuple[float, float, float]:
    """Model mm (X east, Y north, Z up) -> glTF metres (X, Y up, Z south).

    The one place millimetres become metres and Z-up becomes Y-up. Both conversions in
    one function so neither can be applied twice or forgotten.
    """
    x, y, z = point
    return (x / 1000.0, z / 1000.0, -y / 1000.0)


# ---------------------------------------------------------------------------
# Geometry extraction from the model
# ---------------------------------------------------------------------------
def _is_horizontal(wall: Any) -> bool:
    return wall.a.y == wall.b.y


def _wall_solid_and_opening_spans(house: Any, wall: Any) -> Tuple[
    List[Tuple[int, int]], List[Tuple[int, int, Any]]
]:
    """``(solid spans, [(lo, hi, opening)])`` along the wall's axis."""
    if _is_horizontal(wall):
        lo, hi = min(wall.a.x, wall.b.x), max(wall.a.x, wall.b.x)
        step = 1 if wall.b.x >= wall.a.x else -1
        origin = wall.a.x
    else:
        lo, hi = min(wall.a.y, wall.b.y), max(wall.a.y, wall.b.y)
        step = 1 if wall.b.y >= wall.a.y else -1
        origin = wall.a.y

    openings: List[Tuple[int, int, Any]] = []
    for opening in sorted(
        (o for o in house.openings if o.wall_id == wall.id), key=lambda o: o.offset_mm
    ):
        centre = origin + step * opening.offset_mm
        half = opening.width_mm // 2
        openings.append((max(lo, centre - half), min(hi, centre + half), opening))
    openings.sort(key=lambda item: item[0])

    solids: List[Tuple[int, int]] = []
    cursor = lo
    for start, end, _opening in openings:
        if start > cursor:
            solids.append((cursor, start))
        cursor = max(cursor, end)
    if cursor < hi:
        solids.append((cursor, hi))
    return (solids, openings)


def _wall_box(wall: Any, span: Tuple[int, int], z0: int, z1: int) -> Tuple[
    Tuple[int, int, int], Tuple[int, int, int]
]:
    half = wall.thickness_mm // 2
    line = wall.a.y if _is_horizontal(wall) else wall.a.x
    lo, hi = span
    if _is_horizontal(wall):
        return ((lo, line - half, z0), (hi, line + half, z1))
    return ((line - half, lo, z0), (line + half, hi, z1))


def _storey_ffl_mm(house: Any, index: int) -> int:
    ffls = house.levels.ffl_per_storey_mm
    if index < len(ffls):
        return ffls[index]
    ffl = house.levels.plinth_mm
    for storey in house.storeys[:index]:
        ffl += storey.height_mm
    return ffl


def _collect_groups(doc: Any) -> List[MeshGroup]:
    """Walls (opening-cut), slabs, stairs, plinth and parapet, as five mesh groups."""
    house = doc.house
    walls = MeshGroup("Walls", "Wall")
    slabs = MeshGroup("Slabs", "Slab")
    stairs = MeshGroup("Stairs", "Stair")
    plinth = MeshGroup("Plinth", "Plinth")
    parapet = MeshGroup("Parapet", "Parapet")

    levels = house.levels
    for index, storey in enumerate(house.storeys):
        ffl = _storey_ffl_mm(house, index)
        top = ffl + storey.height_mm
        for wall in sorted(
            (w for w in house.walls if w.storey_id == storey.id), key=lambda w: w.id
        ):
            if not (_is_horizontal(wall) or wall.a.x == wall.b.x):
                # Non-orthogonal walls are out of the MVP envelope (§5); skipping is
                # honest, and the caller's summary reports the count.
                continue
            solids, openings = _wall_solid_and_opening_spans(house, wall)
            for span in solids:
                low, high = _wall_box(wall, span, ffl, top)
                walls.add_box(low, high)
            for start, end, opening in openings:
                sill = ffl + opening.sill_mm
                head = sill + opening.height_mm
                if sill > ffl:
                    low, high = _wall_box(wall, (start, end), ffl, sill)
                    walls.add_box(low, high)
                if head < top:
                    low, high = _wall_box(wall, (start, end), head, top)
                    walls.add_box(low, high)

        for slab in sorted(
            (s for s in house.slabs if s.storey_id == storey.id), key=lambda s: s.id
        ):
            ring = [(p.x, p.y) for p in slab.polygon]
            slabs.add_prism(ring, ffl - slab.thickness_mm, ffl)

        for stair in sorted(
            (s for s in house.stairs if s.storey_id == storey.id), key=lambda s: s.id
        ):
            _add_stair_solid(stairs, stair, ffl)

    # Plinth: the ground storey's slab footprint, from ground to the ground FFL.
    if house.storeys:
        ground = house.storeys[0]
        for slab in sorted(
            (s for s in house.slabs if s.storey_id == ground.id), key=lambda s: s.id
        ):
            plinth.add_prism([(p.x, p.y) for p in slab.polygon], 0, levels.plinth_mm)

    # Parapet: a ring wall on the top storey's slab outline.
    if house.storeys and levels.parapet_mm > 0:
        top_storey = house.storeys[-1]
        roof = _storey_ffl_mm(house, len(house.storeys) - 1) + top_storey.height_mm
        for slab in sorted(
            (s for s in house.slabs if s.storey_id == top_storey.id), key=lambda s: s.id
        ):
            ring = [(p.x, p.y) for p in slab.polygon]
            _add_parapet_ring(parapet, ring, roof, roof + levels.parapet_mm, 115)

    return [group for group in (walls, slabs, stairs, plinth, parapet) if not group.is_empty()]


def _add_stair_solid(group: MeshGroup, stair: Any, ffl: int) -> None:
    """One box per tread — a real stepped solid, not a ramp.

    A ramp would be less code and would look wrong in every render: a client looking at
    a Lumion image of their house notices a staircase with no steps.
    """
    ox, oy = stair.origin.x, stair.origin.y
    width = stair.width_mm
    tread = stair.tread_mm
    riser = stair.riser_mm
    count = max(1, stair.risers_count)
    if stair.landing is not None:
        count = max(1, count // 2)
    for step in range(count):
        z0 = ffl
        z1 = ffl + riser * (step + 1)
        if stair.direction == "N":
            low = (ox, oy + step * tread, z0)
            high = (ox + width, oy + (step + 1) * tread, z1)
        elif stair.direction == "S":
            low = (ox, oy - (step + 1) * tread, z0)
            high = (ox + width, oy - step * tread, z1)
        elif stair.direction == "E":
            low = (ox + step * tread, oy, z0)
            high = (ox + (step + 1) * tread, oy + width, z1)
        else:
            low = (ox - (step + 1) * tread, oy, z0)
            high = (ox - step * tread, oy + width, z1)
        group.add_box(low, high)


def _add_parapet_ring(
    group: MeshGroup, ring: Sequence[Tuple[int, int]], z0: int, z1: int, thickness_mm: int
) -> None:
    """A parapet as one box per ring edge. Corners double up, which is invisible."""
    count = len(ring)
    half = max(1, thickness_mm // 2)
    for index in range(count):
        x0, y0 = ring[index]
        x1, y1 = ring[(index + 1) % count]
        if y0 == y1:
            lo = (min(x0, x1), y0 - half, z0)
            hi = (max(x0, x1), y0 + half, z1)
        elif x0 == x1:
            lo = (x0 - half, min(y0, y1), z0)
            hi = (x0 + half, max(y0, y1), z1)
        else:
            continue
        group.add_box(lo, hi)


# ---------------------------------------------------------------------------
# glTF assembly
# ---------------------------------------------------------------------------
def _pad4(data: bytearray, filler: int = 0) -> None:
    while len(data) % 4 != 0:
        data.append(filler)


def build_gltf(
    doc: Any, *, name: str = "garh-model", embed_buffer: bool = False
) -> Tuple[Dict[str, Any], bytes]:
    """Build the glTF JSON and its binary buffer from a folded model.

    Returns ``(gltf_dict, buffer_bytes)``. With ``embed_buffer`` the buffer is inlined
    as a base64 data URI and the returned bytes are empty — that form is a valid
    standalone ``.gltf``; the default form is what :func:`write_glb` packs into a GLB.
    """
    groups = _collect_groups(doc)
    buffer = bytearray()
    accessors: List[Dict[str, Any]] = []
    buffer_views: List[Dict[str, Any]] = []
    meshes: List[Dict[str, Any]] = []
    nodes: List[Dict[str, Any]] = []

    for group in groups:
        # -- indices ------------------------------------------------------
        index_offset = len(buffer)
        for value in group.indices:
            buffer.extend(struct.pack("<I", value))
        index_length = len(buffer) - index_offset
        _pad4(buffer)
        buffer_views.append(
            {"buffer": 0, "byteOffset": index_offset, "byteLength": index_length,
             "target": 34963}
        )
        accessors.append(
            {
                "bufferView": len(buffer_views) - 1,
                "componentType": _UNSIGNED_INT,
                "count": len(group.indices),
                "type": "SCALAR",
                "max": [max(group.indices)],
                "min": [min(group.indices)],
            }
        )
        index_accessor = len(accessors) - 1

        # -- positions ----------------------------------------------------
        position_offset = len(buffer)
        vertices = [_vertex(point) for point in group.positions]
        for vertex in vertices:
            buffer.extend(struct.pack("<fff", *vertex))
        position_length = len(buffer) - position_offset
        _pad4(buffer)
        buffer_views.append(
            {"buffer": 0, "byteOffset": position_offset, "byteLength": position_length,
             "target": 34962}
        )
        xs = [v[0] for v in vertices]
        ys = [v[1] for v in vertices]
        zs = [v[2] for v in vertices]
        accessors.append(
            {
                "bufferView": len(buffer_views) - 1,
                "componentType": _FLOAT,
                "count": len(vertices),
                "type": "VEC3",
                # min/max are REQUIRED on POSITION accessors by the spec, and importers
                # that skip frustum culling without them are the exception.
                "min": [min(xs), min(ys), min(zs)],
                "max": [max(xs), max(ys), max(zs)],
            }
        )
        position_accessor = len(accessors) - 1

        meshes.append(
            {
                "name": group.name,
                "primitives": [
                    {
                        "attributes": {"POSITION": position_accessor},
                        "indices": index_accessor,
                        "material": _MATERIAL_INDEX[group.material],
                        "mode": 4,  # TRIANGLES
                    }
                ],
            }
        )
        nodes.append({"name": group.name, "mesh": len(meshes) - 1})

    materials = [
        {
            "name": material_name,
            "pbrMetallicRoughness": {
                "baseColorFactor": list(colour),
                "metallicFactor": metallic,
                "roughnessFactor": roughness,
            },
            "doubleSided": False,
        }
        for material_name, colour, metallic, roughness in _MATERIALS
    ]

    gltf: Dict[str, Any] = {
        # No generator version string and no timestamp: this file is byte-compared in
        # the same spirit as the SVG goldens.
        "asset": {"version": "2.0", "generator": "garh-drawings"},
        "scene": 0,
        "scenes": [{"name": name, "nodes": list(range(len(nodes)))}],
        "nodes": nodes,
        "meshes": meshes,
        "materials": materials,
        "accessors": accessors,
        "bufferViews": buffer_views,
        "buffers": [{"byteLength": len(buffer)}],
    }
    if embed_buffer:
        gltf["buffers"] = [
            {
                "byteLength": len(buffer),
                "uri": "data:application/octet-stream;base64,"
                + base64.b64encode(bytes(buffer)).decode("ascii"),
            }
        ]
        return (gltf, b"")
    return (gltf, bytes(buffer))


def validate_gltf(gltf: Dict[str, Any], buffer: bytes) -> None:
    """Check the parts of glTF 2.0 that break importers. Raises on the first problem.

    Not a full spec validator — a full validator is a dependency. These are the checks
    that catch the mistakes a hand-written exporter actually makes, and they run over
    real output in :mod:`services.drawings.tests.test_export`.
    """
    if gltf.get("asset", {}).get("version") != "2.0":
        raise GltfValidationError("asset.version must be exactly '2.0'")
    for key in ("scenes", "nodes", "meshes", "accessors", "bufferViews", "buffers"):
        if key not in gltf:
            raise GltfValidationError("missing required top-level array %r" % key)
    if len(gltf["buffers"]) != 1:
        raise GltfValidationError("this exporter emits exactly one buffer")

    declared = int(gltf["buffers"][0]["byteLength"])
    has_uri = "uri" in gltf["buffers"][0]
    if not has_uri and declared != len(buffer):
        raise GltfValidationError(
            "buffer.byteLength is %d but the binary chunk is %d bytes"
            % (declared, len(buffer))
        )

    for index, view in enumerate(gltf["bufferViews"]):
        offset = int(view["byteOffset"])
        length = int(view["byteLength"])
        if view["buffer"] != 0:
            raise GltfValidationError("bufferViews[%d].buffer must be 0" % index)
        if offset % 4 != 0:
            raise GltfValidationError(
                "bufferViews[%d].byteOffset is %d, which is not 4-byte aligned; "
                "importers reject unaligned float and uint32 views" % (index, offset)
            )
        if offset + length > declared:
            raise GltfValidationError(
                "bufferViews[%d] runs to %d, past the %d-byte buffer"
                % (index, offset + length, declared)
            )

    for index, accessor in enumerate(gltf["accessors"]):
        component = accessor["componentType"]
        kind = accessor["type"]
        size = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4}.get(kind)
        if size is None:
            raise GltfValidationError("accessors[%d].type %r is not supported" % (index, kind))
        component_bytes = {_FLOAT: 4, _UNSIGNED_INT: 4}.get(component)
        if component_bytes is None:
            raise GltfValidationError(
                "accessors[%d].componentType %r is not one this exporter writes"
                % (index, component)
            )
        needed = size * component_bytes * int(accessor["count"])
        view = gltf["bufferViews"][accessor["bufferView"]]
        if needed > int(view["byteLength"]):
            raise GltfValidationError(
                "accessors[%d] needs %d bytes but its bufferView holds %d"
                % (index, needed, int(view["byteLength"]))
            )
        if len(accessor.get("min", ())) != size or len(accessor.get("max", ())) != size:
            raise GltfValidationError(
                "accessors[%d] must carry min/max of %d components" % (index, size)
            )

    for index, mesh in enumerate(gltf["meshes"]):
        for primitive_index, primitive in enumerate(mesh["primitives"]):
            if "POSITION" not in primitive["attributes"]:
                raise GltfValidationError(
                    "meshes[%d].primitives[%d] has no POSITION attribute"
                    % (index, primitive_index)
                )
            if primitive.get("mode", 4) != 4:
                raise GltfValidationError(
                    "meshes[%d].primitives[%d] must be TRIANGLES (mode 4)"
                    % (index, primitive_index)
                )
            indices = gltf["accessors"][primitive["indices"]]
            if int(indices["count"]) % 3 != 0:
                raise GltfValidationError(
                    "meshes[%d].primitives[%d] index count %d is not a multiple of 3"
                    % (index, primitive_index, int(indices["count"]))
                )
            material = primitive.get("material")
            if material is not None and material >= len(gltf.get("materials", [])):
                raise GltfValidationError(
                    "meshes[%d].primitives[%d] references material %d, which does not "
                    "exist" % (index, primitive_index, material)
                )

    scene = gltf["scenes"][int(gltf.get("scene", 0))]
    for node_index in scene["nodes"]:
        if node_index >= len(gltf["nodes"]):
            raise GltfValidationError("scene references node %d, which does not exist"
                                      % node_index)


def write_glb_bytes(doc: Any, *, name: str = "garh-model") -> bytes:
    """A complete binary GLB. This is what the ``gltf`` export kind uploads (``.glb``).

    GLB rather than ``.gltf`` + ``.bin`` because ``garh_api.routers.jobs`` maps the
    ``gltf`` export kind to ``model/gltf-binary`` and a ``.glb`` extension: one file the
    architect can drag into Lumion, with no sidecar to lose.
    """
    gltf, buffer = build_gltf(doc, name=name)
    validate_gltf(gltf, buffer)

    json_chunk = bytearray(
        json.dumps(gltf, separators=(",", ":"), sort_keys=True, ensure_ascii=True).encode(
            "utf-8"
        )
    )
    # The spec pads the JSON chunk with SPACES and the BIN chunk with ZEROS. Padding
    # JSON with zeros makes strict parsers choke on a trailing NUL.
    _pad4(json_chunk, 0x20)
    binary_chunk = bytearray(buffer)
    _pad4(binary_chunk, 0x00)

    total = 12 + 8 + len(json_chunk) + (8 + len(binary_chunk) if binary_chunk else 0)
    out = bytearray()
    out.extend(struct.pack("<III", GLB_MAGIC, GLTF_VERSION, total))
    out.extend(struct.pack("<II", len(json_chunk), 0x4E4F534A))  # 'JSON'
    out.extend(json_chunk)
    if binary_chunk:
        out.extend(struct.pack("<II", len(binary_chunk), 0x004E4942))  # 'BIN\0'
        out.extend(binary_chunk)
    return bytes(out)


def write_glb(doc: Any, path: str, *, name: str = "garh-model") -> Dict[str, Any]:
    """Write a GLB to ``path`` and return a summary for the job result."""
    data = write_glb_bytes(doc, name=name)
    with open(path, "wb") as stream:
        stream.write(data)
    gltf, buffer = build_gltf(doc, name=name)
    return {
        "path": path,
        "bytes": len(data),
        "meshes": [mesh["name"] for mesh in gltf["meshes"]],
        "triangles": sum(
            int(gltf["accessors"][p["indices"]]["count"]) // 3
            for mesh in gltf["meshes"]
            for p in mesh["primitives"]
        ),
        "bufferBytes": len(buffer),
    }
