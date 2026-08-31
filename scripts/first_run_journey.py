"""The end-user journey, executed against a live stack.

    python scripts/first_run_journey.py            # http://127.0.0.1:8000/api/v1
    GARH_API=https://host/api/v1 python scripts/first_run_journey.py

Signs up a BRAND-NEW practice and drives the job an architect actually comes here for:
a plot, a brief typed the way a client says it, Generate, apply an option, and out to a
municipal sheet set. Nothing is seeded and nothing is stubbed — every step is the real
HTTP surface the browser calls.

## Why this exists as a script and not only as a Playwright spec

`e2e/tests/happy-path.spec.ts` describes this journey and, for most of the product's
life, skipped six of its seven tests with placeholder bodies. Everything else verified
the SEEDED DEMO project, so the path a real user takes had never once been executed —
and it was broken in six separate places, each failing silently with the job still
reporting "succeeded". See `docs/first-run-verification.md`.

This runs in about a minute against a compose stack and needs no browser, so there is
no excuse not to run it. Requires the api, the solver worker and the drawings worker.
"""

import json
import os
import sys
import time
import urllib.error
import urllib.request
import uuid

API = os.environ.get("GARH_API", "http://127.0.0.1:8000/api/v1").rstrip("/")
FAILS = []


def call(method, path, body=None, token=None, raw=False):
    req = urllib.request.Request(
        API + path, data=json.dumps(body).encode() if body is not None else None, method=method
    )
    req.add_header("content-type", "application/json")
    if token:
        req.add_header("authorization", "Bearer " + token)
    try:
        with urllib.request.urlopen(req, timeout=180) as r:
            payload = r.read()
            return r.status, (payload if raw else json.loads(payload or b"null"))
    except urllib.error.HTTPError as e:
        payload = e.read()
        if raw:
            return e.code, payload
        try:
            return e.code, json.loads(payload or b"null")
        except Exception:
            return e.code, payload[:300].decode("utf-8", "replace")


def step(name, ok, detail=""):
    print(
        ("  PASS  " if ok else "  FAIL  ")
        + name
        + (("  — " + str(detail)[:220]) if detail else ""),
        flush=True,
    )
    if not ok:
        FAILS.append(name)
    return ok


