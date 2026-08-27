"""The tracing underlay, end to end (task #26: Rayon's "import a plan and trace").

Three layers, like ``test_dxf_import.py``:

* **Pure parser tests** — the stdlib PNG/JPEG dimension parsers against
  hand-built headers, positive AND negative. The negative cases are the point
  (CLAUDE.md's "a green check that cannot go red is worse than no check"): each
  one corrupts exactly the byte the parser claims to read.
* **API route tests** — need Postgres + Redis + the local S3 (moto/minio on
  ``settings.s3_endpoint_url``): upload roundtrip with a REAL PNG authored
  in-test, magic-byte rejection, the size cap, PATCH partials, replace
  semantics, DELETE, and the tenancy 404.
* The cross-tenant sweep in ``test_cross_tenant.py`` carries a Case for every
  route here — the coverage guard fails the suite otherwise.

The tiny PNG is BUILT, not embedded: ``_make_png`` writes a spec-correct file
(signature, IHDR, IDAT with real zlib data, IEND, all CRCs) so the fixture can
produce any dimensions and cannot rot into "magic bytes that stopped being a
PNG". The JPEGs are built the same way from SOI/APP0/SOF0 segments.
"""

from __future__ import annotations

import struct
import zlib

import httpx
import pytest
from garh_api.routers.underlay import jpeg_dimensions, png_dimensions, sniff_image

from tests.helpers import problem

# ---------------------------------------------------------------------------
# In-test image authors
# ---------------------------------------------------------------------------


def _png_chunk(kind: bytes, payload: bytes) -> bytes:
    return (
        struct.pack(">I", len(payload))
        + kind
        + payload
        + struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF)
    )


def _make_png(width: int, height: int) -> bytes:
    """A complete, decodable 8-bit greyscale PNG of the given dimensions."""
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 0, 0, 0, 0)
    # Each row: filter byte 0 + `width` grey pixels.
    raw = b"".join(b"\x00" + b"\x80" * width for _ in range(height))
    return (
        b"\x89PNG\r\n\x1a\n"
        + _png_chunk(b"IHDR", ihdr)
        + _png_chunk(b"IDAT", zlib.compress(raw))
        + _png_chunk(b"IEND", b"")
    )


def _jpeg_segment(marker: int, payload: bytes) -> bytes:
    return bytes([0xFF, marker]) + struct.pack(">H", len(payload) + 2) + payload


def _make_jpeg_header(width: int, height: int) -> bytes:
    """SOI + APP0 (JFIF) + DQT filler + SOF0 carrying the dimensions.

    Not a decodable photo — it stops before the entropy-coded data — but every
    byte up to and including SOF0 is laid out exactly per the spec, which is all
    the dimension parser (and the §13 sniff) reads.
    """
    app0 = _jpeg_segment(0xE0, b"JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00")
    dqt = _jpeg_segment(0xDB, b"\x00" + bytes(64))
    sof0 = _jpeg_segment(0xC0, b"\x08" + struct.pack(">HH", height, width) + b"\x01\x11\x00")
    return b"\xff\xd8" + app0 + dqt + sof0


# ---------------------------------------------------------------------------
# PNG parser — positive, and one negative per byte the parser reads
# ---------------------------------------------------------------------------


def test_png_dimensions_reads_a_real_png() -> None:
    assert png_dimensions(_make_png(320, 200)) == (320, 200)
    assert png_dimensions(_make_png(1, 1)) == (1, 1)


def test_png_dimensions_rejects_wrong_signature() -> None:
    data = bytearray(_make_png(10, 10))
    data[0] = 0x88  # not \x89
    assert png_dimensions(bytes(data)) is None


def test_png_dimensions_rejects_non_ihdr_first_chunk() -> None:
    data = bytearray(_make_png(10, 10))
    data[12:16] = b"iHDR"  # right place, wrong chunk name
    assert png_dimensions(bytes(data)) is None


