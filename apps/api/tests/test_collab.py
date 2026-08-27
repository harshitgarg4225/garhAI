"""Live collaboration: the SSE channel, the presence hash, and the post-commit seam.

Three layers, tested at the layer where each claim actually lives:

* **Wire shapes** — the frame payloads are a FROZEN contract the web client is being
  built against in parallel, so the tests assert exact key sets, not "contains".
* **Redis behaviour** — publish lands on ``garh:collab:<projectId>``; presence
  entries join, expire lazily, and leave. Real Redis, per the suite's no-mocks rule.
* **The seam** — ops publish strictly AFTER the appending transaction commits, and a
  rolled-back append publishes nothing. This is the negative test CLAUDE.md demands:
  a notification racing the reader's refetch is exactly the kind of quietly-broken
  gate this repo has been bitten by, so the "before commit: silence" half is asserted
  as hard as the "after commit: one message" half.

The stream itself is proven end to end by driving the frame generator directly while
real HTTP appends land through the app: httpx's ASGI transport runs a response to
completion before returning it, and an SSE response never completes, so iterating
``collab_frames`` *is* the honest way to test streaming under this harness.
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from typing import Any

import pytest
from garh_api import collab, queue
from garh_api.routers.collab import (
    _display_name,
    _last_seen_head,
    _ops_frame_from_message,
    collab_frames,
)

from tests.helpers import main_branch, op_payload, problem

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _OpenRequest:
    """A Request stand-in for driving the generator: the client never disconnects."""

    async def is_disconnected(self) -> bool:
        return False


async def _subscribed(project_id: Any) -> Any:
    """A raw pub/sub subscription on the project's collab channel."""
    pubsub = queue.get_redis().pubsub(ignore_subscribe_messages=True)
    await pubsub.subscribe(collab.collab_channel(project_id))
    return pubsub


async def _channel_message(pubsub: Any, *, within: float = 10.0) -> dict[str, Any]:
    """The next real message on the channel, decoded. Fails loudly on silence."""
    deadline = time.monotonic() + within
    while time.monotonic() < deadline:
        message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=0.2)
        if message is not None and message.get("type") == "message":
            decoded = collab.decode_message(message.get("data"))
            assert decoded is not None, message
            return decoded
    raise AssertionError("no message arrived on the collab channel within %.1fs" % within)


async def _assert_channel_silent(pubsub: Any, *, seconds: float = 1.0) -> None:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=0.2)
        assert message is None or message.get("type") != "message", (
            "expected silence on the collab channel, got %r" % (message,)
        )


async def _next_frame(agen: Any, *, within: float = 10.0) -> dict[str, Any]:
    return await asyncio.wait_for(agen.__anext__(), within)


async def _frame_of_event(agen: Any, event: str, *, within: float = 10.0) -> dict[str, Any]:
    """Read frames (skipping other events) until one of the wanted kind arrives."""
    deadline = time.monotonic() + within
    while True:
        remaining = deadline - time.monotonic()
        assert remaining > 0, "no %r frame arrived within %.1fs" % (event, within)
        frame = await asyncio.wait_for(agen.__anext__(), remaining)
        if frame.get("event") == event:
            return frame


def _stream(actor: Any, project_id: uuid.UUID, *, name: str, last_event_id: str | None = None):
    return collab_frames(
        _OpenRequest(),  # type: ignore[arg-type]
        ctx=actor.ctx(),
        project_id=project_id,
        branch=main_branch(project_id),
        name=name,
        last_event_id=last_event_id,
    )


# ---------------------------------------------------------------------------
# Wire shapes (no datastore) — the frozen contract, key for key
# ---------------------------------------------------------------------------


def test_ops_frame_data_carries_exactly_the_five_contract_keys() -> None:
    """The web client is built against these names. Adding or renaming one is a
    breaking change to a parallel-built consumer, so the assertion is ``==``."""
    notice = collab.OpsAdvanced(
        project_id="p",
        head_idx=42,
        version_branch="b",
        actor_id="u",
        source="copilot",
        group_id="g",
    )
    data = notice.frame_data()
    assert set(data) == {"headIdx", "versionBranch", "actorId", "source", "groupId"}
    assert data["headIdx"] == 42
    assert data["versionBranch"] == "b"
    assert data["actorId"] == "u"
    assert data["source"] == "copilot"
    assert data["groupId"] == "g"


