"""Phase 8 export tests: DXF structure, glTF/GLB, PNG presets, the PDF pipeline.

Two of the four export paths need a dependency this machine does not have (``ezdxf`` for
DXF, a converter binary for PDF) and one needs Pillow for its final step. This file is
therefore explicit about which assertions are real proofs and which are skips:

* **glTF/GLB** — needs nothing. Built, validated, byte-compared and geometrically checked
  end to end. This is the export path that is genuinely *proven*.
* **DXF** — the geometry never reaches ezdxf; it is decided upstream in the primitives.
  What is asserted here is the *structure* the writer will emit
  (:func:`~services.drawings.export.dxf.dxf_structure`), the block naming, and that the
  missing dependency raises with an install command rather than writing a broken file.
* **PNG** — all the sizing arithmetic is pure and fully asserted; the Pillow encode is
  skipped when Pillow is absent, loudly.
* **PDF** — the argv recipes and the failure behaviour are asserted; the conversion is
  skipped when no converter binary exists, loudly.

Runs under pytest in CI and under ``python3 services/drawings/tests/test_export.py``.
"""

from __future__ import annotations

import json
import os
import struct
import sys
from typing import Any, Dict, List, Tuple

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
for _path in (_REPO_ROOT, os.path.join(_REPO_ROOT, "apps", "api")):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from services.dev_stubs import install_worker_dep_stubs  # noqa: E402

STUBBED = install_worker_dep_stubs()

from garh_model.fold import apply_group  # noqa: E402
from garh_model.model import empty_project_doc  # noqa: E402
from garh_model.ops import Op  # noqa: E402
from services.drawings.export import EXPORTERS, EXPORT_KINDS, requirements_for  # noqa: E402
from services.drawings.export.dxf import (  # noqa: E402
    DXF_HATCH_PATTERNS,
    EZDXF_INSTALL_HINT,
    EzdxfMissing,
    block_name,
    dxf_structure,
    sheet_extent_width_mm,
    write_dxf,
)
from services.drawings.export.gltf import (  # noqa: E402
    GLB_MAGIC,
    GltfValidationError,
    MeshGroup,
    build_gltf,
    validate_gltf,
    write_glb_bytes,
)
from services.drawings.export.png import (  # noqa: E402
    MAX_PIXELS,
    PRESETS,
    dpi_for_long_edge,
    pack_plan,
    preset,
    size_for_dpi,
    text_legible_at,
)
from services.drawings.layers import LAYER_NAMES  # noqa: E402
from services.drawings.render.primitives import Dim, Hatch  # noqa: E402
from services.drawings.render.reference_sheets import build_sheet_set  # noqa: E402
from services.drawings.sheets import PAPER_SIZES, TitleBlock  # noqa: E402

INPUT_DIR = os.path.join(_REPO_ROOT, "fixtures", "sheets", "inputs")
RULEPACK_DIR = os.path.join(_REPO_ROOT, "rulepacks")
_CACHE: Dict[str, Any] = {}

SKIPS: List[str] = []


def _skip(reason: str) -> None:
    """Record a skip loudly. A silent skip is how a gate stops being a gate."""
    SKIPS.append(reason)
    print("    SKIP %s" % reason)


def _fixture(name: str = "demo-02-blr-30x40-g1") -> Dict[str, Any]:
    with open(os.path.join(INPUT_DIR, "%s.json" % name), "r", encoding="utf-8") as handle:
        return json.load(handle)


def _doc(name: str = "demo-02-blr-30x40-g1") -> Any:
    key = "doc:%s" % name
    if key not in _CACHE:
        fixture = _fixture(name)
        ops = [Op.from_json(raw) for raw in fixture["ops"]]
        _CACHE[key] = apply_group(
            empty_project_doc(fixture.get("unitsDisplay", "ft-in")), ops
        ).model
    return _CACHE[key]


def _drawings(name: str = "demo-02-blr-30x40-g1") -> Any:
    key = "drawings:%s" % name
    if key not in _CACHE:
        from garh_api.compliance import build_evaluation_context, packs_for
        from garh_rules import evaluate

        doc = _doc(name)
        document = doc.to_json()
        statement = evaluate(
            build_evaluation_context(document, packs=list(packs_for(document))),
            root=RULEPACK_DIR,
        ).areas
        _CACHE[key] = build_sheet_set(
            doc, title_block=TitleBlock(firm_name="Studio Demo"), statement=statement
        )
    return _CACHE[key]


