"""Live collaboration: ``GET /projects/:id/collab/events`` (SSE).

One stream per open project tab. It tells a client exactly two things, and nothing
about geometry: **the op log advanced** (go pull ``/ops?since=`` if you care) and
**who is here**. The wire contract is frozen — the web client is built against it in
parallel — so the frame shapes below are asserted key-for-key in ``tests/test_collab.py``:

``event: hello``
    First frame on every connect. Data ``{"headIdx": <int>, "presence": [{"userId",
    "name"}, ...]}`` — the current head of the active branch and the roster including
    the connecting user.

``event: ops`` (``id:`` = the new head index)
    Data ``{"headIdx", "versionBranch", "actorId", "source", "groupId"}``. Emitted
    when ops land anywhere in the project — the copilot and solver applies are
    exactly the "someone else changed the plan" moments this stream exists for. The
    client treats it as "maybe pull", never as the ops themselves: shipping op
    payloads over a fan-out channel would be a second sync protocol to keep honest.

``event: presence``
    Data ``{"users": [{"userId", "name"}, ...]}`` — the whole roster, re-read from
    Redis on every change (join/leave/expiry). Whole-list replacement, so a missed
    frame self-heals on the next one.

Architecture mirrors the job streams (``routers/jobs.py``): tenancy-check and read
the initial state in a short ``session_scope`` — never holding a pooled connection
for the life of the stream — then fan out from a Redis pub/sub channel
(``garh:collab:<projectId>``, see :mod:`garh_api.collab`). The one deliberate
difference: this channel has **no replay backlog**. A progress stream must not lose
"Placing staircase…", but a collab nudge is derivable state — ``hello`` already
carries ``headIdx``, so a reconnecting client learns everything a backlog would have
told it. The subscribe-before-read ordering below is what makes that airtight.

``Last-Event-ID`` on reconnect: the last ``ops`` id the client saw is its head. If
it is behind the current head, one synthetic ``ops`` frame (``source: "system"``,
no actor) follows ``hello``, so a client whose reconnect logic only reacts to
``ops`` frames still catches up without special-casing ``hello``.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import time
import uuid
from collections.abc import AsyncIterator
from typing import Any

from fastapi import APIRouter, Header, Request
from sse_starlette.sse import EventSourceResponse

from garh_api import collab, queue
from garh_api.db import session_scope
from garh_api.logging import get_logger
from garh_api.repositories import OpRepository, TenantCtx, UserRepository
from garh_api.routers import TenantDep, active_branch, require_project
from garh_api.routers.jobs import SSE_PING_SECONDS

_log = get_logger(__name__)

router = APIRouter(tags=["collab"])

#: How long one pub/sub poll blocks. Short enough that disconnects and heartbeat
#: deadlines are noticed promptly; the SSE ping (15s) covers proxy idle timeouts.
_POLL_TIMEOUT_SECONDS = 1.0


@router.get(
    "/projects/{project_id}/collab/events",
    summary="Live collaboration events for a project (SSE)",
    responses={200: {"content": {"text/event-stream": {}}}},
)
async def collab_events(
    project_id: uuid.UUID,
    request: Request,
    ctx: TenantDep,
    last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
) -> EventSourceResponse:
    """Open the stream. Another firm's project id 404s here, before any frame."""
    async with session_scope() as session:
        await require_project(session, ctx, project_id)
        branch = await active_branch(session, ctx, project_id)
        name = await _display_name(session, ctx)
    return EventSourceResponse(
        collab_frames(
            request,
            ctx=ctx,
            project_id=project_id,
            branch=branch,
            name=name,
            last_event_id=last_event_id,
        ),
        ping=SSE_PING_SECONDS,
        headers={
            "cache-control": "no-store",
            # Same opt-out as the job streams: nginx would otherwise buffer the
            # stream into one burst at the end.
            "x-accel-buffering": "no",
        },
    )


async def _display_name(session: Any, ctx: TenantCtx) -> str:
    """What the roster calls this user: their profile name, else the email localpart.

    The repository refuses blank names, so the fallback chain is short; a context
    whose user row has vanished (token outliving a removed seat) still gets a
    truthful generic label rather than a crash on connect.
    """
    if ctx.user_id is None:
        return "Member"
    user = await UserRepository(session, ctx).get(ctx.user_id)
    if user is None:
        return "Member"
    name = (user.name or "").strip()
    if name:
        return name
    return user.email.split("@", 1)[0]


def _last_seen_head(raw: str | None) -> int | None:
    """``Last-Event-ID`` → the head the client believes in. ``None`` = fresh connect.

    Unreadable values are treated as "far behind": the cost of over-replaying is one
    redundant ``ops`` frame (the client pulls and finds nothing new); the cost of
    under-replaying is a client that stays stale until someone else edits.
    """
    if raw is None:
        return None
    try:
        return int(raw.strip())
    except ValueError:
        return -(2**31)


