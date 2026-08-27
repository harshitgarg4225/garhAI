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

``event: cursor``
    Data ``{"userId", "name", "x", "y", "storeyIndex"}`` — one collaborator's live
    cursor, plot-local integer mm. Published by ``POST /projects/:id/collab/cursor``
    below; nothing is stored, so a missed frame is simply superseded by the next
    position ~100ms later. Deliberately carries **no** ``id:`` — the browser replays
    the newest id of *any* event as ``Last-Event-ID`` on reconnect, and this stream
    reads that header as an ops head (:func:`_last_seen_head`), so an id here would
    corrupt reconnect catch-up on every cursor twitch.

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

from fastapi import APIRouter, Header, Request, Response, status
from pydantic import ConfigDict, Field, StrictInt
from pydantic.alias_generators import to_camel
from sse_starlette.sse import EventSourceResponse

from garh_api import collab, queue
from garh_api.db import session_scope
from garh_api.logging import get_logger
from garh_api.repositories import OpRepository, TenantCtx, UserRepository
from garh_api.routers import SessionDep, TenantDep, active_branch, require_project
from garh_api.routers.jobs import SSE_PING_SECONDS
from garh_api.schemas import CamelModel, Mm

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


# ---------------------------------------------------------------------------
# Live cursors: POST /projects/:id/collab/cursor → `event: cursor` fan-out
# ---------------------------------------------------------------------------

#: |x| and |y| ceiling, integer mm. 10km either side of the plot origin is far beyond
#: any plot this product will ever hold, and small enough that arithmetic on the values
#: can never surprise a consumer.
MAX_CURSOR_COORD_MM = 10_000_000


class CursorIn(CamelModel):
    """One cursor position on its way in: ``{"x", "y", "storeyIndex"}``, plot-local mm.

    The one request model in the API with ``extra="ignore"`` instead of the package's
    ``extra="forbid"`` convention, for two reasons specific to this path:

    * **A client-supplied identity must be inert, not an error.** The server stamps
      ``userId``/``name`` from the authenticated context; the contract says the client
      never supplies them. The right answer to a body that tries (``userId``/``name``
      keys, forged or well-meaning) is "your claim is discarded and the stamped truth
      is published" — proven by the forged-identity test — not a 422 that makes the
      identity guarantee rest on request validation instead of on the stamping.
    * **A 422 here is invisible.** Cursor POSTs are fire-and-forget at ~10Hz; no
      client surfaces their failures, so strictness would not produce a fixable error
      message — it would produce live cursors that silently never render, the classic
      "gate that never fires" failure. Typo protection survives where it matters: all
      three real fields are required, so a misspelled ``storeyIndex`` still 422s as a
      missing field rather than being silently defaulted.
    """

    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        extra="ignore",
        str_strip_whitespace=True,
        from_attributes=True,
        protected_namespaces=(),
    )

    x: Mm = Field(ge=-MAX_CURSOR_COORD_MM, le=MAX_CURSOR_COORD_MM)
    y: Mm = Field(ge=-MAX_CURSOR_COORD_MM, le=MAX_CURSOR_COORD_MM)
    storey_index: StrictInt | None = Field(
        ge=0, description="Which storey the cursor is on; null when not storey-bound."
    )