def test_png_dimensions_rejects_truncated_header() -> None:
    assert png_dimensions(_make_png(10, 10)[:20]) is None
    assert png_dimensions(b"\x89PNG\r\n\x1a\n") is None
    assert png_dimensions(b"") is None


def test_png_dimensions_rejects_zero_dimensions() -> None:
    # Hand-corrupt width to 0; the CRC no longer matches but the parser must
    # already have refused on the dimension itself.
    data = bytearray(_make_png(10, 10))
    data[16:20] = struct.pack(">I", 0)
    assert png_dimensions(bytes(data)) is None


# ---------------------------------------------------------------------------
# JPEG parser — the SOF walk, positive and negative
# ---------------------------------------------------------------------------


def test_jpeg_dimensions_walks_segments_to_sof0() -> None:
    assert jpeg_dimensions(_make_jpeg_header(640, 480)) == (640, 480)
    # Different SOF variant (progressive, SOF2) must work identically.
    progressive = _make_jpeg_header(99, 44).replace(b"\xff\xc0", b"\xff\xc2")
    assert jpeg_dimensions(progressive) == (99, 44)


def test_jpeg_dimensions_skips_dht_despite_sof_like_marker() -> None:
    # A DHT (0xC4) before SOF0 sits inside the 0xC0-0xCF range; reading its
    # payload as a frame would yield garbage dimensions.
    dht = _jpeg_segment(0xC4, b"\x00" + bytes(16))
    data = (
        b"\xff\xd8"
        + dht
        + _jpeg_segment(0xC0, b"\x08" + struct.pack(">HH", 7, 9) + b"\x01\x11\x00")
    )
    assert jpeg_dimensions(data) == (9, 7)


def test_jpeg_dimensions_rejects_missing_sof() -> None:
    # A valid segment chain that ends before any SOF appears.
    assert jpeg_dimensions(b"\xff\xd8" + _jpeg_segment(0xE0, b"JFIF\x00")) is None


def test_jpeg_dimensions_rejects_truncation_and_garbage() -> None:
    good = _make_jpeg_header(640, 480)
    assert jpeg_dimensions(good[:10]) is None  # cut inside APP0
    assert jpeg_dimensions(b"\xff\xd8\xff") is None
    assert jpeg_dimensions(b"") is None
    # Lost sync: shrink APP0's declared length so the walk lands mid-payload,
    # where the next "marker" byte is JFIF text rather than 0xFF.
    corrupt = bytearray(good)
    corrupt[4:6] = struct.pack(">H", 5)  # real APP0 length is 16
    assert jpeg_dimensions(bytes(corrupt)) is None


def test_sniff_image_dispatches_on_magic_bytes_only() -> None:
    assert sniff_image(_make_png(5, 6)) == ("png", 5, 6)
    assert sniff_image(_make_jpeg_header(6, 5)) == ("jpg", 6, 5)
    assert sniff_image(b"GIF89a not welcome here") is None
    assert sniff_image(b"%PDF-1.7") is None
    assert sniff_image(b"") is None


# ---------------------------------------------------------------------------
# The API surface (Postgres + Redis + local object storage)
# ---------------------------------------------------------------------------


def _url(api: str, project_id: object, suffix: str = "") -> str:
    return "%s/projects/%s/underlay%s" % (api, project_id, suffix)


