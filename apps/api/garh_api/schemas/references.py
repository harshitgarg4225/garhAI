"""The inspiration board's API shapes (§11).

A reference is a picture plus four answers the architect gives: where it applies, what
to take from it, what to leave, and how hard to push. Those four are the feature — a
picture alone is ambiguous, because "use this kitchen" could mean the cabinets, the
island or the light.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import Field, StrictInt, StrictStr

from garh_api.models import REFERENCE_INTENTS, REFERENCE_SCOPES
from garh_api.schemas import CamelModel, ResponseModel

_SCOPE_PATTERN = "^(%s)$" % "|".join(REFERENCE_SCOPES)
_INTENT_PATTERN = "^(%s)$" % "|".join(REFERENCE_INTENTS)

#: Long enough for a real instruction ("the walnut cabinet fronts and the brass
#: handles, not the island"), short enough that it stays a note rather than an essay
#: nobody reads back.
MAX_NOTE_CHARS = 400


class ReferenceOut(ResponseModel):
    """One picture on the board, with a freshly signed URL."""

    id: uuid.UUID
    project_id: uuid.UUID
    label: StrictStr
    scope: StrictStr
    why: StrictStr = ""
    ignore: StrictStr = ""
    intent: StrictStr = "guide"
    position: StrictInt = 0
    filename: StrictStr = ""
    width_px: StrictInt = 0
    height_px: StrictInt = 0
    #: Short-lived signed GET (§13). Minted per response, never stored.
    image_url: StrictStr | None = None
    created_at: datetime | None = None


class ReferenceListOut(ResponseModel):
    references: list[ReferenceOut] = Field(default_factory=list)


class ReferencePatchIn(CamelModel):
    """Partial update. Every field is one of the architect's four answers.

    All optional: annotating is something they come back to, and forcing the whole set
    on every edit is how a board ends up with one picture on it.
    """

    label: StrictStr | None = Field(default=None, min_length=1, max_length=120)
    scope: StrictStr | None = Field(default=None, pattern=_SCOPE_PATTERN)
    why: StrictStr | None = Field(default=None, max_length=MAX_NOTE_CHARS)
    ignore: StrictStr | None = Field(default=None, max_length=MAX_NOTE_CHARS)
    intent: StrictStr | None = Field(default=None, pattern=_INTENT_PATTERN)
    position: StrictInt | None = Field(default=None, ge=0, le=999)


class ReferenceConflictOut(ResponseModel):
    """Something to settle before the render is worth making."""

    kind: StrictStr
    reference_ids: list[StrictStr] = Field(default_factory=list)
    question: StrictStr
    #: What happens if the architect does nothing. Always stated: a question with an
    #: unknown default is one people dismiss.
    default: StrictStr


class ReferenceReviewOut(ResponseModel):
    """What the board contributes to one preset, and what to ask first."""

    project_id: uuid.UUID
    preset: StrictStr
    #: The references this view can actually use, in the architect's own order.
    applies: list[ReferenceOut] = Field(default_factory=list)
    #: Chosen but not usable here — listed so the architect knows, rather than
    #: wondering why a picture they picked changed nothing.
    not_in_view: list[ReferenceOut] = Field(default_factory=list)
    conflicts: list[ReferenceConflictOut] = Field(default_factory=list)
    #: The prompt fragments the render would receive. Shown so the instruction an
    #: architect wrote and the instruction the model gets are the same thing.
    positive: StrictStr = ""
    negative: StrictStr = ""
