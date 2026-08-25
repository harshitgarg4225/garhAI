"""The op catalog — **generated** from ``packages/model/schema/ops.schema.json``.

§10 is explicit: the copilot's system prompt is "op catalog (from §4, machine-generated
from the schema — single source of truth)". So nothing about the 32 ops is written down
in this file. The op list, their order, every payload field, which fields are required,
every enum, every numeric bound and every id pattern is read out of the schema at load
time. Adding an op to ``ops.schema.json`` adds it to the prompt; renaming a field
renames it in the prompt; deleting one deletes it. There is no second place to update
and therefore no way for the prompt to lie about what the system accepts.

The same schema drives :meth:`OpCatalog.validate_op`, so the ops an LLM is *told* about
and the ops that are *accepted* cannot diverge either.

What is hand-written here is only the rendering — how a field's constraints turn into a
line of prompt text. That is presentation, not contract.
"""

from __future__ import annotations

import functools
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from services.common.config import REPO_ROOT
from services.common.jsonschema_lite import SchemaValidator, ValidationFailure

#: Where the cross-language schema contract lives. Owned by packages/model.
SCHEMA_DIR = REPO_ROOT / "packages" / "model" / "schema"

OPS_SCHEMA = "ops.schema.json"
COMMON_SCHEMA = "common.schema.json"

#: Fields every op may carry alongside `type` and `payload`. Also read from the schema;
#: named here only so the renderer can describe them once instead of 32 times.
_ENVELOPE_FIELDS = ("groupId", "clientOpId", "source")


class OpCatalogError(RuntimeError):
    """The op schema is missing or unreadable. Fatal at boot, never mid-request."""


@dataclass(frozen=True)
class OpField:
    """One payload field, described for a prompt."""

    name: str
    required: bool
    description: str

    def render(self) -> str:
        return "%s%s: %s" % (self.name, "" if self.required else "?", self.description)


@dataclass(frozen=True)
class OpSpec:
    """One op, as the prompt sees it."""

    type: str
    #: Schema `title`, e.g. "9. wall.add" — carries the §4 ordinal.
    title: str
    summary: str
    fields: tuple[OpField, ...]

    def render(self) -> str:
        if not self.fields:
            return "- %s — %s\n    payload: {}" % (self.type, self.summary)
        body = "; ".join(field.render() for field in self.fields)
        return "- %s — %s\n    payload: { %s }" % (self.type, self.summary, body)