def test_channel_and_presence_key_shapes() -> None:
    project_id = uuid.uuid4()
    assert collab.collab_channel(project_id) == "garh:collab:%s" % project_id
    assert collab.presence_key(project_id) == "garh:presence:%s" % project_id


def test_channel_envelope_round_trips_into_an_sse_frame() -> None:
    """encode → decode → frame keeps only the contract keys and sets ``id`` = head."""
    notice = collab.OpsAdvanced(
        project_id="proj",
        head_idx=9,
        version_branch="branch",
        actor_id=None,
        source="solver",
        group_id=None,
    )
    message = collab.decode_message(notice.encode())
    assert message is not None
    assert message["kind"] == "ops"
    assert message["projectId"] == "proj"

    frame = _ops_frame_from_message(message)
    assert frame is not None
    assert frame["event"] == "ops"
    assert frame["id"] == "9"
    data = json.loads(frame["data"])
    assert set(data) == {"headIdx", "versionBranch", "actorId", "source", "groupId"}
    assert data["actorId"] is None and data["groupId"] is None


def test_unreadable_messages_and_incomplete_envelopes_are_dropped() -> None:
    """The gate must be able to go red: garbage on the channel yields no frame,
    rather than a malformed frame or a dead stream."""
    assert collab.decode_message(None) is None
    assert collab.decode_message("not json") is None
    assert collab.decode_message(b"\xff\xfe") is None
    assert collab.decode_message('["a","list"]') is None
    # An ops envelope missing a required field produces no frame at all.
    assert _ops_frame_from_message({"kind": "ops", "source": "manual"}) is None


def test_last_seen_head_parsing() -> None:
    assert _last_seen_head(None) is None
    assert _last_seen_head("41") == 41
    assert _last_seen_head(" -1 ") == -1
    # Unreadable = assume far behind: the safe direction costs one redundant pull.
    assert _last_seen_head("not-a-number") < -1


# ---------------------------------------------------------------------------
# Redis behaviour
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_publish_ops_advanced_lands_on_the_project_channel(clean_redis: Any) -> None:
    project_id = uuid.uuid4()
    pubsub = await _subscribed(project_id)
    try:
        notice = collab.OpsAdvanced(
            project_id=str(project_id),
            head_idx=3,
            version_branch=str(main_branch(project_id)),
            actor_id="actor",
            source="manual",
            group_id="group",
        )
        assert await collab.publish_ops_advanced(notice) is True
        message = await _channel_message(pubsub)
        assert message["kind"] == "ops"
        assert message["projectId"] == str(project_id)
        assert message["headIdx"] == 3
        assert message["source"] == "manual"
    finally:
        await pubsub.aclose()


@pytest.mark.integration
async def test_presence_join_prune_and_leave(clean_redis: Any) -> None:
    """The whole presence lifecycle against a real hash — including the lazy pruner
    actually deleting the expired field, not just hiding it."""
    project_id = uuid.uuid4()
    live_id, dead_id = str(uuid.uuid4()), str(uuid.uuid4())

    await collab.presence_join(project_id, live_id, "Asha Rao")
    # A client that died 2 minutes ago: written raw, exactly as a stale entry looks.
    clean_redis.hset(
        collab.presence_key(project_id),
        dead_id,
        json.dumps({"name": "Ghost", "ts": int(time.time()) - 120}),
    )

    users = await collab.presence_users(project_id)
    assert users == [{"userId": live_id, "name": "Asha Rao"}]
    for user in users:
        assert set(user) == {"userId", "name"}
    # Pruned means DELETED, so the hash cannot grow without bound.
    assert clean_redis.hget(collab.presence_key(project_id), dead_id) is None

    await collab.presence_leave(project_id, live_id)
    assert await collab.presence_users(project_id) == []


