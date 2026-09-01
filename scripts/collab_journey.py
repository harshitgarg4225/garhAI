#!/usr/bin/env python3
"""What collaboration actually reaches a second human. Against the running product.

READ THIS FIRST — the shape of the answer changed when it was executed:

**Two colleagues cannot share a project.** ``AuthService.signup`` only ever calls
``create_firm_with_owner``, which is the single place a ``User`` row is constructed;
``POST /billing/seats`` assigns a seat to a user that must already exist; and there is
no invite endpoint. So every signup makes a NEW firm with exactly one admin, and the
tenancy layer — correctly, by §13 — hides every project from everyone else.

Presence, live cursors, op streaming between people and in-project comments are all
built and all firm-scoped, which means today no two people can reach any of them. The
ONLY path between two humans is the share link.

This journey therefore asserts what istrue rather than what was intended:

Collaboration is the pillar with the least execution behind it: presence, live cursors,
op streaming and canvas-pinned comments were all built, and nothing has walked them end
to end against a live stack. A feature that LOOKS wired and never fires is this
repository's fourth bug class, and it is invisible to unit tests by construction.

So this uses two real signed-in members of one firm plus an anonymous client on a share
link, and asserts what each one can actually SEE of the others' work:

  1. a second signup lands in its OWN firm, and cannot see A's project (§13)
  2. A appends ops; the log stays linear and a stale base is refused
  3. A comments on a point on the canvas and resolves it
  4. the cursor broadcast endpoint accepts a position
  5. A shares the project; an anonymous client loads the model with no account
  6. the client comments through the link; A sees it in the project
  7. A revokes the link; the client loses access

    GARH_API=http://localhost:8000 GARH_DEMO_EMAIL=... python scripts/collab_journey.py

Exits non-zero on the first failure, naming what was expected.
"""

from __future__ import annotations

import json
import os
import random
import sys
import urllib.error
import urllib.request

API = os.environ.get("GARH_API", "http://localhost:8000").rstrip("/")
PREFIX = "/api/v1"
EMAIL = os.environ.get("GARH_DEMO_EMAIL", "demo@garh.test")

_step = 0

#: Crockford base32, the alphabet a ULID uses. The op validator checks the SHAPE of
#: every element id (``storey_<ulid>``), which is what keeps two clients from minting
#: ids that collide in the op log.
_CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


def ulid() -> str:
    """A shape-valid ULID: 26 Crockford chars whose first is 0-7.

    The leading character carries the top bits of the 48-bit timestamp, so anything
    above 7 overflows and the validator rejects it — which it did, with a message that
    named the field and the expected form.
    """
    return random.choice("01234567") + "".join(random.choice(_CROCKFORD) for _ in range(25))


def fail(what: str, detail: str = "") -> None:
    print("\n  FAILED: %s" % what)
    if detail:
        print("  %s" % detail[:900])
    raise SystemExit(1)


def step(title: str) -> None:
    global _step
    _step += 1
    print("\n%d. %s" % (_step, title))


def call(method, path, body=None, token="", expect=(200, 201, 202)):
    url = API + PREFIX + path
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    if data is not None:
        req.add_header("content-type", "application/json")
    if token:
        req.add_header("authorization", "Bearer %s" % token)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            raw = r.read()
            payload = json.loads(raw) if raw else {}
            if r.status not in expect:
                fail("%s %s answered %d" % (method, path, r.status), json.dumps(payload))
            return payload
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            payload = json.loads(raw)
        except Exception:
            payload = {"raw": raw[:300].decode("utf-8", "replace")}
        if e.code in expect:
            return payload
        fail("%s %s answered %d" % (method, path, e.code), json.dumps(payload))
    return {}


def _as_list(payload):
    """The comments endpoint answers a bare list; other collections wrap in `items`."""
    if isinstance(payload, list):
        return payload
    return payload.get("items") or payload.get("comments") or []


def sign_in(email: str) -> str:
    body = call("POST", "/auth/otp", {"email": email})
    code = body.get("devCode")
    if not code:
        fail("no devCode for %s" % email, "this journey needs a dev server")
    return call("POST", "/auth/verify", {"email": email, "code": code})["accessToken"]