@router.post(
    "/projects/{project_id}/collab/cursor",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Broadcast the caller's live cursor position to collaborators",
)
async def collab_cursor(
    project_id: uuid.UUID,
    body: CursorIn,
    ctx: TenantDep,
    session: SessionDep,
) -> Response:
    """Stamp identity, publish, 204. Nothing is stored, so there is nothing to return.

    ``TenantDep`` and not ``WriterDep``: a cursor is presence, not a change to the
    design, so read access is enough — a future read-only seat should still be visible
    to the people it is watching. The tenancy check is the same ``require_project`` 404
    every project route uses, and it runs before anything is published.

    This path is DB-write-free and (on the hot path) DB-read-free beyond that ownership
    check; the display name comes from Redis (:func:`_cursor_display_name`). It is also
    deliberately NOT rate-limited: ~10Hz per user is the feature working as designed,
    not abuse, so the §11 mutation budget (60 ops/s per firm) would throttle a healthy
    six-person session into missing cursors with no error anyone sees. The existing
    sliding-window limiter would also add a Redis round trip per call — on a route
    whose entire cost after auth is one PUBLISH — to guard a path that is
    authenticated, stores nothing, and answers 204: the ceiling on what an abusive
    caller can make us do is roughly what the limiter itself would cost.
    """
    await require_project(session, ctx, project_id)
    if ctx.user_id is None:  # pragma: no cover - TenantCtx guarantees human roles carry one
        # A cursor frame without an identity is unrenderable; degrade to a no-op 204
        # rather than inventing an uncontracted anonymous frame.
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    name = await _cursor_display_name(session, ctx, project_id)
    await collab.publish_cursor(
        project_id,
        ctx.user_id,
        name,
        x=body.x,
        y=body.y,
        storey_index=body.storey_index,
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


async def _cursor_display_name(session: Any, ctx: TenantCtx, project_id: uuid.UUID) -> str:
    """Display name for a cursor frame, cheap enough to call at 10Hz per user.

    DECISION: Redis-first, database-fallback, write-through — per-request caching is
    useless here (a request's lifetime is one publish), and an in-process dict would
    go stale across replicas and deploys. The SSE connect already stores exactly this
    name in the presence hash (``presence_join``, refreshed by heartbeats), so the
    overwhelmingly common case — a user moving their mouse in a project whose stream
    they hold open — is one HGET. Only a cursor that beats its own stream to the
    server (connect race, presence expiry, Redis restart) pays the one
    :func:`_display_name` query, and the result is written back through the same
    presence entry so the next several hundred posts of that minute hit the hash
    again.

    The write-through reuses ``presence_heartbeat`` rather than a second value shape:
    same entry, same TTL discipline, no publish (from the roster's point of view
    nothing changed *yet*). It does make the user visible to the lazy pruner's roster
    for a TTL even if their stream never joined — which is honest: someone moving a
    cursor inside the project *is* present.
    """
    name = await collab.presence_name(project_id, ctx.user_id)
    if name is not None:
        return name
    name = await _display_name(session, ctx)
    await collab.presence_heartbeat(project_id, ctx.user_id, name)
    return name


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
            elif kind == "cursor":
                # Explicit, not a default branch: unknown kinds stay dropped (a future
                # schema must opt in here, where its frame shape is asserted by tests).
                frame = _cursor_frame_from_message(decoded)
                if frame is not None:
                    yield frame

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


def _cursor_frame_from_message(message: dict[str, Any]) -> dict[str, Any] | None:
    """Channel message → ``event: cursor`` frame, keeping ONLY the five contract keys.

    Two deliberate absences:

    * **No ``id:`` field.** EventSource replays the newest id of *any* event as
      ``Last-Event-ID`` on reconnect, and this stream reads that header as an ops head
      (:func:`_last_seen_head`); an id here would poison reconnect catch-up within
      ~100ms of any cursor movement.
    * **No own-cursor filtering.** Every subscriber gets every cursor, including the
      author's own echo; the client drops frames carrying its own ``userId``.
      Filtering here would cost a comparison per frame per stream and conceal nothing
      — the author already knows where their cursor is.

    Field-checked the same way :func:`_ops_frame_from_message` is: a malformed publish
    yields no frame, never a malformed frame or a dead stream. A missing
    ``storeyIndex`` key is malformed (the publisher always sends it); only an explicit
    ``null`` means "not storey-bound".
    """
    try:
        storey = message["storeyIndex"]
        data: dict[str, Any] = {
            "userId": str(message["userId"]),
            "name": str(message["name"]),
            "x": int(message["x"]),
            "y": int(message["y"]),
            "storeyIndex": int(storey) if storey is not None else None,
        }
    except (KeyError, TypeError, ValueError):
        return None
    return {
        "event": "cursor",
        "data": json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False),
    }


def _presence_frame(roster: list[dict[str, str]]) -> dict[str, Any]:
    return {
        "event": "presence",
        "data": json.dumps(
            {"users": roster}, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ),
    }


__all__ = ["collab_frames", "router"]