# ===========================================================================
# export/__init__ — the kind table
# ===========================================================================
def test_export_kinds_match_the_api() -> None:
    """A kind the API accepts must be a kind this package can produce."""
    assert tuple(sorted(EXPORTERS)) == tuple(sorted(EXPORT_KINDS))
    # The extension/content-type pairs must agree with garh_api.routers.jobs, which keeps
    # a deliberate mirror rather than importing services.*.
    expected = {
        "pdf-set": ("pdf", "application/pdf"),
        "dxf": ("dxf", "application/dxf"),
        "gltf": ("glb", "model/gltf-binary"),
        "png-pack": ("zip", "application/zip"),
    }
    for kind, (extension, content_type) in expected.items():
        spec = requirements_for(kind)
        assert spec["extension"] == extension, kind
        assert spec["contentType"] == content_type, kind
    # Only glTF has no hard requirement — the reason it is the tested path.
    assert requirements_for("gltf")["requires"] is None
    for kind in ("pdf-set", "dxf", "png-pack"):
        assert requirements_for(kind)["requires"]


def test_unknown_export_kind_is_rejected() -> None:
    try:
        requirements_for("obj")
    except KeyError as exc:
        assert "export kind" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("an unknown export kind must raise")


# ===========================================================================
# DXF — structure, naming, and an honest failure
# ===========================================================================
def test_dxf_structure_accounts_for_every_primitive() -> None:
    """Nothing may be silently dropped: counts must reconcile exactly."""
    for drawing in _drawings():
        structure = dxf_structure(drawing)
        counted = sum(structure["entities"].values())
        assert counted == drawing.primitive_count(), (
            "sheet %s: %d entities for %d primitives — something is being skipped"
            % (drawing.sheet.number, counted, drawing.primitive_count())
        )
        for key in structure["entities"]:
            layer, entity = key.split("/")
            assert layer in LAYER_NAMES, layer
            assert entity in (
                "LINE", "LWPOLYLINE", "ARC", "CIRCLE", "TEXT", "HATCH", "DIMENSION"
            ), entity
        assert structure["layers"] == list(LAYER_NAMES)
        assert set(structure["layersUsed"]) <= set(LAYER_NAMES)


def test_dxf_structure_dimension_segments_match_the_chains() -> None:
    """One native DIMENSION per chain segment — that is what a CAD user can edit."""
    for drawing in _drawings():
        structure = dxf_structure(drawing)
        expected = sum(len(chain.segments) for chain in drawing.chains)
        # Dim primitives carry the chains, so the two must agree.
        from_primitives = sum(
            len(p.chain.segments)
            for group in drawing.groups
            for p in group.primitives
            if isinstance(p, Dim)
        )
        assert structure["dimensionSegments"] == from_primitives
        assert structure["dimensionSegments"] == expected, drawing.sheet.number


def test_dxf_structure_is_deterministic_and_sorted() -> None:
    first = dxf_structure(_drawings()[1])
    second = dxf_structure(_drawings()[1])
    assert first == second
    assert list(first["entities"]) == sorted(first["entities"])


def test_every_hatch_pattern_maps_to_a_dxf_pattern() -> None:
    """A hatch the renderer can emit must have a DXF pattern name, or the writer dies."""
    for drawing in _drawings():
        for group in drawing.groups:
            for primitive in group.primitives:
                if isinstance(primitive, Hatch):
                    assert primitive.pattern in DXF_HATCH_PATTERNS, primitive.pattern


def test_block_name_is_deterministic_and_dxf_safe() -> None:
    class _Sheet:
        def __init__(self, number: str) -> None:
            self.number = number

    class _Drawing:
        def __init__(self, number: str) -> None:
            self.sheet = _Sheet(number)

    assert block_name(_Drawing("A-02A"), 0) == "SHEET-A-02A"
    assert block_name(_Drawing("a-01"), 0) == "SHEET-A-01"
    # Spaces and punctuation are illegal in DXF block names.
    assert block_name(_Drawing("A 02/rev B"), 0) == "SHEET-A-02-REV-B"
    assert block_name(_Drawing(""), 4) == "SHEET-05"
    assert block_name(_Drawing("A-01"), 0) == block_name(_Drawing("A-01"), 3)
    for drawing in _drawings():
        name = block_name(drawing, 0)
        assert " " not in name and name.replace("-", "").isalnum()


