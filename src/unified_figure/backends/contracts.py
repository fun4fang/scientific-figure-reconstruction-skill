from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import Enum
from hashlib import sha256
import json

from unified_figure.model.enums import CostClass, RepresentationType


class BackendAvailability(str, Enum):
    AVAILABLE = "AVAILABLE"
    UNAVAILABLE = "UNAVAILABLE"
    PLANNED = "PLANNED"


class BackendInputType(str, Enum):
    CANONICAL_COMPONENT = "CANONICAL_COMPONENT"
    SOURCE_CROP = "SOURCE_CROP"
    REFERENCE_IMAGE = "REFERENCE_IMAGE"
    SEMANTIC_CONSTRAINTS = "SEMANTIC_CONSTRAINTS"


class BackendOutputType(str, Enum):
    PPTX_OBJECT = "PPTX_OBJECT"
    SVG_OBJECT = "SVG_OBJECT"
    RASTER_ASSET = "RASTER_ASSET"
    VECTOR_ASSET = "VECTOR_ASSET"


class EditabilityLevel(str, Enum):
    NONE = "NONE"
    ASSET_LEVEL = "ASSET_LEVEL"
    OBJECT_LEVEL = "OBJECT_LEVEL"
    FULLY_NATIVE = "FULLY_NATIVE"


class FidelityPotential(str, Enum):
    SOURCE_EXACT = "SOURCE_EXACT"
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LIMITED_FOR_COMPLEX_CONTENT = "LIMITED_FOR_COMPLEX_CONTENT"


class ScientificContentRisk(str, Enum):
    SOURCE_AUTHORITATIVE = "SOURCE_AUTHORITATIVE"
    TRANSFORMATIVE_BUT_CONSTRAINED = "TRANSFORMATIVE_BUT_CONSTRAINED"
    GENERATIVE_NON_AUTHORITATIVE = "GENERATIVE_NON_AUTHORITATIVE"


class LatencyClass(str, Enum):
    LOCAL_LOW = "LOCAL_LOW"
    EXTERNAL_MEDIUM = "EXTERNAL_MEDIUM"
    EXTERNAL_HIGH = "EXTERNAL_HIGH"
    NOT_EXPOSED = "NOT_EXPOSED"


class DeterminismLevel(str, Enum):
    DETERMINISTIC = "DETERMINISTIC"
    PROVIDER_VARIABLE = "PROVIDER_VARIABLE"
    NOT_EXPOSED = "NOT_EXPOSED"


def _primitive(value: object) -> object:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, tuple):
        return [_primitive(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _primitive(item) for key, item in sorted(value.items())}
    return value


@dataclass(frozen=True, slots=True)
class BackendCapability:
    backend_id: str
    backend_version: str
    availability: BackendAvailability
    supported_representation_types: tuple[RepresentationType, ...]
    supported_input_types: tuple[BackendInputType, ...]
    supported_output_types: tuple[BackendOutputType, ...]
    can_generate: bool
    can_edit: bool
    can_extract: bool
    can_vectorize: bool
    can_composite: bool
    supports_alpha: bool
    supports_reference_guidance: bool
    supports_local_region_operation: bool
    supports_batch_assets: bool
    editability_level: EditabilityLevel
    fidelity_potential: FidelityPotential
    scientific_content_risk: ScientificContentRisk
    latency_class: LatencyClass
    cost_class: CostClass
    requires_model_call: bool
    requires_external_software: bool
    determinism_level: DeterminismLevel
    fallback_supported: bool

    def __post_init__(self) -> None:
        if not self.backend_id or not self.backend_version:
            raise ValueError("backend identity must be non-empty")
        object.__setattr__(
            self,
            "supported_representation_types",
            tuple(sorted(set(self.supported_representation_types), key=lambda item: item.value)),
        )
        object.__setattr__(
            self,
            "supported_input_types",
            tuple(sorted(set(self.supported_input_types), key=lambda item: item.value)),
        )
        object.__setattr__(
            self,
            "supported_output_types",
            tuple(sorted(set(self.supported_output_types), key=lambda item: item.value)),
        )

    def supports(self, representation: RepresentationType) -> bool:
        return representation in self.supported_representation_types

    def to_dict(self) -> dict[str, object]:
        return _primitive(asdict(self))  # type: ignore[return-value]

    def identity(self) -> str:
        payload = json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":")).encode("utf-8")
        return "sha256:" + sha256(payload).hexdigest()


@dataclass(frozen=True, slots=True)
class BackendCandidateEvidence:
    backend_id: str
    representation: RepresentationType
    availability: BackendAvailability
    accepted_for_selection: bool
    rejection_reasons: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class BackendDecision:
    decision_id: str
    component_id: str
    requested_representation: RepresentationType
    realized_representation: RepresentationType
    candidate_backends: tuple[BackendCandidateEvidence, ...]
    chosen_backend: str
    fidelity_rationale: str
    editability_rationale: str
    cost_rationale: str
    scientific_risk_rationale: str
    fallback_backend: str
    fallback_representation: RepresentationType
    decision_policy_version: str

    def __post_init__(self) -> None:
        ids = {item.backend_id for item in self.candidate_backends}
        if self.chosen_backend not in ids:
            raise ValueError("chosen backend must be a candidate")
        if self.fallback_backend not in ids:
            raise ValueError("fallback backend must be a candidate")

    def to_dict(self) -> dict[str, object]:
        return _primitive(asdict(self))  # type: ignore[return-value]