@pytest.mark.integration
async def test_presence_join_and_leave_publish_a_nudge(clean_redis: Any) -> None:
    """Joins/leaves must wake the other streams — a roster nobody is told to re-read
    is a presence feature that silently never fires."""
    project_id = uuid.uuid4()
    pubsub = await _subscribed(project_id)
    try:
        await collab.presence_join(project_id, str(uuid.uuid4()), "Asha Rao")
        assert (await _channel_message(pubsub))["kind"] == "presence"
        await collab.presence_leave(project_id, str(uuid.uuid4()))
        assert (await _channel_message(pubsub))["kind"] == "presence"
    finally:
        await pubsub.aclose()


# ---------------------------------------------------------------------------
# The post-commit seam — publish after durability, never before (and never on
# rollback). This is the negative test for the gate.
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_append_publishes_after_commit_and_not_before(
    session: Any, clean_redis: Any, firm_a: Any, project_a: Any
) -> None:
    from garh_api.routers.ops import dispatch_ops
    from garh_api.schemas.ops import OpIn

    pubsub = await _subscribed(project_a.id)
    try:
        result = await dispatch_ops(
            session,
            firm_a.ctx(),
            project_a.id,
            [OpIn(type="plot.set_north", payload={"deg": 90})],
            source="manual",
        )
        # The append has flushed but NOT committed: a notification now would tell
        # clients to refetch ops that no other transaction can see yet.
        await _assert_channel_silent(pubsub)

        await session.commit()
        message = await _channel_message(pubsub)
        assert message["kind"] == "ops"
        assert message["projectId"] == str(project_a.id)
        assert message["headIdx"] == result.head_idx == 0
        assert message["versionBranch"] == str(main_branch(project_a.id))
        assert message["actorId"] == str(firm_a.user_id)
        assert message["source"] == "manual"
        assert message["groupId"] is None
    finally:
        await pubsub.aclose()


@pytest.mark.integration
async def test_rolled_back_append_publishes_nothing(
    session: Any, clean_redis: Any, firm_a: Any, project_a: Any
) -> None:
    """Ops that never became durable must never be advertised — not even on the
    session's NEXT commit, which is where a leaked pending notice would surface."""
    from garh_api.routers.ops import dispatch_ops
    from garh_api.schemas.ops import OpIn

    pubsub = await _subscribed(project_a.id)
    try:
        await dispatch_ops(
            session,
            firm_a.ctx(),
            project_a.id,
            [OpIn(type="plot.set_north", payload={"deg": 45})],
            source="manual",
        )
        await session.rollback()
        await session.commit()
        await _assert_channel_silent(pubsub)
    finally:
        await pubsub.aclose()


@pytest.mark.integration
async def test_seed_source_appends_do_not_publish(
    session: Any, clean_redis: Any, firm_a: Any, project_a: Any
) -> None:
    """``source="system"`` is the seed's provenance; nobody is connected during a
    seed, and a reseed must not spray frames. The skip is at the seam on purpose —
    solver/copilot sources MUST publish and are asserted elsewhere in this file."""
    from garh_api.routers.ops import dispatch_ops
    from garh_api.schemas.ops import OpIn

    pubsub = await _subscribed(project_a.id)
    try:
        await dispatch_ops(
            session,
            firm_a.ctx(),
            project_a.id,
            [OpIn(type="plot.set_north", payload={"deg": 180})],
            source="system",
        )
        await session.commit()
        await _assert_channel_silent(pubsub)
    finally:
        await pubsub.aclose()


