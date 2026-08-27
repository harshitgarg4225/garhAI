"""Live cursors: ``POST /projects/:id/collab/cursor`` → ``event: cursor`` fan-out.

The wire contract is FROZEN — the web canvas half is built against it in parallel — so
the assertions here are exact (``==`` on key sets), the same discipline as
``test_collab.py``. Four claims, each tested where it lives:

* **The frame** — a 204 POST publishes exactly ``{kind, userId, name, x, y,
  storeyIndex}`` on ``garh:collab:<projectId>``, and the SSE generator forwards it as
  an ``event: cursor`` frame with exactly the five contract keys and **no** ``id:``
  (an id would be replayed as ``Last-Event-ID`` and poison ops catch-up).
* **Identity is stamped, never believed.** The negative test sends a forged
  ``userId``/``name`` in the body and asserts the published frame carries the real
  ones — proving the guarantee rests on the server's stamping, not on validation
  happening to reject the field.
* **The name is Redis-first.** The frame's name comes from the presence hash when an
  entry exists (asserted by planting a name the user row does NOT have), and the DB
  fallback writes through so it runs once — enforced by making the DB path explode on
  a second call, the "gate that can go red" the repo's history demands.
* **Tenancy.** Firm B's valid request 404s before anything is published. (The
  table-driven sweep in ``test_cross_tenant.py`` covers the route too; this is the
  readable copy, plus the channel-silence half the sweep cannot see.)

Real Redis throughout, reusing ``test_collab.py``'s helpers — per the suite's
no-datastore-mocks rule, a fake pub/sub would let a broken channel name or a
non-forwarding generator pass.
"""

from __future__ import annotations

import json
import time
import uuid
from typing import Any

import pytest
from garh_api import collab
from garh_api.routers.collab import _cursor_frame_from_message

from tests.helpers import problem
from tests.test_collab import (
    _assert_channel_silent,
    _channel_message,
    _frame_of_event,
    _next_frame,
    _stream,
    _subscribed,
)

#: The exact channel envelope: the five frame keys plus the routing discriminator.
ENVELOPE_KEYS = {"kind", "userId", "name", "x", "y", "storeyIndex"}

#: The exact ``event: cursor`` data keys the web client is built against.
FRAME_KEYS = {"userId", "name", "x", "y", "storeyIndex"}

#: A body that passes every bound, so any non-204 answer to it is not validation.
VALID_BODY = {"x": 1200, "y": -3400, "storeyIndex": 1}


def _cursor_url(api: str, project_id: Any) -> str:
    return "%s/projects/%s/collab/cursor" % (api, project_id)


# ---------------------------------------------------------------------------
# Wire shapes (no datastore) — the frame builder, key for key
# ---------------------------------------------------------------------------


def test_cursor_frame_carries_exactly_the_five_contract_keys_and_no_id() -> None:
    frame = _cursor_frame_from_message(
        {"kind": "cursor", "userId": "u", "name": "Asha Rao", "x": 10, "y": -20, "storeyIndex": 2}
    )
    assert frame is not None
    assert frame["event"] == "cursor"
    # Not a stylistic nicety: an ``id:`` here would be echoed back as Last-Event-ID on
    # reconnect and read as an ops head, silently breaking catch-up.
    assert "id" not in frame
    data = json.loads(frame["data"])
    assert set(data) == FRAME_KEYS
    assert data["userId"] == "u"
    assert data["name"] == "Asha Rao"
    assert data["x"] == 10
    assert data["y"] == -20
    assert data["storeyIndex"] == 2


def test_cursor_frame_null_storey_round_trips_as_null() -> None:
    frame = _cursor_frame_from_message(
        {"kind": "cursor", "userId": "u", "name": "n", "x": 0, "y": 0, "storeyIndex": None}
    )
    assert frame is not None
    assert json.loads(frame["data"])["storeyIndex"] is None


def test_malformed_cursor_messages_yield_no_frame() -> None:
    """Garbage on the channel must produce silence, not a malformed frame."""
    assert _cursor_frame_from_message({"kind": "cursor"}) is None
    # A missing storeyIndex key is malformed — only an explicit null means "unbound".
    assert (
        _cursor_frame_from_message({"kind": "cursor", "userId": "u", "name": "n", "x": 0, "y": 0})
        is None
    )
    assert (
        _cursor_frame_from_message(
            {"kind": "cursor", "userId": "u", "name": "n", "x": "abc", "y": 0, "storeyIndex": None}
        )
        is None
    )
    assert (
        _cursor_frame_from_message(
            {"kind": "cursor", "userId": "u", "name": "n", "x": 0, "y": None, "storeyIndex": None}
        )
        is None
    )


