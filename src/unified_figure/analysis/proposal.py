from __future__ import annotations

from dataclasses import dataclass, fields
from hashlib import sha256
import json
from typing import Any

from unified_figure.serialization.canonical import from_primitive, to_primitive


PROPOSAL_VERSION = "analysis-proposal-v0.1"


@dataclass(frozen=True, slots=True)
class ProposalBBox:
    x: float
    y: float
    width: float
    height: float
    coordinate_space: str


@dataclass(frozen=True, slots=True)
class ObservedCanvas:
    width: int
    height: int
    coordinate_space: str
    orientation: str


@dataclass(frozen=True, slots=True)
class StyleHint:
    key: str
    value: str


@dataclass(frozen=True, slots=True)
class PanelProposal:
    proposal_id: str
    semantic_role: str
    bbox: ProposalBBox
    confidence: str
    uncertainty: str
    notes: str


@dataclass(frozen=True, slots=True)
class ComponentProposal:
    proposal_id: str
    panel_proposal_id: str | None
    proposed_type: str
    semantic_role: str
    bbox: ProposalBBox
    text_content: str | None
    style_hints: tuple[StyleHint, ...]
    representation_hints: tuple[str, ...]
    asset_proposal_id: str | None
    confidence: str
    uncertainty: str
    evidence_region: ProposalBBox
    observation_basis: str
    notes: str


@dataclass(frozen=True, slots=True)
class GroupProposal:
    proposal_id: str
    parent_group_proposal_id: str | None
    member_proposal_ids: tuple[str, ...]
    semantic_role: str
    confidence: str
    uncertainty: str
    evidence_region: ProposalBBox
    notes: str


@dataclass(frozen=True, slots=True)
class RelationProposal:
    proposal_id: str
    source_candidate_id: str | None
    target_candidate_id: str | None
    direction: str
    relation_type: str
    route_type: str
    route_points: tuple[tuple[float, float], ...]
    coordinate_space: str
    visual_style_hints: tuple[StyleHint, ...]
    confidence: str
    uncertainty: str
    evidence_region: ProposalBBox
    observation_basis: str
    notes: str


@dataclass(frozen=True, slots=True)
class AssetProposal:
    proposal_id: str
    component_proposal_id: str
    asset_type: str
    source_type: str
    source_region: ProposalBBox
    separable: bool
    has_alpha: str
    authoritative_data_ref: str | None
    representation_hints: tuple[str, ...]
    confidence: str
    uncertainty: str
    notes: str


@dataclass(frozen=True, slots=True)
class ChartProposal:
    proposal_id: str
    component_proposal_id: str
    chart_type_hint: str
    authoritative_data_available: bool
    authoritative_data_ref: str | None
    proposed_values: tuple[str, ...]
    confidence: str
    uncertainty: str
    notes: str


@dataclass(frozen=True, slots=True)
class CoverageObservation:
    dimension: str
    status: str
    confidence: str
    target_proposal_ids: tuple[str, ...]
    notes: str


@dataclass(frozen=True, slots=True)
class AnalysisUncertainty:
    uncertainty_id: str
    target_proposal_id: str
    reason_type: str
    severity: str
    region: ProposalBBox
    resolvable_by_roi: bool
    notes: str


@dataclass(frozen=True, slots=True)
class ReviewCandidate:
    candidate_id: str
    target_proposal_id: str
    reason_type: str
    severity: str
    candidate_actions: tuple[str, ...]
    candidate_representations: tuple[str, ...]
    notes: str


@dataclass(frozen=True, slots=True)
class ModelEvidence:
    evidence_id: str
    target_proposal_ids: tuple[str, ...]
    observation: str
    evidence_region: ProposalBBox
    confidence: str


@dataclass(frozen=True, slots=True)
class AnalysisRuntimeMetadata:
    provider: str
    model: str
    response_id: str
    prompt_version: str
    prompt_hash: str
    proposal_schema_version: str
    proposal_schema_hash: str
    input_hash: str
    analysis_mode: str
    input_tokens: int | str
    output_tokens: int | str
    wall_time_ms: float | str
    infrastructure_attempts: int
    cache_hit: bool


