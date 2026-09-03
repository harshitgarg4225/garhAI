"""Seed the ready-made plan library from REAL solver runs, one cell at a time.

    GARH_API=http://127.0.0.1:8000/api/v1 python scripts/seed_plan_library.py [cell-id ...]

For each cell: sign in (dev OTP echo), create a project, draw the plot with its road,
write the brief's rooms directly (the way the Brief tab does), Generate, pick the best
option the solver offered, apply it exactly as the Options screen does
(``solver.apply_option``), then capture the project's WHOLE op log — plot, brief,
storeys, and the expanded wall/opening/stair/room ops — as ``fixtures/plans/<id>.json``.

The demo seed's docstring is the rule this script obeys: a template plan "must come
from a real solver run, captured as a golden file — not typed by hand". Every plan
here therefore folds, passes the hard rule checks and renders by construction, because
it is the same op stream an architect would have on their canvas after pressing Apply.
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from typing import Any

API = os.environ.get("GARH_API", "http://127.0.0.1:8000/api/v1").rstrip("/")
OUT = Path(__file__).resolve().parents[1] / "fixtures" / "plans"
FT = 304.8  # mm


#: Room lists in the brief's own vocabulary — the same helper the brief matrix uses.
def rooms_for(beds: int, baths: int, extras: tuple[str, ...]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = [
        {"type": "living_dining", "count": 1},
        {"type": "kitchen", "count": 1},
    ]
    out += [{"type": t, "count": 1} for t in extras]
    out += [{"type": "bedroom_master", "count": 1}]
    if beds > 1:
        out += [{"type": "bedroom", "count": beds - 1}]
    out += [{"type": "bath_wc", "count": baths}]
    return out


#: id, name, description, city pack, plot ft (w, d), storeys, beds, baths, extras, road m
CELLS: list[dict[str, Any]] = [
    dict(
        id="blr-30x40-g1-2bhk",
        name="Bengaluru 30 × 40, G+1 2BHK",
        city="blr",
        plot=(30, 40),
        storeys=2,
        beds=2,
        baths=2,
        extras=(),
        road=9000,
        description="Two bedrooms with generous living on the classic 30 × 40 site.",
    ),
    dict(
        id="ncr-30x50-g2-3bhk",
        name="NCR 30 × 50, G+2 3BHK",
        city="ncr",
        plot=(30, 50),
        storeys=3,
        beds=3,
        baths=3,
        extras=("utility", "pooja"),
        road=9000,
        parking=2,
        description="A 30 × 50 plot under the NCR pack at G+2: three bedrooms, three baths.",
    ),
    dict(
        id="blr-25x40-g1-2bhk",
        name="Bengaluru 25 × 40, G+1 2BHK",
        city="blr",
        plot=(25, 40),
        storeys=2,
        beds=2,
        baths=2,
        extras=(),
        road=9000,
        description="The everyday 25 × 40 site: two bedrooms and two baths over a living-kitchen ground floor.",
    ),
    dict(
        id="blr-40x60-g2-4bhk",
        name="Bengaluru 40 × 60, G+2 4BHK",
        city="blr",
        plot=(40, 60),
        storeys=3,
        beds=4,
        baths=3,
        extras=("utility", "pooja"),
        road=12000,
        description="Four bedrooms over three floors on a 12 m road, with utility and pooja.",
    ),
    dict(
        id="ncr-40x60-g2-3bhk",
        name="NCR 40 × 60, G+2 3BHK",
        city="ncr",
        plot=(40, 60),
        storeys=3,
        beds=3,
        baths=3,
        extras=("utility", "pooja"),
        road=12000,
        parking=2,
        description="A 40 × 60 plot under the NCR pack at G+2: three bedrooms, three baths.",
    ),
    dict(
        id="blr-20x30-g1-2bhk",
        name="Bengaluru 20 × 30, G+1 2BHK",
        city="blr",
        plot=(20, 30),
        storeys=2,
        beds=2,
        baths=1,
        extras=(),
        road=6000,
        description="A compact infill plot: living-dining and kitchen below, two bedrooms above.",
    ),
    dict(
        id="blr-30x40-g1-3bhk",
        name="Bengaluru 30 × 40, G+1 3BHK",
        city="blr",
        plot=(30, 40),
        storeys=2,
        beds=3,
        baths=2,
        extras=("utility", "pooja"),
        road=9000,
        description="The most common Bengaluru site: 3BHK with utility and pooja on a 9 m road.",
    ),
    dict(
        id="blr-30x50-g2-3bhk",
        name="Bengaluru 30 × 50, G+2 3BHK",
        city="blr",
        plot=(30, 50),
        storeys=3,
        beds=3,
        baths=3,
        extras=("utility", "pooja"),
        road=9000,
        description="Three floors on a 30 × 50 site — a bath per bedroom, utility and pooja.",
    ),
    dict(
        id="blr-40x60-g1-4bhk",
        name="Bengaluru 40 × 60, G+1 4BHK",
        city="blr",
        plot=(40, 60),
        storeys=2,
        beds=4,
        baths=3,
        extras=("utility", "pooja"),
        road=12000,
        description="A wider family house on a 12 m road: four bedrooms over two floors.",
    ),
    dict(
        id="hyd-30x40-g1-3bhk",
        name="Hyderabad 30 × 40, G+1 3BHK",
        city="hyd",
        plot=(30, 40),
        storeys=2,
        beds=3,
        baths=2,
        extras=("utility",),
        road=9000,
        description="The 30 × 40 plan under the Hyderabad (GHMC) rule pack.",
    ),
    dict(
        id="ncr-50x80-g2-4bhk",
        name="NCR 50 × 80, G+2 4BHK",
        city="ncr",
        plot=(50, 80),
        storeys=3,
        beds=4,
        baths=4,
        extras=("utility", "pooja"),
        road=12000,
        parking=3,
        description="A large NCR plot at G+2: four bedrooms, four baths, utility and pooja.",
    ),
]

_token = ""


def call(method: str, path: str, body: Any = None) -> tuple[int, Any]:
    req = urllib.request.Request(
        API + path,
        data=json.dumps(body).encode() if body is not None else None,
        method=method,
    )
    req.add_header("content-type", "application/json")
    if _token:
        req.add_header("authorization", "Bearer " + _token)
    try:
        with urllib.request.urlopen(req, timeout=180) as r:
            raw = r.read()
            return r.status, (json.loads(raw) if raw else {})
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            return e.code, json.loads(raw)
        except Exception:
            return e.code, {"raw": raw[:300].decode("utf-8", "replace")}


def sign_in() -> None:
    global _token
    email = "plan-library-%s@studio.test" % uuid.uuid4().hex[:8]
    status, body = call(
        "POST",
        "/auth/signup",
        {"firmName": "Plan Library", "name": "Seeder", "email": email},
    )
    if status not in (200, 201):
        raise SystemExit("signup failed: %s %s" % (status, body))
    code = body.get("devCode")
    if not code:
        raise SystemExit(
            "no devCode — the API must run with DEV_ECHO_OTP=1 and no mailer"
        )
    status, body = call("POST", "/auth/verify", {"email": email, "code": code})
    _token = body.get("accessToken", "")
    if not _token:
        raise SystemExit("verify failed: %s %s" % (status, body))


def fetch_all_ops(pid: str) -> list[dict[str, Any]]:
    """Every op on the active branch, in index order, whatever the page shape."""
    ops: list[dict[str, Any]] = []
    cursor: str | None = None
    for _ in range(50):
        path = "/projects/%s/ops?limit=500" % pid + (
            "&cursor=%s" % cursor if cursor else ""
        )
        status, page = call("GET", path)
        if status != 200:
            raise RuntimeError("ops page %s: %s" % (status, str(page)[:200]))
        items = page.get("items") if isinstance(page, dict) else page
        if items is None and isinstance(page, dict):
            items = page.get("ops")
        ops.extend(items or [])
        cursor = page.get("nextCursor") if isinstance(page, dict) else None
        if not cursor:
            break
    ops.sort(key=lambda o: o.get("idx", 0))
    return ops


def best_option(options: list[dict[str, Any]]) -> int:
    def key(pair: tuple[int, dict[str, Any]]) -> tuple[int, float]:
        scores = pair[1].get("scores") or {}
        circ = scores.get("circulationPercent")
        within = 1 if isinstance(circ, int) and circ <= 18 else 0
        return (within, float(scores.get("composite") or 0))

    return max(enumerate(options), key=key)[0]


def seed(cell: dict[str, Any]) -> dict[str, Any] | None:
    label = cell["id"]
    w, d = int(cell["plot"][0] * FT), int(cell["plot"][1] * FT)
    status, project = call(
        "POST",
        "/projects",
        {"name": cell["name"], "units": "ft-in", "cityPack": cell["city"]},
    )
    if status not in (200, 201):
        print(
            "  %-22s project create failed %s %s" % (label, status, str(project)[:120])
        )
        return None
    pid = project["id"]
    status, _ = call(
        "PUT",
        "/projects/%s/plot" % pid,
        {
            "boundary": [
                {"x": 0, "y": 0},
                {"x": w, "y": 0},
                {"x": w, "y": d},
                {"x": 0, "y": d},
            ],
            "northDeg": 0,
            "roads": [{"edgeIndex": 0, "widthMm": cell["road"]}],
            "regProfile": {"cityPack": cell["city"], "overrides": {}},
        },
    )
    if status not in (200, 201):
        print("  %-22s plot failed %s" % (label, status))
        return None
    status, _ = call(
        "PUT",
        "/projects/%s/brief" % pid,
        {
            # The mode is the op's own field (the fold and the pack set read it there),
            # not a key in data — see put_brief.
            "vastuMode": "advisory",
            "data": {
                "storeys": cell["storeys"],
                "rooms": rooms_for(cell["beds"], cell["baths"], cell["extras"]),
                "cityPack": cell["city"],
                # Every city pack makes car parking a HARD rule (blr.parking.plot.le240,
                # hyd.parking.plot.le200, ncr.parking.ecs). A brief that declares none is a
                # brief every candidate fails — the first seeding pass lost all six cells to it.
                "carParking": cell.get("parking", 1),
            },
        },
    )
    if status not in (200, 201):
        print("  %-22s brief failed %s" % (label, status))
        return None
    status, job = call("POST", "/projects/%s/solve" % pid, {"optionCount": 3})
    if status not in (200, 202):
        print("  %-22s solve refused %s %s" % (label, status, str(job)[:160]))
        return None
    jid = job["id"]
    final: dict[str, Any] = {}
    deadline = time.time() + 420
    while time.time() < deadline:
        _, final = call("GET", "/solver-jobs/%s" % jid)
        if final.get("status") in ("succeeded", "failed", "cancelled"):
            break
        time.sleep(3)
    options = final.get("options") or []
    if final.get("status") != "succeeded" or not options:
        print(
            "  %-22s NO OPTIONS (%s) %s"
            % (label, final.get("status"), (final.get("message") or "")[:120])
        )
        return None
    index = best_option(options)
    option = options[index]
    status, branch = call("GET", "/projects/%s/branch" % pid)
    base = int((branch or {}).get("headIdx", -1))
    status, applied = call(
        "POST",
        "/projects/%s/ops" % pid,
        {
            "ops": [
                {
                    "type": "solver.apply_option",
                    "payload": {
                        "solverJobId": jid,
                        "optionIndex": index,
                        "ops": option.get("ops") or [],
                    },
                }
            ],
            "baseIdx": base,
            "source": "solver",
        },
    )
    if status != 200:
        print("  %-22s apply failed %s %s" % (label, status, str(applied)[:160]))
        return None
    status, model = call("GET", "/projects/%s/model" % pid)
    snap = (model or {}).get("snapshot") or {}
    house = snap.get("house") or snap
    walls = len(house.get("walls") or [])
    rooms = len(house.get("rooms") or [])
    stairs = len(house.get("stairs") or [])
    openings = len(house.get("openings") or [])
    ops = fetch_all_ops(pid)
    # The sequencer stores the applied option as ONE wrapper op whose payload carries the
    # server-built expansion; a template must carry the expansion itself (see
    # scripts/flatten_plan_recipes.py for why).
    recipe: list[dict[str, Any]] = []
    for o in ops:
        if o["type"] == "solver.apply_option":
            recipe.extend(
                {"type": i["type"], "payload": i.get("payload") or {}}
                for i in (o.get("payload") or {}).get("ops") or []
            )
        else:
            recipe.append({"type": o["type"], "payload": o.get("payload") or {}})
    record = {
        "id": cell["id"],
        "name": cell["name"],
        "description": cell["description"],
        "cityPack": cell["city"],
        "plotFt": list(cell["plot"]),
        "storeys": cell["storeys"],
        "brief": {
            "beds": cell["beds"],
            "baths": cell["baths"],
            "extras": list(cell["extras"]),
        },
        "solver": {
            "jobId": jid,
            "optionIndex": index,
            "scores": option.get("scores") or {},
            "optionsOffered": len(options),
        },
        "model": {
            "walls": walls,
            "rooms": rooms,
            "openings": openings,
            "stairs": stairs,
        },
        "ops": recipe,
    }
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / ("%s.json" % cell["id"])).write_text(
        json.dumps(record, indent=2, sort_keys=False) + "\n"
    )
    print(
        "  %-22s %d options → #%d  composite=%s circ=%s%%  walls=%d rooms=%d openings=%d stairs=%d  ops=%d"
        % (
            label,
            len(options),
            index,
            (option.get("scores") or {}).get("composite"),
            (option.get("scores") or {}).get("circulationPercent"),
            walls,
            rooms,
            openings,
            stairs,
            len(recipe),
        )
    )
    return record


def main(argv: list[str]) -> int:
    wanted = set(argv) or {c["id"] for c in CELLS}
    print("Seeding the plan library against %s\n" % API)
    sign_in()
    failures = 0
    for cell in CELLS:
        if cell["id"] not in wanted:
            continue
        if seed(cell) is None:
            failures += 1
    print("\n%d cell(s) produced no plan." % failures)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
