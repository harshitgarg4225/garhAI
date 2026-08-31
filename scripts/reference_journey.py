#!/usr/bin/env python3
"""Does a render actually follow a reference the architect named? Ask the product.

`services/render/tests/test_reference_wiring.py` proves the join in isolation. This
walks it through a LIVE stack, because every one of the six defects found by
`first_run_journey.py` passed its unit tests: each was a field written under one name
and read under another, and only a real request found them.

The walk:

  1. sign in, open the demo project
  2. pin a picture to the board
  3. say what it is for, in words distinctive enough that finding them later cannot
     be a coincidence
  4. ask for a review of a specific style, and check the questions are the right ones
  5. start a render
  6. wait for the worker, then ask the finished render which references it followed

Step 6 is the one that matters. Everything before it can be green while the board
contributes nothing — that is CLAUDE.md's fourth bug class, and it is why this script
ends at the finished job rather than at a 202.

    GARH_API=http://localhost:8000 python scripts/reference_journey.py

Exits non-zero on the first failure, naming what was expected.
"""

from __future__ import annotations

import base64
import json
import os
import struct
import sys
import time
import urllib.error
import urllib.request
import zlib

API = os.environ.get("GARH_API", "http://localhost:8000").rstrip("/")
PREFIX = "/api/v1"
EMAIL = os.environ.get("GARH_DEMO_EMAIL", "demo@garh.test")

#: Distinctive enough that finding it in a prompt cannot be a coincidence.
WHY = "a deep shaded verandah with slender teak columns"
IGNORE = "the mirror-glass balustrade"
LABEL = "Client's verandah photo"

#: An elevation preset: the reference below is scoped to the facade, so an exterior
#: view is the one that must use it — and an interior view is the control.
PRESET = "elevation-north-morning"
MODE = "precise"


def _png(width: int, height: int) -> bytes:
    def chunk(kind: bytes, payload: bytes) -> bytes:
        return (
            struct.pack(">I", len(payload))
            + kind
            + payload
            + struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF)
        )

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 0, 0, 0, 0)
    raw = b"".join(b"\x00" + b"\x80" * width for _ in range(height))
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", ihdr)
        + chunk(b"IDAT", zlib.compress(raw))
        + chunk(b"IEND", b"")
    )


TINY_PNG_B64 = base64.b64encode(_png(1, 1)).decode("ascii")

_token = ""
_step = 0


def fail(what: str, detail: str = "") -> None:
    print("\n  FAILED: %s" % what)
    if detail:
        print("  %s" % detail[:1500])
    raise SystemExit(1)


def step(title: str) -> None:
    global _step
    _step += 1
    print("\n%d. %s" % (_step, title))


def call(
    method: str,
    path: str,
    body: object = None,
    *,
    raw: bytes | None = None,
    content_type: str = "application/json",
    expect: int | tuple[int, ...] = 200,
) -> dict:
    url = API + PREFIX + path
    data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
    request = urllib.request.Request(url, data=data, method=method)
    if data is not None:
        request.add_header("content-type", content_type)
    if _token:
        request.add_header("authorization", "Bearer %s" % _token)
    wanted = expect if isinstance(expect, tuple) else (expect,)
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            payload = response.read()
            if response.status not in wanted:
                fail("%s %s answered %d" % (method, path, response.status), payload.decode())
            return json.loads(payload) if payload else {}
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode()
        if exc.code in wanted:
            return json.loads(detail) if detail else {}
        fail("%s %s answered %d" % (method, path, exc.code), detail)
    except urllib.error.URLError as exc:
        fail("%s %s could not be reached" % (method, path), str(exc))
    return {}


