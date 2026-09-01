#!/usr/bin/env python3
"""Do real briefs produce real, GATED options? Ask the running product.

``scripts/solver_coverage.py`` measures stage A offline: can the packer find a
topology. That is necessary and not sufficient. A topology that every §5.4 gate then
rejects reaches the architect as the same blank screen as no topology at all — so this
walks whole briefs through the LIVE API and reports what actually comes back, including
each option's circulation percentage against the §5.6 cap.

    GARH_API=http://localhost:8000 GARH_DEMO_EMAIL=... python scripts/brief_matrix_journey.py

Exit code is the number of briefs that produced no options.
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request

API = os.environ.get("GARH_API", "http://localhost:8000").rstrip("/")
PREFIX = "/api/v1"
EMAIL = os.environ.get("GARH_DEMO_EMAIL", "demo@garh.test")
FT = 304

_token = ""


def call(method, path, body=None, expect=(200, 201, 202)):
    url = API + PREFIX + path
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    if data is not None:
        req.add_header("content-type", "application/json")
    if _token:
        req.add_header("authorization", "Bearer %s" % _token)
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            raw = r.read()
            return r.status, (json.loads(raw) if raw else {})
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            return e.code, json.loads(raw)
        except Exception:
            return e.code, {"raw": raw[:300].decode("utf-8", "replace")}


def rooms_for(beds, baths, extras):
    out = [{"type": "living_dining", "count": 1}, {"type": "kitchen", "count": 1}]
    out += [{"type": t, "count": 1} for t in extras]
    out += [{"type": "bedroom_master", "count": 1}]
    if beds > 1:
        out += [{"type": "bedroom", "count": beds - 1}]
    out += [{"type": "bath_wc", "count": baths}]
    return out


CASES = [
    ("2BHK  G+1  30x40", 2, 2, (), 2, 30, 40),
    ("2BHK  G+1  50x80", 2, 2, (), 2, 50, 80),
    ("2BHK  G+2  40x60", 2, 2, (), 3, 40, 60),
    ("3BHK  G+1  30x40", 3, 2, ("utility", "pooja"), 2, 30, 40),
    ("3BHK  G+2  50x80", 3, 2, ("utility", "pooja"), 3, 50, 80),
    ("4BHK  G+2  50x80", 4, 3, ("utility", "pooja"), 3, 50, 80),
]


def main() -> int:
    global _token
    print("Brief matrix against %s\n" % API)
    status, body = call("POST", "/auth/otp", {"email": EMAIL})
    code = body.get("devCode")
    if not code:
        print("  cannot sign in (no devCode; needs a dev server)")
        return 99
    status, body = call("POST", "/auth/verify", {"email": EMAIL, "code": code})
    _token = body.get("accessToken", "")
    if not _token:
        print("  sign-in failed: %s" % json.dumps(body)[:200])
        return 99

    failures = 0
    for label, beds, baths, extras, storeys, wf, hf in CASES:
        w, h = int(wf * FT), int(hf * FT)
        status, project = call("POST", "/projects", {"name": "matrix %s" % label})
        if status not in (200, 201):
            print("  %-18s  project create failed %s" % (label, status))
            failures += 1
            continue
        pid = project["id"]
        # The boundary is an OPEN ring of integer-mm points, and the road edge is
        # what the rules engine derives real setbacks from — the coverage sweep's
        # fixed 1.5/1.5/1.0 m are a harsher offline stand-in for exactly this.
        call(
            "PUT",
            "/projects/%s/plot" % pid,
            {
                "boundary": [
                    {"x": 0, "y": 0},
                    {"x": w, "y": 0},
                    {"x": w, "y": h},
                    {"x": 0, "y": h},
                ],
                "northDeg": 0,
                "roads": [{"edgeIndex": 0, "widthMm": 9000}],
            },
        )
        call(
            "PUT",
            "/projects/%s/brief" % pid,
            {
                "data": {
                    "storeys": storeys,
                    "rooms": rooms_for(beds, baths, extras),
                    "cityPack": "blr",
                    "vastuMode": "advisory",
                }
            },
        )
        status, job = call("POST", "/projects/%s/solve" % pid, {"optionCount": 3})
        if status not in (200, 202):
            print("  %-18s  solve refused %s %s" % (label, status, json.dumps(job)[:120]))
            failures += 1
            continue
        job_id = job["id"]
        deadline = time.time() + 180
        final = {}
        while time.time() < deadline:
            _, final = call("GET", "/solver-jobs/%s" % job_id)
            if final.get("status") in ("succeeded", "failed", "cancelled"):
                break
            time.sleep(3)
        options = final.get("options") or []
        if final.get("status") != "succeeded" or not options:
            print(
                "  %-18s  NO OPTIONS  (%s) %s"
                % (label, final.get("status"), (final.get("message") or "")[:90])
            )
            failures += 1
            continue
        circ = [o.get("scores", {}).get("circulationPercent") for o in options]
        comp = [o.get("scores", {}).get("composite") for o in options]
        over = [c for c in circ if isinstance(c, int) and c > 18]
        print(
            "  %-18s  %d options  circulation %s%%  composite %s%s"
            % (label, len(options), circ, comp, "   <-- OVER 18%% GATE" if over else "")
        )
    print("\n%d of %d briefs produced no options." % (failures, len(CASES)))
    return failures


if __name__ == "__main__":
    sys.exit(main())
