"""LLM integration (playbook §10) — brief parsing, the copilot, rationales, explanations.

Imported by the API rather than run as a worker: these calls are request-scoped and
short, and none of them belongs on a queue.

Three locked properties hold across this package:

1. **Mock by default.** ``PROVIDER_LLM=mock`` is the default, its fixtures are
   schema-validated against the same schemas the real provider must satisfy, and
   importing anything here pulls in no SDK. Zero keys, full product.
2. **LLMs never emit geometry.** The copilot returns typed ops from the §4 taxonomy —
   generated into its prompt from ``ops.schema.json`` so the prompt cannot drift from
   what the system accepts — and every op is schema-checked, dry-run folded and
   rules-checked before a human sees a diff.
3. **Assumptions are visible.** Brief parsing emits an assumption chip for every value
   the client did not state, and the parser manufactures one for anything the model
   filled in silently.
4. **Numbers come from the engine, never the model.** The compliance explainer forwards
   the rules engine's own numbers and citation and fact-checks the prose against them;
   a fabricated number falls back to a deterministic, cited explanation.

Typical use::

    provider = get_llm_provider()
    service = CopilotService(provider, folder=api_folder)

    # Blocking:
    proposal = await service.propose(command, model=doc, history=turns)
    if proposal.applicable:
        ...  # show the diff; apply as one group on accept

    # Streaming (same gates, same generator — see services.llm.streaming):
    async for event in service.propose_stream(command, model=doc, history=turns):
        ...  # StageEvent | TextDelta, then exactly one ProposalEvent
"""

from __future__ import annotations

from services.llm.adapters import BriefParserAdapter, get_brief_parser
from services.llm.brief import BriefParser, BriefParseResult
from services.llm.brief_mock import synthesize_brief_parse
from services.llm.conversation import (
    MAX_HISTORY_TURNS,
    TURN_STATUSES,
    ConversationContext,
    ConversationRedactionError,
    ConversationTurn,
)
from services.llm.copilot import (
    CopilotProposal,
    CopilotService,
    DryRunFolder,
    FoldIssue,
    FoldOutcome,
    ProposalEvent,
    RulesChecker,
    SchemaOnlyFolder,
)
from services.llm.explainer import (
    ComplianceExplainer,
    Explanation,
    NotExplainable,
    compose_explanation,
    finding_facts,
    verify_explanation,
)
from services.llm.op_catalog import OpCatalog, OpCatalogError, get_op_catalog
from services.llm.provider import (
    PROVIDER_NAMES,
    LlmProvider,
    SchemaGate,
    get_llm_provider,
)
from services.llm.rationale import Rationale, RationaleWriter
from services.llm.schemas import (
    BRIEF_PARSE_SCHEMA,
    COMPLIANCE_EXPLAIN_SCHEMA,
    COPILOT_SCHEMA,
    RATIONALE_SCHEMA,
    SCHEMAS_BY_TASK,
)
from services.llm.streaming import (
    Answer,
    ProviderDraft,
    StageEvent,
    StreamingLlmProvider,
    TextDelta,
    guarded_stream,
)
from services.llm.types import (
    LlmError,
    LlmRefusalError,
    LlmResult,
    LlmTask,
    LlmUnavailableError,
    LlmUsage,
    SchemaViolationError,
)

__all__ = [
    "BRIEF_PARSE_SCHEMA",
    "COMPLIANCE_EXPLAIN_SCHEMA",
    "COPILOT_SCHEMA",
    "MAX_HISTORY_TURNS",
    "PROVIDER_NAMES",
    "RATIONALE_SCHEMA",
    "SCHEMAS_BY_TASK",
    "TURN_STATUSES",
    "Answer",
    "BriefParseResult",
    "BriefParser",
    "BriefParserAdapter",
    "ComplianceExplainer",
    "ConversationContext",
    "ConversationRedactionError",
    "ConversationTurn",
    "CopilotProposal",
    "CopilotService",
    "DryRunFolder",
    "Explanation",
    "FoldIssue",
    "FoldOutcome",
    "LlmError",
    "LlmProvider",
    "LlmRefusalError",
    "LlmResult",
    "LlmTask",
    "LlmUnavailableError",
    "LlmUsage",
    "NotExplainable",
    "OpCatalog",
    "OpCatalogError",
    "ProposalEvent",
    "ProviderDraft",
    "Rationale",
    "RationaleWriter",
    "RulesChecker",
    "SchemaGate",
    "SchemaOnlyFolder",
    "SchemaViolationError",
    "StageEvent",
    "StreamingLlmProvider",
    "TextDelta",
    "compose_explanation",
    "finding_facts",
    "get_brief_parser",
    "get_llm_provider",
    "get_op_catalog",
    "guarded_stream",
    "synthesize_brief_parse",
    "verify_explanation",
]
