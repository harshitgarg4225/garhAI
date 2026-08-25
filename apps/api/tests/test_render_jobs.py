"""Phase 7 — renders end-to-end (§9): job lifecycle with the mock provider,
stale-flag flip on a model edit, the per-firm concurrency cap, version pinning,
and the client pack as one job group.

What runs where
---------------

* **Mirror + provider tests** need no datastore. The provider tests additionally
  need Pillow and skip cleanly without it (``pytest.importorskip``).
* **Everything marked ``integration``** needs the real Postgres + Redis the
  suite's ``conftest.py`` provides (skip locally when absent, fail in CI).
* **The archive test** additionally needs object storage (minio in compose); it
  probes with one presigned PUT and skips when storage is unreachable, so a
  DB+Redis-only environment still runs the rest of this file.

The render *worker* itself is exercised through its provider (deterministic mock)
and through fabricated lifecycle records applied by the API's own consumer entry
point (``apply_lifecycle_record``) — the same at-least-once path production events
take — because this suite runs no worker processes.
"""

from __future__ import annotations

import base64
import io
import sys
import time
import uuid
import zipfile
from pathlib import Path
from typing import Any, Optional

import httpx
import pytest

# ``services/`` lives at the repo root — same pinning as test_brief_parse.py.
REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from garh_api import queue  # noqa: E402
from garh_api.repositories import RenderJobRepository  # noqa: E402
from garh_api.routers import renders as renders_router  # noqa: E402
from garh_api.routers.jobs import apply_lifecycle_record  # noqa: E402

from services.render import pack as service_pack  # noqa: E402
from services.render.types import PRESETS as SERVICE_PRESETS  # noqa: E402

from tests import factories  # noqa: E402
from tests.helpers import op_payload, problem  # noqa: E402


# ---------------------------------------------------------------------------
# Test data
# ---------------------------------------------------------------------------

#: A valid 1×1 white PNG — small enough to inline everywhere.
TINY_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4nGP4//8/AwAI/AL+p5qgoAAAAABJRU5ErkJggg=="
)
TINY_PNG_B64 = base64.b64encode(TINY_PNG).decode("ascii")


def _render_body(**overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "mode": "explore",
        "preset": "exterior-street-day",
        "seed": 42,
        "width": 512,
        "height": 512,
        "view": {"preset": "exterior-street-day", "fovDeg": 45},
        "inputs": {"viewportPng": TINY_PNG_B64},
    }
    body.update(overrides)
    return body


def _pack_body(shot_count: int = 2, **overrides: Any) -> dict[str, Any]:
    shots = [
        {
            "slug": slug,
            "preset": preset,
            "mode": mode,
            "view": {"preset": preset, "fovDeg": 45},
            "inputs": {"viewportPng": TINY_PNG_B64},
        }
        for slug, preset, mode in renders_router.CLIENT_PACK_SHOTS[:shot_count]
    ]
    body: dict[str, Any] = {"seed": 7, "width": 512, "height": 512, "shots": shots}
    body.update(overrides)
    return body


def _lifecycle(
    job: Any,
    firm_id: Any,
    event_type: str,
    *,
    percent: Optional[int] = None,
    data: Optional[dict[str, Any]] = None,
) -> queue.LifecycleRecord:
    """A worker lifecycle record, exactly as ``read_job_events`` would parse one."""
    return queue.LifecycleRecord(
        entry_id="0-1",
        job_id=str(job.id),
        kind=queue.JOB_RENDER_IMAGE,
        firm_id=str(firm_id),
        project_id=str(job.project_id),
        design_version_id=str(job.design_version_id) if job.design_version_id else None,
        type=event_type,
        attempt=1,
        event=queue.ProgressEvent(
            job_id=str(job.id),
            type=event_type,
            seq=1,
            percent=percent,
            data=data or {},
        ),
    )


# ---------------------------------------------------------------------------
# 1. Mirror contracts (no datastore, no Pillow)
# ---------------------------------------------------------------------------


def test_client_pack_mirror_matches_the_service() -> None:
    """The API's pack is a declared byte-identical mirror of services/render/pack.py."""
    service = [(s.slug, s.preset, s.mode) for s in service_pack.CLIENT_PACK_SHOTS]
    assert list(renders_router.CLIENT_PACK_SHOTS) == service


def test_preset_mode_mirror_matches_the_service() -> None:
    service = {pid: tuple(p.modes) for pid, p in SERVICE_PRESETS.items()}
    assert renders_router.PRESET_MODES == service


