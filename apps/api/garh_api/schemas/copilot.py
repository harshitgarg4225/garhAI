"""Request/response models for ``POST /projects/:id/copilot`` (playbook §10, §11).

The response is shaped for the §12 DiffPreview component — the ONE component the
copilot shares with the solver: an op list with a plain-language line per op, plus
the ``groupId``/``baseIdx`` the client needs to apply the diff through the ordinary
op sequencer. There is deliberately no "apply" flag anywhere in these models: the
copilot route proposes, the client applies via ``POST /ops`` after human review.
"""

from __future__ import annotations

import uuid
from typing import Any, Literal, Optional

from pydantic import Field, StrictBool, StrictInt, StrictStr

from garh_api.schemas import CamelModel, ResponseModel

#: One command's text. Long enough for a real sentence, short enough that nobody
#: pastes a whole brief (that is what /brief/parse is for).
MAX_COMMAND_LENGTH = 2_000

#: What a proposal turned out to be. `ops` is the only one carrying an applicable diff.
CopilotOutcome = Literal["ops", "needsClarification", "cannotDo", "invalid"]

#: What the client reports back after the human decided (§10's eval log).
CopilotDecision = Literal["applied", "rejected"]


class CopilotCommandIn(CamelModel):
    """One natural-language editing command plus what the architect has open."""

    text: StrictStr = Field(min_length=1, max_length=MAX_COMMAND_LENGTH)
    active_storey_id: Optional[StrictStr] = Field(
        default=None,
        max_length=64,
        description="The storey tab the architect is looking at, e.g. storey_<ULID>.",
    )
    selection_ids: list[StrictStr] = Field(
        default_factory=list,
        max_length=20,
        description="Currently selected element ids — lets 'this wall' resolve.",
    )
    version_branch: Optional[uuid.UUID] = Field(
        default=None, description="Defaults to the project's active branch."
    )


class CopilotOpOut(ResponseModel):
    """One proposed op, with the plain-language line the diff panel shows (§10)."""

    type: str
    payload: dict[str, Any]
    description: str


class CopilotIssueOut(ResponseModel):
    """Why a proposal was rejected — same shape as the op sequencer's 422 issues."""

    code: str
    message: str
    severity: str = "error"
    element_ids: list[str] = Field(default_factory=list)
    field: Optional[str] = None


class CopilotProposeOut(ResponseModel):
    """A previewable, reversible proposal — never an applied change.

    When ``outcome == "ops"``: the client folds ``ops`` locally for the before/after
    mini-canvases, shows ``description`` per op, and on Apply dispatches the same ops
    to ``POST /projects/:id/ops`` with this ``groupId`` (one undo group) and
    ``source: "copilot"`` at ``baseIdx``. On Reject it sends nothing — nothing
    happened.
    """

    outcome: CopilotOutcome
    intent: str
    ops: list[CopilotOpOut] = Field(default_factory=list)
    needs_clarification: Optional[str] = None
    cannot_do: Optional[str] = None
    issues: list[CopilotIssueOut] = Field(default_factory=list)
    #: Mint once per proposal; applying with it makes the whole diff one undo group.
    group_id: uuid.UUID
    #: The branch HEAD this proposal was validated against. Apply with this baseIdx;
    #: a 409 means the design moved and the diff needs re-proposing.
    base_idx: int
    version_branch: uuid.UUID
    provider: str
    attempts: int = 1
    self_corrected: bool = False
    #: Whether the rules engine actually ran on the dry-run result (it cannot before
    #: a plot boundary exists). Honest reporting, not a pass.
    rules_checked: bool = True
    #: §14 budget telemetry: the dry-run fold's duration for this proposal.
    dry_run_ms: float = 0.0


class CopilotDecisionIn(CamelModel):
    """The human verdict on a proposal — closes §10's eval-log loop.

    The apply itself goes through ``POST /ops``; this endpoint only records
    applied/rejected so the eval corpus can learn from real usage.
    """

    command: StrictStr = Field(min_length=1, max_length=MAX_COMMAND_LENGTH)
    outcome: CopilotDecision
    ops_count: StrictInt = Field(ge=0, le=64)
    group_id: Optional[uuid.UUID] = Field(
        default=None, description="The proposal's groupId, for correlation."
    )
    intent: Optional[StrictStr] = Field(default=None, max_length=300)


class CopilotDecisionOut(ResponseModel):
    logged: StrictBool = True


__all__ = [
    "MAX_COMMAND_LENGTH",
    "CopilotCommandIn",
    "CopilotDecision",
    "CopilotDecisionIn",
    "CopilotDecisionOut",
    "CopilotIssueOut",
    "CopilotOpOut",
    "CopilotOutcome",
    "CopilotProposeOut",
]
