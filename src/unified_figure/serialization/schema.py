from __future__ import annotations

from dataclasses import fields, is_dataclass
from enum import Enum
import types
from typing import Any, get_args, get_origin, get_type_hints

from unified_figure.model.figure import CanonicalFigure, SCHEMA_VERSION


def build_json_schema() -> dict[str, Any]:
    definitions: dict[str, Any] = {}

    def schema_for(annotation: Any) -> dict[str, Any]:
        origin = get_origin(annotation)
        args = get_args(annotation)
        if origin in (types.UnionType, getattr(__import__("typing"), "Union")):
            return {"anyOf": [schema_for(item) for item in args]}
        if origin is tuple:
            return {"type": "array", "items": schema_for(args[0] if args else Any)}
        if origin is dict:
            return {"type": "object", "additionalProperties": schema_for(args[1])}
        if annotation is Any:
            return {}
        if isinstance(annotation, type) and issubclass(annotation, Enum):
            return {"type": "string", "enum": [item.value for item in annotation]}
        if isinstance(annotation, type) and is_dataclass(annotation):
            name = annotation.__name__
            if name not in definitions:
                definitions[name] = {}
                hints = get_type_hints(annotation)
                properties: dict[str, Any] = {}
                required: list[str] = []
                for item in fields(annotation):
                    item_schema = schema_for(hints[item.name])
                    if annotation.__name__ == "BBox" and item.name in {"width", "height"}:
                        item_schema = {**item_schema, "minimum": 0}
                    if annotation.__name__ == "AuthoritativeInputGeometry" and item.name.endswith("_px"):
                        item_schema = {**item_schema, "exclusiveMinimum": 0}
                    if annotation.__name__ == "Style" and item.name == "opacity":
                        item_schema = {**item_schema, "minimum": 0, "maximum": 1}
                    properties[item.name] = item_schema
                    required.append(item.name)
                definitions[name] = {
                    "type": "object",
                    "properties": properties,
                    "required": required,
                    "additionalProperties": False,
                }
            return {"$ref": f"#/$defs/{name}"}
        if annotation is str:
            return {"type": "string"}
        if annotation is bool:
            return {"type": "boolean"}
        if annotation is int:
            return {"type": "integer"}
        if annotation is float:
            return {"type": "number"}
        if annotation is type(None):
            return {"type": "null"}
        return {}

    root = schema_for(CanonicalFigure)
    definitions["CanonicalFigure"]["properties"]["schema_version"] = {
        "type": "string",
        "const": SCHEMA_VERSION,
    }
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "https://example.invalid/schemas/unified_figure_v0_1.schema.json",
        "title": "Unified Figure Canonical Model v0.1",
        **root,
        "$defs": dict(sorted(definitions.items())),
    }