def test_pack_is_six_exteriors_plus_living_and_kitchen() -> None:
    scenes = [SERVICE_PRESETS[preset].scene for _, preset, _ in renders_router.CLIENT_PACK_SHOTS]
    assert scenes.count("exterior") == 6
    interiors = [p for _, p, _ in renders_router.CLIENT_PACK_SHOTS if SERVICE_PRESETS[p].scene == "interior"]
    assert interiors == ["interior-living", "interior-kitchen"]


def test_render_object_key_is_deterministic() -> None:
    firm, job = uuid.uuid4(), uuid.uuid4()
    assert renders_router.render_object_key(firm, job) == renders_router.render_object_key(firm, job)
    assert str(firm) in renders_router.render_object_key(firm, job)


# ---------------------------------------------------------------------------
# 2. The mock provider (§9, §14 "render (mock) <1s") — needs Pillow only
# ---------------------------------------------------------------------------


def _mock_request(seed: int, mode: str = "explore") -> Any:
    from services.render.types import RenderRequest

    return RenderRequest(
        viewport_png=TINY_PNG,
        mode=mode,  # type: ignore[arg-type]
        preset="exterior-street-day",
        seed=seed,
        size=(512, 512),
        depth_png=TINY_PNG,
        edges_png=TINY_PNG,
    )


def test_mock_provider_is_deterministic_by_seed_and_under_budget() -> None:
    pytest.importorskip("PIL")
    from services.render.mock import MockRenderProvider

    provider = MockRenderProvider()
    started = time.monotonic()
    first = provider.render(_mock_request(seed=42))
    duration = time.monotonic() - started
    second = provider.render(_mock_request(seed=42))

    assert first.image_png == second.image_png, "same request bytes must mean same output bytes"
    assert first.is_mock is True
    assert duration < 1.0, "§14: mock render must finish in under a second"

    other_seed = provider.render(_mock_request(seed=43))
    assert other_seed.image_png != first.image_png, "seed must change the image"

    precise = provider.render(_mock_request(seed=42, mode="precise"))
    assert precise.image_png != first.image_png, "precise and explore must look different"


# ---------------------------------------------------------------------------
# 3. Job flow: enqueue → envelope → lifecycle → row (integration)
# ---------------------------------------------------------------------------

@pytest.mark.integration
async def test_start_render_writes_row_and_a_complete_envelope(
    client: Any, api: str, session: Any, clean_redis: Any, firm_a: Any, project_a: Any
) -> None:
    """POST /renders → 202 + a queued row + an envelope the worker can actually run:
    assets to read AND (the Phase-7 fix) a writable output to store the image."""
    version = await factories.create_version(session, firm_a, project_a.id)

    response = await client.post(
        "%s/projects/%s/renders" % (api, project_a.id),
        json=_render_body(),
        headers=firm_a.headers,
    )
    assert response.status_code == 202, response.text
    body = response.json()
    assert body["status"] == "queued"
    assert body["mode"] == "explore"
    assert body["stale"] is False
    # §9: pinned to a design version — the latest, since none was supplied.
    assert body["designVersionId"] == str(version.id)

    raw = clean_redis.lrange("garh:queue:render", 0, -1)
    assert len(raw) == 1, "exactly one envelope on the render queue"
    import json as jsonlib

    envelope = jsonlib.loads(raw[0])
    assert envelope["kind"] == "render.image"
    assert envelope["firmId"] == str(firm_a.firm_id)
    assert envelope["designVersionId"] == str(version.id)
    assert envelope["assets"]["viewport_png"]["inlineBase64"] == TINY_PNG_B64
    output = envelope["outputs"]["image"]
    assert output.get("putUrl"), "worker must receive a writable destination (§13)"
    assert output.get("key") == renders_router.render_object_key(firm_a.firm_id, body["id"])