def test_sheet_extent_width_is_positive_for_real_sheets() -> None:
    for drawing in _drawings():
        assert sheet_extent_width_mm(drawing) > 0, drawing.sheet.number


def test_missing_ezdxf_fails_with_the_install_command() -> None:
    """No fallback, no partial file: a clear error naming the fix.

    An export that quietly writes something DXF-shaped gets submitted to a municipality.
    """
    try:
        import ezdxf  # noqa: F401
    except ImportError:
        pass
    else:
        _skip("ezdxf IS installed — the missing-dependency path cannot be exercised here")
        return

    import tempfile

    handle, path = tempfile.mkstemp(suffix=".dxf")
    os.close(handle)
    os.unlink(path)
    try:
        write_dxf(_drawings(), path)
    except EzdxfMissing as exc:
        assert "pip install -e services/" in str(exc)
        assert str(exc) == EZDXF_INSTALL_HINT
    else:  # pragma: no cover
        raise AssertionError("write_dxf must raise EzdxfMissing when ezdxf is absent")
    assert not os.path.exists(path), "a failed DXF export must leave no file behind"


# ===========================================================================
# glTF / GLB — the fully proven path
# ===========================================================================
def test_gltf_validates_and_is_deterministic() -> None:
    doc = _doc()
    gltf, buffer = build_gltf(doc)
    validate_gltf(gltf, buffer)
    again, buffer_again = build_gltf(doc)
    assert gltf == again and buffer == buffer_again
    assert write_glb_bytes(doc) == write_glb_bytes(doc)


def test_gltf_has_one_mesh_per_element_class() -> None:
    """Five selectable groups for a renderer artist, not four hundred wall segments."""
    gltf, _buffer = build_gltf(_doc())
    names = [mesh["name"] for mesh in gltf["meshes"]]
    assert names == ["Walls", "Slabs", "Stairs", "Plinth", "Parapet"], names
    assert len(gltf["nodes"]) == len(names)
    assert gltf["scenes"][0]["nodes"] == list(range(len(names)))
    for mesh in gltf["meshes"]:
        material = gltf["materials"][mesh["primitives"][0]["material"]]
        assert material["name"] in ("Wall", "Slab", "Stair", "Plinth", "Parapet")


def test_gltf_units_are_metres_and_up_is_y() -> None:
    """mm -> m and Z-up -> Y-up, applied exactly once.

    Checked against numbers taken from the fixture's own ops, not from the exporter: the
    building sits 1000 mm off each side boundary and 3000 mm off the front, the plinth is
    600 mm, and the parapet tops out at roof + 1000 mm.
    """
    doc = _doc()
    gltf, _buffer = build_gltf(doc)
    by_name = {mesh["name"]: mesh for mesh in gltf["meshes"]}

    def bounds(name: str) -> Tuple[List[float], List[float]]:
        accessor = gltf["accessors"][
            by_name[name]["primitives"][0]["attributes"]["POSITION"]
        ]
        return (accessor["min"], accessor["max"])

    plinth_min, plinth_max = bounds("Plinth")
    # X: 1000 mm side setback -> 1.0 m. Metres, not millimetres.
    assert abs(plinth_min[0] - 1.0) < 1e-4, plinth_min
    # Y is height: the plinth runs from ground (0) to 600 mm.
    assert abs(plinth_min[1] - 0.0) < 1e-4 and abs(plinth_max[1] - 0.6) < 1e-4
    # Z is negated model Y, so the whole building is at negative Z.
    assert plinth_max[2] < 0, plinth_max

    walls_min, walls_max = bounds("Walls")
    levels = doc.house.levels
    roof = levels.ffl_per_storey_mm[-1] + doc.house.storeys[-1].height_mm
    assert abs(walls_min[1] - levels.plinth_mm / 1000.0) < 1e-4
    assert abs(walls_max[1] - roof / 1000.0) < 1e-4

    _parapet_min, parapet_max = bounds("Parapet")
    assert abs(parapet_max[1] - (roof + levels.parapet_mm) / 1000.0) < 1e-4


