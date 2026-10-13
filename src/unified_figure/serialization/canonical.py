from __future__ import annotations

from dataclasses import fields, is_dataclass
from enum import Enum
from hashlib import sha256
import json
import types
from typing import Any, get_args, get_origin, get_type_hints

from unified_figure.model.figure import CanonicalFigure
from unified_figure.model.geometry import ModelValidationError


_ORDER_KEYS = {
    "components": "component_id",
    "groups": "group_id",
    "relations": "relation_id",
    "assets": "asset_id",
    "representation_decisions": "decision_id",
    "fallbacks": "fallback_id",
    "coverage": "coverage_id",
    "review_items": "review_id",
    "failures": "failure_id",
    "evidence": "evidence_id",
    "renderer_realizations": "realization_id",
}


def to_primitive(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if is_dataclass(value):
        return {item.name: to_primitive(getattr(value, item.name)) for item in fields(value)}
    if isinstance(value, dict):
        return {str(key): to_primitive(value[key]) for key in sorted(value)}
    if isinstance(value, (tuple, list)):
        return [to_primitive(item) for item in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise TypeError(f"unsupported canonical value: {type(value)!r}")


def canonical_dict(figure: CanonicalFigure) -> dict[str, object]:
    result = to_primitive(figure)
    for field_name, key_name in _ORDER_KEYS.items():
        result[field_name] = sorted(result[field_name], key=lambda item: item[key_name])
    for group in result["groups"]:
        group["member_component_ids"] = sorted(group["member_component_ids"])
    return result


def canonical_json(figure: CanonicalFigure) -> str:
    return json.dumps(
        canonical_dict(figure),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def canonical_state_hash(figure: CanonicalFigure) -> str:
    state = canonical_dict(figure)
    # A revision stores the hash it names. Blank only that self-referential
    # field when calculating the state hash; all runtime/cost data remains in.
    state["revision"]["canonical_state_hash"] = ""
    encoded = json.dumps(
        state,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return sha256(encoded).hexdigest()


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ModelValidationError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def canonical_loads(text: str) -> CanonicalFigure:
    try:
        data = json.loads(text, object_pairs_hook=_reject_duplicate_keys)
    except json.JSONDecodeError as exc:
        raise ModelValidationError(f"invalid canonical JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise ModelValidationError("canonical JSON root must be an object")
    return CanonicalFigure.from_dict(data)


def from_primitive(expected_type: Any, value: Any) -> Any:
    origin = get_origin(expected_type)
    args = get_args(expected_type)

    if origin in (types.UnionType, getattr(__import__("typing"), "Union")):
        if value is None and type(None) in args:
            return None
        errors: list[str] = []
        for candidate in args:
            if candidate is type(None):
                continue
            try:
                return from_primitive(candidate, value)
            except (TypeError, ValueError, KeyError) as exc:
                errors.append(str(exc))
        raise ModelValidationError(
            f"value {value!r} does not match union {expected_type!r}: {'; '.join(errors)}"
        )

    if origin is tuple:
        if not isinstance(value, list):
            raise ModelValidationError("tuple field must be encoded as a JSON array")
        item_type = args[0] if args else Any
        return tuple(from_primitive(item_type, item) for item in value)

    if origin is dict:
        if not isinstance(value, dict):
            raise ModelValidationError("dict field must be encoded as a JSON object")
        key_type, item_type = args
        return {
            from_primitive(key_type, key): from_primitive(item_type, item)
            for key, item in value.items()
        }

    if expected_type is Any:
        return value

    if isinstance(expected_type, type) and issubclass(expected_type, Enum):
        return expected_type(value)

    if isinstance(expected_type, type) and is_dataclass(expected_type):
        if not isinstance(value, dict):
            raise ModelValidationError(f"{expected_type.__name__} must be a JSON object")
        expected_fields = {item.name for item in fields(expected_type)}
        unknown = set(value) - expected_fields
        missing = expected_fields - set(value)
        if unknown:
            raise ModelValidationError(
                f"unknown fields for {expected_type.__name__}: {sorted(unknown)}"
            )
        if missing:
            raise ModelValidationError(
                f"missing fields for {expected_type.__name__}: {sorted(missing)}"
            )
        hints = get_type_hints(expected_type)
        return expected_type(
            **{
                item.name: from_primitive(hints[item.name], value[item.name])
                for item in fields(expected_type)
            }
        )

    if expected_type is bool:
        if type(value) is not bool:
            raise ModelValidationError(f"expected bool, got {type(value).__name__}")
        return value
    if expected_type is int:
        if type(value) is not int:
            raise ModelValidationError(f"expected int, got {type(value).__name__}")
        return value
    if expected_type is float:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ModelValidationError(f"expected number, got {type(value).__name__}")
        return float(value)
    if expected_type is str:
        if not isinstance(value, str):
            raise ModelValidationError(f"expected string, got {type(value).__name__}")
        return value
    if value is None and expected_type is type(None):
        return None
    if isinstance(value, expected_type):
        return value
    raise ModelValidationError(f"cannot decode {value!r} as {expected_type!r}")