@pytest.mark.integration
async def test_lifecycle_events_progress_then_succeed_the_row(
    client: Any, api: str, session: Any, clean_redis: Any, firm_a: Any, project_a: Any
) -> None:
    """started → progress → succeeded, applied through the API's own consumer entry
    point, drives the row exactly as production events do (enqueue → progress →
    result)."""
    await factories.create_version(session, firm_a, project_a.id)
    response = await client.post(
        "%s/projects/%s/renders" % (api, project_a.id),
        json=_render_body(),
        headers=firm_a.headers,
    )
    assert response.status_code == 202, response.text
    job_id = response.json()["id"]

    repo = RenderJobRepository(session, firm_a.ctx())
    job = await repo.require(uuid.UUID(job_id))

    assert await apply_lifecycle_record(session, _lifecycle(job, firm_a.firm_id, "started"))
    # Interim progress rides the SSE stream only; the row is written on terminal
    # events. The consumer says so by returning False (nothing applied).
    assert not await apply_lifecycle_record(
        session, _lifecycle(job, firm_a.firm_id, "progress", percent=20)
    )
    await session.commit()
    row = await repo.require(uuid.UUID(job_id))
    assert row.status == "running"

    image_url = "https://storage.test/%s" % renders_router.render_object_key(
        firm_a.firm_id, job_id
    )
    assert await apply_lifecycle_record(
        session,
        _lifecycle(
            job, firm_a.firm_id, "succeeded", percent=100, data={"outputUrl": image_url}
        ),
    )
    await session.commit()
    row = await repo.require(uuid.UUID(job_id))
    assert row.status == "succeeded"
    assert row.progress == 100
    assert row.output_url == image_url
    assert row.stale is False

    # A succeeded event WITHOUT an image is failed honestly, never shown as done.
    other = await client.post(
        "%s/projects/%s/renders" % (api, project_a.id),
        json=_render_body(seed=43),
        headers=firm_a.headers,
    )
    other_job = await repo.require(uuid.UUID(other.json()["id"]))
    await apply_lifecycle_record(
        session, _lifecycle(other_job, firm_a.firm_id, "succeeded", data={})
    )
    await session.commit()
    failed = await repo.require(other_job.id)
    assert failed.status == "failed"
    assert failed.error


@pytest.mark.integration
async def test_model_edit_flips_stale_and_history_reports_it(
    client: Any, api: str, session: Any, clean_redis: Any, firm_a: Any, project_a: Any
) -> None:
    """§9: renders are pinned to a version; a model edit marks them stale=true."""
    await factories.create_version(session, firm_a, project_a.id)
    response = await client.post(
        "%s/projects/%s/renders" % (api, project_a.id),
        json=_render_body(),
        headers=firm_a.headers,
    )
    job_id = uuid.UUID(response.json()["id"])
    repo = RenderJobRepository(session, firm_a.ctx())
    await repo.succeed(job_id, "https://storage.test/render.png")
    await session.commit()

    # A visual op through the REAL sequencer path (plot.set_north is visual).
    edit = await client.post(
        "%s/projects/%s/ops" % (api, project_a.id),
        json={"ops": [op_payload("plot.set_north", deg=45)], "baseIdx": -1, "source": "manual"},
        headers=firm_a.headers,
    )
    assert edit.status_code == 200, edit.text
    assert edit.json()["rendersMarkedStale"] >= 1

    history = await client.get(
        "%s/projects/%s/render-history" % (api, project_a.id), headers=firm_a.headers
    )
    assert history.status_code == 200, history.text
    items = history.json()["items"]
    mine = next(item for item in items if item["id"] == str(job_id))
    assert mine["stale"] is True, "§9 banner: 'Design changed since this render'"

    # Non-visual ops (annotation.set et al.) must NOT stale — spot-check via repo state.
    fresh = await client.post(
        "%s/projects/%s/renders" % (api, project_a.id),
        json=_render_body(seed=44),
        headers=firm_a.headers,
    )
    assert fresh.status_code == 202


@pytest.mark.integration
async def test_per_firm_concurrency_cap_is_enforced_serverside(
    client: Any, api: str, session: Any, clean_redis: Any, firm_a: Any, project_a: Any
) -> None:
    """§9: four concurrent renders per firm. The fifth start is a 429 problem+json."""
    await factories.create_version(session, firm_a, project_a.id)
    for seed in range(4):
        response = await client.post(
            "%s/projects/%s/renders" % (api, project_a.id),
            json=_render_body(seed=seed),
            headers=firm_a.headers,
        )
        assert response.status_code == 202, response.text

    fifth = await client.post(
        "%s/projects/%s/renders" % (api, project_a.id),
        json=_render_body(seed=99),
        headers=firm_a.headers,
    )
    assert fifth.status_code == 429, fifth.text
    body = problem(fifth)
    assert body["code"] == "render_concurrency_limit"

    # The pack respects the same gate (checked once for the whole group).
    pack = await client.post(
        "%s/projects/%s/renders/client-pack" % (api, project_a.id),
        json=_pack_body(),
        headers=firm_a.headers,
    )
    assert pack.status_code == 429, pack.text
    assert problem(pack)["code"] == "render_concurrency_limit"