def test_gltf_openings_are_cut_out_of_the_walls() -> None:
    """Span splitting must actually remove geometry, not just add lintel boxes.

    Compared against the same model with its openings stripped: the opening-free version
    must use fewer triangles per wall run only if nothing is cut, so the assertion is that
    the two differ and that the cut version's wall volume is smaller.
    """
    from dataclasses import replace

    doc = _doc()
    assert doc.house.openings, "the fixture must have openings for this to mean anything"
    stripped = replace(doc, house=replace(doc.house, openings=()))

    with_openings, _b1 = build_gltf(doc)
    without, _b2 = build_gltf(stripped)

    def wall_triangles(gltf: Dict[str, Any]) -> int:
        mesh = next(m for m in gltf["meshes"] if m["name"] == "Walls")
        return int(gltf["accessors"][mesh["primitives"][0]["indices"]]["count"]) // 3

    # Each opening replaces one solid run with up to two smaller boxes plus splits the
    # run either side, so the cut model has MORE triangles and less volume.
    assert wall_triangles(with_openings) > wall_triangles(without)
    assert _wall_volume_mm3(doc) < _wall_volume_mm3(stripped)


def _wall_volume_mm3(doc: Any) -> int:
    """Sum of wall box volumes, computed from the exporter's own group output."""
    from services.drawings.export.gltf import _collect_groups

    walls = next(group for group in _collect_groups(doc) if group.name == "Walls")
    volume = 0
    for index in range(0, len(walls.positions), 36):  # 36 vertices per box
        chunk = walls.positions[index : index + 36]
        if len(chunk) < 36:
            break
        xs = [p[0] for p in chunk]
        ys = [p[1] for p in chunk]
        zs = [p[2] for p in chunk]
        volume += (max(xs) - min(xs)) * (max(ys) - min(ys)) * (max(zs) - min(zs))
    return volume


def test_box_faces_wind_outward() -> None:
    """Backface culling shows an inverted box as a hole in the building.

    Every triangle's normal must point away from the box centre. Computed in model space,
    before the Y-up conversion, because that is where the winding is authored.
    """
    group = MeshGroup("t", "Wall")
    group.add_box((0, 0, 0), (100, 200, 300))
    centre = (50, 100, 150)
    assert len(group.indices) == 36
    for index in range(0, len(group.indices), 3):
        a = group.positions[group.indices[index]]
        b = group.positions[group.indices[index + 1]]
        c = group.positions[group.indices[index + 2]]
        ux, uy, uz = (b[0] - a[0], b[1] - a[1], b[2] - a[2])
        vx, vy, vz = (c[0] - a[0], c[1] - a[1], c[2] - a[2])
        normal = (uy * vz - uz * vy, uz * vx - ux * vz, ux * vy - uy * vx)
        outward = (
            (a[0] + b[0] + c[0]) / 3.0 - centre[0],
            (a[1] + b[1] + c[1]) / 3.0 - centre[1],
            (a[2] + b[2] + c[2]) / 3.0 - centre[2],
        )
        dot = sum(n * o for n, o in zip(normal, outward))
        assert dot > 0, "triangle %d winds inward: normal %s, outward %s" % (
            index // 3, normal, outward,
        )


def test_degenerate_box_is_dropped_not_emitted() -> None:
    group = MeshGroup("t", "Wall")
    group.add_box((0, 0, 0), (0, 100, 100))     # zero width
    group.add_box((0, 0, 0), (100, 100, 0))     # zero height
    group.add_box((100, 0, 0), (0, 100, 100))   # inverted
    assert group.is_empty(), "a zero-volume box must not reach the buffer"


