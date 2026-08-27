"""The Phase-6 copilot validation pipeline (§10) — pure, testable, no I/O.

This module is the seam Phase 0 deliberately left open: ``services/llm/copilot.py``
shipped the control flow (schema gate → dry-run fold → rules → ONE self-correction →
diff-or-refusal) but folded through a :class:`~services.llm.copilot.SchemaOnlyFolder`
because the Python model core did not exist yet. It exists now. This file supplies the
two real gates and one entry point:

* :class:`ModelFolder` — a :class:`~services.llm.copilot.DryRunFolder` over the REAL
  ``garh_model`` fold. Every candidate op is validated against the §3 invariants and
  applied to a **fork**: the input document is JSON, the fold is pure, and nothing here
  can reach the persisted op log — there is no session, no repository, no project id in
  scope. §14 budget: the fold of a copilot batch must stay under
  :data:`DRY_RUN_BUDGET_MS`; the folder records ``last_duration_ms`` so tests and logs
  can hold it to that.
* :class:`NewFailureRulesGate` — a :class:`~services.llm.copilot.RulesChecker` over
  ``garh_api.compliance``. §10 says the gate is "no NEW hard failure": a design that
  already fails a setback must not have every copilot edit rejected for a violation the
  edit did not cause, so the gate diffs post-edit failures against a baseline computed
  from the pre-edit document. When the rules cannot run at all (no plot boundary yet,
  packs missing) the gate reports nothing — compliance informs, it never silently
  blocks an edit for reasons it cannot state (golden rule 5).
* :func:`run_copilot_command` — command + document in, :class:`CopilotProposal` out.
  The route calls this; tests call this; the eval harness calls this. Same code path.

Deliberately importable without FastAPI or SQLAlchemy: the imports are ``garh_model``,
``garh_api.compliance`` and ``services.llm`` only, so the whole pipeline runs on a bare
interpreter (plus the ``services/dev_stubs.py`` shims) and in CI without a database.
"""

from __future__ import annotations

import time
from collections.abc import Mapping, Sequence
from typing import Any

from garh_model.fold import try_fold
from garh_model.model import ProjectDoc, empty_project_doc

from services.llm.copilot import (
    CopilotProposal,
    CopilotService,
    FoldIssue,
    FoldOutcome,
)
from services.llm.op_catalog import OpCatalog, OpSpec, get_op_catalog
from services.llm.provider import LlmProvider, get_llm_provider

#: §14: "copilot round-trip is I/O-bound but the dry-run fold must be <10ms".
DRY_RUN_BUDGET_MS = 10


# ---------------------------------------------------------------------------
# Gate 2: the real dry-run fold
# ---------------------------------------------------------------------------


class ModelFolder:
    """Dry-runs candidate ops through the real ``garh_model`` fold, on a fork.

    "Fork" is structural, not a copy discipline anyone has to remember:
    ``ProjectDoc.from_json`` builds a fresh frozen document from the JSON snapshot and
    ``fold`` is pure (a new document per op, the input never mutated). The persisted op
    log is unreachable from here by construction — this class holds no session and no
    repository, which is the §13 containment boundary in code.
    """

    def __init__(self, catalog: OpCatalog | None = None) -> None:
        self.catalog = catalog or get_op_catalog()
        #: Duration of the most recent :meth:`dry_run`, for the §14 budget check.
        self.last_duration_ms: float = 0.0

    def describe(self) -> str:
        return "garh_model fold (invariants + geometry, dry-run on a fork)"

    def dry_run(
        self, ops: Sequence[Mapping[str, Any]], *, model: Mapping[str, Any] | None
    ) -> FoldOutcome:
        started = time.perf_counter()
        try:
            doc = _document_of(model)
        except (KeyError, TypeError, ValueError) as exc:
            self.last_duration_ms = (time.perf_counter() - started) * 1000
            return FoldOutcome(
                ok=False,
                issues=(
                    FoldIssue(
                        code="MODEL_UNREADABLE",
                        message="The current design could not be loaded for a dry run: %s" % exc,
                    ),
                ),
            )

        plain: list[str] = []
        for index, op in enumerate(ops):
            # Plain language is written against the *pre-op* document so names refer
            # to what the architect currently sees, not to the intermediate state.
            plain.append(describe_op(op, doc, catalog=self.catalog))
            outcome = try_fold(doc, dict(op), compute_inverse=False)
            if not outcome.ok:
                self.last_duration_ms = (time.perf_counter() - started) * 1000
                return FoldOutcome(
                    ok=False,
                    issues=tuple(
                        FoldIssue(
                            code=issue.code,
                            message=issue.message,
                            severity=issue.severity,
                            element_ids=tuple(issue.element_ids),
                            field="ops[%d].%s" % (index, issue.field)
                            if issue.field
                            else "ops[%d]" % index,
                        )
                        for issue in outcome.issues
                    ),
                )
            doc = outcome.model

        self.last_duration_ms = (time.perf_counter() - started) * 1000
        return FoldOutcome(
            ok=True,
            model_after=doc.to_json(),
            plain_language=tuple(plain),
        )