@dataclass(frozen=True, slots=True)
class AnalysisProposal:
    """Provider-neutral, provisional, untrusted, non-canonical model output."""

    proposal_version: str
    source_ref: str
    observed_canvas: ObservedCanvas
    panels: tuple[PanelProposal, ...]
    component_proposals: tuple[ComponentProposal, ...]
    group_proposals: tuple[GroupProposal, ...]
    relation_proposals: tuple[RelationProposal, ...]
    asset_proposals: tuple[AssetProposal, ...]
    chart_proposals: tuple[ChartProposal, ...]
    coverage_observations: tuple[CoverageObservation, ...]
    uncertainties: tuple[AnalysisUncertainty, ...]
    review_candidates: tuple[ReviewCandidate, ...]
    model_evidence: tuple[ModelEvidence, ...]
    runtime_metadata: AnalysisRuntimeMetadata

    def to_dict(self) -> dict[str, Any]:
        return to_primitive(self)

    def to_json(self) -> str:
        return json.dumps(
            self.to_dict(), ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
        )

    def content_hash(self) -> str:
        return "sha256:" + sha256(self.to_json().encode("utf-8")).hexdigest()

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "AnalysisProposal":
        return from_primitive(cls, value)

    @classmethod
    def from_json(cls, value: str) -> "AnalysisProposal":
        parsed = json.loads(value)
        if not isinstance(parsed, dict):
            raise ValueError("AnalysisProposal JSON root must be an object")
        return cls.from_dict(parsed)


def _strict_object(properties: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": properties,
        "required": list(properties),
        "additionalProperties": False,
    }


