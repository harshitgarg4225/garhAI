"""The inspiration board over HTTP (§11): pin, annotate, review, remove.

The pure core — which reference applies to which view, which pairs conflict, what
prompt fragments they produce — is tested in ``tests/test_references.py`` against
``services.render.references`` with no database anywhere. This file tests the parts
only a live stack can prove: that the bytes reach storage and come back, that the
architect's four answers survive a round trip, that the review endpoint reads the same
board the render worker will read, and that a picture nobody annotated produces a
QUESTION rather than a silent no-op.

The last one is the feature. A board that quietly contributes nothing is
indistinguishable from a board that works, which is exactly the failure mode CLAUDE.md
names — so the negative controls here break the annotation and assert the question
appears, and break the conflict and assert it disappears.
"""

from __future__ import annotations

import struct
import zlib

import httpx
import pytest

from tests.helpers import problem
from tests.test_render_jobs import TINY_PNG_B64


def _png_chunk(kind: bytes, payload: bytes) -> bytes:
    return (
        struct.pack(">I", len(payload))
        + kind
        + payload
        + struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF)
    )


def _make_png(width: int, height: int) -> bytes:
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 0, 0, 0, 0)
    raw = b"".join(b"\x00" + b"\x80" * width for _ in range(height))
    return (
        b"\x89PNG\r\n\x1a\n"
        + _png_chunk(b"IHDR", ihdr)
        + _png_chunk(b"IDAT", zlib.compress(raw))
        + _png_chunk(b"IEND", b"")
    )


def _url(api: str, project_id, suffix: str = "") -> str:
    return "%s/projects/%s/references%s" % (api, project_id, suffix)


async def _pin(client, api, firm, project_id, *, width: int = 640, height: int = 480) -> dict:
    response = await client.post(
        _url(api, project_id),
        headers={**firm.headers, "content-type": "image/png"},
        content=_make_png(width, height),
    )
    assert response.status_code == 201, response.text
    return response.json()


