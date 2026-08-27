"""A tiny JSON Schema (draft 2020-12) validator — exactly the keywords our schemas use.

Why this exists: ``rulepacks/schema/rulepack.schema.json`` is the contract that
makes a pack loadable, and the engine must enforce it at load time (playbook §6).
``jsonschema`` is not in ``apps/api/pyproject.toml``, and this package is imported
by both the API and the solver worker, so it stays dependency-free.

The load-bearing design rule, borrowed from ``services/common/jsonschema_lite.py``:
**an unimplemented keyword raises** :class:`~garh_rules.errors.SchemaFeatureError`
rather than being ignored. A validator that skips what it does not understand
reports "valid" for a schema it only partly checked, and a partly checked pack is
an unchecked pack. Adding a keyword to the schema therefore requires adding it
here, in the same commit.

Implemented: ``$ref`` (local ``#/...`` pointers only), ``$defs``, ``type``
(string or list), ``enum``, ``const``, ``properties``, ``required``,
``additionalProperties`` (bool or schema), ``minProperties``, ``maxProperties``,
``items``, ``minItems``, ``maxItems``, ``uniqueItems``, ``minimum``, ``maximum``,
``exclusiveMinimum``, ``exclusiveMaximum``, ``multipleOf``, ``minLength``,
``maxLength``, ``pattern``, ``allOf``, ``anyOf``, ``oneOf``, ``not``,
``if``/``then``/``else``, ``format`` (``date`` only, validated).

Ignored as annotations: ``$schema``, ``$id``, ``$anchor``, ``title``,
``description``, ``default``, ``examples``, ``deprecated``, ``readOnly``,
``writeOnly``, ``$comment``, and any ``x-``-prefixed extension keyword (that is
how ``x-garh-check-meta`` rides along in the pack schema).
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from typing import Any

from .errors import SchemaFeatureError

__all__ = ["validate", "SchemaValidator", "is_valid"]

#: Keywords that carry no assertion. Ignoring these is safe by specification.
_ANNOTATIONS = frozenset(
    {
        "$schema",
        "$id",
        "$anchor",
        "$comment",
        "$defs",
        "title",
        "description",
        "default",
        "examples",
        "deprecated",
        "readOnly",
        "writeOnly",
    }
)

_JSON_TYPES: dict[str, tuple[type, ...]] = {
    "object": (dict,),
    "array": (list,),
    "string": (str,),
    "boolean": (bool,),
    "null": (type(None),),
}

_DATE_RE = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")


def _type_matches(value: Any, name: str) -> bool:
    if name == "integer":
        # A JSON "integer" excludes booleans (Python's bool is an int subclass) and
        # accepts 1.0-style floats only when integral. Packs must never contain a
        # float at all, and the pack schema's `type: integer` is what enforces it.
        if isinstance(value, bool):
            return False
        if isinstance(value, int):
            return True
        return isinstance(value, float) and float(value).is_integer()
    if name == "number":
        return not isinstance(value, bool) and isinstance(value, int | float)
    expected = _JSON_TYPES.get(name)
    if expected is None:
        raise SchemaFeatureError("unknown JSON Schema type %r" % (name,))
    if name == "boolean":
        return isinstance(value, bool)
    if name == "object":
        return isinstance(value, dict)
    if name == "string":
        return isinstance(value, str)
    return isinstance(value, expected)


def _canonical(value: Any) -> str:
    """Stable key for ``uniqueItems``."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


