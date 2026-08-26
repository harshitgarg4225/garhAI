"""DXF boundary import, end to end (Phase 2, playbook F1 + §13 upload security).

Three layers, guarded independently so each runs wherever its dependencies exist:

* **Pure geometry/unit tests** — no ezdxf, no datastores. The documented conversion
  rules (``$INSUNITS`` mapping, half-away-from-zero rounding, ring normalisation) are
  asserted directly.
* **Parser tests** — need ``ezdxf`` (``pytest.importorskip``). The hand-written
  fixtures in ``fixtures/dxf/`` parse to exact integer-mm polygons; the malformed
  fixture fails with a typed :class:`DxfImportError`, never a raw exception.
* **API route tests** — need the usual Postgres/Redis fixtures. Upload happy path,
  oversize → 413, sniffed-out content → 415, cross-tenant → 404, and the result
  surfacing on ``GET /import-jobs/:id`` after the worker's ``succeeded`` event.

``services/*`` is a repo-root package that is not on ``sys.path`` when pytest runs
from ``apps/api``, so this module adds the repo root — the same relationship
``garh_api.routers.projects`` has with ``services.llm``.
"""

from __future__ import annotations

import asyncio
import base64
import json
import sys
import uuid
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

FIXTURES = REPO_ROOT / "fixtures" / "dxf"

from garh_api import queue as api_queue  # noqa: E402
from tests.helpers import problem  # noqa: E402

from services.drawings import dxf_import  # noqa: E402

#: fixture → (layer, expected CCW ring starting at the lexicographic minimum, mm²).
RECT_RING = [(0, 0), (9144, 0), (9144, 12192), (0, 12192)]
RECT_AREA = 9144 * 12192  # 111,483,648 mm²
L_FEET_RING = [(0, 0), (9144, 0), (9144, 6096), (4572, 6096), (4572, 12192), (0, 12192)]
L_FEET_AREA = 83_612_736  # 900 sq ft × 304.8² mm²


def _ring(polyline: dict) -> list[tuple[int, int]]:
    return [(p["x"], p["y"]) for p in polyline["points"]]


# ---------------------------------------------------------------------------
# Documented conversion rules (pure, always run)
# ---------------------------------------------------------------------------


def test_rounding_is_half_away_from_zero() -> None:
    cases = [(0.5, 1), (1.5, 2), (2.5, 3), (0.49, 0), (-0.5, -1), (-1.5, -2), (-0.49, 0)]
    for value, expected in cases:
        assert dxf_import.round_half_away_from_zero(value) == expected, value


def test_insunits_mapping_and_mm_fallback() -> None:
    assert dxf_import.mm_per_insunit(4) == (1.0, False)  # millimetres
    assert dxf_import.mm_per_insunit(1) == (25.4, False)  # inches
    assert dxf_import.mm_per_insunit(2) == (304.8, False)  # feet
    assert dxf_import.mm_per_insunit(6) == (1000.0, False)  # metres
    # 0 (unitless) and anything exotic fall back to mm, flagged as an assumption.
    assert dxf_import.mm_per_insunit(0) == (1.0, True)
    assert dxf_import.mm_per_insunit(11) == (1.0, True)


def test_normalise_ring_canonicalises_cw_offset_ring() -> None:
    # Clockwise, offset from the origin, with the first point repeated at the end.
    raw = [(5000, 7000), (5000, 19192), (14144, 19192), (14144, 7000), (5000, 7000)]
    ring = dxf_import.normalise_ring(raw)
    assert ring is not None
    points = _ring(ring)
    assert points[0] == (0, 0)  # plot-local: bbox min at origin
    assert set(points) == set(RECT_RING)
    assert dxf_import.shoelace_twice_area(points) > 0  # CCW, as plot.set_boundary needs
    assert ring["closedArea"] == RECT_AREA


def test_normalise_ring_rejects_degenerate() -> None:
    assert dxf_import.normalise_ring([(0, 0), (10, 0)]) is None
    assert dxf_import.normalise_ring([(0, 0), (10, 0), (20, 0)]) is None  # zero area
    assert dxf_import.normalise_ring([(0, 0), (0, 0), (0, 0), (0, 0)]) is None


# ---------------------------------------------------------------------------
# Fixture parsing (needs ezdxf; runs the real subprocess boundary)
# ---------------------------------------------------------------------------


def _parse_fixture(name: str) -> dict:
    pytest.importorskip("ezdxf")
    data = (FIXTURES / name).read_bytes()
    return dxf_import.parse_dxf_bytes(data, timeout_seconds=10, memory_limit_mb=512)