@pytest.mark.integration
async def test_copilot_source_appends_publish_with_their_provenance(
    session: Any, clean_redis: Any, firm_a: Any, project_a: Any
) -> None:
    """A copilot apply is exactly the "someone else changed the plan" moment the
    stream exists for; the frame must say so, with the group for undo affinity."""
    from garh_api.routers.ops import dispatch_ops
    from garh_api.schemas.ops import OpIn

    group_id = uuid.uuid4()
    pubsub = await _subscribed(project_a.id)
    try:
        await dispatch_ops(
            session,
            firm_a.ctx(),
            project_a.id,
            [OpIn(type="plot.set_north", payload={"deg": 270})],
            source="copilot",
            group_id=group_id,
        )
        await session.commit()
        message = await _channel_message(pubsub)
        assert message["source"] == "copilot"
        assert message["groupId"] == str(group_id)
    finally:
        await pubsub.aclose()


@pytest.mark.integration
async def test_post_ops_route_publishes_through_the_request_session(
    client: Any, api: str, session: Any, clean_redis: Any, firm_a: Any, project_a: Any
) -> None:
    """The real HTTP path: request session, dependency-teardown commit, greenlet
    ``after_commit`` — the exact machinery production runs on."""
    pubsub = await _subscribed(project_a.id)
    try:
        response = await client.post(
            "%s/projects/%s/ops" % (api, project_a.id),
            json={"ops": [op_payload("plot.set_north", deg=90)], "baseIdx": -1},
            headers=firm_a.headers,
        )
        assert response.status_code == 200, response.text
        message = await _channel_message(pubsub)
        assert message["kind"] == "ops"
        assert message["headIdx"] == response.json()["headIdx"] == 0
        assert message["actorId"] == str(firm_a.user_id)
        assert message["source"] == "manual"
    finally:
        await pubsub.aclose()


# ---------------------------------------------------------------------------
# The stream, end to end: hello → live ops → presence → clean leave
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_stream_hello_then_live_ops_frame_end_to_end(
    client: Any, api: str, session: Any, clean_redis: Any, firm_a: Any, project_a: Any
) -> None:
    agen = _stream(firm_a, project_a.id, name="Asha Rao")
    try:
        hello = await _next_frame(agen)
        assert hello["event"] == "hello"
        data = json.loads(hello["data"])
        assert set(data) == {"headIdx", "presence"}
        assert data["headIdx"] == -1  # empty branch
        assert data["presence"] == [{"userId": str(firm_a.user_id), "name": "Asha Rao"}]

        # An edit lands through the real route while the stream is open.
        response = await client.post(
            "%s/projects/%s/ops" % (api, project_a.id),
            json={"ops": [op_payload("plot.set_north", deg=90)], "baseIdx": -1},
            headers=firm_a.headers,
        )
        assert response.status_code == 200, response.text

        ops_frame = await _frame_of_event(agen, "ops")
        assert ops_frame["id"] == "0"
        payload = json.loads(ops_frame["data"])
        assert set(payload) == {"headIdx", "versionBranch", "actorId", "source", "groupId"}
        assert payload["headIdx"] == 0
        assert payload["versionBranch"] == str(main_branch(project_a.id))
        assert payload["actorId"] == str(firm_a.user_id)
        assert payload["source"] == "manual"
        assert payload["groupId"] is None
    finally:
        await agen.aclose()

    # The finally block left presence: the roster is empty once the stream closes.
    assert await collab.presence_users(project_a.id) == []


@pytest.mark.integration
async def test_reconnect_behind_head_gets_one_catch_up_ops_frame(
    session: Any, clean_redis: Any, firm_a: Any, project_a: Any
) -> None:
    """``Last-Event-ID`` below the current head → hello, then a synthetic ops frame
    (server-authored: source ``system``, no actor) so the client's ops handler pulls."""
    from garh_api.repositories.domain import NewOp

    from tests import factories

    await factories.append_ops(
        session, firm_a, project_a.id, [NewOp(type="plot.set_north", payload={"deg": 90})]
    )

    agen = _stream(firm_a, project_a.id, name="Asha Rao", last_event_id="-1")
    try:
        hello = await _next_frame(agen)
        assert json.loads(hello["data"])["headIdx"] == 0

        catch_up = await _next_frame(agen)
        assert catch_up["event"] == "ops"
        assert catch_up["id"] == "0"
        payload = json.loads(catch_up["data"])
        assert set(payload) == {"headIdx", "versionBranch", "actorId", "source", "groupId"}
        assert payload["headIdx"] == 0
        assert payload["source"] == "system"
        assert payload["actorId"] is None
    finally:
        await agen.aclose()