class OpCatalog:
    """Loaded op taxonomy: prompt text, validation, and a drift digest."""

    def __init__(self, ops_schema: Mapping[str, Any], common_schema: Mapping[str, Any]) -> None:
        self._documents = {OPS_SCHEMA: ops_schema, COMMON_SCHEMA: common_schema}
        self.validator = SchemaValidator.from_files(self._documents, root=OPS_SCHEMA)
        self._defs: Mapping[str, Any] = ops_schema.get("$defs", {})
        self.op_types: tuple[str, ...] = _ordered_op_types(ops_schema)
        self.specs: tuple[OpSpec, ...] = tuple(
            self._build_spec(op_type) for op_type in self.op_types
        )
        self._by_type = {spec.type: spec for spec in self.specs}

    # ------------------------------------------------------------------
    # loading
    # ------------------------------------------------------------------
    @classmethod
    def load(cls, schema_dir: Path | None = None) -> OpCatalog:
        directory = schema_dir or SCHEMA_DIR
        documents: dict[str, Mapping[str, Any]] = {}
        for name in (OPS_SCHEMA, COMMON_SCHEMA):
            path = directory / name
            try:
                with path.open(encoding="utf-8") as handle:
                    parsed = json.load(handle)
            except FileNotFoundError as exc:
                raise OpCatalogError(
                    "Op schema %s is missing. The copilot prompt is generated from it, so "
                    "the LLM layer cannot start without it." % path
                ) from exc
            except json.JSONDecodeError as exc:
                raise OpCatalogError("Op schema %s is not valid JSON: %s" % (path, exc)) from exc
            if not isinstance(parsed, dict):
                raise OpCatalogError("Op schema %s must be a JSON object" % path)
            documents[name] = parsed
        return cls(documents[OPS_SCHEMA], documents[COMMON_SCHEMA])

    # ------------------------------------------------------------------
    # validation
    # ------------------------------------------------------------------
    def validate_op(self, op: Any) -> list[ValidationFailure]:
        """Schema-validate one op. Empty list ⇒ structurally acceptable.

        Structural only: this proves the op is *well-formed*, not that it is *legal
        against the current model* (that is the dry-run fold's job — an opening can be
        perfectly well-formed and still be wider than its wall).
        """
        return self.validator.validate(op)

    def validate_ops(self, ops: Sequence[Any]) -> list[ValidationFailure]:
        failures: list[ValidationFailure] = []
        for index, op in enumerate(ops):
            for failure in self.validate_op(op):
                failures.append(
                    ValidationFailure(
                        path="ops[%d]%s"
                        % (index, "." + failure.path if failure.path else ""),
                        message=failure.message,
                        keyword=failure.keyword,
                    )
                )
        return failures

    def knows(self, op_type: str) -> bool:
        return op_type in self._by_type

    def spec(self, op_type: str) -> OpSpec | None:
        return self._by_type.get(op_type)

    # ------------------------------------------------------------------
    # prompt rendering
    # ------------------------------------------------------------------
    def render_prompt_section(self) -> str:
        """The op-catalog block of the copilot system prompt."""
        lines = [
            "You may emit ONLY these %d op types. Each op is "
            '{ "type": <one of the below>, "payload": { ... } }.' % len(self.specs),
            "`?` marks an optional payload field. All lengths are INTEGER MILLIMETRES —"
            " never a decimal, never a unit string.",
            "Ids: to REFER to something, copy an id verbatim from the model summary."
            " To CREATE something, mint a new id as `<prefix>_<ULID>` (26 Crockford"
            " base32 chars, first char 0-7) and reuse it in later ops of the same batch.",
            "",
        ]
        lines.extend(spec.render() for spec in self.specs)
        lines.append("")
        lines.append(
            "Ops may also carry %s, but leave them out — the server sets them."
            % ", ".join("`%s`" % name for name in _ENVELOPE_FIELDS)
        )
        return "\n".join(lines)

    def digest(self) -> str:
        """Stable hash of the rendered catalog.

        A prompt-contract test pins this. When it changes, the copilot's system prompt
        changed, which means the eval fixtures need re-checking — that is the signal.
        """
        return hashlib.sha256(self.render_prompt_section().encode("utf-8")).hexdigest()

    # ------------------------------------------------------------------
    # spec construction (everything below reads the schema, nothing is hardcoded)
    # ------------------------------------------------------------------
    def _build_spec(self, op_type: str) -> OpSpec:
        definition = self._defs.get(op_type)
        if not isinstance(definition, dict):
            raise OpCatalogError("ops.schema.json has no $defs entry for %r" % op_type)
        payload = definition.get("properties", {}).get("payload", {})
        payload, _ = self._deref(payload)
        required = set(payload.get("required", []) or [])
        properties = payload.get("properties", {})
        properties = properties if isinstance(properties, dict) else {}

        fields: list[OpField] = []
        for name, child in properties.items():
            fields.append(
                OpField(
                    name=str(name),
                    required=name in required,
                    description=self._describe(child),
                )
            )
        # Conditional payloads (column/furniture/balcony/annotation `.set`) put their
        # branch-specific fields under if/then. Surface them so the prompt is complete.
        fields.extend(self._conditional_fields(payload, {field.name for field in fields}))

        summary = str(definition.get("description") or definition.get("title") or op_type)
        return OpSpec(
            type=op_type,
            title=str(definition.get("title") or op_type),
            summary=summary,
            fields=tuple(fields),
        )

    def _conditional_fields(
        self, payload: Mapping[str, Any], seen: set[str]
    ) -> list[OpField]:
        out: list[OpField] = []
        branches: list[Any] = []
        if isinstance(payload.get("allOf"), list):
            branches.extend(payload["allOf"])
        if "if" in payload:
            branches.append(payload)
        for branch in branches:
            if not isinstance(branch, dict):
                continue
            for key in ("then", "else"):
                sub = branch.get(key)
                if not isinstance(sub, dict):
                    continue
                condition = _condition_summary(branch.get("if"))
                sub_required = set(sub.get("required", []) or [])
                sub_properties = sub.get("properties", {})
                sub_properties = sub_properties if isinstance(sub_properties, dict) else {}
                names = sorted(set(sub_properties) | sub_required)
                for name in names:
                    if name in seen:
                        continue
                    seen.add(name)
                    child = sub_properties.get(name, {})
                    described = self._describe(child) if child else "value"
                    note = " (when %s)" % condition if condition else " (conditional)"
                    out.append(
                        OpField(name=name, required=False, description=described + note)
                    )
        return out

    def _deref(self, schema: Any, doc: str = OPS_SCHEMA) -> tuple[Mapping[str, Any], str]:
        """Follow ``$ref`` chains, returning the target and the document that owns it.

        The document must travel with the schema: ``common.schema.json`` refers to its
        own ``#/$defs/Pt``, which does not exist in ``ops.schema.json``.
        """
        seen = 0
        while isinstance(schema, dict) and "$ref" in schema and seen < 10:
            file_part, _, pointer = str(schema["$ref"]).partition("#")
            if file_part:
                doc = file_part
            document = self._documents.get(doc, {})
            node: Any = document
            for token in pointer.split("/"):
                if not token:
                    continue
                if isinstance(node, dict) and token in node:
                    node = node[token]
                else:
                    return {}, doc
            schema = node
            seen += 1
        return (schema if isinstance(schema, dict) else {}), doc

    def _describe(self, schema: Any, doc: str = OPS_SCHEMA) -> str:
        """Turn a field schema into a compact prompt description."""
        if not isinstance(schema, dict):
            return "value"
        ref = schema.get("$ref")
        resolved, resolved_doc = self._deref(schema, doc)
        name = _ref_name(ref) if isinstance(ref, str) else ""

        # union of a concrete type and null → "X or null"
        for key in ("oneOf", "anyOf"):
            branch = schema.get(key) if isinstance(schema.get(key), list) else None
            if branch:
                parts = [self._describe(item, doc) for item in branch]
                unique: list[str] = []
                for part in parts:
                    if part not in unique:
                        unique.append(part)
                return " or ".join(unique)

        enum = resolved.get("enum")
        if isinstance(enum, list):
            return "one of " + "|".join(str(value) for value in enum)
        const = resolved.get("const")
        if const is not None:
            return "literally %s" % json.dumps(const, ensure_ascii=False)

        kind = resolved.get("type")
        if kind == "object":
            properties = resolved.get("properties")
            if isinstance(properties, dict) and properties:
                inner = ", ".join(sorted(properties))
                return "%s{%s}" % (name + " " if name else "", inner)
            return name or "object"
        if kind == "array":
            item = self._describe(resolved.get("items", {}), resolved_doc)
            return "%s[] of %s" % (name, item) if name else "array of %s" % item
        if kind == "boolean":
            return "boolean"
        if kind == "null":
            return "null"
        if kind in ("integer", "number"):
            return _describe_number(name, resolved)
        if kind == "string":
            return _describe_string(name, resolved)
        return name or "value"