def test_parse_rect_mm_fixture() -> None:
    result = _parse_fixture("plot_rect_mm.dxf")
    layers = {layer["name"]: layer for layer in result["layers"]}

    assert "PLOT" in layers and len(layers["PLOT"]["polylines"]) == 1
    ring = layers["PLOT"]["polylines"][0]
    assert _ring(ring) == RECT_RING  # translated from its (5000, 7000) offset
    assert ring["closedArea"] == RECT_AREA

    # The open polyline on ROADS is not a candidate, but the layer still shows.
    assert "ROADS" in layers and layers["ROADS"]["polylines"] == []
    assert result["skipped"]["openPolylines"] == 1
    assert result["units"] == {"insunits": 4, "mmPerUnit": "1", "assumed": False}


def test_parse_l_shape_feet_fixture() -> None:
    result = _parse_fixture("plot_l_feet.dxf")
    layers = {layer["name"]: layer for layer in result["layers"]}
    ring = layers["BOUNDARY"]["polylines"][0]
    assert _ring(ring) == L_FEET_RING
    assert ring["closedArea"] == L_FEET_AREA
    assert result["units"]["insunits"] == 2
    assert result["units"]["mmPerUnit"] == "304.8"
    assert result["units"]["assumed"] is False


def test_parse_metres_lwpolyline_cw_is_normalised_ccw() -> None:
    result = _parse_fixture("plot_rect_metres.dxf")
    layers = {layer["name"]: layer for layer in result["layers"]}
    ring = layers["PLOT"]["polylines"][0]
    points = _ring(ring)
    assert points[0] == (0, 0)
    assert set(points) == set(RECT_RING)
    assert dxf_import.shoelace_twice_area(points) > 0  # CW in the file, CCW out
    assert ring["closedArea"] == RECT_AREA
    assert result["units"]["insunits"] == 6
    assert result["units"]["mmPerUnit"] == "1000"


def test_malformed_fixture_fails_with_typed_error() -> None:
    pytest.importorskip("ezdxf")
    data = (FIXTURES / "malformed.dxf").read_bytes()
    with pytest.raises(dxf_import.DxfImportError) as excinfo:
        dxf_import.parse_dxf_bytes(data, timeout_seconds=10, memory_limit_mb=512)
    # ezdxf.recover may either refuse the file or salvage an empty document; both
    # must surface as a typed, user-copy error — never a raw ezdxf exception.
    assert excinfo.value.code in ("dxf_unreadable", "dxf_no_boundary")
    assert excinfo.value.message
    assert excinfo.value.action


# ---------------------------------------------------------------------------
# The drawings handler (worker side, FakeRedis — no infrastructure)
# ---------------------------------------------------------------------------


def _worker_ctx(envelope):  # type: ignore[no-untyped-def]
    from services.common.blobs import BlobClient
    from services.common.checkpoint import JobCheckpoint
    from services.common.config import WorkerSettings
    from services.common.progress import NullProgressReporter
    from services.common.runtime import JobContext
    from services.common.testing import FakeRedis

    return JobContext(
        envelope=envelope,
        settings=WorkerSettings(),
        progress=NullProgressReporter(envelope),
        checkpoint=JobCheckpoint(FakeRedis(), envelope.job_id),
        blobs=BlobClient(),
        cancel_event=asyncio.Event(),
    )


def _import_envelope(assets):  # type: ignore[no-untyped-def]
    from services.common.envelope import JobEnvelope

    return JobEnvelope(
        job_id=str(uuid.uuid4()),
        kind="drawings.import_dxf",
        firm_id=str(uuid.uuid4()),
        project_id=str(uuid.uuid4()),
        assets=assets,
    )


async def test_handler_parses_inline_dxf_to_layer_candidates() -> None:
    pytest.importorskip("ezdxf")
    from services.common.envelope import BlobRef
    from services.drawings.handler import DrawingsJobHandler

    data = (FIXTURES / "plot_rect_mm.dxf").read_bytes()
    envelope = _import_envelope(
        {
            "dxf": BlobRef(
                inline_base64=base64.b64encode(data).decode("ascii"),
                content_type="application/dxf",
            )
        }
    )
    result = await DrawingsJobHandler().handle(_worker_ctx(envelope))
    layers = {layer["name"]: layer for layer in result.data["layers"]}
    assert layers["PLOT"]["polylines"][0]["closedArea"] == RECT_AREA
    assert result.message


async def test_handler_rejects_oversize_claim_before_fetching() -> None:
    from services.common.envelope import BlobRef
    from services.common.errors import InvalidJobError
    from services.drawings.handler import DrawingsJobHandler

    envelope = _import_envelope(
        {
            "dxf": BlobRef(
                inline_base64=base64.b64encode(b"0\nSECTION\n").decode("ascii"),
                size_bytes=21 * 1024 * 1024,  # the uploader's claim, over the 20MB cap
            )
        }
    )
    with pytest.raises(InvalidJobError):
        await DrawingsJobHandler().handle(_worker_ctx(envelope))


