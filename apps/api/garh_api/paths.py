"""Where the repository's data lives — resolved without importing anything.

``repo_root`` used to sit in :mod:`garh_api.routers`, which imports FastAPI at module
scope. Everything that needed a path therefore dragged the whole HTTP layer in with it,
and the rules path is required to run on a bare interpreter (``make bare``: Python with
no third-party packages at all). This module is stdlib-only for that reason, and
:mod:`garh_api.routers` re-exports ``repo_root`` so existing imports keep working.
"""

from __future__ import annotations

import os


def repo_root() -> str:
    """Filesystem root that holds ``rulepacks/`` and ``fixtures/``.

    Honours ``GARH_ROOT`` and falls back to walking up from this file, which covers both
    the container layout (``/app``) and a bare checkout.
    """
    override = os.environ.get("GARH_ROOT")
    if override:
        return override
    here = os.path.abspath(os.path.dirname(__file__))
    # garh_api -> apps/api -> apps -> <repo root>
    return os.path.abspath(os.path.join(here, "..", "..", ".."))


__all__ = ["repo_root"]
