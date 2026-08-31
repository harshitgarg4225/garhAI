"""The one place the inspiration board is turned into a render job's payload.

Both render enqueue paths — the single render in ``routers/jobs.py`` and the client
pack in ``routers/renders.py`` — call :func:`board_for_render`. One source, deliberately:
a board that reached one path and not the other would mean an architect's references
worked in Explore and vanished in a client pack, which is exactly the kind of silent
half-integration CLAUDE.md's bug list is made of.

The shape is ``services.render.references.Reference.to_json()``'s, and
``tests/test_reference_payload.py`` asserts that rather than trusting the comment.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from garh_api.logging import get_logger
from garh_api.repositories import ReferenceRepository
from garh_api.tenancy import TenantCtx

_log = get_logger(__name__)


async def board_for_render(
    session: AsyncSession, ctx: TenantCtx, project_id: uuid.UUID
) -> list[dict[str, Any]]:
    """The project's annotated board, in the architect's own order.

    Unannotated pictures are dropped here rather than sent: the render side skips them
    anyway, and shipping them would make every job payload carry rows that cannot
    affect the result. The review endpoint is where the architect is told about them,
    at the moment they can still answer.

    Never raises. A board that could not be read must not stop a render — the feature
    is additive, and a render without references is the render this product made
    before the board existed.
    """
    try:
        board = await ReferenceRepository(session, ctx).list_for_project(project_id)
    except Exception as exc:  # pragma: no cover - a read failure must not block a render
        _log.warning("references.board_unavailable", project_id=str(project_id), error=str(exc))
        return []
    return [
        {
            "id": str(item.id),
            "label": item.label,
            "scope": item.scope,
            "why": item.why,
            "ignore": item.ignore_note,
            "intent": item.intent,
        }
        for item in board
        if item.why or item.ignore_note
    ]


__all__ = ["board_for_render"]