async def test_handler_sniffs_out_non_dxf_bytes() -> None:
    from services.common.envelope import BlobRef
    from services.common.errors import InvalidJobError
    from services.drawings.handler import DrawingsJobHandler

    envelope = _import_envelope(
        {
            "dxf": BlobRef(
                inline_base64=base64.b64encode(b"%PDF-1.7 definitely not a dxf").decode(
                    "ascii"
                )
            )
        }
    )
    with pytest.raises(InvalidJobError):
        await DrawingsJobHandler().handle(_worker_ctx(envelope))


# ---------------------------------------------------------------------------
# The API surface (needs Postgres + Redis, like every route test)
# ---------------------------------------------------------------------------


def _upload_files(name: str = "plot_rect_mm.dxf") -> dict:
    data = (FIXTURES / name).read_bytes()
    return {"file": (name, data, "application/dxf")}


async def test_upload_queues_job_and_envelope(
    client, api, firm_a, project_a, settings, clean_redis
) -> None:
    response = await client.post(
        "%s/projects/%s/import/dxf" % (api, project_a.id),
        headers=firm_a.headers,
        files=_upload_files(),
    )
    assert response.status_code == 202, response.text
    body = response.json()
    assert body["kind"] == "dxf-import"
    assert body["status"] == "queued"
    assert body["filename"] == "plot_rect_mm.dxf"
    assert body["sizeBytes"] == len((FIXTURES / "plot_rect_mm.dxf").read_bytes())
    assert "/export-jobs/%s/events" % body["id"] in body["eventsUrl"]
    assert body["result"] is None

    # The envelope really is on the drawings queue, small file inlined.
    raw_entries = clean_redis.lrange(settings.queue_drawings, 0, -1)
    envelopes = [json.loads(raw) for raw in raw_entries]
    match = [e for e in envelopes if e["jobId"] == body["id"]]
    assert len(match) == 1
    envelope = match[0]
    assert envelope["kind"] == "drawings.import_dxf"
    assert envelope["firmId"] == str(firm_a.firm_id)
    assert envelope["projectId"] == str(project_a.id)
    inline = envelope["assets"]["dxf"]["inlineBase64"]
    assert base64.b64decode(inline) == (FIXTURES / "plot_rect_mm.dxf").read_bytes()

    # And the job is immediately pollable.
    poll = await client.get("%s/import-jobs/%s" % (api, body["id"]), headers=firm_a.headers)
    assert poll.status_code == 200
    assert poll.json()["status"] == "queued"


async def test_upload_raw_body_with_filename_param(client, api, firm_a, project_a) -> None:
    data = (FIXTURES / "plot_l_feet.dxf").read_bytes()
    response = await client.post(
        "%s/projects/%s/import/dxf?filename=site survey.dxf" % (api, project_a.id),
        headers={**firm_a.headers, "content-type": "application/dxf"},
        content=data,
    )
    assert response.status_code == 202, response.text
    assert response.json()["filename"] == "site survey.dxf"


async def test_upload_sniffs_out_non_dxf(client, api, firm_a, project_a) -> None:
    response = await client.post(
        "%s/projects/%s/import/dxf" % (api, project_a.id),
        headers=firm_a.headers,
        files={"file": ("plot.dxf", b"PK\x03\x04 a zip wearing a .dxf name", "application/dxf")},
    )
    assert response.status_code == 415
    assert problem(response)["code"] == "unsupported_media_type"


async def test_upload_oversize_is_413(client, api, firm_a, project_a, settings) -> None:
    # Over BOTH the global body ceiling and the DXF cap, so this holds before and
    # after the route joins main.py's large-body allowlist.
    too_big = max(settings.max_dxf_upload_bytes, settings.max_request_body_bytes) + 1
    body = b"0\nSECTION\n2\nHEADER\n" + b"0" * too_big
    response = await client.post(
        "%s/projects/%s/import/dxf" % (api, project_a.id),
        headers={**firm_a.headers, "content-type": "application/dxf"},
        content=body,
    )
    assert response.status_code == 413
    assert problem(response)["code"] == "payload_too_large"


async def test_upload_empty_body_is_400(client, api, firm_a, project_a) -> None:
    response = await client.post(
        "%s/projects/%s/import/dxf" % (api, project_a.id),
        headers={**firm_a.headers, "content-type": "application/dxf"},
        content=b"",
    )
    assert response.status_code == 400
    assert problem(response)["code"] == "invalid_request"


async def test_upload_requires_auth(client, api, project_a) -> None:
    response = await client.post(
        "%s/projects/%s/import/dxf" % (api, project_a.id), files=_upload_files()
    )
    assert response.status_code == 401


