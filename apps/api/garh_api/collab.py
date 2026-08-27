"""Live collaboration plumbing: the per-project Redis channel and the presence hash.

This is the API side of the §SSE collab contract the web client is built against
(``GET /projects/:id/collab/events``). The module owns three things and nothing else:

1. **The channel** — ``garh:collab:<projectId>`` (pub/sub, mirroring the shape of
   ``queue.progress_channel``). Three message kinds travel on it, discriminated by a
   ``kind`` field: ``ops`` ("the op log advanced", carrying the exact five fields the
   SSE frame will carry), ``presence`` (a contentless nudge — every connected stream
   rebuilds the roster from the hash rather than trusting a possibly-stale list baked
   into the message), and ``cursor`` (one user's live cursor position, carrying the
   exact five fields of the ``event: cursor`` frame — see :func:`publish_cursor`).

2. **The presence hash** — ``garh:presence:<projectId>``, field = user id, value =
   ``{"name": ..., "ts": epoch-seconds}``. Entries are written on connect and
   refreshed every :data:`PRESENCE_REFRESH_SECONDS` while a stream is open; anything
   older than :data:`PRESENCE_TTL_SECONDS` is pruned lazily whenever a roster is
   built, so a client that died without a clean disconnect disappears within a
   heartbeat or two instead of haunting the "who is here" list forever.

3. **The post-commit publish seam** — :func:`queue_ops_advanced` plus a pair of
   session event listeners. Every op append in the API funnels through
   ``routers.ops._append_core``, but that function runs inside the caller's session
   scope and **the commit belongs to the caller** (the request dependency, or
   ``session_scope`` in the seed). Publishing from inside the handler would race the
   reader: a client that receives "head is now 8" and refetches before the writer's
   transaction commits reads head 7, concludes it is up to date, and never learns
   about op 8. So ``_append_core`` only *registers* the notice on the session
   (``session.info``), and a SQLAlchemy ``after_commit`` listener publishes it — on
   the event loop, as a fire-and-forget task — strictly after the ops are durable.
   ``after_rollback`` discards pending notices, so a failed request advertises
   nothing (a notification for ops that never landed is golden rule 9's dishonest
   state, delivered by push).

Like ``queue.publish_progress``, every publish here is best-effort: telemetry must
never break the edit it describes. A dropped message costs a client its live nudge,
not correctness — the op log remains the truth and the client re-syncs on its next
append or reload.

No FastAPI and no repository imports, so tests can exercise the wire shapes and the
presence logic without the HTTP layer. The one SQLAlchemy import is the ORM event
hook — it *is* the seam, not a query path; this module builds no SQL.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import time
from dataclasses import dataclass
from typing import Any

from sqlalchemy import event as sa_event
from sqlalchemy.orm import Session

from garh_api import queue
from garh_api.logging import get_logger

_log = get_logger(__name__)

#: How often a connected stream refreshes its own presence entry (seconds).
PRESENCE_REFRESH_SECONDS = 20

#: A presence entry older than this is treated as gone (seconds). Three missed
#: heartbeats — generous enough for a laptop waking from sleep to survive, short
#: enough that a crashed tab leaves the roster within a minute.
PRESENCE_TTL_SECONDS = 60

#: Idle TTL on the presence hash itself, refreshed on every write. Purely hygiene:
#: without it, every project ever opened keeps an empty-ish hash in Redis forever.
PRESENCE_KEY_TTL_SECONDS = 3600

#: ``session.info`` slot holding not-yet-published :class:`OpsAdvanced` notices.
_PENDING_INFO_KEY = "garh.collab.pending_ops"

#: Strong references to in-flight publish tasks. ``loop.create_task`` alone is not
#: enough — the loop keeps only a weak reference, and a fire-and-forget task with no
#: other referent can be garbage-collected mid-publish.
_inflight_publishes: set[asyncio.Task[Any]] = set()


def collab_channel(project_id: Any) -> str:
    """Pub/sub channel for one project's collab events."""
    return "garh:collab:%s" % project_id