def _describe_number(name: str, schema: Mapping[str, Any]) -> str:
    bounds: list[str] = []
    for keyword, symbol in (
        ("minimum", ">="),
        ("exclusiveMinimum", ">"),
        ("maximum", "<="),
        ("exclusiveMaximum", "<"),
    ):
        value = schema.get(keyword)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            # The full 2^53 sentinel range is noise in a prompt.
            if abs(int(value)) < 9_007_199_254_740_991:
                bounds.append("%s%s" % (symbol, value))
    unit = "mm" if name.endswith("Mm") or "Mm" in name else ""
    if name.endswith("Mm2") or "Mm2" in name:
        unit = "mm2"
    if name.endswith("Deg") or name == "Bearing":
        unit = "deg"
    base = "int" if schema.get("type") == "integer" else "number"
    parts = [base]
    if unit:
        parts.append(unit)
    if bounds:
        parts.append("(%s)" % " ".join(bounds))
    return " ".join(parts)


def _describe_string(name: str, schema: Mapping[str, Any]) -> str:
    pattern = schema.get("pattern")
    if isinstance(pattern, str):
        prefix = _id_prefix(pattern)
        if prefix:
            # Deliberately NOT "existing ... id": the same field is a reference on an
            # edit op and a freshly minted id on a creation op, and the schema cannot
            # tell them apart. The prompt header states the rule once instead.
            return "`%s_<ULID>` id" % prefix
    limits: list[str] = []
    for keyword, label in (("minLength", "min"), ("maxLength", "max")):
        value = schema.get(keyword)
        if isinstance(value, int):
            limits.append("%s %d" % (label, value))
    suffix = " (%s chars)" % ", ".join(limits) if limits else ""
    return "string%s" % suffix


