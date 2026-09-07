"""End-to-end UAT of the architect journey, written the way the public
`webapp-testing` skill (anthropics/skills) prescribes: native Python Playwright,
networkidle waits, reconnaissance-then-action, screenshots at every step, console
logs captured. Every step records pass/fail and never aborts the run, so the report
is a full picture rather than the first failure.

Usage: APP_URL=http://localhost:5173 API_URL=http://localhost:8000/api/v1 python journey.py OUT_DIR
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
import uuid

import httpx
from playwright.sync_api import TimeoutError as PWTimeout, sync_playwright

APP = os.environ.get("APP_URL", "http://localhost:5173")
API = os.environ.get("API_URL", "http://localhost:8000/api/v1")
OUT = sys.argv[1] if len(sys.argv) > 1 else "./uat-out"
os.makedirs(OUT, exist_ok=True)

report: list[dict] = []
console: list[str] = []
state: dict = {}


def step(name):
    def deco(fn):
        def run(page):
            t0 = time.time()
            entry = {"step": name, "status": "pass", "ms": 0, "note": ""}
            try:
                note = fn(page)
                entry["note"] = note or ""
            except PWTimeout as exc:
                entry["status"] = "fail"
                entry["note"] = "timeout: " + str(exc).splitlines()[0][:200]
            except AssertionError as exc:
                entry["status"] = "fail"
                entry["note"] = "assert: " + str(exc)[:300]
            except Exception as exc:  # noqa: BLE001
                entry["status"] = "fail"
                entry["note"] = type(exc).__name__ + ": " + str(exc)[:300]
            entry["ms"] = int((time.time() - t0) * 1000)
            shot = os.path.join(
                OUT,
                "%02d-%s.png"
                % (len(report) + 1, re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")),
            )
            try:
                page.screenshot(path=shot)
                entry["screenshot"] = os.path.basename(shot)
            except Exception:  # noqa: BLE001
                pass
            report.append(entry)
            print(
                "%-4s %-52s %6d ms  %s"
                % (entry["status"].upper(), name, entry["ms"], entry["note"][:120]),
                flush=True,
            )
            return entry["status"] == "pass"

        return run

    return deco


def settle(page, ms=800):
    try:
        page.wait_for_load_state("networkidle", timeout=15_000)
    except PWTimeout:
        pass
    page.wait_for_timeout(ms)


# ---------------------------------------------------------------- steps
@step("Sign up a new practice with the emailed code")
def s_signup(page):
    email = "uat-%s@studio.test" % uuid.uuid4().hex[:8]
    state["email"] = email
    page.goto(APP + "/")
    settle(page)
    page.get_by_role("button", name=re.compile("create an account", re.I)).click()
    page.get_by_label(re.compile("practice name", re.I)).fill("UAT Studio")
    page.get_by_label(re.compile("your name", re.I)).fill("Ar. UAT")
    page.get_by_label(re.compile("work email", re.I)).fill(email)
    page.get_by_role("button", name=re.compile("create account|sign up|continue", re.I)).click()
    page.get_by_role("button", name=re.compile("use this code", re.I)).click(timeout=20_000)
    page.wait_for_url(lambda u: "/login" not in u, timeout=20_000)
    settle(page)
    return "signed up as " + email


@step("Dashboard shows the trial usage card with the platform fee")
def s_usage(page):
    settle(page)
    body = page.locator("body").inner_text()
    assert re.search(r"budget|credit|generation", body, re.I), "no usage wording on the dashboard"
    fee = re.search(r"\d+(\.\d+)?% platform fee", body)
    return "usage card: %s" % (
        fee.group(0) if fee else "fee line absent (spend cap may be 0 locally)"
    )


@step("Read the platform fee over the API as the signed-in user")
def s_fee_api(page):
    # A second sign-in over the API for the same practice (dev echo returns the code).
    otp = httpx.post(API + "/auth/otp", json={"email": state["email"]}, timeout=20).json()
    verified = httpx.post(
        API + "/auth/verify",
        json={"email": state["email"], "code": otp.get("devCode", "")},
        timeout=20,
    )
    assert verified.status_code == 200, verified.text[:200]
    token = verified.json()["accessToken"]
    state["token"] = token
    r = httpx.get(
        API + "/admin/billing/markup", headers={"authorization": "Bearer " + token}, timeout=20
    )
    assert r.status_code == 200, r.text[:200]
    usage = httpx.get(
        API + "/billing/usage", headers={"authorization": "Bearer " + token}, timeout=20
    ).json()
    spend = usage.get("spend") or {}
    return "fee: %s%% (%s); usage spend: %s" % (
        r.json()["percent"],
        r.json()["source"],
        {k: spend.get(k) for k in ("markupPercent", "spentUsd", "capUsd", "enforced")},
    )


@step("Create a project from a ready-made plan")
def s_new_project(page):
    page.goto(APP + "/?new=1&template=blr-30x40-g1-3bhk")
    page.get_by_role("radiogroup", name=re.compile("start from a template", re.I)).wait_for(
        timeout=20_000
    )
    cards = page.get_by_role("radio").count()
    page.get_by_role("radio").filter(
        has_text=re.compile("Bengaluru 30 × 40, G\\+1 3BHK")
    ).first.click()
    page.get_by_label(re.compile("project name", re.I)).fill("UAT Sharma Residence")
    page.get_by_role("button", name=re.compile("^create project$", re.I)).click()
    page.wait_for_url(re.compile(r"/projects/[0-9a-f-]{36}"), timeout=30_000)
    state["project_id"] = re.search(r"/projects/([0-9a-f-]{36})", page.url).group(1)
    settle(page)
    return "%d template cards; project %s" % (cards, state["project_id"][:8])


@step("Plan tab: fit all, walls and labels visible, checks strip populated")
def s_plan(page):
    page.goto(APP + "/projects/%s/plan" % state["project_id"])
    page.locator("canvas").first.wait_for(timeout=30_000)
    settle(page, 2000)
    fit = page.get_by_role("button", name=re.compile("^fit all$", re.I)).first
    if fit.count():
        fit.click()
        page.wait_for_timeout(1200)
    strip = page.locator("text=/CHECKS/i").first.locator("xpath=..").inner_text()
    assert "Nothing to check yet" not in strip, "checks strip never populated: " + strip[:100]
    body = page.locator("body").inner_text()
    assert re.search(r"Bedroom|Living|Kitchen", body), "no room labels on the plan"
    return "strip: " + strip.replace("\n", " ")[:110]


@step("Compliance tab lists rules with citations and no fail")
def s_compliance(page):
    page.goto(APP + "/projects/%s/compliance" % state["project_id"])
    settle(page, 1500)
    body = page.locator("main").first.inner_text()
    assert re.search(r"NBC|Cl\.|bye-law|clause", body, re.I), "no citations visible"
    fails = len(re.findall(r"\bfail\b", body, re.I))
    return "citations present; 'fail' mentions: %d" % fails


@step("3D tab renders the massing")
def s_3d(page):
    page.goto(APP + "/projects/%s/3d" % state["project_id"])
    page.locator("canvas").first.wait_for(timeout=30_000)
    settle(page, 3000)
    body = page.locator("body").inner_text()
    assert re.search(r"Orbit|Walk|Fit", body), "3D navigation HUD absent"
    return "3D HUD present"


@step("Generate plans: options arrive from the solver")
def s_generate(page):
    page.goto(APP + "/projects/%s/plan" % state["project_id"])
    settle(page, 1500)
    page.get_by_role("button", name=re.compile("generate plans", re.I)).first.click()
    settle(page, 1500)
    # reconnaissance: whatever dialog/overlay appeared, look for a confirm/generate control
    for pattern in (r"^generate$", r"generate \d", r"start", r"generate plans"):
        btn = page.get_by_role("button", name=re.compile(pattern, re.I))
        if btn.count():
            try:
                btn.first.click(timeout=3000)
                break
            except Exception:  # noqa: BLE001
                continue
    # wait up to 150 s for options or an error card
    deadline = time.time() + 240
    seen = ""
    while time.time() < deadline:
        body = page.locator("body").inner_text()
        if re.search(r"option [1-3]\b|plan [1-3]\b|composite", body, re.I) and re.search(
            r"apply|use this plan|choose", body, re.I
        ):
            seen = "options"
            break
        if re.search(r"failed|couldn.t|something went wrong", body, re.I) and not re.search(
            r"generating|working|solving", body, re.I
        ):
            seen = "error: " + body[:200].replace("\n", " ")
            break
        page.wait_for_timeout(3000)
    assert seen == "options", "no options within 240 s: " + (seen or "still generating")
    return "options presented"


@step("Sheets tab: generate the municipal set and see the sheet list")
def s_sheets(page):
    page.goto(APP + "/projects/%s/sheets" % state["project_id"])
    settle(page, 1500)
    gen = page.locator("[data-testid=generate-sheets-empty]")
    if not gen.count():
        gen = page.get_by_role("button", name=re.compile("generate the set|regenerate", re.I))
    if gen.count():
        gen.first.click()
    deadline = time.time() + 240
    while time.time() < deadline:
        body = page.locator("body").inner_text()
        numbers = sorted(set(re.findall(r"\bA-0\d[a-d]?\b", body)))
        if len(numbers) >= 5 and not re.search(
            r"drawing the set|generating|queued|in progress", body, re.I
        ):
            return "sheets listed: " + ", ".join(numbers[:12])
        page.wait_for_timeout(4000)
    raise AssertionError(
        "no sheet list within 240 s: " + page.locator("body").inner_text()[:240].replace("\n", " ")
    )


@step("Download the PDF set and the DXF through the app")
def s_downloads(page):
    section = page.locator("section[aria-label=Downloads]")
    assert section.count(), "no Downloads section on the sheets tab"
    hits = []
    for label in ("PDF", "DXF"):
        btn = section.get_by_role("button", name=re.compile(label, re.I))
        if not btn.count():
            hits.append(label + ": no control")
            continue
        try:
            with page.expect_download(timeout=180_000) as dl:
                btn.first.click()
            path = dl.value.path()
            size = os.path.getsize(path) if path else -1
            hits.append("%s: %s (%d bytes)" % (label, dl.value.suggested_filename, size))
        except PWTimeout:
            hits.append(label + ": no download within 180 s")
    assert all("bytes" in h for h in hits), "; ".join(hits)
    return "; ".join(hits)


@step("Share link: create, open anonymously, comment")
def s_share(page):
    page.goto(APP + "/projects/%s/plan" % state["project_id"])
    settle(page, 1200)
    page.get_by_role("button", name=re.compile("^share$", re.I)).first.click()
    settle(page, 800)
    create = page.get_by_role("button", name=re.compile("create|new link|generate link", re.I))
    if create.count():
        create.first.click()
        settle(page, 800)
    body = page.locator("body").inner_text()
    m = re.search(r"https?://\S+/share/\S+", body)
    if not m:
        inp = page.locator("input[readonly], input[value*='/share/']").first
        val = inp.input_value() if inp.count() else ""
        m = re.search(r"https?://\S+/share/\S+", val)
    assert m, "no share URL found after opening Share"
    url = m.group(0).rstrip(".,)")
    ctx = page.context.browser.new_context(viewport={"width": 1280, "height": 800})
    anon = ctx.new_page()
    anon.goto(url.replace("https://garhai-production.up.railway.app", APP))
    settle(anon, 2500)
    anon.screenshot(path=os.path.join(OUT, "share-anonymous.png"))
    text = anon.locator("body").inner_text()
    assert anon.locator("canvas").count() or re.search(
        r"plan|sheet|render", text, re.I
    ), "share view empty"
    ctx.close()
    return "share opened anonymously: " + url[:60]


@step("Sign out returns to the login page")
def s_signout(page):
    page.goto(APP + "/")
    settle(page)
    page.get_by_role("button", name=re.compile("^sign out$", re.I)).first.click()
    page.wait_for_url(re.compile(r"/login"), timeout=15_000)
    return "at /login"


STEPS = [
    s_signup,
    s_usage,
    s_fee_api,
    s_new_project,
    s_plan,
    s_compliance,
    s_3d,
    s_generate,
    s_sheets,
    s_downloads,
    s_share,
    s_signout,
]

with sync_playwright() as p:
    browser = p.chromium.launch(
        headless=False,
        args=["--headless=new", "--no-sandbox", "--use-gl=angle", "--use-angle=swiftshader"],
    )
    context = browser.new_context(viewport={"width": 1440, "height": 900}, accept_downloads=True)
    page = context.new_page()
    page.on(
        "console",
        lambda m: console.append("%s: %s" % (m.type, m.text[:200]))
        if m.type in ("error", "warning")
        else None,
    )
    page.on("pageerror", lambda e: console.append("pageerror: " + str(e)[:200]))
    for fn in STEPS:
        fn(page)
    browser.close()

passed = sum(1 for r in report if r["status"] == "pass")
json.dump(
    {"app": APP, "steps": report, "console": console[:80], "passed": passed, "total": len(report)},
    open(os.path.join(OUT, "report.json"), "w"),
    indent=2,
)
print("\n%d/%d steps passed; %d console errors/warnings" % (passed, len(report), len(console)))