@pytest.mark.integration
async def test_upload_roundtrip_with_a_real_png(
    client, api, firm_a, project_a, settings, clean_redis
) -> None:
    """Raw-body upload → record; GET → same record; the presigned URL serves the bytes."""
    png = _make_png(320, 200)
    response = await client.post(
        _url(api, project_a.id, "/image"),
        headers={**firm_a.headers, "content-type": "image/png"},
        content=png,
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["widthPx"] == 320
    assert body["heightPx"] == 200
    assert body["mmPerPx"] == 1.0
    assert body["originXMm"] == 0 and body["originYMm"] == 0
    assert body["opacity"] == 0.5
    assert body["locked"] is False and body["visible"] is True
    assert body["objectKey"].startswith("underlays/%s/%s/" % (firm_a.firm_id, project_a.id))
    assert body["imageUrl"].startswith(settings.s3_endpoint_url)

    fetched = await client.get(_url(api, project_a.id), headers=firm_a.headers)
    assert fetched.status_code == 200, fetched.text
    assert fetched.json()["objectKey"] == body["objectKey"]
    # A fresh presigned URL is minted per response, never stored (§13).
    assert "X-Amz-Signature" in fetched.json()["imageUrl"]

    # The URL actually serves the bytes we uploaded — the storage write is real.
    async with httpx.AsyncClient(timeout=10.0) as storage:
        stored = await storage.get(fetched.json()["imageUrl"])
    assert stored.status_code == 200, stored.text[:200]
    assert stored.content == png


@pytest.mark.integration
async def test_upload_multipart_works_too(client, api, firm_a, project_a, clean_redis) -> None:
    png = _make_png(64, 32)
    response = await client.post(
        _url(api, project_a.id, "/image"),
        headers=firm_a.headers,
        files={"file": ("plan.png", png, "image/png")},
    )
    assert response.status_code == 200, response.text
    assert response.json()["widthPx"] == 64
    assert response.json()["heightPx"] == 32


@pytest.mark.integration
async def test_upload_jpeg_by_magic_bytes_despite_lying_content_type(
    client, api, firm_a, project_a, clean_redis
) -> None:
    """The declared type is a claim: JPEG bytes sent as image/png still parse as JPEG."""
    response = await client.post(
        _url(api, project_a.id, "/image"),
        headers={**firm_a.headers, "content-type": "image/png"},
        content=_make_jpeg_header(800, 600),
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert (body["widthPx"], body["heightPx"]) == (800, 600)
    assert body["objectKey"].endswith(".jpg")


@pytest.mark.integration
async def test_upload_rejects_fake_image_415(client, api, firm_a, project_a, clean_redis) -> None:
    response = await client.post(
        _url(api, project_a.id, "/image"),
        headers={**firm_a.headers, "content-type": "image/png"},
        content=b"GIF89a pretending, badly",
    )
    assert response.status_code == 415
    assert problem(response)["code"] == "unsupported_media_type"

    # A PNG signature with a corrupt IHDR is also refused — sniffing the first
    # eight bytes alone would have accepted it.
    corrupt = bytearray(_make_png(10, 10))
    corrupt[12:16] = b"JUNK"
    refused = await client.post(
        _url(api, project_a.id, "/image"),
        headers={**firm_a.headers, "content-type": "image/png"},
        content=bytes(corrupt),
    )
    assert refused.status_code == 415


@pytest.mark.integration
async def test_upload_oversize_is_413(
    client, api, firm_a, project_a, settings, clean_redis
) -> None:
    # Over BOTH the image cap and the global ceiling, so the assertion holds
    # regardless of which layer answers (the route is on the large-body allowlist,
    # so in practice it is the route's own streaming cap).
    too_big = max(settings.max_image_upload_bytes, settings.max_request_body_bytes) + 1
    body = _make_png(4, 4) + b"\x00" * too_big
    response = await client.post(
        _url(api, project_a.id, "/image"),
        headers={**firm_a.headers, "content-type": "image/png"},
        content=body,
    )
    assert response.status_code == 413
    assert problem(response)["code"] == "payload_too_large"


@pytest.mark.integration
async def test_upload_empty_body_is_400(client, api, firm_a, project_a, clean_redis) -> None:
    response = await client.post(
        _url(api, project_a.id, "/image"),
        headers={**firm_a.headers, "content-type": "image/png"},
        content=b"",
    )
    assert response.status_code == 400
    assert problem(response)["code"] == "invalid_request"


@pytest.mark.integration
async def test_get_without_underlay_is_404_no_underlay(client, api, firm_a, project_a) -> None:
    response = await client.get(_url(api, project_a.id), headers=firm_a.headers)
    assert response.status_code == 404
    body = problem(response)
    assert body["code"] == "no_underlay"  # distinct from not_found — a teach state


@pytest.mark.integration
async def test_patch_is_partial(client, api, firm_a, project_a, clean_redis) -> None:
    await client.post(
        _url(api, project_a.id, "/image"),
        headers={**firm_a.headers, "content-type": "image/png"},
        content=_make_png(100, 50),
    )

    # One field: only opacity changes.
    first = await client.patch(
        _url(api, project_a.id), headers=firm_a.headers, json={"opacity": 0.25}
    )
    assert first.status_code == 200, first.text
    body = first.json()
    assert body["opacity"] == 0.25
    assert body["mmPerPx"] == 1.0 and body["originXMm"] == 0

    # Calibration + origin + flags in one PATCH; opacity keeps its new value.
    second = await client.patch(
        _url(api, project_a.id),
        headers=firm_a.headers,
        json={
            "mmPerPx": 25.4,
            "originXMm": -1200,
            "originYMm": 3400,
            "locked": True,
            "visible": False,
        },
    )
    assert second.status_code == 200, second.text
    body = second.json()
    assert body["mmPerPx"] == 25.4
    assert body["originXMm"] == -1200 and body["originYMm"] == 3400
    assert body["locked"] is True and body["visible"] is False
    assert body["opacity"] == 0.25


@pytest.mark.integration
async def test_patch_validation_and_missing_row(
    client, api, firm_a, project_a, clean_redis
) -> None:
    missing = await client.patch(
        _url(api, project_a.id), headers=firm_a.headers, json={"opacity": 0.5}
    )
    assert missing.status_code == 404
    assert problem(missing)["code"] == "no_underlay"

    await client.post(
        _url(api, project_a.id, "/image"),
        headers={**firm_a.headers, "content-type": "image/png"},
        content=_make_png(10, 10),
    )
    for bad in ({"opacity": 1.5}, {"mmPerPx": 0}, {"mmPerPx": -3}, {"originXMm": 1.5}):
        response = await client.patch(_url(api, project_a.id), headers=firm_a.headers, json=bad)
        assert response.status_code == 422, (bad, response.text)
    # An unknown field is a 422 too (extra="forbid"), not silently ignored.
    unknown = await client.patch(
        _url(api, project_a.id), headers=firm_a.headers, json={"widthPx": 9999}
    )
    assert unknown.status_code == 422


@pytest.mark.integration
async def test_replace_overwrites_the_one_row(
    client, api, firm_a, project_a, session, clean_redis
) -> None:
    """One underlay per project: same dims keep calibration, new dims reset it."""
    from garh_api.repositories import UnderlayRepository

    first = await client.post(
        _url(api, project_a.id, "/image"),
        headers={**firm_a.headers, "content-type": "image/png"},
        content=_make_png(100, 80),
    )
    first_key = first.json()["objectKey"]
    await client.patch(
        _url(api, project_a.id),
        headers=firm_a.headers,
        json={"mmPerPx": 12.5, "originXMm": 500, "originYMm": 600, "opacity": 0.8},
    )

    # Same dimensions (a cleaner scan of the same sheet): calibration survives.
    same_dims = await client.post(
        _url(api, project_a.id, "/image"),
        headers={**firm_a.headers, "content-type": "image/png"},
        content=_make_png(100, 80),
    )
    assert same_dims.status_code == 200
    body = same_dims.json()
    assert body["objectKey"] != first_key
    assert body["mmPerPx"] == 12.5 and body["originXMm"] == 500
    assert body["opacity"] == 0.8  # view state always survives a replace

    # Different dimensions (a different drawing): calibration resets, prefs stay.
    new_dims = await client.post(
        _url(api, project_a.id, "/image"),
        headers={**firm_a.headers, "content-type": "image/png"},
        content=_make_png(200, 160),
    )
    body = new_dims.json()
    assert body["mmPerPx"] == 1.0 and body["originXMm"] == 0 and body["originYMm"] == 0
    assert body["opacity"] == 0.8

    session.expire_all()
    repo = UnderlayRepository(session, firm_a.ctx())
    assert await repo.count_for_project(project_a.id) == 1, "replace must overwrite, not add"


@pytest.mark.integration
async def test_delete_removes_row_and_object(client, api, firm_a, project_a, clean_redis) -> None:
    created = await client.post(
        _url(api, project_a.id, "/image"),
        headers={**firm_a.headers, "content-type": "image/png"},
        content=_make_png(20, 20),
    )
    image_url = created.json()["imageUrl"]

    deleted = await client.delete(_url(api, project_a.id), headers=firm_a.headers)
    assert deleted.status_code == 200, deleted.text

    gone = await client.get(_url(api, project_a.id), headers=firm_a.headers)
    assert gone.status_code == 404
    assert problem(gone)["code"] == "no_underlay"

    # Best-effort delete really happened against the local store (negative-tests
    # the delete helper in the case that must be common: storage is healthy).
    async with httpx.AsyncClient(timeout=10.0) as storage:
        stored = await storage.get(image_url)
    assert stored.status_code == 404

    again = await client.delete(_url(api, project_a.id), headers=firm_a.headers)
    assert again.status_code == 404


@pytest.mark.integration
async def test_underlay_is_invisible_cross_tenant(
    client, api, firm_a, firm_b, project_a, clean_redis
) -> None:
    """Firm B, valid token, firm A's real project id: 404 not_found on every verb —
    indistinguishable from a project that does not exist (§13)."""
    created = await client.post(
        _url(api, project_a.id, "/image"),
        headers={**firm_a.headers, "content-type": "image/png"},
        content=_make_png(30, 30),
    )
    assert created.status_code == 200

    for method, kwargs in (
        ("GET", {}),
        ("PATCH", {"json": {"opacity": 0.9}}),
        ("DELETE", {}),
    ):
        response = await client.request(
            method, _url(api, project_a.id), headers=firm_b.headers, **kwargs
        )
        assert response.status_code == 404, (method, response.text)
        assert problem(response)["code"] == "not_found"

    upload = await client.post(
        _url(api, project_a.id, "/image"),
        headers={**firm_b.headers, "content-type": "image/png"},
        content=_make_png(30, 30),
    )
    assert upload.status_code == 404
    assert problem(upload)["code"] == "not_found"

    # And firm A's underlay is untouched by all of it.
    mine = await client.get(_url(api, project_a.id), headers=firm_a.headers)
    assert mine.status_code == 200
    assert mine.json()["opacity"] == 0.5


@pytest.mark.integration
async def test_upload_requires_auth(client, api, project_a) -> None:
    response = await client.post(
        _url(api, project_a.id, "/image"),
        headers={"content-type": "image/png"},
        content=_make_png(10, 10),
    )
    assert response.status_code == 401


@pytest.mark.integration
async def test_oversized_pixel_dimensions_are_refused(
    client, api, firm_a, project_a, clean_redis
) -> None:
    """A tiny FILE can claim a huge CANVAS: a 17k×17k PNG header is a GPU
    texture-allocation attack, not a big upload, so the byte cap cannot catch it."""
    from garh_api.repositories import MAX_UNDERLAY_EDGE_PX

    over = MAX_UNDERLAY_EDGE_PX + 1
    data = bytearray(_make_png(10, 10))
    data[16:20] = struct.pack(">I", over)  # lie about width in a real PNG header
    response = await client.post(
        _url(api, project_a.id, "/image"),
        headers={**firm_a.headers, "content-type": "image/png"},
        content=bytes(data),
    )
    assert response.status_code == 400
    assert problem(response)["code"] == "invalid_request"
