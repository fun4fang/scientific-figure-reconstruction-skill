from __future__ import annotations

from dataclasses import asdict, dataclass
from hashlib import sha256
import json
from pathlib import Path
from typing import Any, Iterable, Mapping


STRUCTURED_OUTPUT_COMPATIBILITY_POLICY_VERSION = (
    "codex-cli-structured-output-compatibility-v0.3"
)


@dataclass(frozen=True, slots=True)
class StructuredOutputCompatibilityIssue:
    code: str
    path: str
    message: str


@dataclass(frozen=True, slots=True)
class StructuredOutputCompatibilityResult:
    policy_version: str
    json_schema_valid: bool
    provider_schema_compatible: bool
    canonical_schema_hash: str
    expected_schema_hash: str
    hash_matches: bool
    errors: tuple[StructuredOutputCompatibilityIssue, ...]
    not_verified_keywords: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            **asdict(self),
            "errors": [asdict(item) for item in self.errors],
        }

    def require_request_safe(self) -> None:
        if not self.json_schema_valid:
            raise StructuredOutputSchemaCompatibilityError(
                "structured-output schema is not valid JSON Schema", self
            )
        if not self.provider_schema_compatible:
            paths = ", ".join(item.path for item in self.errors) or "<root>"
            raise StructuredOutputSchemaCompatibilityError(
                f"structured-output schema is not provider compatible: {paths}", self
            )
        if not self.hash_matches:
            raise StructuredOutputSchemaCompatibilityError(
                "structured-output schema hash does not match the frozen contract", self
            )


class StructuredOutputSchemaCompatibilityError(ValueError):
    def __init__(
        self,
        message: str,
        result: StructuredOutputCompatibilityResult,
    ) -> None:
        super().__init__(message)
        self.result = result


def canonical_schema_hash(schema: Mapping[str, Any]) -> str:
    payload = json.dumps(schema, sort_keys=True, separators=(",", ":"))
    return "sha256:" + sha256(payload.encode("utf-8")).hexdigest()


def provider_compatibility_policy_hash() -> str:
    policy = {
        "policy_version": STRUCTURED_OUTPUT_COMPATIBILITY_POLICY_VERSION,
        "confirmed_rules": (
            "const-requires-explicit-compatible-type",
            "enum-requires-explicit-compatible-type",
            "recursive-schema-location-reporting",
        ),
        "unknown_provider_constraints": "NOT_VERIFIED",
    }
    payload = json.dumps(policy, sort_keys=True, separators=(",", ":"))
    return "sha256:" + sha256(payload.encode("utf-8")).hexdigest()