@pytest.mark.integration
async def test_reconnect_at_head_gets_no_catch_up_frame(
    session: Any, clean_redis: Any, firm_a: Any, project_a: Any
) -> None:
    """The other half of the reconnect contract: a client already at head must not
    be told to pull — an unconditional catch-up frame could never fail this test."""
    from garh_api.repositories.domain import NewOp

    from tests import factories

    await factories.append_ops(
        session, firm_a, project_a.id, [NewOp(type="plot.set_north", payload={"deg": 90})]
    )

    agen = _stream(firm_a, project_a.id, name="Asha Rao", last_event_id="0")
    try:
        hello = await _next_frame(agen)
        assert hello["event"] == "hello"
        # Nothing but presence echoes may follow; give the stream a real window in
        # which a wrong catch-up frame would have arrived.
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline:
            try:
                frame = await asyncio.wait_for(agen.__anext__(), deadline - time.monotonic())
            except TimeoutError:
                break
            assert frame["event"] != "ops", frame
    finally:
        await agen.aclose()


@pytest.mark.integration
async def test_presence_frames_track_a_second_user_joining_and_leaving(
    session: Any, clean_redis: Any, firm_a: Any, member_a: Any, project_a: Any
) -> None:
    asha = {"userId": str(firm_a.user_id), "name": "Asha Rao"}
    rahul = {"userId": str(member_a.user_id), "name": "Rahul Verma"}

    stream_a = _stream(firm_a, project_a.id, name="Asha Rao")
    try:
        assert (await _next_frame(stream_a))["event"] == "hello"

        stream_b = _stream(member_a, project_a.id, name="Rahul Verma")
        try:
            hello_b = await _next_frame(stream_b)
            assert json.loads(hello_b["data"])["presence"] == [asha, rahul]

            # Asha's stream learns Rahul arrived.
            deadline = time.monotonic() + 10.0
            while True:
                frame = await _frame_of_event(
                    stream_a, "presence", within=deadline - time.monotonic()
                )
                users = json.loads(frame["data"])["users"]
                if users == [asha, rahul]:
                    break
        finally:
            await stream_b.aclose()

        # ... and learns he left, via the disconnect path's presence_leave.
        deadline = time.monotonic() + 10.0
        while True:
            frame = await _frame_of_event(stream_a, "presence", within=deadline - time.monotonic())
            users = json.loads(frame["data"])["users"]
            if users == [asha]:
                break
    finally:
        await stream_a.aclose()


# ---------------------------------------------------------------------------
# Display names and tenancy
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_display_name_comes_from_the_user_row(session: Any, firm_a: Any) -> None:
    assert await _display_name(session, firm_a.ctx()) == "Asha Rao"


@pytest.mark.integration
async def test_display_name_survives_a_missing_user_row(session: Any, firm_a: Any) -> None:
    """A token can outlive its seat; the roster then shows a truthful generic label
    rather than the connect failing."""
    from garh_api.tenancy import TenantCtx

    ghost = TenantCtx(firm_id=firm_a.firm_id, user_id=uuid.uuid4(), role="member")
    assert await _display_name(session, ghost) == "Member"


@pytest.mark.integration
async def test_another_firms_ctx_gets_404_before_any_frame(
    client: Any, api: str, firm_b: Any, project_a: Any
) -> None:
    """Same guarantee as every project route (§13): firm B asking for firm A's
    stream is told the project does not exist — the check runs before the SSE
    response is built, so nothing ever streams. (The table-driven sweep in
    ``test_cross_tenant.py`` covers this route too; this is the readable copy.)"""
    response = await client.get(
        "%s/projects/%s/collab/events" % (api, project_a.id), headers=firm_b.headers
    )
    assert response.status_code == 404, response.text
    assert problem(response)["code"] == "not_found"