# ---------------------------------------------------------------------------
# Pinning
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_pin_stores_the_bytes_and_returns_a_working_url(
    client, api, firm_a, project_a, settings, clean_redis
) -> None:
    png = _make_png(640, 480)
    response = await client.post(
        _url(api, project_a.id),
        headers={**firm_a.headers, "content-type": "image/png"},
        content=png,
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["widthPx"] == 640 and body["heightPx"] == 480
    # Firm-scoped key: a leaked signed URL cannot be edited into another tenant's.
    assert body["imageUrl"].startswith(settings.s3_endpoint_url)

    async with httpx.AsyncClient(timeout=10.0) as storage:
        stored = await storage.get(body["imageUrl"])
    assert stored.status_code == 200, stored.text[:200]
    assert stored.content == png


@pytest.mark.integration
async def test_a_pinned_reference_starts_unannotated_and_says_so(
    client, api, firm_a, project_a, clean_redis
) -> None:
    """The weakest possible defaults, on purpose.

    Guessing a scope from a filename would be wrong silently. An empty ``why`` is what
    makes the review endpoint ask.
    """
    body = await _pin(client, api, firm_a, project_a.id)
    assert body["scope"] == "whole-house"
    assert body["intent"] == "guide"
    assert body["why"] == ""
    assert body["ignore"] == ""
    assert body["label"], "a blank chip is not a name an architect can recognise"


@pytest.mark.integration
async def test_pin_rejects_a_file_that_is_not_an_image_415(
    client, api, firm_a, project_a, clean_redis
) -> None:
    response = await client.post(
        _url(api, project_a.id),
        headers={**firm_a.headers, "content-type": "image/png"},
        content=b"%PDF-1.7\nnot a picture at all",
    )
    assert response.status_code == 415, response.text
    assert problem(response)["code"] == "unsupported_media_type"
    assert "PNG or JPEG" in problem(response)["message"]


@pytest.mark.integration
async def test_pin_rejects_an_empty_body_400(client, api, firm_a, project_a, clean_redis) -> None:
    response = await client.post(
        _url(api, project_a.id),
        headers={**firm_a.headers, "content-type": "image/png"},
        content=b"",
    )
    assert response.status_code == 400, response.text


@pytest.mark.integration
async def test_pin_enforces_the_image_size_cap_413(
    client, api, firm_a, project_a, settings, clean_redis
) -> None:
    """The board upload is in LARGE_BODY_PATH_SUFFIXES, so the ROUTE's cap is the guard.

    If this ever returns 201 the middleware exemption has become a hole.
    """
    oversized = b"\x89PNG\r\n\x1a\n" + b"\x00" * (settings.max_image_upload_bytes + 1)
    response = await client.post(
        _url(api, project_a.id),
        headers={**firm_a.headers, "content-type": "image/png"},
        content=oversized,
    )
    assert response.status_code == 413, response.status_code


# ---------------------------------------------------------------------------
# Annotating — the architect's four answers
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_the_four_answers_round_trip(client, api, firm_a, project_a, clean_redis) -> None:
    pinned = await _pin(client, api, firm_a, project_a.id)
    response = await client.patch(
        _url(api, project_a.id, "/%s" % pinned["id"]),
        headers=firm_a.headers,
        json={
            "label": "Client's Pinterest kitchen",
            "scope": "kitchen",
            "why": "the walnut cabinet fronts and the brass handles",
            "ignore": "the island — our plan has none",
            "intent": "match",
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["label"] == "Client's Pinterest kitchen"
    assert body["scope"] == "kitchen"
    assert body["intent"] == "match"
    assert "walnut" in body["why"]
    assert "island" in body["ignore"]

    listed = await client.get(_url(api, project_a.id), headers=firm_a.headers)
    assert listed.status_code == 200
    (only,) = listed.json()["references"]
    assert only["why"] == body["why"], "the annotation must survive the round trip"


@pytest.mark.integration
async def test_patch_is_partial(client, api, firm_a, project_a, clean_redis) -> None:
    pinned = await _pin(client, api, firm_a, project_a.id)
    url = _url(api, project_a.id, "/%s" % pinned["id"])
    await client.patch(url, headers=firm_a.headers, json={"why": "the roof pitch"})
    second = await client.patch(url, headers=firm_a.headers, json={"scope": "facade"})
    assert second.status_code == 200, second.text
    assert second.json()["scope"] == "facade"
    assert second.json()["why"] == "the roof pitch", "an untouched answer must not be cleared"


@pytest.mark.integration
async def test_a_scope_the_render_side_cannot_read_is_refused_422(
    client, api, firm_a, project_a, clean_redis
) -> None:
    """A scope outside the shared vocabulary is a picture that steers nothing, forever.

    ``test_reference_vocabulary.py`` keeps this enum equal to the render side's; this
    keeps the boundary from writing one that is not in it.
    """
    pinned = await _pin(client, api, firm_a, project_a.id)
    response = await client.patch(
        _url(api, project_a.id, "/%s" % pinned["id"]),
        headers=firm_a.headers,
        json={"scope": "roof-terrace"},
    )
    assert response.status_code == 422, response.text


@pytest.mark.integration
async def test_a_reference_from_another_project_is_404(
    client, api, firm_a, project_a, session, clean_redis
) -> None:
    """Same firm, wrong project — still not found.

    The repository scopes by firm, so without the router's own project check a
    colleague's reference would be editable through the wrong project's URL.
    """
    from tests import factories

    other = await factories.create_project(session, firm_a, name="A different house")
    await session.commit()
    pinned = await _pin(client, api, firm_a, project_a.id)
    response = await client.patch(
        _url(api, other.id, "/%s" % pinned["id"]),
        headers=firm_a.headers,
        json={"label": "moved"},
    )
    assert response.status_code == 404, response.text


# ---------------------------------------------------------------------------
# Removing
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_delete_removes_it_from_the_board(
    client, api, firm_a, project_a, clean_redis
) -> None:
    pinned = await _pin(client, api, firm_a, project_a.id)
    removed = await client.delete(
        _url(api, project_a.id, "/%s" % pinned["id"]), headers=firm_a.headers
    )
    assert removed.status_code == 200, removed.text
    listed = await client.get(_url(api, project_a.id), headers=firm_a.headers)
    assert listed.json()["references"] == []


# ---------------------------------------------------------------------------
# Review — the questions asked BEFORE the render
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_review_separates_what_applies_from_what_does_not(
    client, api, firm_a, project_a, clean_redis
) -> None:
    """A kitchen picture changes nothing on a street elevation — and it says so.

    Dropping it silently is how an architect concludes the board does nothing.
    """
    kitchen = await _pin(client, api, firm_a, project_a.id)
    facade = await _pin(client, api, firm_a, project_a.id)
    await client.patch(
        _url(api, project_a.id, "/%s" % kitchen["id"]),
        headers=firm_a.headers,
        json={"scope": "kitchen", "why": "walnut fronts", "intent": "match"},
    )
    await client.patch(
        _url(api, project_a.id, "/%s" % facade["id"]),
        headers=firm_a.headers,
        json={"scope": "facade", "why": "the deep verandah", "intent": "match"},
    )

    response = await client.get(
        _url(api, project_a.id, "/review"),
        headers=firm_a.headers,
        params={"preset": "elevation-north-morning"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert [r["id"] for r in body["applies"]] == [facade["id"]]
    assert [r["id"] for r in body["notInView"]] == [kitchen["id"]]
    assert "verandah" in body["positive"], "the architect's own words must reach the prompt"
    assert "walnut" not in body["positive"], "an interior note has no business on an elevation"


@pytest.mark.integration
async def test_review_asks_which_of_two_competing_references_wins(
    client, api, firm_a, project_a, clean_redis
) -> None:
    """Two pictures both marked "match" for the same view is a real question."""
    first = await _pin(client, api, firm_a, project_a.id)
    second = await _pin(client, api, firm_a, project_a.id)
    for reference, why in ((first, "the stone plinth"), (second, "the white render")):
        await client.patch(
            _url(api, project_a.id, "/%s" % reference["id"]),
            headers=firm_a.headers,
            json={"scope": "facade", "why": why, "intent": "match"},
        )

    body = (
        await client.get(
            _url(api, project_a.id, "/review"),
            headers=firm_a.headers,
            params={"preset": "elevation-north-morning"},
        )
    ).json()
    competing = [c for c in body["conflicts"] if c["kind"] == "competing"]
    assert competing, "two 'match closely' references for one view must be surfaced"
    assert set(competing[0]["referenceIds"]) == {first["id"], second["id"]}
    assert competing[0]["default"], "a question with no stated default is one people dismiss"

    # NEGATIVE CONTROL: demote one to a guide and the question must go away. Without
    # this the test would pass against an implementation that flags every pair.
    await client.patch(
        _url(api, project_a.id, "/%s" % second["id"]),
        headers=firm_a.headers,
        json={"intent": "guide"},
    )
    after = (
        await client.get(
            _url(api, project_a.id, "/review"),
            headers=firm_a.headers,
            params={"preset": "elevation-north-morning"},
        )
    ).json()
    assert not [c for c in after["conflicts"] if c["kind"] == "competing"]


@pytest.mark.integration
async def test_review_asks_what_an_unannotated_picture_is_for(
    client, api, firm_a, project_a, clean_redis
) -> None:
    """Pinned and never annotated: ask, do not guess, and do not silently ignore."""
    pinned = await _pin(client, api, firm_a, project_a.id)
    body = (
        await client.get(
            _url(api, project_a.id, "/review"),
            headers=firm_a.headers,
            params={"preset": "elevation-north-morning"},
        )
    ).json()
    unusable = [c for c in body["conflicts"] if c["kind"] == "unusable"]
    assert unusable, "an unannotated reference must produce a question"
    assert pinned["id"] in unusable[0]["referenceIds"]

    # NEGATIVE CONTROL: answer the question and it must stop being asked.
    await client.patch(
        _url(api, project_a.id, "/%s" % pinned["id"]),
        headers=firm_a.headers,
        json={"why": "the deep shaded verandah", "scope": "facade"},
    )
    after = (
        await client.get(
            _url(api, project_a.id, "/review"),
            headers=firm_a.headers,
            params={"preset": "elevation-north-morning"},
        )
    ).json()
    assert not [c for c in after["conflicts"] if c["kind"] == "unusable"]


@pytest.mark.integration
async def test_review_rejects_a_preset_that_does_not_exist_400(
    client, api, firm_a, project_a, clean_redis
) -> None:
    response = await client.get(
        _url(api, project_a.id, "/review"),
        headers=firm_a.headers,
        params={"preset": "elevation-from-the-moon"},
    )
    assert response.status_code == 400, response.text


@pytest.mark.integration
async def test_review_answers_tenancy_before_it_touches_the_render_package(
    client, api, firm_a, firm_b, project_a, clean_redis, monkeypatch
) -> None:
    """§13 ordering, negative-controlled.

    This defect has shipped twice in this repo: a module-availability check placed
    ahead of the tenancy check turns another firm's 404 into a 503, which is an
    existence oracle. Here the render package is made unimportable, so if the order is
    ever flipped this test fails with 503 instead of 404 — and the passing case in
    ``test_cross_tenant.py`` could not catch it, because there the import succeeds.
    """
    import builtins

    real_import = builtins.__import__

    def _refuse(name, *args, **kwargs):
        if name.startswith("services.render"):
            raise ImportError("render package deliberately unavailable")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _refuse)
    response = await client.get(
        _url(api, project_a.id, "/review"),
        headers=firm_b.headers,
        params={"preset": "elevation-north-morning"},
    )
    assert response.status_code == 404, response.text


# ---------------------------------------------------------------------------
# The join: does the board actually reach a render job?
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_the_board_travels_with_the_render_job(
    client, api, session, clean_redis, firm_a, project_a
) -> None:
    """The join CLAUDE.md's fourth bug class is made of.

    A board that is stored, annotated, reviewed — and never put on the queue — would
    pass every other test in this file while contributing nothing to a single render.
    So this reads the actual envelope off Redis.
    """
    import json as jsonlib

    from tests import factories

    await factories.create_version(session, firm_a, project_a.id)
    pinned = await _pin(client, api, firm_a, project_a.id)
    await client.patch(
        _url(api, project_a.id, "/%s" % pinned["id"]),
        headers=firm_a.headers,
        json={
            "label": "Client's verandah photo",
            "scope": "facade",
            "why": "the deep shaded verandah",
            "intent": "match",
        },
    )
    clean_redis.delete("garh:queue:render")

    response = await client.post(
        "%s/projects/%s/renders" % (api, project_a.id),
        json={
            "mode": "precise",
            "preset": "elevation-north-morning",
            "view": {"preset": "elevation-north-morning", "fovDeg": 45},
            "inputs": {"viewportPng": TINY_PNG_B64},
        },
        headers=firm_a.headers,
    )
    assert response.status_code == 202, response.text

    (raw,) = clean_redis.lrange("garh:queue:render", 0, -1)
    payload = jsonlib.loads(raw)["payload"]
    assert "references" in payload, "the board never reached the worker"
    (carried,) = payload["references"]
    assert carried["id"] == pinned["id"]
    assert carried["label"] == "Client's verandah photo"
    assert carried["why"] == "the deep shaded verandah"
    assert carried["intent"] == "match"

    # And it is the shape the worker's parser consumes — not merely a dict that
    # happens to be present. This is the assertion that catches a rename on one side.
    from services.render.handler import _references_from

    (parsed,) = _references_from(payload["references"])
    assert parsed.label == "Client's verandah photo"


@pytest.mark.integration
async def test_an_unannotated_reference_is_not_shipped_to_the_worker(
    client, api, session, clean_redis, firm_a, project_a
) -> None:
    """NEGATIVE CONTROL for the test above.

    Without it, that test would also pass against an implementation that shipped every
    row on the board regardless of whether the architect had said anything about it —
    and every render payload would carry references that cannot steer anything.
    """
    import json as jsonlib

    from tests import factories

    await factories.create_version(session, firm_a, project_a.id)
    await _pin(client, api, firm_a, project_a.id)  # pinned, never annotated
    clean_redis.delete("garh:queue:render")

    response = await client.post(
        "%s/projects/%s/renders" % (api, project_a.id),
        json={
            "mode": "precise",
            "preset": "elevation-north-morning",
            "view": {"preset": "elevation-north-morning", "fovDeg": 45},
            "inputs": {"viewportPng": TINY_PNG_B64},
        },
        headers=firm_a.headers,
    )
    assert response.status_code == 202, response.text
    (raw,) = clean_redis.lrange("garh:queue:render", 0, -1)
    assert "references" not in jsonlib.loads(raw)["payload"]


@pytest.mark.integration
async def test_a_finished_render_names_the_references_it_followed(
    client, api, session, clean_redis, firm_a, project_a
) -> None:
    """The last layer of the join, and the one that shipped broken.

    The worker credits the references its prompt consumed; ``render_jobs`` kept only
    ``output_url`` from a worker's result, so the credit was computed, logged and
    dropped one layer before the architect ever saw it. Every unit test still passed:
    they asserted the worker's ``JobResult.data``, which is not where an architect
    looks.

    A live run of ``scripts/reference_journey.py`` is what found it. This is that
    finding turned into a gate that needs no live stack — applied through
    ``apply_lifecycle_record``, the API's own consumer entry point, so it exercises
    the path production events take.
    """
    from garh_api.routers.jobs import apply_lifecycle_record

    from tests import factories
    from tests.test_render_jobs import _lifecycle

    await factories.create_version(session, firm_a, project_a.id)
    job = await factories.create_render_job(session, firm_a, project_a.id)
    await session.commit()

    applied = await apply_lifecycle_record(
        session,
        _lifecycle(
            job,
            firm_a.firm_id,
            "succeeded",
            percent=100,
            data={
                "outputUrl": "https://example.invalid/renders/x.png",
                "references": [
                    {"id": "ref-1", "label": "Client's verandah photo", "intent": "match"}
                ],
            },
        ),
    )
    assert applied
    await session.commit()

    fetched = await client.get("%s/render-jobs/%s" % (api, job.id), headers=firm_a.headers)
    assert fetched.status_code == 200, fetched.text
    body = fetched.json()
    assert body["status"] == "succeeded"
    assert body["referencesUsed"] == [
        {"id": "ref-1", "label": "Client's verandah photo", "intent": "match"}
    ]


@pytest.mark.integration
async def test_a_render_that_followed_nothing_credits_nothing(
    client, api, session, clean_redis, firm_a, project_a
) -> None:
    """NEGATIVE CONTROL for the case above.

    Without it, a schema field hard-coded to a non-empty list would satisfy that
    assertion, and every render ever made would claim a reference.
    """
    from garh_api.routers.jobs import apply_lifecycle_record

    from tests import factories
    from tests.test_render_jobs import _lifecycle

    await factories.create_version(session, firm_a, project_a.id)
    job = await factories.create_render_job(session, firm_a, project_a.id)
    await session.commit()

    await apply_lifecycle_record(
        session,
        _lifecycle(
            job,
            firm_a.firm_id,
            "succeeded",
            percent=100,
            data={"outputUrl": "https://example.invalid/renders/y.png"},
        ),
    )
    await session.commit()

    fetched = await client.get("%s/render-jobs/%s" % (api, job.id), headers=firm_a.headers)
    assert fetched.json()["referencesUsed"] == []
