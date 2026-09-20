"""The rules path must import on a bare interpreter, and this proves it statically.

``make bare`` runs the rule fixtures, the copilot corpus and the fixture derivations on
a Python with no third-party packages at all — that is the one command CLAUDE.md
promises works anywhere, and it is the gate that has caught every "this only ran because
my venv had it" regression so far. It caught one on 2026-09-20: the compliance engine
grew a parking measurement that read the furniture catalogue by importing
``garh_api.routers.catalog``, and ``garh_api.routers`` imports FastAPI at module scope,
so the copilot fixture derivation died with ``No module named 'fastapi'``.

That failure took a full ``make bare`` run to surface, in a subprocess, several call
frames deep. This file states the rule directly instead: **nothing reachable from the
bare entry points may import the HTTP layer**, at module scope or inside a function.
A function-level ``from garh_api.routers.catalog import ...`` is exactly what broke it,
so the walk reads every ``Import``/``ImportFrom`` node in the file, not just the
top-level ones.

The negative control is :func:`test_the_walk_would_catch_a_router_import`, which builds
the same closure with a deliberately bad module and requires the check to fail. Without
it this file could pass because the walk found nothing to look at.
"""

from __future__ import annotations

import ast
import os
import subprocess
import sys
from collections.abc import Iterator

API_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPO_ROOT = os.path.abspath(os.path.join(API_ROOT, "..", ".."))

#: What ``make bare`` actually executes, named as modules. ``compliance`` is the rules
#: entry the copilot loop folds through; ``copilot_loop`` is what the fixture generator
#: constructs; ``catalog_data`` is the table the compliance path measures against.
BARE_ROOTS: tuple[str, ...] = (
    "garh_api.compliance",
    "garh_api.copilot_loop",
    "garh_api.catalog_data",
    "garh_api.parking_geometry",
    "garh_api.paths",
)

#: Packages a bare interpreter does not have. ``garh_api.routers`` is listed as a
#: package prefix because importing any module under it executes
#: ``routers/__init__.py``, which imports FastAPI.
FORBIDDEN_PREFIXES: tuple[str, ...] = (
    "fastapi",
    "starlette",
    "sqlalchemy",
    "redis",
    "boto3",
    "httpx",
    "garh_api.routers",
    "garh_api.db",
    "garh_api.models",
)

#: Local packages the walk follows. Third-party names are checked against
#: FORBIDDEN_PREFIXES and otherwise left alone — ``structlog`` and ``pydantic`` are
#: allowed because ``services/dev_stubs.py`` installs stand-ins for them, which is the
#: documented bare-run contract.
FOLLOWED_PREFIXES: tuple[str, ...] = ("garh_api", "garh_rules", "garh_model")


def _module_path(module: str) -> str | None:
    relative = module.replace(".", os.sep)
    for candidate in (
        os.path.join(API_ROOT, relative + ".py"),
        os.path.join(API_ROOT, relative, "__init__.py"),
    ):
        if os.path.isfile(candidate):
            return candidate
    return None


def _imports_of(path: str, module: str) -> Iterator[tuple[str, int]]:
    """Every module this file imports, at any depth of the syntax tree."""
    with open(path, encoding="utf-8") as handle:
        tree = ast.parse(handle.read(), filename=path)
    package = module.rsplit(".", 1)[0] if "." in module else module
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield alias.name, node.lineno
        elif isinstance(node, ast.ImportFrom):
            if node.level:  # relative import: resolve against this module's package
                base = package.rsplit(".", max(0, node.level - 1))[0] if node.level > 1 else package
                name = "%s.%s" % (base, node.module) if node.module else base
            else:
                name = node.module or ""
            if name:
                yield name, node.lineno