def poll(path, token, seconds=240):
    """Poll a job until it leaves the running states."""
    last = None
    for _ in range(seconds // 2):
        time.sleep(2)
        s, j = call("GET", path, token=token)
        last = j
        st = (j or {}).get("status") or (j or {}).get("state")
        if st in ("succeeded", "failed", "cancelled", "dead", "error"):
            return st, j
    return (last or {}).get("status"), last


email = "architect+%s@studio.test" % uuid.uuid4().hex[:8]
print("\n=== A NEW ARCHITECT SIGNS UP AND PRODUCES A SUBMISSION SET ===\n")

# 1 ── get in
s, b = call(
    "POST", "/auth/signup", {"email": email, "firmName": "Kumar & Associates", "name": "A Kumar"}
)
if not step("sign up a new practice", s in (200, 201), s):
    sys.exit(1)
s, b = call("POST", "/auth/verify", {"email": email, "code": b.get("devCode")})
if not step("sign in with the emailed code", s == 200 and b.get("accessToken"), s):
    sys.exit(1)
tok = b["accessToken"]

# 2 ── a project on a Bengaluru plot
s, proj = call(
    "POST", "/projects", {"name": "Sharma Residence", "units": "ft-in", "cityPack": "blr"}, tok
)
if not step("create a project on the BBMP rule pack", s == 201, s):
    sys.exit(1)
pid = proj["id"]

# 3 ── the plot: 30 x 40 ft, 9 m road south
s, b = call(
    "PUT",
    "/projects/%s/plot" % pid,
    {
        "boundary": [
            {"x": 0, "y": 0},
            {"x": 9144, "y": 0},
            {"x": 9144, "y": 12192},
            {"x": 0, "y": 12192},
        ],
        "northDeg": 0,
        "roads": [{"edgeIndex": 0, "widthMm": 9000}],
    },
    tok,
)
step("draw the 30 x 40 ft plot with a 9 m road", s in (200, 201), s if s not in (200, 201) else "")

# 4 ── the brief, typed the way a client says it
brief_text = (
    "G+1 for a family of four in Bengaluru. 3BHK — master with attached bath, "
    "two more bedrooms, living-dining, kitchen with utility, a pooja room, "
    "parking for one car. Please follow Vastu."
)
s, parsed = call("POST", "/projects/%s/brief/parse" % pid, {"text": brief_text}, tok)
ok = step(
    "paste the client's brief and have it parsed", s in (200, 201), s if s not in (200, 201) else ""
)
data = (parsed or {}).get("data") or (parsed or {}).get("brief", {}).get("data")
step(
    "the parser returns rooms, not prose",
    bool(data and data.get("rooms")),
    "keys=%s" % list((data or {}).keys())[:8],
)
if data:
    s, b = call("PUT", "/projects/%s/brief" % pid, {"data": data, "vastuMode": "advisory"}, tok)
    step("save the brief", s in (200, 201), s if s not in (200, 201) else "")

# 5 ── generate
jid, opts = None, []
s, job = call("POST", "/projects/%s/solve" % pid, {}, tok)
if step("press Generate", s in (200, 201, 202), "%s %s" % (s, str(job)[:200])):
    jid = job.get("id") or job.get("jobId")
    st, j = poll("/solver-jobs/%s" % jid, tok)
    step(
        "the solver finishes",
        st == "succeeded",
        "state=%s %s" % (st, str(j)[:200] if st != "succeeded" else ""),
    )
    opts = (j or {}).get("options") or (j or {}).get("result", {}).get("options") or []
    globals()["opts"] = opts
    globals()["jid"] = jid
    step("it offers plan options", len(opts) >= 1, "%d options" % len(opts))

# 5b ── apply the first option, the way the Options screen does (op `solver.apply_option`)
if opts:
    option = opts[0]
    s, branch = call("GET", "/projects/%s/branch" % pid, token=tok)
    base = int((branch or {}).get("headIdx", -1))
    s, applied = call(
        "POST",
        "/projects/%s/ops" % pid,
        {
            "ops": [
                {
                    "type": "solver.apply_option",
                    "payload": {
                        "solverJobId": jid,
                        "optionIndex": 0,
                        "ops": option.get("ops") or [],
                    },
                }
            ],
            "baseIdx": base,
            "source": "solver",
        },
        tok,
    )
    step("apply the option the architect picked", s == 200, "%s %s" % (s, str(applied)[:200]))

# 6 ── the model exists and has rooms
s, model = call("GET", "/projects/%s/model" % pid, token=tok)
snap = (model or {}).get("snapshot") or {}
house = (snap.get("house") if isinstance(snap, dict) else None) or {}
step(
    "the project has a model with walls",
    len(house.get("walls") or []) > 0,
    "walls=%d rooms=%d" % (len(house.get("walls") or []), len(house.get("rooms") or [])),
)

# 7 ── compliance
s, comp = call("GET", "/projects/%s/compliance" % pid, token=tok)
step(
    "compliance reports against the BBMP pack",
    s == 200,
    "%s results=%s" % (s, len((comp or {}).get("results") or [])),
)

# 8 ── the municipal sheets
s, job = call("POST", "/projects/%s/sheets/generate" % pid, {}, tok)
if step("generate the municipal sheet set", s in (200, 201, 202), "%s %s" % (s, str(job)[:200])):
    jid = job.get("id") or job.get("jobId")
    st, j = poll("/jobs/%s" % jid if False else "/export-jobs/%s" % jid, tok, seconds=20)
    # the sheets job may be tracked elsewhere; fall back to listing sheets
    for _ in range(45):
        time.sleep(2)
        s, sheets = call("GET", "/projects/%s/sheets" % pid, token=tok)
        if (sheets or {}).get("sheets") or (sheets or {}).get("items"):
            break
    items = (sheets or {}).get("sheets") or (sheets or {}).get("items") or []
    step(
        "sheets appear",
        len(items) > 0,
        "%d sheets: %s" % (len(items), [x.get("kind") for x in items[:8]]),
    )

print("\n=== RESULT: %d failure(s) ===" % len(FAILS))
for f in FAILS:
    print("  -", f)
print(json.dumps({"email": email, "project": pid}))
sys.exit(1 if FAILS else 0)
