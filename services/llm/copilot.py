"""The copilot pipeline (§10) — command → validated ops → previewable diff.

    Pipeline: dry-run fold on a fork → invariants + rules check → if invalid, feed
    reasons back once for self-correction → present diff → apply as one group on accept.

Nothing here executes anything the model said. The model's output becomes a *candidate*
op list, which must survive four gates before an architect is even shown it:

    1. response     — the whole answer validates against ``COPILOT_SCHEMA``
    2. structural   — every op validates against ``ops.schema.json`` (§4 taxonomy)
    3. semantic     — a dry-run fold on a FORK of the project doc succeeds
    4. regulatory   — the rules engine finds no NEW hard failure
    5. human        — the architect presses Apply on the diff

The last gate is the one that makes prompt injection a nuisance rather than a
vulnerability (§13): the worst a successful injection achieves is a diff a human
declines.

**Streaming changes the reading experience, not the gates.** :meth:`CopilotService.
propose_stream` yields progress beats and the model's own prose while the call is in
flight, and :meth:`CopilotService.propose` is that same generator with the beats
discarded — one implementation, so the streamed path cannot quietly gate less. Gate 1
runs in :func:`services.llm.streaming.guarded_stream`, on the complete object, before
gate 3 ever sees it; streamed prose is display text that nothing parses.

**What this module owns and what it delegates.** The control flow, the gates, the single
self-correction round and the refusal to approximate are here and complete. The fold
itself is not: folding is ``packages/model``'s job, so :class:`DryRunFolder` is a
Protocol the API supplies. Since Phase 6 the production implementation is
``garh_api.copilot_loop.ModelFolder`` — the REAL ``garh_model`` fold, dry-run on a
fork — wired in by ``garh_api.routers.copilot``. :class:`SchemaOnlyFolder` remains the
honest structural fallback for prompt-contract tests that have no model core on their
path; it says exactly that in its own ``describe()`` and must never serve a request.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping, Sequence
from dataclasses import dataclass, field, replace
from typing import Any, Protocol, runtime_checkable

from services.common.logging import get_logger
from services.llm.conversation import ConversationContext
from services.llm.op_catalog import OpCatalog, get_op_catalog
from services.llm.prompts import copilot_repair_user, copilot_system, copilot_user
from services.llm.provider import LlmProvider
from services.llm.redaction import strip_pii
from services.llm.schemas import COPILOT_SCHEMA
from services.llm.streaming import Answer, StageEvent, guarded_stream
from services.llm.types import LlmTask, LlmUsage

log = get_logger("llm.copilot")

#: §10 allows exactly one self-correction round. Not configurable: an unbounded repair
#: loop spends a user's patience to produce nothing, and two failures in a row mean the
#: command needs a human, not another sample.
SELF_CORRECTION_ROUNDS = 1


@dataclass(frozen=True)
class FoldIssue:
    """One reason a candidate op list was rejected.

    Shape mirrors ``packages/model/schema/validation-issue.schema.json`` so the API can
    return these straight through and the client already knows how to render them.
    """

    code: str
    message: str
    severity: str = "error"
    element_ids: tuple[str, ...] = ()
    field: str | None = None

    def to_json(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "code": self.code,
            "message": self.message,
            "severity": self.severity,
            "elementIds": list(self.element_ids),
        }
        if self.field:
            out["field"] = self.field
        return out

    def for_llm(self) -> str:
        where = " (%s)" % self.field if self.field else ""
        return "%s%s: %s" % (self.code, where, self.message)


@dataclass(frozen=True)
class FoldOutcome:
    """Result of dry-running ops against a fork of the project document."""

    ok: bool
    issues: tuple[FoldIssue, ...] = ()
    #: Folded document, when the fold succeeded. Used for the rules check and the diff.
    model_after: Mapping[str, Any] | None = None
    #: Human-readable op summaries for the diff panel ("Added a 1200mm window").
    plain_language: tuple[str, ...] = ()


@runtime_checkable
class DryRunFolder(Protocol):
    """Folds candidate ops onto a FORK. Must never mutate the live document."""

    def dry_run(
        self, ops: Sequence[Mapping[str, Any]], *, model: Mapping[str, Any] | None
    ) -> FoldOutcome: ...

    def describe(self) -> str:
        """One line naming what this folder actually checks. Shown in logs."""
        ...


@runtime_checkable
class RulesChecker(Protocol):
    """Runs the §6 rules engine over a folded model."""

    def check(self, model: Mapping[str, Any]) -> Sequence[Mapping[str, Any]]: ...


@dataclass(frozen=True)
class CopilotProposal:
    """What the UI renders: an intent, a diff, and the ops that produce it.

    ``applicable`` is the only thing the API should key on. When it is ``False`` the
    ops list is empty by construction — there is no path that returns "sort of valid"
    ops.
    """

    applicable: bool
    intent: str
    ops: tuple[Mapping[str, Any], ...] = ()
    plain_language: tuple[str, ...] = ()
    needs_clarification: str | None = None
    cannot_do: str | None = None
    issues: tuple[FoldIssue, ...] = ()
    model_after: Mapping[str, Any] | None = None
    attempts: int = 1
    self_corrected: bool = False
    usage: LlmUsage = field(default_factory=LlmUsage)
    meta: Mapping[str, Any] = field(default_factory=dict)

    def to_json(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "applicable": self.applicable,
            "intent": self.intent,
            "ops": [dict(op) for op in self.ops],
            "plainLanguage": list(self.plain_language),
            "attempts": self.attempts,
            "selfCorrected": self.self_corrected,
        }
        if self.needs_clarification:
            out["needsClarification"] = self.needs_clarification
        if self.cannot_do:
            out["cannotDo"] = self.cannot_do
        if self.issues:
            out["issues"] = [issue.to_json() for issue in self.issues]
        return out

    def log_record(self, command: str, outcome: str) -> dict[str, Any]:
        """§10: "Log {command, ops, applied|rejected|invalid} for the eval set".

        ``command`` arrives already masked from the route. ``intent`` is masked *here*
        because it is the model's own paraphrase of that same command: "Widen Ramesh
        Kumar's bedroom door" leaks exactly what masking the command was for, and
        relying on every caller to remember that is how the leak comes back.
        """
        return {
            "command": command,
            "opTypes": [str(op.get("type")) for op in self.ops],
            "opCount": len(self.ops),
            "outcome": outcome,
            "intent": strip_pii(self.intent),
            "selfCorrected": self.self_corrected,
        }


@dataclass(frozen=True)
class ProposalEvent:
    """Terminal event of :meth:`CopilotService.propose_stream`.

    A stream always ends with exactly one of these. Everything before it is a stage
    beat or display prose, so a client that only understands this event still gets the
    complete, fully-gated answer — streaming adds beats, it never becomes the answer.
    """

    proposal: CopilotProposal

    def to_json(self) -> dict[str, Any]:
        return {"type": "proposal", **self.proposal.to_json()}


class SchemaOnlyFolder:
    """A :class:`DryRunFolder` that checks structure and **nothing else**.

    Real and useful — it catches unknown op types, malformed payloads, float lengths and
    bad ids — but it does not simulate geometry, so it cannot tell you an opening is
    wider than its wall or that a wall crosses a stair. Wire the real Python fold in as
    soon as ``garh_model`` lands; until then this keeps the pipeline exercisable
    end-to-end without pretending to be more than it is.
    """

    def __init__(self, catalog: OpCatalog | None = None) -> None:
        self.catalog = catalog or get_op_catalog()

    def describe(self) -> str:
        return "schema-only (structure checked; geometry NOT simulated)"

    def dry_run(
        self, ops: Sequence[Mapping[str, Any]], *, model: Mapping[str, Any] | None
    ) -> FoldOutcome:
        failures = self.catalog.validate_ops(list(ops))
        if failures:
            issues = tuple(
                FoldIssue(
                    code="OP_SCHEMA_INVALID",
                    message=failure.message,
                    field=failure.path or None,
                )
                for failure in failures
            )
            return FoldOutcome(ok=False, issues=issues)
        return FoldOutcome(
            ok=True,
            model_after=model,
            plain_language=tuple(_plain(op) for op in ops),
        )


class CopilotService:
    """Command in, previewable diff out — with every gate in between."""

    def __init__(
        self,
        provider: LlmProvider,
        *,
        catalog: OpCatalog | None = None,
        folder: DryRunFolder | None = None,
        rules: RulesChecker | None = None,
    ) -> None:
        self.provider = provider
        self.catalog = catalog or get_op_catalog()
        self.folder: DryRunFolder = folder or SchemaOnlyFolder(self.catalog)
        self.rules = rules

    async def propose(
        self,
        command: str,
        *,
        model: Mapping[str, Any] | None = None,
        violations: Sequence[Mapping[str, Any]] = (),
        active_storey_id: str | None = None,
        selection_ids: Sequence[str] = (),
        history: ConversationContext | Sequence[Mapping[str, Any]] | None = None,
        max_output_tokens: int = 4_096,
    ) -> CopilotProposal:
        """The blocking form: drain :meth:`propose_stream`, keep the proposal.

        Not a parallel implementation — literally the same generator, with the stage
        beats and prose thrown away. Two implementations of a four-gate pipeline would
        drift, and the half nobody looks at would be the half that stops gating.
        """
        proposal: CopilotProposal | None = None
        async for event in self.propose_stream(
            command,
            model=model,
            violations=violations,
            active_storey_id=active_storey_id,
            selection_ids=selection_ids,
            history=history,
            max_output_tokens=max_output_tokens,
        ):
            if isinstance(event, ProposalEvent):
                proposal = event.proposal
        if proposal is None:  # pragma: no cover - the generator always ends with one
            raise RuntimeError("the copilot stream ended without a proposal")
        return proposal

    async def propose_stream(
        self,
        command: str,
        *,
        model: Mapping[str, Any] | None = None,
        violations: Sequence[Mapping[str, Any]] = (),
        active_storey_id: str | None = None,
        selection_ids: Sequence[str] = (),
        history: ConversationContext | Sequence[Mapping[str, Any]] | None = None,
        max_output_tokens: int = 4_096,
    ) -> AsyncIterator[Any]:
        """Command in; stage beats and prose out; exactly one :class:`ProposalEvent`.

        Yields :class:`~services.llm.streaming.StageEvent`,
        :class:`~services.llm.streaming.TextDelta` and finally one
        :class:`ProposalEvent`. The deltas are the model's own sentence about what it
        is doing — they exist so six silent seconds stop reading as a hang, and nothing
        reads them as data. Ops arrive only inside the terminal proposal, and only
        after every gate listed at the top of this module: the answer is schema-gated in
        :func:`~services.llm.streaming.guarded_stream` *before* it is folded, so
        streaming adds no path around §13's containment.
        """
        system = copilot_system(self.catalog)
        user = copilot_user(
            command,
            model=model,
            violations=violations,
            active_storey_id=active_storey_id,
            selection_ids=selection_ids,
            history=history,
        )
        task = LlmTask(
            name="copilot.ops",
            system=system,
            user=user,
            schema=COPILOT_SCHEMA,
            schema_name="copilot_ops",
            fixture_key=command.strip(),
            max_output_tokens=max_output_tokens,
            effort="medium",
        )

        usage = LlmUsage()
        attempts = 0
        issues: tuple[FoldIssue, ...] = ()
        current_user = user

        for round_index in range(SELF_CORRECTION_ROUNDS + 1):
            yield StageEvent("revising" if round_index else "drafting")

            result = None
            async for event in guarded_stream(self.provider, replace(task, user=current_user)):
                if isinstance(event, Answer):
                    result = event.result
                else:
                    # Stage beats and prose, straight through. `guarded_stream` yields
                    # nothing else, and anything a provider invented died there.
                    yield event

            if result is None:  # pragma: no cover - guarded_stream raises instead
                raise RuntimeError("the provider stream ended without a gated answer")

            usage = usage.plus(result.usage)
            attempts += result.attempts
            payload = result.data

            early = self._early_exit(payload, attempts, usage, result.summary())
            if early is not None:
                yield StageEvent("done")
                yield ProposalEvent(early)
                return

            ops = [dict(op) for op in payload.get("ops") or []]

            # The gates, in order. Gate 1 (the copilot response schema) already ran
            # inside guarded_stream; 2-4 are structural, semantic and regulatory, and
            # human review is the last one, in the client.
            issues = self._gate_structure(ops)
            outcome: FoldOutcome | None = None
            if not issues:
                yield StageEvent("folding")
                outcome, issues = self._gate_fold(ops, model)
            if outcome is not None:
                yield StageEvent("checking-rules")
                issues = self._gate_rules(outcome)
                if issues:
                    outcome = None

            if outcome is not None:
                yield StageEvent("done")
                yield ProposalEvent(
                    CopilotProposal(
                        applicable=True,
                        intent=str(payload.get("intent") or "Apply the requested change."),
                        ops=tuple(ops),
                        plain_language=outcome.plain_language or tuple(_plain(op) for op in ops),
                        model_after=outcome.model_after,
                        attempts=attempts,
                        self_corrected=round_index > 0,
                        usage=usage,
                        meta=result.summary(),
                    )
                )
                return

            log.info(
                "llm.copilot.rejected",
                round=round_index,
                issue_count=len(issues),
                codes=sorted({issue.code for issue in issues}),
                folder=self.folder.describe(),
            )
            if round_index >= SELF_CORRECTION_ROUNDS:
                break
            current_user = copilot_repair_user(
                user,
                proposed=payload,
                reasons="\n".join("- %s" % issue.for_llm() for issue in issues[:12]),
            )

        # Both rounds failed. Report honestly — never hand back partial ops.
        yield StageEvent("done")
        yield ProposalEvent(
            CopilotProposal(
                applicable=False,
                intent="Could not make that change safely.",
                ops=(),
                cannot_do=(
                    "I couldn't turn that into a safe edit. Could you try describing it "
                    "a different way, or make the change directly on the plan?"
                ),
                issues=issues,
                attempts=attempts,
                self_corrected=True,
                usage=usage,
            )
        )

    # ------------------------------------------------------------------
    def _early_exit(
        self,
        payload: Mapping[str, Any],
        attempts: int,
        usage: LlmUsage,
        meta: Mapping[str, Any],
    ) -> CopilotProposal | None:
        """Honour ``cannotDo`` / ``needsClarification`` before any gate runs.

        §10: "Out-of-scope … return `cannotDo` … never approximate with wrong ops."
        Ops accompanying either field are dropped rather than half-applied.
        """
        cannot_do = payload.get("cannotDo")
        clarification = payload.get("needsClarification")
        if not cannot_do and not clarification:
            return None
        intent = str(payload.get("intent") or "")
        if payload.get("ops"):
            log.warning(
                "llm.copilot.ops_with_refusal",
                hint="model returned ops alongside cannotDo/needsClarification; dropped",
            )
        return CopilotProposal(
            applicable=False,
            intent=intent or "Ask before changing anything.",
            ops=(),
            needs_clarification=str(clarification) if clarification else None,
            cannot_do=str(cannot_do) if cannot_do else None,
            attempts=attempts,
            usage=usage,
            meta=meta,
        )

    def _gate_structure(self, ops: Sequence[Mapping[str, Any]]) -> tuple[FoldIssue, ...]:
        """Gate 2 — every op validates against ``ops.schema.json`` (§4 taxonomy).

        Runs before the fold on purpose: the fold is the expensive gate and the one
        that touches geometry, so malformed ops must die in front of it.
        """
        if not ops:
            return (
                FoldIssue(
                    code="OP_EMPTY",
                    message="No ops were produced. Say what should change, or use "
                    "cannotDo if it is not possible.",
                ),
            )

        failures = self.catalog.validate_ops(list(ops))
        if not failures:
            return ()
        log.info("llm.copilot.schema_rejected", count=len(failures))
        return tuple(
            FoldIssue(
                code="OP_SCHEMA_INVALID",
                message=failure.message,
                field=failure.path or None,
            )
            for failure in failures
        )

    def _gate_fold(
        self, ops: Sequence[Mapping[str, Any]], model: Mapping[str, Any] | None
    ) -> tuple[FoldOutcome | None, tuple[FoldIssue, ...]]:
        """Gate 3 — a dry-run fold on a FORK of the project document."""
        outcome = self.folder.dry_run(list(ops), model=model)
        if outcome.ok:
            return outcome, ()
        return None, tuple(outcome.issues) or (
            FoldIssue(code="FOLD_REJECTED", message="These ops could not be applied."),
        )

    def _gate_rules(self, outcome: FoldOutcome) -> tuple[FoldIssue, ...]:
        """Gate 4 — the rules engine finds no NEW hard failure."""
        if self.rules is None or outcome.model_after is None:
            return ()
        hard = _hard_failures(self.rules.check(outcome.model_after))
        return tuple(
            FoldIssue(
                code=str(finding.get("ruleId") or "RULE_FAIL"),
                message=str(
                    finding.get("fixHint")
                    or finding.get("message")
                    or "This edit breaks a regulation."
                ),
            )
            for finding in hard
        )


def _hard_failures(findings: Sequence[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    """Only ``fail`` blocks an edit.

    Golden rule 5 — "compliance never blocks, it informs" — is about the architect's
    own edits. A *copilot* edit that introduces a hard failure is a different case: the
    architect did not choose it, so it is shown as a rejection they can override by
    making the change by hand.
    """
    return [finding for finding in findings if str(finding.get("status")) == "fail"]


def _plain(op: Mapping[str, Any]) -> str:
    """Fallback plain-language line for the diff panel.

    The real folder supplies better text (it knows room names); this keeps the diff
    readable when it does not.
    """
    op_type = str(op.get("type", "change"))
    verb, _, noun = op_type.partition(".")
    return "%s %s" % (noun.replace("_", " ").capitalize() or "Update", verb)


__all__ = [
    "SELF_CORRECTION_ROUNDS",
    "CopilotProposal",
    "CopilotService",
    "DryRunFolder",
    "FoldIssue",
    "FoldOutcome",
    "ProposalEvent",
    "RulesChecker",
    "SchemaOnlyFolder",
]
