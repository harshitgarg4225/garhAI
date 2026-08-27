"""LLM integration (playbook §10) — brief parsing, the copilot, and rationales.

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

Typical use::

    provider = get_llm_provider()
    proposal = await CopilotService(provider, folder=api_folder).propose(command, model=doc)
    if proposal.applicable:
        ...  # show the diff; apply as one group on accept
"""

from __future__ import annotations

from services.llm.adapters import BriefParserAdapter, get_brief_parser
from services.llm.brief import BriefParser, BriefParseResult
from services.llm.brief_mock import synthesize_brief_parse
from services.llm.copilot import (
    CopilotProposal,
    CopilotService,
    DryRunFolder,
    FoldIssue,
    FoldOutcome,
    RulesChecker,
    SchemaOnlyFolder,
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
    COPILOT_SCHEMA,
    RATIONALE_SCHEMA,
    SCHEMAS_BY_TASK,
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
    "COPILOT_SCHEMA",
    "PROVIDER_NAMES",
    "RATIONALE_SCHEMA",
    "SCHEMAS_BY_TASK",
    "BriefParseResult",
    "BriefParser",
    "BriefParserAdapter",
    "CopilotProposal",
    "CopilotService",
    "DryRunFolder",
    "FoldIssue",
    "FoldOutcome",
    "LlmError",
    "LlmProvider",
    "LlmRefusalError",
    "LlmResult",
    "LlmTask",
    "LlmUnavailableError",
    "LlmUsage",
    "OpCatalog",
    "OpCatalogError",
    "Rationale",
    "RationaleWriter",
    "RulesChecker",
    "SchemaGate",
    "SchemaOnlyFolder",
    "SchemaViolationError",
    "get_brief_parser",
    "get_llm_provider",
    "get_op_catalog",
    "synthesize_brief_parse",
]
