#!/usr/bin/env python3
"""Prove the §9 render catalogue is one catalogue — on a bare python3.9.

``services/render`` is the source of truth for presets, their legal modes and the
8-shot client pack. Two consumers deliberately *copy* it instead of importing it:

* ``apps/api/garh_api/routers/renders.py`` — the API must not import ``services.*``
  (different image, different dependency set), so it declares a mirror and validates a
  pack up front. Drift there means a request the API accepts and the worker refuses,
  or eight dead jobs instead of one 422.
* ``apps/web/src/features/renders/presets.ts`` — the browser cannot import Python.
  Drift there means a preset in the picker that no worker can render.

Both mirrors say "byte-identical mirror" in a comment. A comment is not a gate: the
only enforcement was inside pytest suites that need Postgres, so on a machine without
the toolchain the mirrors were unchecked. This script is the check, and it runs
anywhere python3 does.

It reads both mirrors as TEXT (no fastapi, no node) and compares them against the
imported Python source of truth.

    python3 scripts/render_mirrors.py
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
for _path in (str(REPO_ROOT), str(REPO_ROOT / "apps" / "api")):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from services.dev_stubs import install_worker_dep_stubs  # noqa: E402

_STUBBED = install_worker_dep_stubs()

# `services/render/__init__.py` is lazy (PEP 562) precisely so these pure modules
# import without Pillow. If that ever regresses, this import is the canary.
from services.render.pack import CLIENT_PACK_SHOTS, pack_filenames, shot_seed  # noqa: E402
from services.render.types import PRESETS, RenderRequest  # noqa: E402

API_ROUTER = REPO_ROOT / "apps/api/garh_api/routers/renders.py"
WEB_PRESETS = REPO_ROOT / "apps/web/src/features/renders/presets.ts"

_FAILURES: list[str] = []


def check(label: str, condition: bool, detail: Any = "") -> None:
    if condition:
        print("  ok    %s" % label)
    else:
        print("  FAIL  %s%s" % (label, (" — %s" % (detail,)) if detail else ""))
        _FAILURES.append(label)


def _code_only(path: Path) -> str:
    """A module's source with every docstring and comment removed.

    Needed because a module that *promises* not to call ``os.urandom`` says so in
    prose, and a grep for the promise reads exactly like a grep for the sin. ``ast``
    parses without importing, so this works on modules whose dependencies (Pillow) are
    absent.
    """
    import ast
    import io
    import tokenize

    source = path.read_text(encoding="utf-8")
    docstrings: set[int] = set()
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            body = getattr(node, "body", None) or []
            first = body[0] if body else None
            if (
                isinstance(first, ast.Expr)
                and isinstance(first.value, ast.Constant)
                and isinstance(first.value.value, str)
            ):
                for line in range(first.lineno, (first.end_lineno or first.lineno) + 1):
                    docstrings.add(line)
    comments: set[int] = set()
    for token in tokenize.generate_tokens(io.StringIO(source).readline):
        if token.type == tokenize.COMMENT:
            comments.add(token.start[0])
    return "\n".join(
        line
        for number, line in enumerate(source.splitlines(), start=1)
        if number not in docstrings and number not in comments
    )


def _block(source: str, name: str, open_char: str, close_char: str) -> str:
    """The text of a top-level ``NAME ... = (…)``/``{…}`` literal."""
    match = re.search(
        r"^%s[^=\n]*=\s*%s(.*?)^%s"
        % (re.escape(name), re.escape(open_char), re.escape(close_char)),
        source,
        re.S | re.M,
    )
    if match is None:
        raise SystemExit("could not find %s in %s" % (name, API_ROUTER))
    return match.group(1)


def main() -> int:
    print("==> render catalogue mirrors (§9)")
    if _STUBBED:
        print("    stubbed dependencies: %s" % ", ".join(sorted(_STUBBED)))

    source_shots = [(s.slug, s.preset, s.mode) for s in CLIENT_PACK_SHOTS]
    source_modes = {key: tuple(spec.modes) for key, spec in PRESETS.items()}

    # -- the API mirror ----------------------------------------------------
    api_src = API_ROUTER.read_text(encoding="utf-8")

    api_shots = [
        tuple(row)
        for row in re.findall(
            r'\(\s*"([^"]+)"\s*,\s*"([^"]+)"\s*,\s*"([^"]+)"\s*\)',
            _block(api_src, "CLIENT_PACK_SHOTS", "(", ")"),
        )
    ]
    check(
        "API CLIENT_PACK_SHOTS matches services/render/pack.py",
        api_shots == source_shots,
        "api=%s src=%s" % (api_shots, source_shots),
    )

    api_modes = {
        key: tuple(re.findall(r'"([^"]+)"', body))
        for key, body in re.findall(
            r'"([^"]+)":\s*\(([^)]*)\)', _block(api_src, "PRESET_MODES", "{", "}")
        )
    }
    check(
        "API PRESET_MODES matches services/render/types.py PRESETS",
        api_modes == source_modes,
        "api=%s src=%s" % (api_modes, source_modes),
    )

    # The pack's derived-seed rule is a formula, mirrored in prose in two places and
    # in code in three. shot_seed(base, i) == base + i is the whole contract; the API
    # writes it inline as `base_seed + index`.
    check(
        "shot_seed is base+i",
        [shot_seed(1000, i) for i in range(len(source_shots))]
        == [1000 + i for i in range(len(source_shots))],
    )
    check(
        "the API derives its per-shot seed the same way",
        re.search(r'"seed":\s*base_seed\s*\+\s*index', api_src) is not None,
    )

    # -- the web mirror ----------------------------------------------------
    if not WEB_PRESETS.exists():
        check("apps/web presets.ts exists", False, WEB_PRESETS)
    else:
        web_src = WEB_PRESETS.read_text(encoding="utf-8")
        web_ids = re.findall(r"\bid:\s*'([^']+)'", web_src)
        check(
            "web preset ids match PRESETS (same set, no extras)",
            sorted(set(web_ids)) == sorted(source_modes),
            "web=%s src=%s" % (sorted(set(web_ids)), sorted(source_modes)),
        )
        check("web preset ids are unique", len(web_ids) == len(set(web_ids)), web_ids)
        web_slugs = re.findall(r"\bslug:\s*'([^']+)'", web_src)
        check(
            "web pack slugs match the pack, in order",
            web_slugs == [slug for slug, _p, _m in source_shots],
            "web=%s src=%s" % (web_slugs, [s[0] for s in source_shots]),
        )

    # -- shape invariants the whole feature leans on -----------------------
    check("the pack is 8 shots (§9)", len(source_shots) == 8, len(source_shots))
    check(
        "interiors are Explore-only",
        all(
            modes == ("explore",)
            for key, modes in source_modes.items()
            if key.startswith("interior-")
        ),
        source_modes,
    )
    check(
        "every pack shot uses a mode its preset allows",
        all(mode in source_modes[preset] for _slug, preset, mode in source_shots),
        [(s, p, m) for s, p, m in source_shots if m not in source_modes[p]],
    )
    # -- determinism by seed (§9/§14), without Pillow -----------------------
    # The mock provider's whole randomness budget is random.Random(seed material), and
    # the material comes from RenderRequest alone. Prove that here rather than in a
    # Pillow test nobody on this machine can run.
    def request(**over: Any) -> Any:
        base = dict(
            viewport_png=b"x",
            mode="explore",
            preset="exterior-street-day",
            seed=7,
            size=(1536, 1024),
        )
        base.update(over)
        return RenderRequest(**base)  # type: ignore[arg-type]

    check(
        "identical requests derive identical seed material",
        request().grade_seed_material() == request().grade_seed_material(),
    )
    varied = {
        "seed": request(seed=8),
        "preset": request(preset="exterior-night"),
        "mode": request(mode="precise", depth_png=b"d"),
        "size": request(size=(1024, 1024)),
    }
    baseline = request().grade_seed_material()
    for field_name, req in varied.items():
        check(
            "changing %s changes the seed material" % field_name,
            req.grade_seed_material() != baseline,
        )
    check(
        "the seed material names no clock, pid or address",
        "0x" not in baseline and baseline == "garh-mock|7|exterior-street-day|explore|1536x1024",
        baseline,
    )
    # Scan CODE, not prose: the module docstring legitimately mentions "os.urandom" in
    # the course of promising not to use it, and `time.monotonic()` is fine — it times
    # the render for the §14 budget log, it never reaches the grade.
    mock_code = _code_only(REPO_ROOT / "services/render/mock.py")
    banned = [token for token in ("urandom(", "random.seed(", "time.time(", "id(") if token in mock_code]
    check(
        "mock.py seeds only from the request",
        "random.Random(req.grade_seed_material())" in mock_code and not banned,
        banned,
    )
    check(
        "mock.py constructs no other Random",
        mock_code.count("random.Random(") == 1,
        mock_code.count("random.Random("),
    )

    names = pack_filenames()
    check(
        "pack filenames are ordered and unique (01-… .. 08-…)",
        len(set(names)) == len(names) == 8 and names[0].startswith("01-"),
        names,
    )

    print("")
    if _FAILURES:
        print("FAILED %d mirror check(s):" % len(_FAILURES))
        for label in _FAILURES:
            print("  - %s" % label)
        print("")
        print("Fix the MIRROR, not services/render — services/render is the source.")
        return 1
    print("  render mirrors are in sync")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