def test_glb_container_is_well_formed() -> None:
    data = write_glb_bytes(_doc())
    magic, version, total = struct.unpack("<III", data[:12])
    assert magic == GLB_MAGIC
    assert version == 2
    assert total == len(data), "the header's total length must match the file"
    assert len(data) % 4 == 0, "GLB must be 4-byte aligned"

    json_length, json_type = struct.unpack("<II", data[12:20])
    assert json_type == 0x4E4F534A  # 'JSON'
    assert json_length % 4 == 0
    json_bytes = data[20 : 20 + json_length]
    # The JSON chunk is padded with SPACES, never NULs — strict parsers reject a NUL.
    assert not json_bytes.endswith(b"\x00")
    parsed = json.loads(json_bytes.decode("utf-8"))
    assert parsed["asset"]["version"] == "2.0"

    offset = 20 + json_length
    bin_length, bin_type = struct.unpack("<II", data[offset : offset + 8])
    assert bin_type == 0x004E4942  # 'BIN\0'
    assert bin_length % 4 == 0
    assert offset + 8 + bin_length == len(data)


def test_validate_gltf_catches_each_structural_break() -> None:
    """The validator must fail on the mistakes a hand-written exporter actually makes."""
    import copy

    good, buffer = build_gltf(_doc())

    def expect_failure(mutate: Any, label: str) -> None:
        broken = copy.deepcopy(good)
        mutate(broken)
        try:
            validate_gltf(broken, buffer)
        except GltfValidationError:
            return
        raise AssertionError("validate_gltf passed a broken document: %s" % label)

    expect_failure(lambda g: g["asset"].__setitem__("version", "1.0"), "wrong version")
    expect_failure(lambda g: g.pop("meshes"), "missing meshes")
    expect_failure(
        lambda g: g["buffers"][0].__setitem__("byteLength", 7), "buffer length mismatch"
    )
    expect_failure(
        lambda g: g["bufferViews"][0].__setitem__("byteOffset", 3), "unaligned view"
    )
    expect_failure(
        lambda g: g["bufferViews"][0].__setitem__("byteLength", 10 ** 9), "view past buffer"
    )
    expect_failure(lambda g: g["accessors"][0].pop("min"), "accessor without min")
    expect_failure(
        lambda g: g["accessors"][0].__setitem__("count", 10 ** 7), "accessor too big"
    )
    expect_failure(
        lambda g: g["meshes"][0]["primitives"][0].__setitem__("mode", 1), "not triangles"
    )
    expect_failure(
        lambda g: g["meshes"][0]["primitives"][0]["attributes"].pop("POSITION"),
        "no POSITION",
    )
    expect_failure(
        lambda g: g["meshes"][0]["primitives"][0].__setitem__("material", 99),
        "missing material",
    )
    expect_failure(lambda g: g["scenes"][0]["nodes"].append(99), "missing node")
    # Index counts must be a multiple of 3 for TRIANGLES.
    expect_failure(
        lambda g: g["accessors"][g["meshes"][0]["primitives"][0]["indices"]].__setitem__(
            "count", 7
        ),
        "index count not a multiple of 3",
    )


def test_embedded_buffer_form_is_also_valid() -> None:
    gltf, buffer = build_gltf(_doc(), embed_buffer=True)
    assert buffer == b""
    assert gltf["buffers"][0]["uri"].startswith("data:application/octet-stream;base64,")
    validate_gltf(gltf, buffer)


# ===========================================================================
# PNG — pure sizing arithmetic
# ===========================================================================
def test_size_for_dpi_is_exact_integer_arithmetic() -> None:
    a2 = PAPER_SIZES["A2"].landscape()
    # 300 dpi on 594x420 mm: 594/25.4*300 = 7015.7 -> 7016 (half away from zero).
    width, height, dpi = size_for_dpi(a2.width_mm, a2.height_mm, preset("print"))
    assert (width, height, dpi) == (7016, 4961, 300), (width, height, dpi)
    # Repeatable, and never float.
    assert size_for_dpi(a2.width_mm, a2.height_mm, preset("print")) == (7016, 4961, 300)
    assert isinstance(width, int) and isinstance(height, int) and isinstance(dpi, int)