async def test_import_job_is_invisible_cross_tenant(
    client, api, firm_a, firm_b, project_a
) -> None:
    created = await client.post(
        "%s/projects/%s/import/dxf" % (api, project_a.id),
        headers=firm_a.headers,
        files=_upload_files(),
    )
    assert created.status_code == 202
    job_id = created.json()["id"]

    stolen = await client.get("%s/import-jobs/%s" % (api, job_id), headers=firm_b.headers)
    assert stolen.status_code == 404
    assert problem(stolen)["code"] == "not_found"


async def test_result_layers_surface_after_worker_success(
    client, api, firm_a, project_a
) -> None:
    created = await client.post(
        "%s/projects/%s/import/dxf" % (api, project_a.id),
        headers=firm_a.headers,
        files=_upload_files(),
    )
    job_id = created.json()["id"]

    # Simulate the worker's terminal event (the runtime publishes exactly this shape:
    # JobResult.data verbatim under event.data).
    layers = [
        {
            "name": "PLOT",
            "polylines": [
                {
                    "points": [{"x": x, "y": y} for x, y in RECT_RING],
                    "closedArea": RECT_AREA,
                }
            ],
        }
    ]
    await api_queue.publish_progress(
        api_queue.ProgressEvent(
            job_id=job_id,
            type="succeeded",
            percent=100,
            data={
                "layers": layers,
                "units": {"insunits": 4, "mmPerUnit": "1", "assumed": False},
                "skipped": {"openPolylines": 1},
            },
        )
    )

    polled = await client.get("%s/import-jobs/%s" % (api, job_id), headers=firm_a.headers)
    assert polled.status_code == 200, polled.text
    body = polled.json()
    assert body["status"] == "succeeded"
    ring = body["result"]["layers"][0]["polylines"][0]
    assert ring["points"][0] == {"x": 0, "y": 0}
    assert ring["closedArea"] == RECT_AREA
    assert body["result"]["units"]["assumed"] is False

    # The result is pinned to the record, so it outlives the 1h event backlog.
    again = await client.get("%s/import-jobs/%s" % (api, job_id), headers=firm_a.headers)
    assert again.json()["result"]["layers"][0]["name"] == "PLOT"


async def test_failed_import_carries_worker_copy(client, api, firm_a, project_a) -> None:
    created = await client.post(
        "%s/projects/%s/import/dxf" % (api, project_a.id),
        headers=firm_a.headers,
        files=_upload_files(),
    )
    job_id = created.json()["id"]

    await api_queue.publish_progress(
        api_queue.ProgressEvent(
            job_id=job_id,
            type="failed",
            message="We couldn't find a closed boundary in that drawing.",
            data={
                "code": "dxf_no_boundary",
                "message": "We couldn't find a closed boundary in that drawing.",
                "action": "Close the plot boundary polyline and export again.",
            },
        )
    )

    polled = await client.get("%s/import-jobs/%s" % (api, job_id), headers=firm_a.headers)
    body = polled.json()
    assert body["status"] == "failed"
    assert "closed boundary" in body["error"]
    assert "export again" in body["error"]


async def test_idempotent_replay_returns_the_stored_job_for_free(
    client, api, firm_a, project_a, settings, clean_redis
) -> None:
    """Same Idempotency-Key twice → the SAME job back, nothing re-done.

    The route answers a replay BEFORE charging the rate-limit slot and before
    buffering the body (routers/imports.py — the mobile-data §15 rationale).
    The phase-2 ledger cited this test before it existed; the branch had never
    executed until it was written.
    """
    key = "e2e-replay-0001"
    first = await client.post(
        "%s/projects/%s/import/dxf" % (api, project_a.id),
        headers={**firm_a.headers, "Idempotency-Key": key},
        files=_upload_files(),
    )
    assert first.status_code == 202, first.text
    job_id = first.json()["id"]

    queued_before = len(clean_redis.lrange(settings.queue_drawings, 0, -1))

    second = await client.post(
        "%s/projects/%s/import/dxf" % (api, project_a.id),
        headers={**firm_a.headers, "Idempotency-Key": key},
        files=_upload_files(),
    )
    assert second.status_code in (200, 202), second.text
    assert second.json()["id"] == job_id, "a replay must return the stored job"

    # Nothing re-enqueued, so nothing re-uploaded and no worker re-run.
    queued_after = len(clean_redis.lrange(settings.queue_drawings, 0, -1))
    assert queued_after == queued_before

    # A DIFFERENT key is a new request and mints a new job.
    third = await client.post(
        "%s/projects/%s/import/dxf" % (api, project_a.id),
        headers={**firm_a.headers, "Idempotency-Key": "e2e-replay-0002"},
        files=_upload_files(),
    )
    assert third.status_code == 202, third.text
    assert third.json()["id"] != job_id