def _document_of(model: Mapping[str, Any] | None) -> ProjectDoc:
    """JSON snapshot → a fresh :class:`ProjectDoc` fork (or an empty document)."""
    if model is None:
        return empty_project_doc()
    if isinstance(model, ProjectDoc):  # tests may pass the dataclass directly
        return model
    return ProjectDoc.from_json(model)


# ---------------------------------------------------------------------------
# Gate 3: the rules engine, diffed against a baseline
# ---------------------------------------------------------------------------


class NewFailureRulesGate:
    """Blocks a copilot edit only for hard failures the edit itself introduces.

    The baseline is computed once from the pre-edit document. ``check`` then returns
    the post-edit ``fail`` findings whose ``ruleId`` is not already failing — exactly
    §10's "the rules engine finds no NEW hard failure". Pre-existing failures stay the
    architect's business (they are shown as chips in the editor, golden rule 5); an
    edit that *fixes* rules is never punished for the ones it did not fix.

    When the rules engine cannot run (no plot boundary, packs not importable) both the
    baseline and the check degrade to "nothing to report", and ``available`` says so —
    the route logs it rather than pretending a green light was a check.
    """

    def __init__(self, baseline_document: Mapping[str, Any] | None = None) -> None:
        self.available = False
        self._baseline_fail_ids: frozenset = frozenset()
        if baseline_document is not None:
            failures, available = _hard_failures_of(baseline_document)
            self.available = available
            self._baseline_fail_ids = frozenset(
                str(f.get("ruleId")) for f in failures if f.get("ruleId")
            )

    def check(self, model: Mapping[str, Any]) -> Sequence[Mapping[str, Any]]:
        if not self.available:
            # No baseline means no diff. It is not enough that the rules can run on
            # the *result*: if they could not run on the input (no plot boundary yet),
            # every finding would look new, and an edit that merely made the design
            # measurable — setting the plot boundary — would be blocked for setbacks
            # that were always going to be there. A gate that cannot compare must not
            # pretend it did; ``available`` is False and the route logs it.
            return ()
        failures, available = _hard_failures_of(model)
        if not available:
            return ()
        return [
            finding
            for finding in failures
            if str(finding.get("ruleId")) not in self._baseline_fail_ids
        ]


def _hard_failures_of(document: Mapping[str, Any]) -> tuple[list, bool]:
    """``(hard failures, rules_ran)`` for a document. Never raises."""
    from garh_api.compliance import ComplianceUnavailable, evaluate_document

    try:
        payload, _versions = evaluate_document(dict(document))
    except ComplianceUnavailable:
        return [], False
    results = payload.get("results") or []
    return [
        dict(row)
        for row in results
        if isinstance(row, Mapping) and str(row.get("status")) == "fail"
    ], True


# ---------------------------------------------------------------------------
# Plain language (§10: "op list in plain language", from OP_CATALOG metadata)
# ---------------------------------------------------------------------------

#: Payload fields worth quoting to an architect, with a display suffix.
_MM_SUFFIX = "mm"

#: Above this, a catalog description is trimmed at its first comma (see :func:`_sentence`).
#: The copilot rail is ~320px wide; a 60-character stem plus its details is already two
#: lines there, and longer stems push the mm values off the row.
STEM_MAX_CHARS = 60


def describe_op(
    op: Mapping[str, Any],
    document: ProjectDoc | None = None,
    *,
    catalog: OpCatalog | None = None,
) -> str:
    """One line of UI copy for the diff panel.

    The *sentence stem* comes from the op catalog — the schema `description` §10 calls
    "machine-generated ... single source of truth" — so it cannot drift from what the
    op does. The *specifics* (names, mm values) come from the payload and the current
    document, because "Assign a room's type" is honest but "Make Room A the kitchen"
    is what a human wants to read.
    """
    cat = catalog or get_op_catalog()
    op_type = str(op.get("type") or "")
    payload = op.get("payload") if isinstance(op.get("payload"), Mapping) else {}
    spec = cat.spec(op_type)
    stem = _sentence(spec, op_type)

    details: list[str] = []
    name = _element_name(payload, document)
    if name:
        details.append(name)
    for field_name in ("widthMm", "heightMm", "thicknessMm", "offsetMm", "atMm"):
        value = payload.get(field_name)
        if isinstance(value, int) and not isinstance(value, bool):
            details.append("%s %d%s" % (_humanise(field_name), value, _MM_SUFFIX))
    for field_name in ("kind", "type", "swing", "vastuMode", "cityPack", "kitId", "materialId"):
        value = payload.get(field_name)
        if isinstance(value, str) and value:
            details.append(value.replace("_", " "))

    if details:
        # De-duplicate while keeping order; "window, window" reads like a stutter.
        seen: list[str] = []
        for item in details:
            if item not in seen:
                seen.append(item)
        return "%s (%s)" % (stem, ", ".join(seen[:4]))
    return stem