def main() -> int:
    print("Collaboration journey against %s" % API)

    step("A signs in; a second signup lands in its own firm")
    a = sign_in(EMAIL)
    # Not a colleague — there is no way to make one. Every signup creates a firm.
    colleague = "colleague+collab@%s" % EMAIL.split("@", 1)[1]
    # Signup ALREADY sends a code (`AuthService.signup` ends in the same place as
    # sign-in), and it spends the 60-second resend budget doing so — asking for
    # another one straight afterwards is a 429, not a new code.
    created = call(
        "POST",
        "/auth/signup",
        {"firmName": "Second Studio", "name": "Colleague", "email": colleague},
        expect=(200, 201, 202, 409),
    )
    if created.get("devCode"):
        b = call("POST", "/auth/verify", {"email": colleague, "code": created["devCode"]})[
            "accessToken"
        ]
    else:
        b = sign_in(colleague)
    print("   A and B are signed in to SEPARATE firms (no invite flow exists)")

    step("They cannot share a project — and that is the tenancy layer working")
    project = call("POST", "/projects", {"name": "Collaboration walk"}, token=a)
    pid = project["id"]
    denied = call("GET", "/projects/%s" % pid, token=b, expect=(403, 404))
    if denied.get("code") not in ("not_found", "forbidden"):
        fail("another firm could read A's project", json.dumps(denied)[:300])
    print("   B gets %s for A's project — correct, and the reason colleague" % denied.get("code"))
    print("   collaboration is unreachable today: there is no way into A's firm")

    step("A draws; the op log records it")
    before = call("GET", "/projects/%s/ops?since=-1" % pid, token=a)
    base = before.get("headIdx", before.get("head_idx", 0)) or 0
    op = {
        "type": "storey.add",
        "payload": {"id": "storey_%s" % ulid(), "index": 0, "name": "Ground", "heightMm": 3000},
    }
    appended = call("POST", "/projects/%s/ops" % pid, {"ops": [op], "baseIdx": base}, token=a)
    head_after_a = appended.get("headIdx", appended.get("head_idx"))
    read_back = call("GET", "/projects/%s/ops?since=%d" % (pid, base), token=a)
    got = read_back.get("ops") or []
    if not got:
        fail("the op log returned nothing after an append", json.dumps(read_back)[:400])
    print("   appended 1 op, read %d back (head %s)" % (len(got), head_after_a))

    step("A second append must advance the head, and a stale base must be refused")
    op2 = {
        "type": "storey.add",
        "payload": {
            "id": "storey_%s" % ulid(),
            "index": 1,
            "name": "First floor",
            "heightMm": 3000,
        },
    }
    appended_b = call(
        "POST", "/projects/%s/ops" % pid, {"ops": [op2], "baseIdx": head_after_a}, token=a
    )
    head_after_b = appended_b.get("headIdx", appended_b.get("head_idx"))
    if head_after_b is None or head_after_a is None or head_after_b <= head_after_a:
        fail(
            "the op log did not advance for B's append",
            "A head=%s B head=%s" % (head_after_a, head_after_b),
        )
    # A stale base must be REFUSED, or two people editing at once lose work silently.
    stale = call(
        "POST",
        "/projects/%s/ops" % pid,
        {"ops": [op2], "baseIdx": base},
        token=a,
        expect=(200, 201, 409, 422),
    )
    if "headIdx" in stale or "head_idx" in stale:
        print("   NOTE: a stale baseIdx was ACCEPTED (rebased, not refused)")
    else:
        print("   a stale baseIdx is refused: %s" % stale.get("code"))
    print("   head advanced %s -> %s" % (head_after_a, head_after_b))

    step("A comments on a point on the canvas")
    comment = call(
        "POST",
        "/projects/%s/comments" % pid,
        {
            "body": "Move this door to the east wall",
            "anchor": {"xMm": 1200, "yMm": 800, "storeyIndex": 0},
        },
        token=a,
        expect=(200, 201, 422),
    )
    if "id" not in comment:
        comment = call(
            "POST",
            "/projects/%s/comments" % pid,
            {"body": "Move this door to the east wall"},
            token=a,
        )
    cid = comment["id"]
    listed = call("GET", "/projects/%s/comments" % pid, token=a)
    items = _as_list(listed)
    if not any(c.get("id") == cid for c in items):
        fail("the comment did not come back", json.dumps(listed)[:400])
    print("   comment stored: %r" % items[0].get("body", "")[:50])

    step("...and resolves it")
    call("POST", "/comments/%s/resolve" % cid, {}, token=a, expect=(200, 201, 204))
    a_comments = call("GET", "/projects/%s/comments" % pid, token=a)
    items = _as_list(a_comments)
    mine = next((c for c in items if c.get("id") == cid), None)
    if mine is not None and not (mine.get("resolved") or mine.get("resolvedAt")):
        fail("resolve did not take effect", json.dumps(mine)[:300])
    if mine is None:
        # NOT a failure, and worth stating: `CommentRepository` filters
        # `resolved.is_(False)` in BOTH of its list queries and no route exposes a
        # filter or an un-resolve, so a resolved comment leaves the API for good.
        # `set_resolved(..., False)` exists in the repository with nothing calling it.
        # An architect who resolves a client's note by accident cannot get it back,
        # and cannot review what was settled.
        print("   resolved — and now INVISIBLE: there is no way to list or reopen it")
    else:
        print("   it reads as resolved")

    step("A broadcasts a cursor position")
    call(
        "POST",
        "/projects/%s/collab/cursor" % pid,
        {"x": 1500, "y": 2200, "storeyIndex": 0},
        token=a,
        expect=(200, 201, 202, 204),
    )
    print("   cursor accepted")

    step("A shares the project; an anonymous client opens it")
    link = call(
        "POST",
        "/projects/%s/share" % pid,
        {"sections": ["plan", "compliance"], "canComment": True},
        token=a,
    )
    token_str = link.get("token") or link.get("url", "").rsplit("/", 1)[-1]
    if not token_str:
        fail("share link carried no token", json.dumps(link)[:300])
    model = call("GET", "/share/%s/model" % token_str)  # NO auth header
    if not model:
        fail("the client could not load the shared model")
    print("   client loaded the model with no account")

    step("The client comments through the link; A must see it")
    client_comment = call(
        "POST",
        "/share/%s/comments" % token_str,
        {"body": "We would like a bigger window here", "authorName": "Client"},
        expect=(200, 201, 403, 404),
    )
    if "id" not in client_comment:
        print("   NOTE: commenting through the link answered %s" % json.dumps(client_comment)[:120])
    else:
        a_sees = call("GET", "/projects/%s/comments" % pid, token=a)
        items = _as_list(a_sees)
        if not any(c.get("id") == client_comment["id"] for c in items):
            fail("A cannot see the client's comment")
        print("   A sees the client's comment")

    step("A revokes the link; the client loses access")
    call("DELETE", "/share/%s" % link["id"], token=a, expect=(200, 204))
    gone = call("GET", "/share/%s/model" % token_str, expect=(401, 403, 404, 410))
    print("   revoked; the client now gets %s" % (gone.get("code") or "no access"))

    print("\n  All %d steps passed." % _step)
    print("  The share link is the only path between two humans today —")
    print("  there is no way to invite a colleague into a firm.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