def test_long_edge_cap_reduces_dpi_rather_than_resampling() -> None:
    a2 = PAPER_SIZES["A2"].landscape()
    width, height, dpi = size_for_dpi(a2.width_mm, a2.height_mm, preset("whatsapp"))
    assert max(width, height) <= preset("whatsapp").max_long_edge_px
    assert dpi < preset("whatsapp").dpi, "the DPI must come down, not the pixels afterwards"
    # Aspect ratio is preserved to within a pixel.
    assert abs(width / height - a2.width_mm / a2.height_mm) < 0.01


def test_whatsapp_preset_keeps_dimension_text_readable() -> None:
    """The whole point of the preset: 2.5 mm dim text must survive the downscale."""
    a2 = PAPER_SIZES["A2"].landscape()
    _w, _h, dpi = size_for_dpi(a2.width_mm, a2.height_mm, preset("whatsapp"))
    assert text_legible_at(dpi), "WhatsApp preset produces unreadable dimension text"
    # And the thumbnail honestly reports that it does not.
    _w2, _h2, thumb_dpi = size_for_dpi(a2.width_mm, a2.height_mm, preset("thumbnail"))
    assert not text_legible_at(thumb_dpi)


def test_dpi_for_long_edge_is_the_largest_dpi_that_fits() -> None:
    """DPI is an integer, so the cap is approached from below, never hit exactly.

    Asserting "within N pixels of 1600" would be a magic tolerance. The real property is
    tighter and meaningful: the chosen DPI fits, and one more DPI would not.
    """
    a2 = PAPER_SIZES["A2"].landscape()
    target = 1600
    dpi = dpi_for_long_edge(a2.width_mm, a2.height_mm, target)

    def long_edge(at_dpi: int) -> int:
        return (max(a2.width_mm, a2.height_mm) * at_dpi * 20 + 254) // 508

    assert long_edge(dpi) <= target, (dpi, long_edge(dpi))
    assert long_edge(dpi + 1) > target, "a larger DPI would still have fitted"

    # And size_for_dpi's capping path agrees with it exactly.
    width, height, capped_dpi = size_for_dpi(a2.width_mm, a2.height_mm, preset("whatsapp"))
    assert capped_dpi == dpi
    assert max(width, height) == long_edge(dpi)


def test_pack_plan_filenames_sort_in_submission_order() -> None:
    sheets = [drawing.sheet for drawing in _drawings()]
    entries = pack_plan(sheets, preset_name="whatsapp")
    assert len(entries) == len(sheets)
    names = [entry["filename"] for entry in entries]
    assert names == sorted(names), "an unzip must list the set in submission order"
    for entry in entries:
        assert entry["filename"].endswith(".png")
        assert entry["filename"].isascii(), "non-ASCII filenames break on Windows"
        assert " " not in entry["filename"]
        assert entry["widthPx"] > 0 and entry["heightPx"] > 0
        assert entry["dpi"] > 0
    assert names[0].startswith("01-") and names[1].startswith("02-")


def test_oversized_raster_is_refused() -> None:
    from services.drawings.export.png import PngPreset

    huge = PngPreset("huge", 1200, 1_000_000)
    try:
        size_for_dpi(1189, 841, huge)  # A0 at 1200 dpi
    except ValueError as exc:
        assert str(MAX_PIXELS) in str(exc)
    else:  # pragma: no cover
        raise AssertionError("a decompression-bomb-sized raster must be refused")


def test_unknown_preset_is_rejected() -> None:
    try:
        preset("instagram")
    except KeyError as exc:
        assert "PNG preset" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("an unknown preset must raise")


def test_preset_table_is_ordered_and_complete() -> None:
    assert set(PRESETS) == {"thumbnail", "whatsapp", "review", "print"}
    for spec in PRESETS.values():
        assert spec.dpi > 0 and spec.max_long_edge_px > 0
        assert spec.description, "%s has no description" % spec.name


def test_pillow_encode_when_available() -> None:
    try:
        from PIL import Image  # noqa: F401
    except ImportError:
        _skip("Pillow is not installed — the PNG encode step cannot be exercised here")
        return
    from services.drawings.export.png import blank_sheet_image, encode

    image = blank_sheet_image(64, 48)
    data = encode(image)
    assert data.startswith(b"\x89PNG\r\n\x1a\n")
    assert encode(blank_sheet_image(64, 48)) == data, "PNG encode must be deterministic"