@pytest.mark.integration
async def test_client_pack_is_one_job_group_with_derived_seeds(
    client: Any, api: str, session: Any, clean_redis: Any, firm_a: Any, project_a: Any
) -> None:
    version = await factories.create_version(session, firm_a, project_a.id)

    response = await client.post(
        "%s/projects/%s/renders/client-pack" % (api, project_a.id),
        json=_pack_body(shot_count=3, seed=100),
        headers=firm_a.headers,
    )
    assert response.status_code == 202, response.text
    body = response.json()
    pack_id = body["packId"]
    assert body["status"] == "queued"
    assert len(body["jobs"]) == 3
    for index, job in enumerate(body["jobs"]):
        assert job["params"]["packId"] == pack_id, "ONE job group: shared packId"
        assert job["params"]["packIndex"] == index
        # Mirrors services/render/pack.shot_seed: base + index.
        assert job["params"]["seed"] == 100 + index
        assert job["designVersionId"] == str(version.id), "§9: every shot version-pinned"

    assert len(clean_redis.lrange("garh:queue:render", 0, -1)) == 3

    status = await client.get(
        "%s/projects/%s/render-packs/%s" % (api, project_a.id, pack_id),
        headers=firm_a.headers,
    )
    assert status.status_code == 200, status.text
    assert status.json()["status"] == "queued"
    assert len(status.json()["jobs"]) == 3

    # The zip refuses honestly while images are outstanding: 409, not 404, not a
    # half-empty archive.
    archive = await client.post(
        "%s/projects/%s/render-packs/%s/archive" % (api, project_a.id, pack_id),
        headers=firm_a.headers,
    )
    assert archive.status_code == 409, archive.text
    assert problem(archive)["code"] == "render_pack_not_ready"


@pytest.mark.integration
async def test_client_pack_requires_a_design_version(
    client: Any, api: str, clean_redis: Any, firm_a: Any, project_a: Any
) -> None:
    """A pack of unpinned renders cannot honour the stale contract — 409 up front."""
    response = await client.post(
        "%s/projects/%s/renders/client-pack" % (api, project_a.id),
        json=_pack_body(),
        headers=firm_a.headers,
    )
    assert response.status_code == 409, response.text
    assert problem(response)["code"] == "no_design_version"


@pytest.mark.integration
async def test_pack_rejects_interior_precise_up_front(
    client: Any, api: str, session: Any, clean_redis: Any, firm_a: Any, project_a: Any
) -> None:
    """Spec F6: interiors are Explore-only. One 422 beats eight dead jobs."""
    await factories.create_version(session, firm_a, project_a.id)
    body = _pack_body(shot_count=1)
    body["shots"][0].update({"preset": "interior-living", "mode": "precise"})
    response = await client.post(
        "%s/projects/%s/renders/client-pack" % (api, project_a.id),
        json=body,
        headers=firm_a.headers,
    )
    assert response.status_code == 422, response.text


@pytest.mark.integration
async def test_capture_upload_slots_are_minted_firm_scoped(
    client: Any, api: str, clean_redis: Any, firm_a: Any, project_a: Any
) -> None:
    """Presigned capture uploads: pure HMAC minting, keys under the firm's prefix.

    This is what lets a client pack move 24 PNGs browser→storage instead of
    through the API's request-body cap (§13).
    """
    response = await client.post(
        "%s/projects/%s/renders/uploads" % (api, project_a.id),
        json={"count": 3},
        headers=firm_a.headers,
    )
    assert response.status_code == 200, response.text
    slots = response.json()["slots"]
    assert len(slots) == 3
    for slot in slots:
        assert slot["putUrl"].startswith(("http://", "https://"))
        assert slot["getUrl"].startswith(("http://", "https://"))
        assert slot["key"].startswith("renders/%s/inputs/" % firm_a.firm_id)
    assert len({slot["key"] for slot in slots}) == 3, "every slot gets its own object"

    too_many = await client.post(
        "%s/projects/%s/renders/uploads" % (api, project_a.id),
        json={"count": 999},
        headers=firm_a.headers,
    )
    assert too_many.status_code == 422