def _sentence(spec: OpSpec | None, op_type: str) -> str:
    if spec is not None and spec.summary and spec.summary != spec.type:
        text = spec.summary.strip()
        # Schema titles carry a "12. wall.delete" ordinal; descriptions do not, but be
        # tolerant of either shape.
        if ". " in text and text.split(". ", 1)[0].isdigit():
            text = text.split(". ", 1)[1]
        # Descriptions often carry constraint prose after the first sentence
        # ("Add a stair. risersCount * riserMm must equal ..."). The diff panel wants
        # the action, not the invariant — the fold enforces the invariant.
        text = text.split(". ", 1)[0].rstrip(".")
        # Some descriptions then enumerate every settable field ("…programme type,
        # name, tags and lock flag"). A diff row is one line in a narrow rail: keep
        # the verb and its object, drop the inventory.
        if len(text) > STEM_MAX_CHARS and "," in text:
            text = text.split(",", 1)[0].rstrip()
        return text
    verb, _, noun = op_type.partition(".")
    return "%s %s" % (noun.replace("_", " ").capitalize() or "Update", verb)


def _humanise(field_name: str) -> str:
    return {
        "widthMm": "width",
        "heightMm": "height",
        "thicknessMm": "thickness",
        "offsetMm": "offset",
        "atMm": "at",
    }.get(field_name, field_name)


def _element_name(payload: Mapping[str, Any], document: ProjectDoc | None) -> str:
    """Best human name for whatever element the payload points at."""
    if document is None:
        return ""
    for key in ("roomId", "openingId", "wallId", "storeyId", "stairId"):
        element_id = payload.get(key)
        if not isinstance(element_id, str) or not element_id:
            continue
        found = _lookup(document, element_id)
        if found:
            return found
    return ""


def _lookup(document: ProjectDoc, element_id: str) -> str:
    house = document.house
    for room in house.rooms:
        if room.id == element_id:
            if room.name:
                return room.name
            # An UNASSIGNED room has no programme yet, so its type is not a name —
            # "(unassigned, kitchen)" on a room.assign diff reads like a stutter or a
            # contradiction. Say "the room" and let the payload supply the new type.
            if room.type in ("", "unassigned"):
                return "the room"
            return "the %s" % room.type.replace("_", " ")
    for storey in house.storeys:
        if storey.id == element_id:
            return storey.name or "storey %d" % storey.index
    for opening in house.openings:
        if opening.id == element_id:
            return "the %s" % opening.kind
    for wall in house.walls:
        if wall.id == element_id:
            return "%s %s wall" % (_article(wall.kind), wall.kind.replace("_", " "))
    return ""


def _article(word: str) -> str:
    """ "a" or "an" — "a internal wall" in the diff panel reads as a bug in the product.

    Note the explicit tuple rather than ``word[:1] in "aeiou"``: for an empty string
    that idiom is ``"" in "aeiou"``, which is ``True``, and would render "an  wall".
    """
    return "an" if word[:1].lower() in ("a", "e", "i", "o", "u") else "a"


# ---------------------------------------------------------------------------
# The entry point (route and tests share it)
# ---------------------------------------------------------------------------


def build_copilot_service(
    provider: LlmProvider | None = None,
    *,
    document: Mapping[str, Any] | None = None,
    catalog: OpCatalog | None = None,
) -> tuple[CopilotService, ModelFolder, NewFailureRulesGate]:
    """Wire the §10 pipeline: provider → schema gate → real fold → rules diff.

    Returns the folder and gate alongside the service so callers can read
    ``folder.last_duration_ms`` (§14 budget) and ``gate.available`` (honest logging).
    """
    folder = ModelFolder(catalog)
    gate = NewFailureRulesGate(document)
    service = CopilotService(
        provider or get_llm_provider(),
        catalog=folder.catalog,
        folder=folder,
        rules=gate,
    )
    return service, folder, gate


async def run_copilot_command(
    command: str,
    *,
    document: Mapping[str, Any] | None,
    violations: Sequence[Mapping[str, Any]] = (),
    provider: LlmProvider | None = None,
    active_storey_id: str | None = None,
    selection_ids: Sequence[str] = (),
) -> CopilotProposal:
    """One copilot command through every gate. Pure with respect to persistence.

    The returned proposal's ops — when ``applicable`` — have passed, in order:
    the copilot output schema, the op-catalog schema (§4), the real ``garh_model``
    fold on a fork, and the no-new-hard-failure rules diff. There is no other exit
    that carries ops.
    """
    service, _folder, _gate = build_copilot_service(provider, document=document)
    return await service.propose(
        command,
        model=dict(document) if document is not None else None,
        violations=list(violations),
        active_storey_id=active_storey_id,
        selection_ids=list(selection_ids),
    )


__all__ = [
    "DRY_RUN_BUDGET_MS",
    "ModelFolder",
    "NewFailureRulesGate",
    "build_copilot_service",
    "describe_op",
    "run_copilot_command",
]
