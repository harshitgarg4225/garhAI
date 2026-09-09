# Browser UAT of the architect's journey

`journey.py` walks the product the way a trial architect would, in a real Chromium,
against a running stack: sign up with the emailed code → dashboard usage card with the
platform fee → fee over the API → new project from a ready-made plan → Plan tab (fit
all, checks strip) → Compliance tab → 3D → Generate (options arrive) → Sheets (the
municipal set is drawn and listed) → PDF and DXF downloads → share link opened
anonymously with a comment → sign out. Twelve steps; every step records pass or fail
with its timing and a screenshot, and the run never aborts on the first failure, so
`report.json` is a full picture.

It is written the way the public `webapp-testing` skill (anthropics/skills) prescribes:
native Python Playwright, settle-on-networkidle waits, reconnaissance before action,
screenshots at every step, console errors captured into the report.

## Run it

```bash
# a local stack: postgres, redis, an S3-compatible store, the api, the three workers, vite
pip install playwright==1.48.0 httpx      # into the repo's .venv
python -m playwright install chromium     # or point PLAYWRIGHT_BROWSERS_PATH at a install
APP_URL=http://localhost:5173 API_URL=http://localhost:8000/api/v1 \
  python e2e/uat/journey.py ./uat-out
```

`./uat-out/report.json` lists the steps; `NN-<step>.png` is the screen at the end of each.
The run needs `DEV_ECHO_OTP=1` on the api (the sign-up step reads the code from the
response the way the dev stack echoes it) and the mock render provider; it spends two
generations of the trial allowance on the account it creates.

## What the first eleven runs found

Every failure was a product defect, not a test defect, and each is recorded in
`docs/trial-readiness.md`: the compliance report thrown away over vastu rows; Generate
answering "no plan cleared" for a plan the solver had produced (single-seed search);
a zero-option run still charged; a reload during a token refresh signing the architect
out; three delivered plans dropped by the options screen's scalar-only rule schema;
"Generate the set" refusing a project that never saved a version; the Sheets tab
listening for the drawn set on a stream the API never served; a terminal job event
outrunning the row it announced ("still generating" over a delivered plan); and the
download step failing three different ways in three runs — the signed link dropped by
the view-model, a Content-Disposition on the redirect that browsers discard, and the
export request itself refused with a 422 because the client sent a key the server's
schema had never declared.

One test-side defect too: the sheet-list regex missed the lettered sheet numbers
(`A-02A`) and reported a rendered set as missing. Read a failing step's screenshot
before believing it.

Runs 10, 11 and 12 passed all twelve steps; run 11 confirmed the downloads arrive
under the project's name (`<project>-drawing-set-<date>.pdf`) rather than a generic
stem, and run 12 was the first on the merged build wave (drawings, billing, team,
compliance).

Read the console, not only the steps. Run 12's twelve green steps carried a real
defect in their console: the Manifold WebAssembly module behind opening holes in 3D
never compiled ("expected magic word 00 61 73 6d, found 3c 21 64 6f" — the bytes of
`<!do`, an HTML page). The loader resolves `manifold.wasm` relative to the bundled
script, the dev server answers with the SPA fallback, and every session has silently
run the no-holes fallback. The other console lines are known noise: the six 404s are
`GET /projects/:id/underlay` for a project with no underlay (the client treats that
`no_underlay` answer as null by design), the React Router v7 future-flag warnings, and
headless Chromium's software-WebGL notice.
