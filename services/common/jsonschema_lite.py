"""A small, *strict-by-omission* JSON Schema validator (draft 2020-12 subset).

Why this exists rather than the ``jsonschema`` package:

* The schemas it validates are **ours** — ``packages/model/schema/*.json`` and the
  structured-output schemas in ``services/llm/schemas.py``. They use a known, small
  slice of the vocabulary.
* Workers must install light on the mock path (locked decision: zero keys, zero GPUs,
  and as few dependencies as will do the job).
* Cross-file ``$ref`` (``common.schema.json#/$defs/Pt``) needs a resolver either way,
  so the "just use the library" saving is smaller than it looks.

**The safety property that makes this acceptable**: any keyword this module does not
implement raises :class:`UnsupportedSchemaError` at *compile* time. It can therefore
never silently pass a document it did not actually check — the failure mode is a loud
error in CI, not a false "valid". If you add a keyword to a schema, add it here too.

Usage::

    validator = SchemaValidator.from_files({"ops.schema.json": ops, "common.schema.json": common},
                                           root="ops.schema.json")
    errors = validator.validate(document)
    if errors:
        raise ValueError(format_errors(errors))

Numbers: ``type: "integer"`` accepts ``int`` and rejects ``bool`` (Python's
``isinstance(True, int)`` is a trap this codebase cannot afford — geometry is integer
millimetres and ``True`` is not a length).
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

__all__ = [
    "SUPPORTED_KEYWORDS",
    "SchemaError",
    "SchemaValidator",
    "UnsupportedSchemaError",
    "ValidationFailure",
    "format_errors",
]


class SchemaError(Exception):
    """The schema itself is wrong (bad ``$ref``, unknown keyword)."""


class UnsupportedSchemaError(SchemaError):
    """A keyword this validator does not implement was used.

    Deliberately fatal: silently ignoring an unknown constraint would report
    "valid" for a document that was never checked against it.
    """


#: Keywords with real validation behaviour.
_ASSERTIONS = frozenset(
    {
        "$ref",
        "type",
        "enum",
        "const",
        "properties",
        "patternProperties",
        "required",
        "additionalProperties",
        "propertyNames",
        "minProperties",
        "maxProperties",
        "items",
        "prefixItems",
        "minItems",
        "maxItems",
        "uniqueItems",
        "contains",
        "minimum",
        "maximum",
        "exclusiveMinimum",
        "exclusiveMaximum",
        "multipleOf",
        "minLength",
        "maxLength",
        "pattern",
        "oneOf",
        "anyOf",
        "allOf",
        "not",
        "if",
        "then",
        "else",
    }
)

#: Keywords that carry documentation or identity only. Ignored on purpose.
_ANNOTATIONS = frozenset(
    {
        "$schema",
        "$id",
        "$anchor",
        "$comment",
        "$defs",
        "definitions",
        "title",
        "description",
        "default",
        "examples",
        "deprecated",
        "readOnly",
        "writeOnly",
        # `format` is an annotation in 2020-12 unless a vocabulary opts in; we do not.
        "format",
        # Project-specific extension used by rulepacks/schema/rulepack.schema.json.
        "x-garh-check-meta",
    }
)

SUPPORTED_KEYWORDS: frozenset[str] = _ASSERTIONS | _ANNOTATIONS

_TYPE_NAMES = ("null", "boolean", "object", "array", "number", "integer", "string")


@dataclass(frozen=True)
class ValidationFailure:
    """One reason a document did not validate."""

    #: JSON-pointer-ish path into the *instance*, e.g. ``payload.widthMm`` or ``ops[2].type``.
    path: str
    #: Human-readable, deliberately short — these get fed back to an LLM verbatim.
    message: str
    #: The schema keyword that failed, for machine grouping.
    keyword: str = ""

    def __str__(self) -> str:
        location = self.path or "<root>"
        return "%s: %s" % (location, self.message)


def format_errors(failures: Sequence[ValidationFailure], *, limit: int = 20) -> str:
    """Render failures as one bulleted block (what the self-correction loop sends back)."""
    lines = ["- %s" % failure for failure in failures[:limit]]
    if len(failures) > limit:
        lines.append("- ...and %d more" % (len(failures) - limit))
    return "\n".join(lines)


class SchemaValidator:
    """Validates documents against one root schema, with cross-file ``$ref`` support."""

    def __init__(
        self,
        schema: Mapping[str, Any],
        *,
        documents: Mapping[str, Mapping[str, Any]] | None = None,
        base: str = "",
    ) -> None:
        """``documents`` maps a *file name* (as written in ``$ref``) to its parsed schema."""
        self.schema = schema
        self.documents: dict[str, Mapping[str, Any]] = dict(documents or {})
        self.base = base
        if base and base not in self.documents:
            self.documents[base] = schema
        self._pattern_cache: dict[str, re.Pattern[str]] = {}
        self._checked: set[int] = set()
        self._check_keywords(schema, "#")

    # ------------------------------------------------------------------
    # construction helpers
    # ------------------------------------------------------------------
    @classmethod
    def from_files(
        cls, documents: Mapping[str, Mapping[str, Any]], *, root: str
    ) -> SchemaValidator:
        """Build from a ``{filename: parsed schema}`` map, validating against ``root``."""
        try:
            schema = documents[root]
        except KeyError:
            raise SchemaError(
                "root schema %r is not in the supplied documents (%s)"
                % (root, ", ".join(sorted(documents)))
            ) from None
        return cls(schema, documents=documents, base=root)

    @classmethod
    def from_paths(cls, paths: Mapping[str, str], *, root: str) -> SchemaValidator:
        """Build from a ``{filename: path on disk}`` map."""
        documents: dict[str, Mapping[str, Any]] = {}
        for name, path in paths.items():
            with open(path, encoding="utf-8") as handle:
                parsed = json.load(handle)
            if not isinstance(parsed, dict):
                raise SchemaError("%s is not a JSON object" % path)
            documents[name] = parsed
        return cls.from_files(documents, root=root)

    # ------------------------------------------------------------------
    # public API
    # ------------------------------------------------------------------
    def validate(self, instance: Any) -> list[ValidationFailure]:
        """Return every failure. An empty list means the document is valid."""
        failures: list[ValidationFailure] = []
        self._validate(instance, self.schema, "", failures, self.base)
        return failures

    def is_valid(self, instance: Any) -> bool:
        return not self.validate(instance)

    def subschema(self, pointer: str) -> Mapping[str, Any]:
        """Resolve a pointer such as ``#/$defs/wall.add`` in the root document."""
        return self._resolve(pointer, where="subschema", doc=self.base)[0]

    # ------------------------------------------------------------------
    # schema keyword audit (the safety property)
    # ------------------------------------------------------------------
    def _check_keywords(self, schema: Any, where: str) -> None:
        if isinstance(schema, bool):
            return
        if not isinstance(schema, dict):
            raise SchemaError("%s: schema must be an object or boolean" % where)
        marker = id(schema)
        if marker in self._checked:
            return
        self._checked.add(marker)

        unknown = sorted(set(schema) - SUPPORTED_KEYWORDS)
        if unknown:
            raise UnsupportedSchemaError(
                "%s uses JSON Schema keyword(s) this validator does not implement: %s. "
                "Implement them in services/common/jsonschema_lite.py rather than "
                "letting them pass unchecked." % (where, ", ".join(unknown))
            )

        for key in ("properties", "patternProperties", "$defs", "definitions"):
            branch = schema.get(key)
            if isinstance(branch, dict):
                for name, child in branch.items():
                    self._check_keywords(child, "%s/%s/%s" % (where, key, name))
        for key in (
            "items",
            "additionalProperties",
            "propertyNames",
            "not",
            "contains",
            "if",
            "then",
            "else",
        ):
            if key in schema and not isinstance(schema[key], bool):
                self._check_keywords(schema[key], "%s/%s" % (where, key))
        for key in ("oneOf", "anyOf", "allOf", "prefixItems"):
            branch = schema.get(key)
            if isinstance(branch, list):
                for index, child in enumerate(branch):
                    self._check_keywords(child, "%s/%s/%d" % (where, key, index))

    # ------------------------------------------------------------------
    # $ref resolution
    # ------------------------------------------------------------------
    def _resolve(self, ref: str, *, where: str, doc: str) -> tuple[Mapping[str, Any], str]:
        """Resolve ``ref`` relative to ``doc`` and return ``(schema, owning document)``.

        Returning the owning document is not optional bookkeeping: a fragment-only
        ``$ref`` such as ``#/$defs/IntMm`` inside ``common.schema.json`` must resolve
        against *that* file, not against whichever schema happens to be the root.
        """
        file_part, _, pointer = ref.partition("#")
        if file_part:
            document = self.documents.get(file_part)
            if document is None:
                raise SchemaError(
                    "%s: $ref %r points at %r, which was not supplied. Known documents: %s"
                    % (where, ref, file_part, ", ".join(sorted(self.documents)) or "none")
                )
            doc = file_part
        else:
            document = self.documents.get(doc, self.schema)

        node: Any = document
        for raw_token in pointer.split("/"):
            if raw_token == "":
                continue
            token = raw_token.replace("~1", "/").replace("~0", "~")
            if isinstance(node, list):
                try:
                    node = node[int(token)]
                except (ValueError, IndexError):
                    raise SchemaError(
                        "%s: $ref %r has no element %r" % (where, ref, token)
                    ) from None
            elif isinstance(node, dict) and token in node:
                node = node[token]
            else:
                raise SchemaError("%s: $ref %r cannot be resolved at %r" % (where, ref, token))
        if not isinstance(node, dict):
            raise SchemaError("%s: $ref %r does not point at a schema object" % (where, ref))
        self._check_keywords(node, ref)
        return node, doc

    # ------------------------------------------------------------------
    # validation
    # ------------------------------------------------------------------
    def _validate(
        self, instance: Any, schema: Any, path: str, out: list[ValidationFailure], doc: str
    ) -> None:
        if schema is True or schema == {}:
            return
        if schema is False:
            out.append(ValidationFailure(path, "no value is allowed here", "false"))
            return
        if not isinstance(schema, dict):
            raise SchemaError("%s: schema must be an object or boolean" % (path or "<root>"))

        if "$ref" in schema:
            target, target_doc = self._resolve(str(schema["$ref"]), where=path or "<root>", doc=doc)
            self._validate(instance, target, path, out, target_doc)
            # 2020-12 allows siblings of $ref; every schema in this repo uses $ref alone
            # plus annotations, so continuing through the rest is correct and cheap.

        if "type" in schema and not self._type_ok(instance, schema["type"], path):
            out.append(
                ValidationFailure(
                    path,
                    "expected %s, got %s" % (_describe_type(schema["type"]), _kind(instance)),
                    "type",
                )
            )
            return  # further keywords would produce noise against the wrong kind

        if "const" in schema and instance != schema["const"]:
            out.append(
                ValidationFailure(
                    path, "must be %s" % json.dumps(schema["const"], ensure_ascii=False), "const"
                )
            )
        if "enum" in schema:
            options = schema["enum"]
            if not any(instance == option for option in options):
                out.append(
                    ValidationFailure(
                        path,
                        "must be one of %s"
                        % ", ".join(json.dumps(option, ensure_ascii=False) for option in options),
                        "enum",
                    )
                )

        for combinator in ("allOf", "anyOf", "oneOf"):
            if combinator in schema:
                self._validate_combinator(instance, schema, combinator, path, out, doc)
        if "not" in schema and not self._validate_quietly(instance, schema["not"], doc):
            out.append(ValidationFailure(path, "must not match the forbidden shape", "not"))

        if "if" in schema:
            # Conditional application. `if` NEVER contributes failures of its own —
            # only the selected branch does. The op taxonomy uses this for payloads
            # whose required fields depend on `payload.action` (column/furniture/
            # balcony/annotation `.set`), so a wrong branch must report the branch's
            # own reasons, not "failed the if".
            branch = "then" if self._validate_quietly(instance, schema["if"], doc) else "else"
            if branch in schema:
                self._validate(instance, schema[branch], path, out, doc)

        if isinstance(instance, str):
            self._validate_string(instance, schema, path, out)
        elif isinstance(instance, bool):
            pass  # booleans carry no numeric constraints
        elif isinstance(instance, int | float):
            self._validate_number(instance, schema, path, out)
        elif isinstance(instance, list):
            self._validate_array(instance, schema, path, out, doc)
        elif isinstance(instance, dict):
            self._validate_object(instance, schema, path, out, doc)

    def _validate_combinator(
        self,
        instance: Any,
        schema: Mapping[str, Any],
        keyword: str,
        path: str,
        out: list[ValidationFailure],
        doc: str,
    ) -> None:
        branches = schema[keyword]
        if not isinstance(branches, list):
            raise SchemaError("%s: %s must be an array" % (path or "<root>", keyword))
        if keyword == "allOf":
            for branch in branches:
                self._validate(instance, branch, path, out, doc)
            return

        matches = [branch for branch in branches if self._validate_quietly(instance, branch, doc)]
        if matches and keyword == "anyOf":
            return
        if matches and keyword == "oneOf":
            if len(matches) > 1:
                out.append(
                    ValidationFailure(
                        path,
                        "matches %d alternatives but must match exactly one" % len(matches),
                        "oneOf",
                    )
                )
            return

        # Nothing matched. If this is a discriminated union (every branch pins
        # `type` to a const — the op taxonomy) and the instance names a known
        # discriminator, report why THAT branch failed. Saying "wall.add is not a
        # known type" when the type is fine and only the payload is wrong would send
        # the copilot's self-correction loop chasing the wrong fix.
        index = self._discriminator_index(branches, doc)
        if index is not None and isinstance(instance, dict):
            discriminator = instance.get("type")
            selected = index.get(discriminator) if isinstance(discriminator, str) else None
            if selected is not None:
                branch_schema, branch_doc = selected
                self._validate(instance, branch_schema, path, out, branch_doc)
                return
        out.append(ValidationFailure(path, self._explain_no_branch(instance, index), keyword))

    def _discriminator_index(
        self, branches: Sequence[Any], doc: str
    ) -> dict[str, tuple[Mapping[str, Any], str]] | None:
        """``{type const: (branch schema, owning doc)}`` when this is a tagged union.

        Returns ``None`` unless *every* branch pins ``properties.type.const`` — a
        partial index would let a wrong branch be selected.
        """
        index: dict[str, tuple[Mapping[str, Any], str]] = {}
        for branch in branches:
            resolved: Any = branch
            branch_doc = doc
            if isinstance(branch, dict) and "$ref" in branch:
                try:
                    resolved, branch_doc = self._resolve(
                        str(branch["$ref"]), where="union branch", doc=doc
                    )
                except SchemaError:
                    return None
            if not isinstance(resolved, dict):
                return None
            properties = resolved.get("properties")
            if not isinstance(properties, dict):
                return None
            type_schema = properties.get("type")
            if not isinstance(type_schema, dict) or "const" not in type_schema:
                return None
            index[str(type_schema["const"])] = (resolved, branch_doc)
        return index or None

    def _explain_no_branch(
        self, instance: Any, index: Mapping[str, tuple[Mapping[str, Any], str]] | None
    ) -> str:
        if index is not None:
            actual = instance.get("type") if isinstance(instance, dict) else None
            return "%r is not a known type; expected one of: %s" % (
                actual,
                ", ".join(sorted(index)),
            )
        return "does not match any allowed alternative"

    def _validate_quietly(self, instance: Any, schema: Any, doc: str) -> bool:
        scratch: list[ValidationFailure] = []
        self._validate(instance, schema, "", scratch, doc)
        return not scratch

    # -- per-kind ---------------------------------------------------------
    def _validate_string(
        self, value: str, schema: Mapping[str, Any], path: str, out: list[ValidationFailure]
    ) -> None:
        minimum = schema.get("minLength")
        maximum = schema.get("maxLength")
        if isinstance(minimum, int) and len(value) < minimum:
            out.append(
                ValidationFailure(path, "must be at least %d characters" % minimum, "minLength")
            )
        if isinstance(maximum, int) and len(value) > maximum:
            out.append(
                ValidationFailure(path, "must be at most %d characters" % maximum, "maxLength")
            )
        pattern = schema.get("pattern")
        if isinstance(pattern, str) and not self._compiled(pattern).search(value):
            out.append(ValidationFailure(path, "does not match %s" % pattern, "pattern"))

    def _validate_number(
        self,
        value: float,
        schema: Mapping[str, Any],
        path: str,
        out: list[ValidationFailure],
    ) -> None:
        checks: tuple[tuple[str, Callable[[float], bool], str], ...] = (
            ("minimum", lambda limit: value >= limit, "must be >= %s"),
            ("maximum", lambda limit: value <= limit, "must be <= %s"),
            ("exclusiveMinimum", lambda limit: value > limit, "must be > %s"),
            ("exclusiveMaximum", lambda limit: value < limit, "must be < %s"),
        )
        for keyword, ok, text in checks:
            limit = schema.get(keyword)
            if isinstance(limit, int | float) and not isinstance(limit, bool) and not ok(limit):
                out.append(ValidationFailure(path, text % limit, keyword))
        step = schema.get("multipleOf")
        if isinstance(step, int | float) and not isinstance(step, bool) and step > 0:
            if isinstance(value, int) and isinstance(step, int):
                divides = value % step == 0
            else:
                divides = abs(value / step - round(value / step)) < 1e-9
            if not divides:
                out.append(ValidationFailure(path, "must be a multiple of %s" % step, "multipleOf"))

    def _validate_array(
        self,
        value: list[Any],
        schema: Mapping[str, Any],
        path: str,
        out: list[ValidationFailure],
        doc: str,
    ) -> None:
        minimum = schema.get("minItems")
        maximum = schema.get("maxItems")
        if isinstance(minimum, int) and len(value) < minimum:
            out.append(ValidationFailure(path, "needs at least %d item(s)" % minimum, "minItems"))
        if isinstance(maximum, int) and len(value) > maximum:
            out.append(ValidationFailure(path, "allows at most %d item(s)" % maximum, "maxItems"))
        if schema.get("uniqueItems") is True:
            seen: list[str] = []
            for item in value:
                key = json.dumps(item, sort_keys=True, separators=(",", ":"), default=str)
                if key in seen:
                    out.append(ValidationFailure(path, "items must be unique", "uniqueItems"))
                    break
                seen.append(key)

        prefix = schema.get("prefixItems")
        offset = 0
        if isinstance(prefix, list):
            for index, child_schema in enumerate(prefix):
                if index < len(value):
                    self._validate(value[index], child_schema, "%s[%d]" % (path, index), out, doc)
            offset = len(prefix)
        if "items" in schema:
            for index in range(offset, len(value)):
                self._validate(value[index], schema["items"], "%s[%d]" % (path, index), out, doc)
        if "contains" in schema and not any(
            self._validate_quietly(item, schema["contains"], doc) for item in value
        ):
            out.append(ValidationFailure(path, "must contain a matching item", "contains"))

    def _validate_object(
        self,
        value: dict[str, Any],
        schema: Mapping[str, Any],
        path: str,
        out: list[ValidationFailure],
        doc: str,
    ) -> None:
        required = schema.get("required")
        if isinstance(required, list):
            for name in required:
                if name not in value:
                    out.append(ValidationFailure(_join(path, str(name)), "is required", "required"))

        minimum = schema.get("minProperties")
        maximum = schema.get("maxProperties")
        if isinstance(minimum, int) and len(value) < minimum:
            out.append(
                ValidationFailure(path, "needs at least %d field(s)" % minimum, "minProperties")
            )
        if isinstance(maximum, int) and len(value) > maximum:
            out.append(
                ValidationFailure(path, "allows at most %d field(s)" % maximum, "maxProperties")
            )

        properties = schema.get("properties")
        properties = properties if isinstance(properties, dict) else {}
        pattern_properties = schema.get("patternProperties")
        pattern_properties = pattern_properties if isinstance(pattern_properties, dict) else {}
        additional = schema.get("additionalProperties", True)
        names_schema = schema.get("propertyNames")

        for name, child in value.items():
            child_path = _join(path, name)
            if names_schema is not None:
                self._validate(name, names_schema, child_path, out, doc)
            matched = False
            if name in properties:
                self._validate(child, properties[name], child_path, out, doc)
                matched = True
            for pattern, child_schema in pattern_properties.items():
                if self._compiled(pattern).search(name):
                    self._validate(child, child_schema, child_path, out, doc)
                    matched = True
            if matched:
                continue
            if additional is False:
                known = sorted(properties)
                hint = " Known fields: %s." % ", ".join(known) if known else ""
                out.append(
                    ValidationFailure(
                        child_path, "is not an allowed field.%s" % hint, "additionalProperties"
                    )
                )
            elif additional is not True:
                self._validate(child, additional, child_path, out, doc)

    # -- helpers ----------------------------------------------------------
    def _compiled(self, pattern: str) -> re.Pattern[str]:
        cached = self._pattern_cache.get(pattern)
        if cached is None:
            try:
                cached = re.compile(pattern)
            except re.error as exc:
                raise SchemaError("invalid pattern %r: %s" % (pattern, exc)) from exc
            self._pattern_cache[pattern] = cached
        return cached

    def _type_ok(self, instance: Any, expected: Any, path: str) -> bool:
        names = expected if isinstance(expected, list) else [expected]
        for name in names:
            if name not in _TYPE_NAMES:
                raise SchemaError("%s: unknown type %r" % (path or "<root>", name))
            if _matches_type(instance, str(name)):
                return True
        return False


def _matches_type(instance: Any, name: str) -> bool:
    if name == "null":
        return instance is None
    if name == "boolean":
        return isinstance(instance, bool)
    if name == "object":
        return isinstance(instance, dict)
    if name == "array":
        return isinstance(instance, list)
    if name == "integer":
        # bool is an int in Python. It is never an integer here.
        return isinstance(instance, int) and not isinstance(instance, bool)
    if name == "number":
        return isinstance(instance, int | float) and not isinstance(instance, bool)
    if name == "string":
        return isinstance(instance, str)
    return False


def _describe_type(expected: Any) -> str:
    if isinstance(expected, list):
        return " or ".join(str(name) for name in expected)
    return str(expected)


def _kind(instance: Any) -> str:
    if instance is None:
        return "null"
    if isinstance(instance, bool):
        return "boolean"
    if isinstance(instance, int):
        return "integer"
    if isinstance(instance, float):
        return "number"
    if isinstance(instance, str):
        return "string"
    if isinstance(instance, list):
        return "array"
    if isinstance(instance, dict):
        return "object"
    return type(instance).__name__


def _join(path: str, name: str) -> str:
    return "%s.%s" % (path, name) if path else name