# ---------------------------------------------------------------------------
# The endpoint: happy path, null storey, validation bounds
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_cursor_post_publishes_the_contract_frame_on_the_project_channel(
    client: Any, api: str, clean_redis: Any, firm_a: Any, project_a: Any
) -> None:
    """204, empty body, and EXACTLY the contract envelope on ``garh:collab:<id>``."""
    # Seed presence the way a real session would have (the SSE connect writes it), so
    # this test asserts the hot path — name from the hash, zero DB fallback involved.
    await collab.presence_join(project_a.id, firm_a.user_id, "Asha Rao")
    pubsub = await _subscribed(project_a.id)
    try:
        response = await client.post(
            _cursor_url(api, project_a.id), json=VALID_BODY, headers=firm_a.headers
        )
        assert response.status_code == 204, response.text
        assert response.content == b""

        message = await _channel_message(pubsub)
        assert set(message) == ENVELOPE_KEYS
        assert message["kind"] == "cursor"
        assert message["userId"] == str(firm_a.user_id)
        assert message["name"] == "Asha Rao"
        assert message["x"] == 1200
        assert message["y"] == -3400
        assert message["storeyIndex"] == 1
    finally:
        await pubsub.aclose()


@pytest.mark.integration
async def test_null_storey_index_is_valid_and_published_as_null(
    client: Any, api: str, clean_redis: Any, firm_a: Any, project_a: Any
) -> None:
    pubsub = await _subscribed(project_a.id)
    try:
        response = await client.post(
            _cursor_url(api, project_a.id),
            json={"x": 0, "y": 0, "storeyIndex": None},
            headers=firm_a.headers,
        )
        assert response.status_code == 204, response.text
        message = await _channel_message(pubsub)
        assert message["kind"] == "cursor"
        assert message["storeyIndex"] is None
    finally:
        await pubsub.aclose()


@pytest.mark.integration
@pytest.mark.parametrize(
    "body",
    [
        pytest.param({"x": 10_000_001, "y": 0, "storeyIndex": None}, id="x-above-ceiling"),
        pytest.param({"x": 0, "y": -10_000_001, "storeyIndex": None}, id="y-below-floor"),
        pytest.param({"x": 0, "y": 0, "storeyIndex": -1}, id="negative-storey"),
        # StrictInt (= the repo's Mm): floats and numeric strings must never reach the
        # wire as coordinates.
        pytest.param({"x": 1.5, "y": 0, "storeyIndex": None}, id="float-x"),
        pytest.param({"x": "1200", "y": 0, "storeyIndex": None}, id="string-x"),
        pytest.param({"y": 0, "storeyIndex": None}, id="missing-x"),
        # storeyIndex is required-but-nullable: with extra="ignore", required-ness is
        # the surviving typo guard, so its omission must 422 rather than default.
        pytest.param({"x": 0, "y": 0}, id="missing-storey"),
    ],
)
async def test_out_of_contract_bodies_422_and_publish_nothing(
    client: Any, api: str, clean_redis: Any, firm_a: Any, project_a: Any, body: dict[str, Any]
) -> None:
    pubsub = await _subscribed(project_a.id)
    try:
        response = await client.post(
            _cursor_url(api, project_a.id), json=body, headers=firm_a.headers
        )
        assert response.status_code == 422, response.text
        await _assert_channel_silent(pubsub)
    finally:
        await pubsub.aclose()


# ---------------------------------------------------------------------------
# Identity: stamped from ctx, never believed from the body
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_forged_identity_in_the_body_is_ignored_not_believed(
    client: Any, api: str, clean_redis: Any, firm_a: Any, project_a: Any
) -> None:
    """The negative test the contract demands: a body claiming to be someone else gets
    a 204 (the claim is inert) and the published frame carries the ctx identity. This
    is what ``extra="ignore"`` on ``CursorIn`` exists for — a 422 here would mean the
    stamping path was never exercised."""
    pubsub = await _subscribed(project_a.id)
    try:
        forged = {**VALID_BODY, "userId": str(uuid.uuid4()), "name": "Mallory"}
        response = await client.post(
            _cursor_url(api, project_a.id), json=forged, headers=firm_a.headers
        )
        assert response.status_code == 204, response.text

        message = await _channel_message(pubsub)
        assert message["kind"] == "cursor"
        assert message["userId"] == str(firm_a.user_id)
        assert message["name"] == "Asha Rao"
        assert message["name"] != "Mallory" and message["userId"] != forged["userId"]
    finally:
        await pubsub.aclose()