def _id_prefix(pattern: str) -> str:
    """``^wall_[0-7]...`` → ``wall``. Empty when the pattern is not an element id."""
    if not pattern.startswith("^"):
        return ""
    body = pattern[1:]
    head, sep, _ = body.partition("_")
    if not sep or not head.isalpha() or not head.islower():
        return ""
    return head


def _condition_summary(condition: Any) -> str:
    """``{properties: {action: {const: "add"}}}`` → ``action=add``."""
    if not isinstance(condition, dict):
        return ""
    properties = condition.get("properties")
    if not isinstance(properties, dict):
        return ""
    parts: list[str] = []
    for name, child in properties.items():
        if isinstance(child, dict):
            if "const" in child:
                parts.append("%s=%s" % (name, child["const"]))
            elif isinstance(child.get("enum"), list):
                parts.append("%s in %s" % (name, "|".join(str(v) for v in child["enum"])))
    return ", ".join(parts)


def _ordered_op_types(ops_schema: Mapping[str, Any]) -> tuple[str, ...]:
    """Op types in the schema's own ``oneOf`` order — which is playbook §4 order.

    Falls back to ``$defs`` order (minus ``OpMeta``) if ``oneOf`` is absent, so a
    schema edit degrades to "still correct, maybe reordered" rather than "empty".
    """
    ordered: list[str] = []
    branches = ops_schema.get("oneOf")
    if isinstance(branches, list):
        for branch in branches:
            if isinstance(branch, dict):
                ref = branch.get("$ref")
                if isinstance(ref, str):
                    name = _ref_name(ref)
                    if name and name not in ordered:
                        ordered.append(name)
    if ordered:
        return tuple(ordered)
    defs = ops_schema.get("$defs", {})
    return tuple(name for name in defs if name != "OpMeta") if isinstance(defs, dict) else ()


def _ref_name(ref: str) -> str:
    if not ref:
        return ""
    if ref in ("#", "#/"):
        return "Op"  # the root schema — a nested op (solver.apply_option carries these)
    return ref.rsplit("/", 1)[-1]


@functools.lru_cache(maxsize=1)
def get_op_catalog() -> OpCatalog:
    """Process-wide catalog. Loaded once; the schema does not change at runtime."""
    return OpCatalog.load()


def reset_op_catalog_cache() -> None:
    """Test helper: reload after pointing at a different schema directory."""
    get_op_catalog.cache_clear()


__all__ = [
    "COMMON_SCHEMA",
    "OPS_SCHEMA",
    "SCHEMA_DIR",
    "OpCatalog",
    "OpCatalogError",
    "OpField",
    "OpSpec",
    "get_op_catalog",
    "reset_op_catalog_cache",
]