def _closure(roots: tuple[str, ...]) -> tuple[dict[str, str], list[str]]:
    """Walk the local import graph. Returns (module -> file) and the offences found."""
    seen: dict[str, str] = {}
    offences: list[str] = []
    queue = list(roots)
    while queue:
        module = queue.pop()
        if module in seen:
            continue
        path = _module_path(module)
        if path is None:
            continue
        seen[module] = path
        for imported, lineno in _imports_of(path, module):
            if any(imported == bad or imported.startswith(bad + ".") for bad in FORBIDDEN_PREFIXES):
                offences.append(
                    "%s:%d imports %s"
                    % (os.path.relpath(path, REPO_ROOT).replace(os.sep, "/"), lineno, imported)
                )
            if imported.split(".")[0] in FOLLOWED_PREFIXES:
                queue.append(imported)
    return seen, offences


def test_nothing_the_bare_run_touches_imports_the_http_layer() -> None:
    seen, offences = _closure(BARE_ROOTS)
    assert len(seen) > 10, "the walk resolved almost nothing — it is not looking at the code"
    assert not offences, (
        "%d import(s) would break `make bare` on an interpreter with no packages:\n  %s\n\n"
        "The rules and compliance path runs on a bare Python. Move the data it needs out "
        "of the HTTP layer (see garh_api/catalog_data.py) instead of importing a router."
        % (len(offences), "\n  ".join(sorted(offences)))
    )


def test_the_walk_would_catch_a_router_import() -> None:
    """Negative control: point the same walk at a module that does the forbidden thing."""
    scratch = os.path.join(API_ROOT, "garh_api", "_layering_probe_.py")
    with open(scratch, "w", encoding="utf-8") as handle:
        handle.write(
            "def f():\n"
            "    from garh_api.routers.catalog import load_catalog\n\n"
            "    return load_catalog\n"
        )
    try:
        _seen, offences = _closure(("garh_api._layering_probe_",))
    finally:
        os.remove(scratch)
    assert offences, "the walk missed a function-level router import — the gate cannot go red"
    assert "garh_api.routers.catalog" in offences[0]


def test_the_catalogue_loads_on_an_interpreter_with_no_packages() -> None:
    """The static walk says it should import; this runs it on the real bare interpreter.

    ``sys.executable`` is the venv, which has everything — so this shells out to the
    ``python3`` on PATH, the one ``make bare`` uses (``PY ?= python3``). If that turns
    out to be the venv anyway the assertion still holds, it just proves less.
    """
    script = (
        "import sys; sys.path.insert(0, %r); sys.path.insert(0, %r)\n"
        "from services.dev_stubs import install_worker_dep_stubs\n"
        "install_worker_dep_stubs()\n"
        "from garh_api.catalog_data import load_catalog\n"
        "source, items = load_catalog('furniture')\n"
        "bays = [i for i in items if 'park' in str(i.get('id', '')).lower()]\n"
        "print(source, len(items), len(bays))\n" % (API_ROOT, REPO_ROOT)
    )
    result = subprocess.run(
        ["python3", "-c", script],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
        env={**os.environ, "PYTHONPATH": ""},
    )
    assert result.returncode == 0, (
        "the catalogue does not load without third-party packages:\n%s" % result.stderr[-2000:]
    )
    source, count, bays = result.stdout.split()
    assert source in ("files", "builtin")
    assert int(count) > 0, "the catalogue came back empty"
    assert int(bays) > 0, "no parking bay in the catalogue — compliance would measure zero spaces"


def test_the_bare_interpreter_really_has_no_web_stack() -> None:
    """A control on the control: if python3 had FastAPI, the test above would prove nothing."""
    result = subprocess.run(
        ["python3", "-c", "import importlib.util as u; print(bool(u.find_spec('fastapi')))"],
        capture_output=True,
        text=True,
        env={**os.environ, "PYTHONPATH": ""},
    )
    assert result.returncode == 0
    if result.stdout.strip() == "True":  # pragma: no cover - depends on the host
        print(
            "NOTE: `python3` on this machine has FastAPI installed, so "
            "test_the_catalogue_loads_on_an_interpreter_with_no_packages proves less than "
            "it does in CI, where python3 is the bare runner interpreter.",
            file=sys.stderr,
        )