class StructuredOutputCompatibilityValidator:
    """Validate JSON Schema separately from confirmed provider-subset rules.

    The provider rejected an untyped ``const`` during Attempt 002. The frozen
    compatibility policy therefore requires explicit compatible types for both
    ``const`` and ``enum`` schema nodes and reports their recursive paths. It
    deliberately does not guess additional undocumented provider restrictions.
    """

    _SCHEMA_CONTAINER_KEYS = frozenset(
        {
            "$defs",
            "definitions",
            "properties",
            "patternProperties",
            "dependentSchemas",
        }
    )
    _SCHEMA_SINGLE_KEYS = frozenset(
        {
            "additionalProperties",
            "contains",
            "else",
            "if",
            "items",
            "not",
            "propertyNames",
            "then",
            "unevaluatedItems",
            "unevaluatedProperties",
        }
    )
    _SCHEMA_LIST_KEYS = frozenset({"allOf", "anyOf", "oneOf", "prefixItems"})
    _OBSERVED_PROVIDER_SENSITIVE_KEYWORDS = frozenset(
        {
            "$defs",
            "$ref",
            "allOf",
            "anyOf",
            "default",
            "format",
            "oneOf",
            "pattern",
        }
    )

    def validate(
        self,
        schema: Mapping[str, Any],
        *,
        expected_schema_hash: str | None = None,
    ) -> StructuredOutputCompatibilityResult:
        errors: list[StructuredOutputCompatibilityIssue] = []
        json_schema_valid = True
        try:
            from jsonschema import validators
            from jsonschema.exceptions import SchemaError

            validator = validators.validator_for(schema)
            validator.check_schema(schema)
        except ImportError as exc:  # pragma: no cover - environment contract failure
            json_schema_valid = False
            errors.append(
                StructuredOutputCompatibilityIssue(
                    "JSON_SCHEMA_VALIDATOR_UNAVAILABLE",
                    "<root>",
                    str(exc),
                )
            )
        except SchemaError as exc:
            json_schema_valid = False
            path = self._format_path(tuple(str(item) for item in exc.path))
            errors.append(
                StructuredOutputCompatibilityIssue(
                    "JSON_SCHEMA_INVALID",
                    path,
                    exc.message,
                )
            )

        observed_sensitive: set[str] = set()
        for path, node in self._walk_schema(schema):
            observed_sensitive.update(
                key for key in node if key in self._OBSERVED_PROVIDER_SENSITIVE_KEYWORDS
            )
            if "const" in node:
                errors.extend(self._explicit_type_issues(path, node, "const", (node["const"],)))
            if "enum" in node:
                values = node["enum"] if isinstance(node["enum"], list) else (node["enum"],)
                errors.extend(self._explicit_type_issues(path, node, "enum", values))

        canonical_hash = canonical_schema_hash(schema)
        expected = expected_schema_hash or canonical_hash
        provider_errors = tuple(
            item for item in errors if item.code != "JSON_SCHEMA_INVALID"
        )
        return StructuredOutputCompatibilityResult(
            policy_version=STRUCTURED_OUTPUT_COMPATIBILITY_POLICY_VERSION,
            json_schema_valid=json_schema_valid,
            provider_schema_compatible=json_schema_valid and not provider_errors,
            canonical_schema_hash=canonical_hash,
            expected_schema_hash=expected,
            hash_matches=canonical_hash == expected,
            errors=tuple(errors),
            not_verified_keywords=tuple(sorted(observed_sensitive)),
        )

    def validate_file(
        self,
        path: str | Path,
        *,
        expected_schema_hash: str | None = None,
    ) -> StructuredOutputCompatibilityResult:
        schema_path = Path(path)
        value = json.loads(schema_path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise ValueError("structured-output schema root must be an object")
        return self.validate(value, expected_schema_hash=expected_schema_hash)

    def _walk_schema(
        self,
        schema: Mapping[str, Any],
        path: tuple[str, ...] = (),
    ) -> Iterable[tuple[tuple[str, ...], Mapping[str, Any]]]:
        yield path, schema
        for key, value in schema.items():
            if key in self._SCHEMA_CONTAINER_KEYS and isinstance(value, dict):
                for name, child in value.items():
                    if isinstance(child, dict):
                        yield from self._walk_schema(child, (*path, key, str(name)))
            elif key in self._SCHEMA_SINGLE_KEYS and isinstance(value, dict):
                yield from self._walk_schema(value, (*path, key))
            elif key in self._SCHEMA_LIST_KEYS and isinstance(value, list):
                for index, child in enumerate(value):
                    if isinstance(child, dict):
                        yield from self._walk_schema(child, (*path, key, str(index)))

    def _explicit_type_issues(
        self,
        path: tuple[str, ...],
        node: Mapping[str, Any],
        keyword: str,
        values: Iterable[Any],
    ) -> tuple[StructuredOutputCompatibilityIssue, ...]:
        location = self._format_path(path)
        declared = node.get("type")
        if declared is None:
            return (
                StructuredOutputCompatibilityIssue(
                    f"{keyword.upper()}_MISSING_EXPLICIT_TYPE",
                    location,
                    f"{keyword} requires an explicit compatible type for this provider",
                ),
            )
        allowed = {declared} if isinstance(declared, str) else set(declared)
        incompatible = [value for value in values if not self._value_matches(value, allowed)]
        if incompatible:
            return (
                StructuredOutputCompatibilityIssue(
                    f"{keyword.upper()}_TYPE_MISMATCH",
                    location,
                    f"{keyword} values are incompatible with declared type {declared!r}",
                ),
            )
        return ()

    @staticmethod
    def _value_matches(value: Any, allowed: set[str]) -> bool:
        if value is None:
            return "null" in allowed
        if isinstance(value, bool):
            return "boolean" in allowed
        if isinstance(value, int):
            return "integer" in allowed or "number" in allowed
        if isinstance(value, float):
            return "number" in allowed
        if isinstance(value, str):
            return "string" in allowed
        if isinstance(value, list):
            return "array" in allowed
        if isinstance(value, dict):
            return "object" in allowed
        return False

    @staticmethod
    def _format_path(path: tuple[str, ...]) -> str:
        return ".".join(path) if path else "<root>"


def provider_schema_preflight(
    schema_path: str | Path,
    *,
    expected_schema_hash: str,
) -> StructuredOutputCompatibilityResult:
    result = StructuredOutputCompatibilityValidator().validate_file(
        schema_path,
        expected_schema_hash=expected_schema_hash,
    )
    result.require_request_safe()
    return result