def presence_key(project_id: Any) -> str:
    """Redis hash of who currently has this project open."""
    return "garh:presence:%s" % project_id


# ---------------------------------------------------------------------------
# "The op log advanced"
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class OpsAdvanced:
    """One committed advance of a project's op log, as the wire will carry it.

    Everything is already a string (or ``None``) because this object exists to be
    serialised: it is built after the append, carried across the commit boundary in
    ``session.info``, and encoded for the channel. Keeping UUIDs out of it means the
    frame the SSE handler emits cannot drift from what was published.
    """

    project_id: str
    head_idx: int
    version_branch: str
    actor_id: str | None
    source: str
    group_id: str | None

    def frame_data(self) -> dict[str, Any]:
        """The ``event: ops`` data payload — EXACTLY these five keys, per the frozen
        contract. The web client is built against them; do not add or rename."""
        return {
            "headIdx": self.head_idx,
            "versionBranch": self.version_branch,
            "actorId": self.actor_id,
            "source": self.source,
            "groupId": self.group_id,
        }

    def encode(self) -> str:
        """Canonical JSON for the channel (sorted keys, no whitespace — the same
        encoding every other Redis message in this codebase uses)."""
        message = {"kind": "ops", "projectId": self.project_id, **self.frame_data()}
        return json.dumps(message, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def decode_message(raw: Any) -> dict[str, Any] | None:
    """One channel message → its dict, or ``None`` for anything unreadable.

    Unreadable messages are dropped rather than raised: a malformed publish (a future
    schema, a stray client) must not kill every open stream on the project.
    """
    try:
        text = raw.decode("utf-8") if isinstance(raw, bytes) else str(raw)
        message = json.loads(text)
    except (ValueError, UnicodeDecodeError):
        return None
    return message if isinstance(message, dict) else None


async def publish_ops_advanced(notice: OpsAdvanced) -> bool:
    """Tell every connected stream that the op log moved. Best-effort, never raises."""
    try:
        await queue.get_redis().publish(collab_channel(notice.project_id), notice.encode())
        return True
    except Exception as exc:
        _log.warning(
            "collab.ops_publish_failed",
            project_id=notice.project_id,
            head_idx=notice.head_idx,
            error="%s: %s" % (type(exc).__name__, exc),
        )
        return False


# ---------------------------------------------------------------------------
# The post-commit seam (see the module docstring for why it exists)
# ---------------------------------------------------------------------------


def queue_ops_advanced(session: Any, notice: OpsAdvanced) -> None:
    """Register a notice to be published if — and only after — this session commits.

    ``session`` may be an ``AsyncSession`` or a plain ``Session``; both expose the
    same ``info`` dict (the async class proxies it), and the ``after_commit`` /
    ``after_rollback`` listeners below read that same dict off the sync session.
    """
    session.info.setdefault(_PENDING_INFO_KEY, []).append(notice)


@sa_event.listens_for(Session, "after_commit")
def _publish_pending_after_commit(sync_session: Session) -> None:
    """Drain this session's pending notices into fire-and-forget publish tasks.

    Fires inside ``Session.commit()`` — for the API's async sessions that is a
    greenlet on the running event loop, so ``get_running_loop`` succeeds and the
    publish is scheduled to run right after the current await completes: strictly
    post-commit, never blocking the response. A session with no pending notices
    (every non-ops commit in the process) exits on the first line.

    No running loop means a synchronous script (Alembic, a worker's session) — those
    never queue notices, so there is nothing to drop; returning is exact, not lossy.
    """
    pending = sync_session.info.pop(_PENDING_INFO_KEY, None)
    if not pending:
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:  # pragma: no cover - sync sessions never carry notices
        return
    for notice in pending:
        task = loop.create_task(publish_ops_advanced(notice))
        _inflight_publishes.add(task)
        task.add_done_callback(_inflight_publishes.discard)


@sa_event.listens_for(Session, "after_rollback")
def _drop_pending_after_rollback(sync_session: Session) -> None:
    """A rolled-back append advertises nothing — the ops it described do not exist."""
    sync_session.info.pop(_PENDING_INFO_KEY, None)


# ---------------------------------------------------------------------------
# Presence
# ---------------------------------------------------------------------------


def _presence_value(name: str, *, now: int | None = None) -> str:
    payload = {"name": name, "ts": int(now if now is not None else time.time())}
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


async def presence_join(project_id: Any, user_id: Any, name: str) -> None:
    """Record that a user opened the project, and nudge every stream to re-read.

    Best-effort like every publish here: presence is a convenience layer, and a
    Redis hiccup on connect must not turn into a failed SSE stream.
    """
    try:
        client = queue.get_redis()
        key = presence_key(project_id)
        pipe = client.pipeline(transaction=False)
        pipe.hset(key, str(user_id), _presence_value(name))
        pipe.expire(key, PRESENCE_KEY_TTL_SECONDS)
        pipe.publish(collab_channel(project_id), json.dumps({"kind": "presence"}))
        await pipe.execute()
    except Exception as exc:
        _log.warning(
            "collab.presence_join_failed",
            project_id=str(project_id),
            error="%s: %s" % (type(exc).__name__, exc),
        )


async def presence_heartbeat(project_id: Any, user_id: Any, name: str) -> None:
    """Refresh our own ``ts`` so the lazy pruner keeps us. No publish: from every
    other client's point of view nothing changed, so there is nothing to say."""
    try:
        client = queue.get_redis()
        key = presence_key(project_id)
        pipe = client.pipeline(transaction=False)
        pipe.hset(key, str(user_id), _presence_value(name))
        pipe.expire(key, PRESENCE_KEY_TTL_SECONDS)
        await pipe.execute()
    except Exception as exc:
        _log.warning(
            "collab.presence_heartbeat_failed",
            project_id=str(project_id),
            error="%s: %s" % (type(exc).__name__, exc),
        )


async def presence_leave(project_id: Any, user_id: Any) -> None:
    """Remove our entry (clean disconnect) and nudge the remaining streams."""
    try:
        client = queue.get_redis()
        pipe = client.pipeline(transaction=False)
        pipe.hdel(presence_key(project_id), str(user_id))
        pipe.publish(collab_channel(project_id), json.dumps({"kind": "presence"}))
        await pipe.execute()
    except Exception as exc:
        _log.warning(
            "collab.presence_leave_failed",
            project_id=str(project_id),
            error="%s: %s" % (type(exc).__name__, exc),
        )


async def presence_users(project_id: Any, *, now: int | None = None) -> list[dict[str, str]]:
    """The current roster, pruning expired entries as a side effect.

    Returns ``[{"userId": ..., "name": ...}, ...]`` — the exact objects the ``hello``
    and ``presence`` frames carry — sorted by name (then id) so two streams building
    the roster at the same moment emit byte-identical lists.

    Pruning here, lazily, is what makes expiry work with no reaper process: rosters
    are only ever *seen* through this function, so an entry nobody refreshes stops
    being visible the first time anyone looks, and its field is deleted so the hash
    cannot grow without bound.
    """
    try:
        client = queue.get_redis()
        entries = await client.hgetall(presence_key(project_id))
    except Exception as exc:
        _log.warning(
            "collab.presence_read_failed",
            project_id=str(project_id),
            error="%s: %s" % (type(exc).__name__, exc),
        )
        return []

    floor = int(now if now is not None else time.time()) - PRESENCE_TTL_SECONDS
    users: list[dict[str, str]] = []
    stale: list[str] = []
    for user_id, raw in entries.items():
        field = user_id.decode("utf-8") if isinstance(user_id, bytes) else str(user_id)
        try:
            text = raw.decode("utf-8") if isinstance(raw, bytes) else str(raw)
            record = json.loads(text)
            ts = int(record.get("ts") or 0)
            name = str(record.get("name") or "")
        except (ValueError, TypeError, AttributeError, UnicodeDecodeError):
            stale.append(field)  # unreadable = unowned; prune rather than render garbage
            continue
        if ts < floor:
            stale.append(field)
            continue
        users.append({"userId": field, "name": name})

    if stale:
        with contextlib.suppress(Exception):
            await client.hdel(presence_key(project_id), *stale)

    users.sort(key=lambda user: (user["name"].casefold(), user["userId"]))
    return users


async def presence_name(project_id: Any, user_id: Any) -> str | None:
    """The display name already stored in this user's presence entry, if any.

    This is the cursor endpoint's cheap name source: the SSE connect wrote the same
    name into the hash (:func:`presence_join`), so on the hot path — a user moving
    their mouse in a project whose stream they hold open — resolving the name is one
    HGET and no Postgres round trip, which is what makes a 10Hz POST affordable.

    Freshness (``ts``) is deliberately ignored: a stale-but-present entry still names
    the right person, and expiry is the *roster's* concern, enforced where rosters are
    built (:func:`presence_users`). Returns ``None`` for a missing entry, an unreadable
    one, or any Redis failure — the caller falls back to the database and writes
    through.
    """
    try:
        raw = await queue.get_redis().hget(presence_key(project_id), str(user_id))
    except Exception:
        return None
    if raw is None:
        return None
    try:
        text = raw.decode("utf-8") if isinstance(raw, bytes) else str(raw)
        record = json.loads(text)
    except (ValueError, UnicodeDecodeError):
        return None
    if not isinstance(record, dict):
        return None
    name = record.get("name")
    return name if isinstance(name, str) and name else None


# ---------------------------------------------------------------------------
# Live cursors
# ---------------------------------------------------------------------------


async def publish_cursor(
    project_id: Any,
    user_id: Any,
    name: str,
    *,
    x: int,
    y: int,
    storey_index: int | None,
) -> bool:
    """Fan one user's cursor position out to every open stream on the project.

    ``kind: "cursor"`` is the third message kind on the channel. Identity travels *in*
    the message, stamped by the endpoint from the authenticated context — the client
    never supplies it — so the SSE handler forwards without a lookup of its own, and
    the frame it emits cannot drift from what was published here.

    Deliberately bypasses the post-commit seam that ``ops`` notices ride: a cursor
    writes nothing, so there is no transaction whose visibility a subscriber could
    race. And unlike ``ops``, a lost message here costs nothing at all — the next
    position, ~100ms away at the client's send rate, supersedes it entirely.
    Best-effort like every publish in this module; never raises.
    """
    message = {
        "kind": "cursor",
        "userId": str(user_id),
        "name": name,
        "x": int(x),
        "y": int(y),
        "storeyIndex": int(storey_index) if storey_index is not None else None,
    }
    try:
        await queue.get_redis().publish(
            collab_channel(project_id),
            json.dumps(message, sort_keys=True, separators=(",", ":"), ensure_ascii=False),
        )
        return True
    except Exception as exc:
        _log.warning(
            "collab.cursor_publish_failed",
            project_id=str(project_id),
            error="%s: %s" % (type(exc).__name__, exc),
        )
        return False


__all__ = [
    "PRESENCE_KEY_TTL_SECONDS",
    "PRESENCE_REFRESH_SECONDS",
    "PRESENCE_TTL_SECONDS",
    "OpsAdvanced",
    "collab_channel",
    "decode_message",
    "presence_heartbeat",
    "presence_join",
    "presence_key",
    "presence_leave",
    "presence_name",
    "presence_users",
    "publish_cursor",
    "publish_ops_advanced",
    "queue_ops_advanced",
]