async def collab_frames(
    request: Request,
    *,
    ctx: TenantCtx,
    project_id: uuid.UUID,
    branch: uuid.UUID,
    name: str,
    last_event_id: str | None,
) -> AsyncIterator[dict[str, Any]]:
    """The frame generator, module-level so tests can drive it without HTTP streaming
    (httpx's ASGI transport buffers a response to completion, and this response never
    completes — the suite iterates these frames directly instead)."""
    user_id = ctx.user_id
    client = queue.get_redis()
    pubsub = client.pubsub(ignore_subscribe_messages=True)
    joined = False
    try:
        # Subscribe FIRST, then read the head in a fresh, short session. An append
        # that commits in between is covered from both sides: committed before the
        # read → inside hello's headIdx; committed after → its publish lands on the
        # already-open subscription. Reusing the route's earlier head read instead
        # would leave a gap in which a notification is silently lost — and with no
        # backlog on this channel, lost means lost until the next edit.
        await pubsub.subscribe(collab.collab_channel(project_id))
        async with session_scope() as session:
            head_idx = await OpRepository(session, ctx).head_idx(project_id, branch)

        if user_id is not None:
            await collab.presence_join(project_id, user_id, name)
            joined = True
        roster = await collab.presence_users(project_id)

        yield {
            "event": "hello",
            "data": json.dumps(
                {"headIdx": head_idx, "presence": roster},
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
            ),
        }

        last_seen = _last_seen_head(last_event_id)
        if last_seen is not None and last_seen < head_idx:
            # Reconnect catch-up: one synthetic frame so "maybe pull" fires through
            # the client's ordinary ops handler. Server-authored, hence system/no-actor.
            yield _ops_frame(
                collab.OpsAdvanced(
                    project_id=str(project_id),
                    head_idx=head_idx,
                    version_branch=str(branch),
                    actor_id=None,
                    source="system",
                    group_id=None,
                )
            )

        last_beat = time.monotonic()
        while True:
            message = await pubsub.get_message(
                ignore_subscribe_messages=True, timeout=_POLL_TIMEOUT_SECONDS
            )

            if joined and time.monotonic() - last_beat >= collab.PRESENCE_REFRESH_SECONDS:
                await collab.presence_heartbeat(project_id, user_id, name)
                last_beat = time.monotonic()
                # Expiry has no publisher — the dead client is in no position to
                # announce itself — so each stream re-checks the roster on its own
                # heartbeat cadence and reports a change it is first to notice.
                fresh = await collab.presence_users(project_id)
                if fresh != roster:
                    roster = fresh
                    yield _presence_frame(roster)

            if message is None:
                if await request.is_disconnected():
                    break
                continue

            decoded = collab.decode_message(message.get("data"))
            if decoded is None:
                continue
            kind = decoded.get("kind")
            if kind == "ops":
                frame = _ops_frame_from_message(decoded)
                if frame is not None:
                    yield frame
            elif kind == "presence":
                roster = await collab.presence_users(project_id)
                yield _presence_frame(roster)

    except asyncio.CancelledError:  # pragma: no cover - client went away mid-yield
        raise
    except Exception as exc:
        # End quietly rather than invent an uncontracted frame: EventSource
        # reconnects on close, and the fresh hello re-syncs everything.
        _log.warning(
            "collab.stream_failed",
            project_id=str(project_id),
            error="%s: %s" % (type(exc).__name__, exc),
        )
    finally:
        with contextlib.suppress(Exception):
            await pubsub.aclose()
        if joined:
            await collab.presence_leave(project_id, user_id)


def _ops_frame(notice: collab.OpsAdvanced) -> dict[str, Any]:
    return {
        "id": str(notice.head_idx),
        "event": "ops",
        "data": json.dumps(
            notice.frame_data(), sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ),
    }


def _ops_frame_from_message(message: dict[str, Any]) -> dict[str, Any] | None:
    """Channel message → SSE frame, keeping ONLY the five contract keys.

    The channel envelope carries ``kind`` and ``projectId`` for routing; leaking
    them into the frame would silently widen the frozen contract.
    """
    try:
        notice = collab.OpsAdvanced(
            project_id=str(message.get("projectId") or ""),
            head_idx=int(message["headIdx"]),
            version_branch=str(message["versionBranch"]),
            actor_id=(str(message["actorId"]) if message.get("actorId") is not None else None),
            source=str(message["source"]),
            group_id=(str(message["groupId"]) if message.get("groupId") is not None else None),
        )
    except (KeyError, TypeError, ValueError):
        return None
    return _ops_frame(notice)


def _presence_frame(roster: list[dict[str, str]]) -> dict[str, Any]:
    return {
        "event": "presence",
        "data": json.dumps(
            {"users": roster}, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ),
    }


__all__ = ["collab_frames", "router"]
