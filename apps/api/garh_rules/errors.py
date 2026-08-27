"""Errors raised by the rules engine.

Two families, and the split matters:

* :class:`PackLoadError` — the *pack data* is wrong or names something the engine
  cannot evaluate. Always fatal at load time, never downgraded to a warning.
  Playbook §6: "a silently-ignored rule is a compliance lie". A pack that
  references an unknown check type, an unknown ``when`` field, an unknown
  ``custom.fn``, or a parameter the engine cannot honour must stop the load
  loudly so somebody fixes the pack — it must never produce a green panel.
* :class:`ContextError` — the *caller's* EvaluationContext is malformed (a float
  where integer millimetres are required, a missing storey, an unknown room
  reference). Also fatal: guessing would put a made-up number in a compliance
  report.

Both carry ``code`` / ``action`` / :meth:`as_problem` so a FastAPI router can
render problem+json without importing anything from ``garh_api`` — this package
is shared by the API and by the solver critic (``services/solver``) and must not
depend on either.
"""

from __future__ import annotations

from typing import Any

__all__ = [
    "GarhRulesError",
    "PackLoadError",
    "SchemaValidationError",
    "SchemaFeatureError",
    "ContextError",
    "EvaluationError",
]


class GarhRulesError(Exception):
    """Base class. Subclasses declare an HTTP status, a stable code and an action."""

    http_status = 500
    code = "rules_engine_error"
    action = "Report this with the request id — the rules engine could not run."

    def __init__(self, message: str, **details: Any) -> None:
        super().__init__(message)
        self.message = message
        self.details: dict[str, Any] = {k: v for k, v in details.items() if v is not None}

    def as_problem(self) -> dict[str, Any]:
        """problem+json body, matching the API's ``{code, message, action}`` contract."""
        body: dict[str, Any] = {
            "code": self.code,
            "message": self.message,
            "action": self.action,
        }
        if self.details:
            body["details"] = dict(self.details)
        return body

    def __str__(self) -> str:  # pragma: no cover - trivial
        if not self.details:
            return self.message
        bits = ", ".join("%s=%r" % (k, v) for k, v in sorted(self.details.items()))
        return "%s (%s)" % (self.message, bits)


class PackLoadError(GarhRulesError):
    """A rule pack is unloadable, or names something this engine cannot evaluate.

    Deliberately fatal. The alternative — skip the rule and carry on — reports a
    house as compliant against a rule that never ran.
    """

    http_status = 500
    code = "rulepack_invalid"
    action = "Fix the rule pack (or the engine, if the pack is right) — compliance cannot run against it."

    def __init__(
        self,
        message: str,
        *,
        pack_id: str | None = None,
        rule_id: str | None = None,
        **details: Any,
    ) -> None:
        super().__init__(message, pack_id=pack_id, rule_id=rule_id, **details)
        self.pack_id = pack_id
        self.rule_id = rule_id


class SchemaValidationError(PackLoadError):
    """A pack failed JSON Schema validation. ``errors`` lists every violation."""

    code = "rulepack_schema_invalid"

    def __init__(self, message: str, *, pack_id: str | None = None, errors: Any = None) -> None:
        super().__init__(message, pack_id=pack_id, errors=errors)
        self.errors = list(errors or ())


class SchemaFeatureError(GarhRulesError):
    """The bundled validator met a JSON Schema keyword it does not implement.

    Raised rather than ignored: an unchecked keyword means the pack was only
    partly validated, and a partly validated pack is an unvalidated pack.
    Adding a keyword to ``rulepacks/schema/*.json`` therefore requires adding it
    to :mod:`garh_rules.jsonschema_min`.
    """

    http_status = 500
    code = "schema_feature_unsupported"
    action = "Implement the keyword in garh_rules.jsonschema_min, or drop it from the schema."


class ContextError(GarhRulesError):
    """The EvaluationContext handed to the engine is malformed."""

    http_status = 422
    code = "evaluation_context_invalid"
    action = "Rebuild the model projection — the rules engine needs integer millimetres and complete elements."


class EvaluationError(GarhRulesError):
    """An internal invariant broke while evaluating. Should never reach a user."""

    http_status = 500
    code = "rules_evaluation_failed"
    action = "Report this with the request id."
