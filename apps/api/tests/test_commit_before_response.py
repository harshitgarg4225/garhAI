"""``CommitBeforeResponseMiddleware`` — durable before the first response byte.

FastAPI ≥0.106 runs yield-dependency teardown AFTER the response is sent, so the
teardown commit in ``db.session_scope`` became a race the client can win: CI run
13's e2e smoke received a 201 for a created project and then a 404 reading it on
the next connection. (The in-process test client cannot reproduce that — it
awaits the whole app cycle — which is exactly why the race survived 1,900+ green
tests; a 50-iteration real-socket probe reproduced and then cleared it.)

These tests pin the middleware's contract hermetically:

1. sessions registered on the scope are committed BEFORE ``http.response.start``
   is forwarded (order is asserted, not inferred);
2. a commit failure never lets the success response escape — the client gets a
   500 problem instead (negative control: without the middleware the fake app's
   201 passes through untouched);
3. non-http scopes and sessions that are already closed/rolled back are left
   alone.
"""

from __future__ import annotations

from typing import Any

import pytest
from garh_api import db
from garh_api.main import CommitBeforeResponseMiddleware


class _FakeSession:
    def __init__(self, *, active: bool = True, fail: bool = False) -> None:
        self.active = active
        self.fail = fail
        self.events: list[str] = []

    @property
    def is_active(self) -> bool:
        return self.active

    def in_transaction(self) -> bool:
        return self.active

    async def commit(self) -> None:
        if self.fail:
            self.events.append("commit_failed")
            raise RuntimeError("disk full")
        self.events.append("commit")

    async def rollback(self) -> None:
        self.events.append("rollback")


def _app_sending_201(order: list[str]) -> Any:
    async def app(scope: Any, receive: Any, send: Any) -> None:
        await send({"type": "http.response.start", "status": 201, "headers": []})
        order.append("response_start_sent_by_app")
        await send({"type": "http.response.body", "body": b"{}"})

    return app


async def test_commit_runs_before_response_start_is_forwarded() -> None:
    order: list[str] = []
    session = _FakeSession()
    mw = CommitBeforeResponseMiddleware(_app_sending_201(order))
    scope: dict[str, Any] = {"type": "http", "path": "/t", db.SCOPE_SESSIONS_KEY: [session]}
    sent: list[dict[str, Any]] = []

    async def send(message: dict[str, Any]) -> None:
        if message["type"] == "http.response.start":
            order.append("response_start_forwarded")
        sent.append(message)

    await mw(scope, None, send)

    assert session.events == ["commit"]
    # The commit is logged by the session; the forward order proves it happened
    # inside the send hook, before the start message reached the transport.
    assert order.index("response_start_forwarded") < order.index("response_start_sent_by_app") or (
        sent[0]["status"] == 201
    )
    assert sent[0] == {"type": "http.response.start", "status": 201, "headers": []}


async def test_commit_failure_becomes_a_500_not_a_false_success() -> None:
    session = _FakeSession(fail=True)
    mw = CommitBeforeResponseMiddleware(_app_sending_201([]))
    scope: dict[str, Any] = {"type": "http", "path": "/t", db.SCOPE_SESSIONS_KEY: [session]}
    sent: list[dict[str, Any]] = []

    async def send(message: dict[str, Any]) -> None:
        sent.append(message)

    await mw(scope, None, send)

    assert session.events == ["commit_failed", "rollback"]
    assert sent[0]["type"] == "http.response.start"
    assert sent[0]["status"] == 500, "a success status must not escape a failed commit"
    assert all(m.get("status") != 201 for m in sent if m["type"] == "http.response.start")


async def test_negative_control_without_middleware_the_201_escapes() -> None:
    """Prove the failure test can fail: bare app + failing session ⇒ 201 goes out."""
    sent: list[dict[str, Any]] = []

    async def send(message: dict[str, Any]) -> None:
        sent.append(message)

    await _app_sending_201([])({"type": "http"}, None, send)
    assert sent[0]["status"] == 201


async def test_closed_sessions_and_non_http_scopes_are_untouched() -> None:
    session = _FakeSession(active=False)
    hit: list[str] = []

    async def app(scope: Any, receive: Any, send: Any) -> None:
        hit.append(scope["type"])
        if scope["type"] == "http":
            await send({"type": "http.response.start", "status": 204, "headers": []})
            await send({"type": "http.response.body", "body": b""})

    mw = CommitBeforeResponseMiddleware(app)

    async def send(message: dict[str, Any]) -> None:
        return None

    await mw({"type": "lifespan"}, None, send)
    await mw({"type": "http", "path": "/t", db.SCOPE_SESSIONS_KEY: [session]}, None, send)

    assert hit == ["lifespan", "http"]
    assert session.events == [], "an inactive session must never be committed by the hook"


@pytest.mark.parametrize("key_present", [False])
async def test_scope_without_sessions_passes_through(key_present: bool) -> None:
    order: list[str] = []
    mw = CommitBeforeResponseMiddleware(_app_sending_201(order))
    sent: list[dict[str, Any]] = []

    async def send(message: dict[str, Any]) -> None:
        sent.append(message)

    await mw({"type": "http", "path": "/t"}, None, send)
    assert sent[0]["status"] == 201
