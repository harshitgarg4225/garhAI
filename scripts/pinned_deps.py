"""Print the pinned runtime dependencies of every Python manifest in the repo.

Why this exists
---------------
Pins live in ``apps/api/pyproject.toml`` and ``services/pyproject.toml``; there is no
``requirements.txt`` anywhere (see DECISIONS.md). The container images extract the lists
with stdlib ``tomllib``, which is fine because they run Python 3.11.

``make audit`` cannot assume 3.11. It runs on whatever ``python3`` the developer has, and
``tomllib`` only landed in **3.11** — so the inline ``python -c "import tomllib; ..."``
that used to be in the Makefile died with a bare ``ModuleNotFoundError`` traceback on
3.9/3.10. That is a bad failure mode for a security target: it looks like a broken repo
rather than a missing interpreter feature, and the obvious "fix" is to delete the target.

So: use ``tomllib`` when it is importable, and fall back to a narrow line parser when it
is not. The fallback is deliberately *not* a TOML parser. It handles exactly the shape
these two files use and nothing else, and it cross-checks itself (see ``_parse_fallback``)
so a manifest reformatted into a shape it cannot read is an error rather than a silently
short list — an under-count here would mean quietly auditing fewer packages.

Usage
-----
    python3 scripts/pinned_deps.py                    # runtime deps, one per line
    python3 scripts/pinned_deps.py --extras dev       # ...plus the named extra(s)
    python3 scripts/pinned_deps.py -o requirements.txt

Exit codes: 0 ok, 2 a manifest could not be read.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import Dict, List, Sequence

#: Every Python manifest in the workspace, in install order (the API's pins win on
#: overlap because that is the order the Dockerfiles concatenate them in).
MANIFESTS: Sequence[str] = ("apps/api/pyproject.toml", "services/pyproject.toml")

#: A dependency specifier we are willing to treat as pinned: ``name==version`` with an
#: optional extras bracket. Anything looser (``>=``, a URL, a local path) is reported,
#: because "pin everything" is a repo rule and a range would make the audit
#: non-reproducible.
_PINNED = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*(?:\[[a-z0-9,._-]+\])?==[^\s;]+")


class ManifestError(RuntimeError):
    """A manifest could not be read, or read but not trusted."""


def _strip_comment(line: str) -> str:
    """Drop a trailing ``#`` comment that is not inside the quoted specifier.

    The specifiers in these files are always double-quoted, so anything after the
    closing quote is a comment. Splitting on ``#`` naively would corrupt a specifier
    containing one (no current specifier does, but a URL pin could).
    """
    quote = line.find('"')
    if quote == -1:
        return line.split("#", 1)[0]
    close = line.find('"', quote + 1)
    if close == -1:
        return line.split("#", 1)[0]
    return line[: close + 1]


def _parse_array(lines: List[str], start: int) -> List[str]:
    """Collect the double-quoted strings of an array opened on ``lines[start]``.

    Returns the strings; raises if the array is never closed (a truncated manifest must
    not read as an empty dependency list).
    """
    out: List[str] = []
    depth = 0
    for index in range(start, len(lines)):
        text = _strip_comment(lines[index])
        depth += text.count("[") - text.count("]")
        out.extend(re.findall(r'"([^"]+)"', text))
        if depth <= 0 and index > start or (depth <= 0 and "]" in text):
            return out
    raise ManifestError("unterminated array starting at line %d" % (start + 1))


def _parse_fallback(text: str, path: str) -> Dict[str, List[str]]:
    """Read ``[project].dependencies`` and ``[project.optional-dependencies]`` by hand.

    Only the layout these two manifests actually use is supported: ``dependencies = [``
    at column 0 inside ``[project]``, and one ``name = [`` array per extra inside
    ``[project.optional-dependencies]``. If the expected keys are absent the function
    raises rather than returning ``{}`` — an empty result would make ``make audit``
    pass while auditing nothing.
    """
    lines = text.splitlines()
    groups: Dict[str, List[str]] = {}
    section = ""
    for index, raw in enumerate(lines):
        stripped = raw.strip()
        if stripped.startswith("[") and stripped.endswith("]") and "=" not in stripped:
            section = stripped[1:-1]
            continue
        if section == "project" and re.match(r"^dependencies\s*=\s*\[", stripped):
            groups[""] = _parse_array(lines, index)
        elif section == "project.optional-dependencies":
            match = re.match(r"^([A-Za-z0-9_-]+)\s*=\s*\[", stripped)
            if match:
                groups[match.group(1)] = _parse_array(lines, index)
    if "" not in groups:
        raise ManifestError(
            "%s: could not find `[project] dependencies = [` with the line parser. "
            "Either the manifest was reformatted, or run this on Python 3.11+ where "
            "tomllib does the parsing." % path
        )
    return groups


def read_manifest(path: Path) -> Dict[str, List[str]]:
    """``{"": runtime_deps, "<extra>": deps}`` for one pyproject.toml.

    Prefers ``tomllib``; falls back to :func:`_parse_fallback` on older interpreters.
    """
    if not path.exists():
        raise ManifestError("%s does not exist" % path)
    text = path.read_text(encoding="utf-8")
    try:
        import tomllib
    except ImportError:
        return _parse_fallback(text, str(path))
    try:
        project = tomllib.loads(text)["project"]
    except Exception as exc:  # noqa: BLE001 - report the file, not just the error
        raise ManifestError("%s: %s" % (path, exc)) from exc
    groups: Dict[str, List[str]] = {"": list(project.get("dependencies") or [])}
    for name, specs in (project.get("optional-dependencies") or {}).items():
        groups[name] = list(specs)
    return groups


def collect(root: Path, extras: Sequence[str] = ()) -> List[str]:
    """Deduplicated, order-preserving specifier list across every manifest."""
    wanted = ("",) + tuple(extras)
    seen: Dict[str, None] = {}
    for relative in MANIFESTS:
        groups = read_manifest(root / relative)
        for group in wanted:
            for spec in groups.get(group, []):
                seen.setdefault(spec.strip(), None)
    return list(seen)


def unpinned(specs: Sequence[str]) -> List[str]:
    """Specifiers that are not a plain ``name==version`` pin."""
    return [s for s in specs if not _PINNED.match(s)]


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--extras",
        nargs="*",
        default=[],
        metavar="NAME",
        help="also include these optional-dependency groups (e.g. dev llm)",
    )
    parser.add_argument(
        "-o", "--output", metavar="PATH", help="write to PATH instead of stdout"
    )
    parser.add_argument(
        "--root",
        default=str(Path(__file__).resolve().parent.parent),
        help="repo root (default: the parent of scripts/)",
    )
    parser.add_argument(
        "--allow-unpinned",
        action="store_true",
        help="do not fail when a specifier is not an exact == pin",
    )
    args = parser.parse_args(argv)

    try:
        specs = collect(Path(args.root), args.extras)
    except ManifestError as exc:
        print("pinned_deps: %s" % exc, file=sys.stderr)
        return 2

    loose = unpinned(specs)
    if loose and not args.allow_unpinned:
        print(
            "pinned_deps: %d specifier(s) are not exact pins, which makes the audit "
            "non-reproducible: %s" % (len(loose), ", ".join(loose)),
            file=sys.stderr,
        )
        return 2

    body = "\n".join(specs) + "\n"
    if args.output:
        Path(args.output).write_text(body, encoding="utf-8")
        print(
            "pinned_deps: wrote %d specifiers to %s" % (len(specs), args.output),
            file=sys.stderr,
        )
    else:
        sys.stdout.write(body)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