# ---------------------------------------------------------------------------
# Name resolution: Redis-first, one DB fallback, write-through
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_name_comes_from_the_presence_hash_before_the_database(
    client: Any, api: str, clean_redis: Any, firm_a: Any, project_a: Any
) -> None:
    """Plant a presence name the user row does NOT have: the frame must carry the
    hash's name, proving the hot path is the HGET (a DB read would say 'Asha Rao')."""
    clean_redis.hset(
        collab.presence_key(project_a.id),
        str(firm_a.user_id),
        json.dumps({"name": "Presence Hash Name", "ts": int(time.time())}),
    )
    pubsub = await _subscribed(project_a.id)
    try:
        response = await client.post(
            _cursor_url(api, project_a.id), json=VALID_BODY, headers=firm_a.headers
        )
        assert response.status_code == 204, response.text
        assert (await _channel_message(pubsub))["name"] == "Presence Hash Name"
    finally:
        await pubsub.aclose()


@pytest.mark.integration
async def test_cold_hash_falls_back_to_the_db_once_and_writes_through(
    client: Any,
    api: str,
    clean_redis: Any,
    firm_a: Any,
    project_a: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """First POST with no presence entry pays the one DB lookup and writes the name
    back into the presence hash; the second POST must be served from the hash alone —
    enforced by making the DB path raise, so a cache that quietly stopped working
    turns this test red instead of turning every cursor POST into a query."""
    pubsub = await _subscribed(project_a.id)
    try:
        first = await client.post(
            _cursor_url(api, project_a.id), json=VALID_BODY, headers=firm_a.headers
        )
        assert first.status_code == 204, first.text
        assert (await _channel_message(pubsub))["name"] == "Asha Rao"

        stored = clean_redis.hget(collab.presence_key(project_a.id), str(firm_a.user_id))
        assert stored is not None, "the DB fallback did not write through to the presence hash"
        assert json.loads(stored)["name"] == "Asha Rao"

        async def _boom(session: Any, ctx: Any) -> str:
            raise AssertionError("cursor POST hit the database despite a warm presence entry")

        monkeypatch.setattr("garh_api.routers.collab._display_name", _boom)

        second = await client.post(
            _cursor_url(api, project_a.id), json=VALID_BODY, headers=firm_a.headers
        )
        assert second.status_code == 204, second.text
        assert (await _channel_message(pubsub))["name"] == "Asha Rao"
    finally:
        await pubsub.aclose()


# ---------------------------------------------------------------------------
# The stream forwards it, end to end
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_stream_forwards_a_posted_cursor_end_to_end(
    client: Any,
    api: str,
    session: Any,
    clean_redis: Any,
    firm_a: Any,
    member_a: Any,
    project_a: Any,
) -> None:
    """The whole loop the feature is: member A posts over real HTTP, firm A's open
    stream emits ``event: cursor`` with exactly the five contract keys and no id."""
    agen = _stream(firm_a, project_a.id, name="Asha Rao")
    try:
        assert (await _next_frame(agen))["event"] == "hello"

        response = await client.post(
            _cursor_url(api, project_a.id), json=VALID_BODY, headers=member_a.headers
        )
        assert response.status_code == 204, response.text

        frame = await _frame_of_event(agen, "cursor")
        assert "id" not in frame
        data = json.loads(frame["data"])
        assert set(data) == FRAME_KEYS
        assert data["userId"] == str(member_a.user_id)
        assert data["name"] == "Rahul Verma"
        assert data["x"] == 1200
        assert data["y"] == -3400
        assert data["storeyIndex"] == 1
    finally:
        await agen.aclose()


# ---------------------------------------------------------------------------
# Tenancy
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_another_firms_ctx_gets_404_and_publishes_nothing(
    client: Any, api: str, clean_redis: Any, firm_b: Any, project_a: Any
) -> None:
    """Firm B, valid body on purpose (so tenancy is what fails, not validation): 404
    problem+json, and — the half the table-driven sweep cannot assert — nothing was
    fanned out to firm A's streams."""
    pubsub = await _subscribed(project_a.id)
    try:
        response = await client.post(
            _cursor_url(api, project_a.id), json=VALID_BODY, headers=firm_b.headers
        )
        assert response.status_code == 404, response.text
        assert problem(response)["code"] == "not_found"
        await _assert_channel_silent(pubsub)
    finally:
        await pubsub.aclose()