def main() -> int:
    global _token
    print("Reference journey against %s" % API)

    # -- 1. sign in ----------------------------------------------------
    step("Sign in as the demo architect")
    requested = call("POST", "/auth/otp", {"email": EMAIL}, expect=(200, 202))
    # Dev returns the code in the response so a journey needs no mailbox; in
    # production the field is absent and this script is not what runs.
    code = requested.get("devCode")
    if not code:
        fail(
            "the OTP response carried no devCode",
            "this journey needs a dev/staging server (ENV=dev), not production",
        )
    verified = call("POST", "/auth/verify", {"email": EMAIL, "code": code})
    _token = verified.get("accessToken", "")
    if not _token:
        fail("sign-in returned no access token", json.dumps(verified))
    print("   signed in")

    # -- 2. the demo project -------------------------------------------
    step("Open the demo project")
    projects = call("GET", "/projects?limit=5").get("items", [])
    if not projects:
        fail("no projects for the demo firm", "run `make seed` first")
    project_id = projects[0]["id"]
    print("   %s" % projects[0].get("name", project_id))

    # -- 3. pin a picture ----------------------------------------------
    step("Pin a picture to the inspiration board")
    # An earlier run that failed part-way leaves its reference behind, and two
    # identical ones make step 6's assertion fail for the wrong reason. Only rows
    # carrying THIS script's own label are removed — an architect's real board is
    # never touched by a journey script.
    for stale in call("GET", "/projects/%s/references" % project_id)["references"]:
        if stale["label"] == LABEL:
            call("DELETE", "/projects/%s/references/%s" % (project_id, stale["id"]))
            print("   cleared a leftover from an earlier run")
    pinned = call(
        "POST",
        "/projects/%s/references" % project_id,
        raw=_png(640, 480),
        content_type="image/png",
        expect=201,
    )
    reference_id = pinned["id"]
    if pinned["why"] != "" or pinned["scope"] != "whole-house":
        fail(
            "a freshly pinned reference must start unannotated",
            "got scope=%r why=%r" % (pinned["scope"], pinned["why"]),
        )
    print(
        "   pinned %s (%dx%d), unannotated as expected"
        % (reference_id[:8], pinned["widthPx"], pinned["heightPx"])
    )

    # -- 4. the product asks about it before it is annotated -----------
    step("Check the board BEFORE annotating — it must ask, not guess")
    review = call("GET", "/projects/%s/references/review?preset=%s" % (project_id, PRESET))
    unusable = [c for c in review["conflicts"] if c["kind"] == "unusable"]
    if not unusable:
        fail(
            "an unannotated picture must produce a question",
            "conflicts were %s" % json.dumps(review["conflicts"]),
        )
    if not unusable[0]["default"]:
        fail("every question must state what happens if the architect does nothing")
    print("   asked: %s" % unusable[0]["question"])
    print("   default: %s" % unusable[0]["default"])

    # -- 5. annotate ---------------------------------------------------
    step("Say what the picture is for")
    call(
        "PATCH",
        "/projects/%s/references/%s" % (project_id, reference_id),
        {"label": LABEL, "scope": "facade", "why": WHY, "ignore": IGNORE, "intent": "match"},
    )
    print("   where: facade · take: %s · leave: %s" % (WHY, IGNORE))

    # -- 6. the review now approves and shows the exact words ----------
    step("Check it again — the question must be gone, and the words must be visible")
    review = call("GET", "/projects/%s/references/review?preset=%s" % (project_id, PRESET))
    if [c for c in review["conflicts"] if c["kind"] == "unusable"]:
        fail("answering the question must stop it being asked")
    if [r["id"] for r in review["applies"]] != [reference_id]:
        fail("the annotated reference must apply to an exterior view", json.dumps(review))
    if WHY not in review["positive"]:
        fail("the architect's own words must reach the prompt", review["positive"])
    if IGNORE not in review["negative"]:
        fail('"what to leave out" must reach the negative prompt', review["negative"])
    print("   will draw:  %s" % review["positive"])
    print("   will avoid: %s" % review["negative"])

    # -- 7. and it must know when a reference does NOT apply -----------
    step("Check an interior view — a facade reference must be reported, not dropped")
    # Named rather than discovered: the API has no preset-listing route, and an id
    # the server does not know would 400 here and read as a tenancy failure.
    inside = "interior-living"
    other = call("GET", "/projects/%s/references/review?preset=%s" % (project_id, inside))
    if [r["id"] for r in other["applies"]]:
        fail("a facade reference cannot inform an interior view", json.dumps(other))
    if [r["id"] for r in other["notInView"]] != [reference_id]:
        fail("silently dropping it is how an architect concludes the board does nothing")
    print("   %s: not used here, and it says so" % inside)

    # -- 8. render -----------------------------------------------------
    step("Start a render of the elevation")
    # §9 pins every render to a saved version, so a project that has never been
    # checkpointed cannot be rendered at all. Make one rather than assume the demo
    # has one — that assumption is what made the first run of this script fail with
    # a message about the design rather than about references.
    if not call("GET", "/projects/%s/versions" % project_id).get("items"):
        call(
            "POST",
            "/projects/%s/versions" % project_id,
            {"name": "reference journey checkpoint"},
            expect=(200, 201),
        )
        print("   saved a version first (§9: renders pin to one)")
    job = call(
        "POST",
        "/projects/%s/renders" % project_id,
        {
            "mode": MODE,
            "preset": PRESET,
            "seed": 4242,
            "width": 512,
            "height": 512,
            "view": {"preset": PRESET, "fovDeg": 45},
            # An elevation preset is Precise-only, and Precise needs the depth map —
            # geometry lock is the whole point of it. A viewport alone fails at the
            # worker with a message about render settings, not about references.
            "inputs": {
                "viewportPng": TINY_PNG_B64,
                "depthPng": TINY_PNG_B64,
                "edgesPng": TINY_PNG_B64,
            },
        },
        expect=(200, 202),
    )
    job_id = job["id"]
    print("   job %s queued" % job_id[:8])

    # -- 9. THE ONE THAT MATTERS ---------------------------------------
    step("Wait for the render, then ask it which references it followed")
    deadline = time.time() + 120
    final: dict = {}
    while time.time() < deadline:
        final = call("GET", "/render-jobs/%s" % job_id)
        if final.get("status") in {"succeeded", "failed", "cancelled"}:
            break
        time.sleep(2)
    if final.get("status") != "succeeded":
        fail(
            "the render did not succeed",
            "status=%s %s" % (final.get("status"), json.dumps(final.get("error") or {})),
        )

    credited = final.get("referencesUsed") or []
    if not credited:
        fail(
            "the finished render credits no references",
            "this is the join going silently inert — the board was annotated, the "
            "review approved it, and the render followed nothing. The job said %s"
            % json.dumps({k: final.get(k) for k in ("status", "referencesUsed")})[:800],
        )
    labels = [entry.get("label") for entry in credited]
    if LABEL not in labels:
        fail("the render credits the wrong references", json.dumps(credited))
    if any("verandah" in str(entry.get("intent", "")) for entry in credited):
        fail("§13: the credit list must carry no prompt text", json.dumps(credited))
    print("   render succeeded, and it followed: %s" % ", ".join(str(x) for x in labels))

    # -- 10. tidy up ---------------------------------------------------
    step("Take the reference off the board")
    call("DELETE", "/projects/%s/references/%s" % (project_id, reference_id))
    remaining = call("GET", "/projects/%s/references" % project_id)["references"]
    if any(r["id"] == reference_id for r in remaining):
        fail("delete left the reference on the board")
    print("   removed")

    print("\n  All %d steps passed. A render provably followed a named reference." % _step)
    return 0


if __name__ == "__main__":
    sys.exit(main())