def analysis_proposal_json_schema() -> dict[str, Any]:
    """Return the frozen model-output JSON Schema; canonical schema v0.1 is unrelated."""

    bbox = _strict_object(
        {
            "x": {"type": "number"},
            "y": {"type": "number"},
            "width": {"type": "number"},
            "height": {"type": "number"},
            "coordinate_space": {"type": "string", "enum": ["SOURCE_PIXELS", "NORMALIZED"]},
        }
    )
    style_hint = _strict_object({"key": {"type": "string"}, "value": {"type": "string"}})
    nullable_string = {"anyOf": [{"type": "string"}, {"type": "null"}]}
    confidence = {"type": "string", "enum": ["HIGH", "MEDIUM", "LOW"]}
    uncertainty = {
        "type": "string",
        "enum": ["NONE", "LOW", "MEDIUM", "HIGH", "AMBIGUOUS", "REVIEW_REQUIRED"],
    }
    string_array = {"type": "array", "items": {"type": "string"}}
    properties = {
        "proposal_version": {"type": "string", "const": PROPOSAL_VERSION},
        "source_ref": {"type": "string"},
        "observed_canvas": _strict_object(
            {
                "width": {"type": "integer"},
                "height": {"type": "integer"},
                "coordinate_space": {"type": "string", "enum": ["SOURCE_PIXELS", "NORMALIZED"]},
                "orientation": {
                    "type": "string",
                    "enum": ["NORMAL", "ROTATE_90", "ROTATE_180", "ROTATE_270"],
                },
            }
        ),
        "panels": {
            "type": "array",
            "items": _strict_object(
                {
                    "proposal_id": {"type": "string"},
                    "semantic_role": {"type": "string"},
                    "bbox": bbox,
                    "confidence": confidence,
                    "uncertainty": uncertainty,
                    "notes": {"type": "string"},
                }
            ),
        },
        "component_proposals": {
            "type": "array",
            "items": _strict_object(
                {
                    "proposal_id": {"type": "string"},
                    "panel_proposal_id": nullable_string,
                    "proposed_type": {
                        "type": "string",
                        "enum": [
                            "TEXT", "SIMPLE_SHAPE", "RELATION_ANCHOR", "COMPLEX_VISUAL",
                            "CHART", "IMAGE_ASSET", "PANEL", "UNKNOWN",
                        ],
                    },
                    "semantic_role": {"type": "string"},
                    "bbox": bbox,
                    "text_content": nullable_string,
                    "style_hints": {"type": "array", "items": style_hint},
                    "representation_hints": string_array,
                    "asset_proposal_id": nullable_string,
                    "confidence": confidence,
                    "uncertainty": uncertainty,
                    "evidence_region": bbox,
                    "observation_basis": {
                        "type": "string", "enum": ["OBSERVED", "INFERRED", "UNCERTAIN"]
                    },
                    "notes": {"type": "string"},
                }
            ),
        },
        "group_proposals": {
            "type": "array",
            "items": _strict_object(
                {
                    "proposal_id": {"type": "string"},
                    "parent_group_proposal_id": nullable_string,
                    "member_proposal_ids": string_array,
                    "semantic_role": {"type": "string"},
                    "confidence": confidence,
                    "uncertainty": uncertainty,
                    "evidence_region": bbox,
                    "notes": {"type": "string"},
                }
            ),
        },
        "relation_proposals": {
            "type": "array",
            "items": _strict_object(
                {
                    "proposal_id": {"type": "string"},
                    "source_candidate_id": nullable_string,
                    "target_candidate_id": nullable_string,
                    "direction": {
                        "type": "string",
                        "enum": ["SOURCE_TO_TARGET", "TARGET_TO_SOURCE", "BIDIRECTIONAL", "UNDIRECTED", "UNKNOWN"],
                    },
                    "relation_type": {
                        "type": "string",
                        "enum": ["DIRECTED", "UNDIRECTED", "INHIBITORY", "ASSOCIATION", "FLOW", "DEPENDENCY", "UNKNOWN"],
                    },
                    "route_type": {"type": "string", "enum": ["STRAIGHT", "ELBOW", "CURVED", "BRANCH", "SELF_LOOP"]},
                    "route_points": {
                        "type": "array",
                        "items": {"type": "array", "items": {"type": "number"}, "minItems": 2, "maxItems": 2},
                    },
                    "coordinate_space": {"type": "string", "enum": ["SOURCE_PIXELS", "NORMALIZED"]},
                    "visual_style_hints": {"type": "array", "items": style_hint},
                    "confidence": confidence,
                    "uncertainty": uncertainty,
                    "evidence_region": bbox,
                    "observation_basis": {
                        "type": "string", "enum": ["OBSERVED", "INFERRED", "UNCERTAIN"]
                    },
                    "notes": {"type": "string"},
                }
            ),
        },
        "asset_proposals": {
            "type": "array",
            "items": _strict_object(
                {
                    "proposal_id": {"type": "string"},
                    "component_proposal_id": {"type": "string"},
                    "asset_type": {"type": "string", "enum": ["RASTER", "VECTOR", "GENERATED", "EXTERNAL"]},
                    "source_type": {
                        "type": "string",
                        "enum": ["SOURCE_EXACT", "SOURCE_VECTOR", "SOURCE_PRESERVED_RASTER", "SOURCE_GENERATED", "SOURCE_EXTERNAL", "UNKNOWN"],
                    },
                    "source_region": bbox,
                    "separable": {"type": "boolean"},
                    "has_alpha": {"type": "string", "enum": ["YES", "NO", "UNKNOWN"]},
                    "authoritative_data_ref": nullable_string,
                    "representation_hints": string_array,
                    "confidence": confidence,
                    "uncertainty": uncertainty,
                    "notes": {"type": "string"},
                }
            ),
        },
        "chart_proposals": {
            "type": "array",
            "items": _strict_object(
                {
                    "proposal_id": {"type": "string"},
                    "component_proposal_id": {"type": "string"},
                    "chart_type_hint": {"type": "string"},
                    "authoritative_data_available": {"type": "boolean"},
                    "authoritative_data_ref": nullable_string,
                    "proposed_values": string_array,
                    "confidence": confidence,
                    "uncertainty": uncertainty,
                    "notes": {"type": "string"},
                }
            ),
        },
        "coverage_observations": {
            "type": "array",
            "items": _strict_object(
                {
                    "dimension": {
                        "type": "string",
                        "enum": ["TEXT_COVERAGE", "STRUCTURAL_OBJECT_COVERAGE", "RELATION_COVERAGE", "COMPLEX_ASSET_COVERAGE", "CHART_COVERAGE", "PANEL_COVERAGE"],
                    },
                    "status": {"type": "string", "enum": ["PRESENT", "PARTIAL", "MISSING", "ALTERED", "UNKNOWN"]},
                    "confidence": confidence,
                    "target_proposal_ids": string_array,
                    "notes": {"type": "string"},
                }
            ),
        },
        "uncertainties": {
            "type": "array",
            "items": _strict_object(
                {
                    "uncertainty_id": {"type": "string"},
                    "target_proposal_id": {"type": "string"},
                    "reason_type": {
                        "type": "string",
                        "enum": ["UNCERTAIN_RELATION", "UNCERTAIN_GROUPING", "REPRESENTATION_AMBIGUITY", "EXTRACTION_LIMITATION", "MISSING_CONTENT", "GEOMETRY_CONFLICT", "UNSUPPORTED_FEATURE"],
                    },
                    "severity": {"type": "string", "enum": ["INFO", "WARNING", "ERROR", "CRITICAL"]},
                    "region": bbox,
                    "resolvable_by_roi": {"type": "boolean"},
                    "notes": {"type": "string"},
                }
            ),
        },
        "review_candidates": {
            "type": "array",
            "items": _strict_object(
                {
                    "candidate_id": {"type": "string"},
                    "target_proposal_id": {"type": "string"},
                    "reason_type": {
                        "type": "string",
                        "enum": ["UNCERTAIN_RELATION", "UNCERTAIN_GROUPING", "REPRESENTATION_AMBIGUITY", "EXTRACTION_LIMITATION", "SCIENTIFIC_GENERATION_RISK", "MISSING_CONTENT", "ALTERED_CONTENT", "BACKEND_CAPABILITY_LIMIT", "GEOMETRY_CONFLICT", "UNSUPPORTED_FEATURE"],
                    },
                    "severity": {"type": "string", "enum": ["INFO", "WARNING", "ERROR", "CRITICAL"]},
                    "candidate_actions": string_array,
                    "candidate_representations": string_array,
                    "notes": {"type": "string"},
                }
            ),
        },
        "model_evidence": {
            "type": "array",
            "items": _strict_object(
                {
                    "evidence_id": {"type": "string"},
                    "target_proposal_ids": string_array,
                    "observation": {"type": "string"},
                    "evidence_region": bbox,
                    "confidence": confidence,
                }
            ),
        },
        "runtime_metadata": _strict_object(
            {
                "provider": {"type": "string"},
                "model": {"type": "string"},
                "response_id": {"type": "string"},
                "prompt_version": {"type": "string"},
                "prompt_hash": {"type": "string"},
                "proposal_schema_version": {"type": "string"},
                "proposal_schema_hash": {"type": "string"},
                "input_hash": {"type": "string"},
                "analysis_mode": {"type": "string"},
                "input_tokens": {"anyOf": [{"type": "integer"}, {"type": "string"}]},
                "output_tokens": {"anyOf": [{"type": "integer"}, {"type": "string"}]},
                "wall_time_ms": {"anyOf": [{"type": "number"}, {"type": "string"}]},
                "infrastructure_attempts": {"type": "integer"},
                "cache_hit": {"type": "boolean"},
            }
        ),
    }
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "https://unified-figure.local/schemas/analysis-proposal-v0.1.json",
        "title": "Unified Figure AnalysisProposal v0.1",
        **_strict_object(properties),
    }


def proposal_schema_hash() -> str:
    encoded = json.dumps(
        analysis_proposal_json_schema(), sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return "sha256:" + sha256(encoded).hexdigest()


def assert_complete_dataclass_contract() -> None:
    """Small guard used by tests to keep the schema and dataclass top level aligned."""

    schema_fields = set(analysis_proposal_json_schema()["properties"])
    dataclass_fields = {item.name for item in fields(AnalysisProposal)}
    if schema_fields != dataclass_fields:
        raise AssertionError(f"proposal schema mismatch: {schema_fields ^ dataclass_fields}")