# ===========================================================================
# PDF — pipeline shape and honest failure
# ===========================================================================
def test_converter_report_is_honest_about_what_is_missing() -> None:
    from services.drawings.export.pdf import converter_report

    report = converter_report()
    assert set(report) == {
        "converter", "converterPath", "mergeTool", "available", "canMerge", "installHint",
    }
    if report["available"]:
        assert report["converter"] and report["converterPath"]
        assert report["installHint"] is None
    else:
        assert report["converter"] is None
        assert "rsvg-convert" in report["installHint"]
        assert "chromium" in report["installHint"]


def test_pdf_argv_recipes_honour_the_page_size() -> None:
    """Each converter must be told to use the SVG's own physical size, not a paper flag."""
    from services.drawings.export.pdf import _command

    rsvg = _command("/usr/bin/rsvg-convert", "rsvg-convert", "/tmp/a.svg", "/tmp/a.pdf")
    assert "--format=pdf" in rsvg and "--keep-aspect-ratio" in rsvg
    assert "/tmp/a.svg" == rsvg[-1]

    chromium = _command("/usr/bin/chromium", "chromium", "/tmp/a.svg", "/tmp/a.pdf")
    assert "--headless=new" in chromium
    assert "--print-to-pdf=/tmp/a.pdf" in chromium
    assert "file:///tmp/a.svg" in chromium
    # No header/footer: a submission drawing must not carry a browser's page furniture.
    assert any("no-pdf-header" in arg or "no-header" in arg for arg in chromium)

    inkscape = _command("/usr/bin/inkscape", "inkscape", "/tmp/a.svg", "/tmp/a.pdf")
    assert "--export-type=pdf" in inkscape

    try:
        _command("/x", "acrobat", "/tmp/a.svg", "/tmp/a.pdf")
    except Exception as exc:
        assert "argv recipe" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("an unknown converter must not be invoked blindly")


def test_pdf_path_either_works_or_says_what_is_missing() -> None:
    from services.drawings.export.pdf import (
        PdfToolMissing,
        converter_report,
        svg_set_to_pdf,
    )
    from services.drawings.render.svg import render_sheet_svg

    svgs = [render_sheet_svg(drawing) for drawing in _drawings()][:2]
    report = converter_report()
    if not report["available"]:
        try:
            svg_set_to_pdf(svgs, "/tmp/garh-should-not-exist.pdf")
        except PdfToolMissing as exc:
            assert "no fallback" in str(exc).lower() or "deliberately" in str(exc).lower()
            assert "rsvg-convert" in str(exc)
        else:  # pragma: no cover
            raise AssertionError("svg_set_to_pdf must refuse without a converter")
        assert not os.path.exists("/tmp/garh-should-not-exist.pdf")
        _skip("no SVG->PDF converter installed — real conversion not exercised here")
        return

    import tempfile

    handle, path = tempfile.mkstemp(suffix=".pdf")
    os.close(handle)
    try:
        result = svg_set_to_pdf(svgs, path)
        assert result["pages"] == len(svgs) or result["mergeTool"] is None
        with open(path, "rb") as stream:
            assert stream.read(5) == b"%PDF-"
        assert os.path.getsize(path) > 1000
    finally:
        if os.path.exists(path):
            os.unlink(path)


def test_empty_pdf_set_is_rejected() -> None:
    from services.drawings.export.pdf import svg_set_to_pdf

    try:
        svg_set_to_pdf([], "/tmp/nope.pdf")
    except ValueError as exc:
        assert "at least one sheet" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("an empty PDF set must raise")


# ---------------------------------------------------------------------------
if __name__ == "__main__":  # pragma: no cover
    import traceback

    failures = 0
    for _name, _fn in sorted(globals().items()):
        if _name.startswith("test_") and callable(_fn):
            try:
                _fn()
                print("PASS %s" % _name)
            except Exception:  # noqa: BLE001
                failures += 1
                print("FAIL %s" % _name)
                traceback.print_exc()
    print()
    if SKIPS:
        print("%d skip(s) — each one is an assertion nobody made:" % len(SKIPS))
        for reason in SKIPS:
            print("  - %s" % reason)
    print("%d failure(s)" % failures)
    sys.exit(1 if failures else 0)