class SchemaValidator:
    """Validates instances against one schema document. Reusable and stateless."""

    def __init__(self, schema: Mapping[str, Any]) -> None:
        self.root: Mapping[str, Any] = schema
        self._pattern_cache: dict[str, re.Pattern[str]] = {}

    # -- public ------------------------------------------------------------
    def validate(self, instance: Any, *, path: str = "$") -> list[str]:
        """Return a list of human-readable violations. Empty means valid."""
        errors: list[str] = []
        self._check(instance, self.root, path, errors)
        return errors

    # -- internals ---------------------------------------------------------
    def _pattern(self, pattern: str) -> re.Pattern[str]:
        compiled = self._pattern_cache.get(pattern)
        if compiled is None:
            compiled = re.compile(pattern)
            self._pattern_cache[pattern] = compiled
        return compiled

    def _resolve(self, ref: str, path: str) -> Mapping[str, Any]:
        if not ref.startswith("#"):
            raise SchemaFeatureError(
                "only local $ref pointers are supported, got %r at %s" % (ref, path)
            )
        pointer = ref[1:]
        node: Any = self.root
        if pointer in ("", "/"):
            return self.root
        for raw in pointer.lstrip("/").split("/"):
            token = raw.replace("~1", "/").replace("~0", "~")
            if isinstance(node, dict):
                if token not in node:
                    raise SchemaFeatureError("$ref %r does not resolve (at %s)" % (ref, path))
                node = node[token]
            elif isinstance(node, list):
                node = node[int(token)]
            else:  # pragma: no cover - malformed schema
                raise SchemaFeatureError("$ref %r does not resolve (at %s)" % (ref, path))
        if not isinstance(node, dict):
            raise SchemaFeatureError("$ref %r does not point at a schema (at %s)" % (ref, path))
        return node

    def _subschema_ok(self, instance: Any, schema: Mapping[str, Any], path: str) -> bool:
        probe: list[str] = []
        self._check(instance, schema, path, probe)
        return not probe

    def _check(self, instance: Any, schema: Any, path: str, errors: list[str]) -> None:
        if schema is True or schema == {}:
            return
        if schema is False:
            errors.append("%s: schema forbids any value here" % path)
            return
        if not isinstance(schema, dict):
            raise SchemaFeatureError("schema at %s is not an object or boolean" % path)

        for keyword in schema:
            if keyword in _ANNOTATIONS or keyword.startswith("x-"):
                continue
            if keyword not in _HANDLED:
                raise SchemaFeatureError(
                    "JSON Schema keyword %r (at %s) is not implemented by "
                    "garh_rules.jsonschema_min" % (keyword, path)
                )

        if "$ref" in schema:
            self._check(instance, self._resolve(schema["$ref"], path), path, errors)
            # Draft 2020-12 applies sibling keywords alongside $ref, so we keep going.

        if "type" in schema:
            declared = schema["type"]
            names: Sequence[str] = [declared] if isinstance(declared, str) else list(declared)
            if not any(_type_matches(instance, n) for n in names):
                errors.append(
                    "%s: expected type %s, got %s"
                    % (path, "/".join(names), type(instance).__name__)
                )
                return  # every remaining assertion would be noise

        if "const" in schema and instance != schema["const"]:
            errors.append("%s: must equal %r, got %r" % (path, schema["const"], instance))
        if "enum" in schema and instance not in schema["enum"]:
            errors.append("%s: %r is not one of %r" % (path, instance, schema["enum"]))

        if isinstance(instance, str):
            self._check_string(instance, schema, path, errors)
        if isinstance(instance, bool):
            pass  # bool is not a number for our purposes
        elif isinstance(instance, int | float):
            self._check_number(instance, schema, path, errors)
        if isinstance(instance, list):
            self._check_array(instance, schema, path, errors)
        if isinstance(instance, dict):
            self._check_object(instance, schema, path, errors)

        self._check_logic(instance, schema, path, errors)

    def _check_string(
        self, value: str, schema: Mapping[str, Any], path: str, errors: list[str]
    ) -> None:
        if "minLength" in schema and len(value) < schema["minLength"]:
            errors.append("%s: shorter than %d characters" % (path, schema["minLength"]))
        if "maxLength" in schema and len(value) > schema["maxLength"]:
            errors.append("%s: longer than %d characters" % (path, schema["maxLength"]))
        if "pattern" in schema and not self._pattern(schema["pattern"]).search(value):
            errors.append("%s: %r does not match /%s/" % (path, value, schema["pattern"]))
        fmt = schema.get("format")
        if fmt is not None:
            if fmt != "date":
                raise SchemaFeatureError(
                    "format %r (at %s) is not implemented; only 'date' is" % (fmt, path)
                )
            if not _DATE_RE.match(value):
                errors.append("%s: %r is not a YYYY-MM-DD date" % (path, value))

    def _check_number(
        self, value: Any, schema: Mapping[str, Any], path: str, errors: list[str]
    ) -> None:
        if "minimum" in schema and value < schema["minimum"]:
            errors.append("%s: %r is below the minimum %r" % (path, value, schema["minimum"]))
        if "maximum" in schema and value > schema["maximum"]:
            errors.append("%s: %r is above the maximum %r" % (path, value, schema["maximum"]))
        if "exclusiveMinimum" in schema and value <= schema["exclusiveMinimum"]:
            errors.append("%s: %r must be > %r" % (path, value, schema["exclusiveMinimum"]))
        if "exclusiveMaximum" in schema and value >= schema["exclusiveMaximum"]:
            errors.append("%s: %r must be < %r" % (path, value, schema["exclusiveMaximum"]))
        if "multipleOf" in schema:
            step = schema["multipleOf"]
            if step <= 0:
                raise SchemaFeatureError("multipleOf must be positive (at %s)" % path)
            if isinstance(value, int) and isinstance(step, int):
                ok = value % step == 0
            else:  # pragma: no cover - packs carry no floats
                ok = abs(value / step - round(value / step)) < 1e-9
            if not ok:
                errors.append("%s: %r is not a multiple of %r" % (path, value, step))

    def _check_array(
        self, value: list[Any], schema: Mapping[str, Any], path: str, errors: list[str]
    ) -> None:
        if "minItems" in schema and len(value) < schema["minItems"]:
            errors.append("%s: needs at least %d items" % (path, schema["minItems"]))
        if "maxItems" in schema and len(value) > schema["maxItems"]:
            errors.append("%s: allows at most %d items" % (path, schema["maxItems"]))
        if schema.get("uniqueItems"):
            seen: set[str] = set()
            for i, item in enumerate(value):
                key = _canonical(item)
                if key in seen:
                    errors.append("%s[%d]: duplicate item %r" % (path, i, item))
                seen.add(key)
        if "items" in schema:
            for i, item in enumerate(value):
                self._check(item, schema["items"], "%s[%d]" % (path, i), errors)

    def _check_object(
        self, value: dict[str, Any], schema: Mapping[str, Any], path: str, errors: list[str]
    ) -> None:
        if "minProperties" in schema and len(value) < schema["minProperties"]:
            errors.append("%s: needs at least %d properties" % (path, schema["minProperties"]))
        if "maxProperties" in schema and len(value) > schema["maxProperties"]:
            errors.append("%s: allows at most %d properties" % (path, schema["maxProperties"]))
        for key in schema.get("required", ()):
            if key not in value:
                errors.append("%s: required property %r is missing" % (path, key))
        properties: Mapping[str, Any] = schema.get("properties") or {}
        for key, sub in properties.items():
            if key in value:
                self._check(value[key], sub, "%s.%s" % (path, key), errors)
        if "additionalProperties" in schema:
            extra = schema["additionalProperties"]
            for key in value:
                if key in properties:
                    continue
                if extra is False:
                    errors.append("%s: property %r is not allowed here" % (path, key))
                elif extra is not True:
                    self._check(value[key], extra, "%s.%s" % (path, key), errors)

    def _check_logic(
        self, instance: Any, schema: Mapping[str, Any], path: str, errors: list[str]
    ) -> None:
        for sub in schema.get("allOf", ()):
            self._check(instance, sub, path, errors)
        if "anyOf" in schema and not any(
            self._subschema_ok(instance, sub, path) for sub in schema["anyOf"]
        ):
            errors.append("%s: matches none of the anyOf alternatives" % path)
        if "oneOf" in schema:
            matched = [
                i
                for i, sub in enumerate(schema["oneOf"])
                if self._subschema_ok(instance, sub, path)
            ]
            if len(matched) != 1:
                # Report why, because "matched 0 of 18" on a `check` object is the
                # single most common pack-authoring mistake and the reason has to
                # be actionable.
                detail = self._explain_one_of(instance, schema["oneOf"], path)
                errors.append(
                    "%s: must match exactly one alternative, matched %d%s"
                    % (path, len(matched), detail)
                )
        if "not" in schema and self._subschema_ok(instance, schema["not"], path):
            errors.append("%s: must not match the `not` schema" % path)
        if "if" in schema:
            branch = "then" if self._subschema_ok(instance, schema["if"], path) else "else"
            if branch in schema:
                self._check(instance, schema[branch], path, errors)

    def _explain_one_of(self, instance: Any, alternatives: Sequence[Any], path: str) -> str:
        """Pick the closest alternative and quote its complaints.

        A discriminated union keyed on a ``const`` (which is how every ``check``
        subschema is written) has exactly one plausible branch. Naming it turns
        "matched 0 of 18" into "wall thickness is not a valid setback_min field".
        """
        best: tuple[int, str, list[str]] | None = None
        for alt in alternatives:
            schema = (
                self._resolve(alt["$ref"], path) if isinstance(alt, dict) and "$ref" in alt else alt
            )
            if not isinstance(schema, dict):
                continue
            discriminator = ((schema.get("properties") or {}).get("type") or {}).get("const")
            probe: list[str] = []
            self._check(instance, schema, path, probe)
            if discriminator is not None and isinstance(instance, dict):
                if instance.get("type") != discriminator:
                    continue  # not this branch at all
                return " — as %r: %s" % (discriminator, "; ".join(probe))
            if best is None or len(probe) < best[0]:
                best = (len(probe), str(discriminator), probe)
        if best is not None and best[2]:
            return " — closest: %s" % "; ".join(best[2][:3])
        return ""


#: Every keyword the validator asserts on. Anything outside this set (and outside
#: ``_ANNOTATIONS`` / an ``x-`` prefix) raises rather than passing unchecked.
_HANDLED = frozenset(
    {
        "$ref",
        "type",
        "const",
        "enum",
        "format",
        "minLength",
        "maxLength",
        "pattern",
        "minimum",
        "maximum",
        "exclusiveMinimum",
        "exclusiveMaximum",
        "multipleOf",
        "minItems",
        "maxItems",
        "uniqueItems",
        "items",
        "minProperties",
        "maxProperties",
        "required",
        "properties",
        "additionalProperties",
        "allOf",
        "anyOf",
        "oneOf",
        "not",
        "if",
        "then",
        "else",
    }
)


def validate(instance: Any, schema: Mapping[str, Any], *, path: str = "$") -> list[str]:
    """One-shot validation. Returns the (possibly empty) list of violations."""
    return SchemaValidator(schema).validate(instance, path=path)


def is_valid(instance: Any, schema: Mapping[str, Any]) -> bool:
    return not validate(instance, schema)