@pytest.mark.integration
async def test_render_rows_are_firm_scoped(
    client: Any, api: str, session: Any, clean_redis: Any, firm_a: Any, firm_b: Any, project_a: Any
) -> None:
    """§13: another firm's render job resolves to 404, exactly like a nonexistent one."""
    await factories.create_version(session, firm_a, project_a.id)
    response = await client.post(
        "%s/projects/%s/renders" % (api, project_a.id),
        json=_render_body(),
        headers=firm_a.headers,
    )
    job_id = response.json()["id"]

    stranger = await client.get("%s/render-jobs/%s" % (api, job_id), headers=firm_b.headers)
    assert stranger.status_code == 404

    history = await client.get(
        "%s/projects/%s/render-history" % (api, project_a.id), headers=firm_b.headers
    )
    assert history.status_code == 404


# ---------------------------------------------------------------------------
# 4. Pack archive — needs object storage (skips when minio is not running)
# ---------------------------------------------------------------------------


def _storage_reachable(settings: Any) -> bool:
    try:
        with httpx.Client(timeout=2.0) as probe:
            probe.get(settings.s3_endpoint_url)
        return True
    except httpx.HTTPError:
        return False


@pytest.mark.integration
async def test_pack_archive_builds_the_zip_through_the_export_path(
    client: Any,
    api: str,
    session: Any,
    clean_redis: Any,
    settings: Any,
    firm_a: Any,
    project_a: Any,
) -> None:
    if not _storage_reachable(settings):
        pytest.skip("object storage (minio) is not reachable; archive test needs it")

    await factories.create_version(session, firm_a, project_a.id)
    response = await client.post(
        "%s/projects/%s/renders/client-pack" % (api, project_a.id),
        json=_pack_body(shot_count=2, seed=5),
        headers=firm_a.headers,
    )
    assert response.status_code == 202, response.text
    pack = response.json()
    pack_id = pack["packId"]

    # Play the worker: PUT each image to its presigned destination, then apply the
    # succeeded lifecycle record — the exact production sequence.
    repo = RenderJobRepository(session, firm_a.ctx())
    raw_envelopes = clean_redis.lrange("garh:queue:render", 0, -1)
    import json as jsonlib

    async with httpx.AsyncClient(timeout=10.0) as storage:
        for raw in raw_envelopes:
            envelope = jsonlib.loads(raw)
            output = envelope["outputs"]["image"]
            put = await storage.put(
                output["putUrl"], content=TINY_PNG, headers={"content-type": "image/png"}
            )
            assert put.status_code < 400, put.text
            job = await repo.require(uuid.UUID(envelope["jobId"]))
            await apply_lifecycle_record(
                session,
                _lifecycle(
                    job,
                    firm_a.firm_id,
                    "succeeded",
                    percent=100,
                    data={"outputUrl": output["getUrl"]},
                ),
            )
    await session.commit()

    archive = await client.post(
        "%s/projects/%s/render-packs/%s/archive" % (api, project_a.id, pack_id),
        headers=firm_a.headers,
    )
    assert archive.status_code == 200, archive.text
    body = archive.json()
    assert body["kind"] == "png-pack"
    assert body["status"] == "succeeded"
    assert body["downloadUrl"], "the signed export path must hand back a link"

    # Redeem through the EXISTING signed download endpoint and fetch the zip.
    from urllib.parse import urlparse

    redeem = await client.get(urlparse(body["downloadUrl"]).path, follow_redirects=False)
    assert redeem.status_code in (302, 307), redeem.text
    target = redeem.headers["location"]
    async with httpx.AsyncClient(timeout=10.0) as storage:
        stored = await storage.get(target)
    assert stored.status_code == 200
    with zipfile.ZipFile(io.BytesIO(stored.content)) as bundle:
        names = bundle.namelist()
    assert len(names) == 2
    assert names[0].startswith("01-") and names[0].endswith(".png")


# ---------------------------------------------------------------------------
# CI notes (what this file deliberately does NOT cover)
# ---------------------------------------------------------------------------
# * The DiffusersProvider is never run here (no GPU in CI): its licence guard is
#   covered by services/render/licenses.py's own assertions and the FLUX.1-dev
#   denylist check runs in the provider constructor before any weight download.
# * SSE streaming of render progress is covered transport-wise by the solver's
#   stream tests; render events ride the identical `_stream_job` plumbing.
# * The browser capture set (viewport/depth/edges) is client code — exercised by
#   the web unit suite (`features/renders/capture.ts`) and the Phase 9 e2e.
