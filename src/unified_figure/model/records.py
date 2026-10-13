from __future__ import annotations

from dataclasses import dataclass, field
from typing import TypeAlias

from .enums import (
    AlphaState,
    AssetSourceType,
    AssetType,
    AttachmentIntent,
    CheckStatus,
    ConfidenceLevel,
    CostClass,
    CoverageDimension,
    CoverageStatus,
    DecisionStatus,
    EvidenceSourceType,
    FailureReason,
    FailureState,
    FallbackPolicy,
    FidelityStatus,
    RealizationStatus,
    ReconstructionStatus,
    RelationDirection,
    RelationType,
    Replaceability,
    RepresentationType,
    ReviewDisposition,
    ReviewReason,
    ReviewStatus,
    RevisionStatus,
    RouteType,
    RuntimeVerification,
    Severity,
    TargetType,
    UncertaintyLevel,
    ComponentType,
)
from .geometry import BBox, Geometry, ModelValidationError, Point, require_nonempty


@dataclass(frozen=True, slots=True)
class FontStyle:
    family: str = ""
    weight: str = "normal"
    italic: bool = False


@dataclass(frozen=True, slots=True)
class Style:
    fill: str | None = None
    stroke: str | None = None
    stroke_width: float = 0.0
    opacity: float = 1.0
    font: FontStyle | None = None
    font_size: float | None = None
    text_alignment: str | None = None
    z_order: int = 0
    arrowhead_start: str | None = None
    arrowhead_end: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "stroke_width", float(self.stroke_width))
        object.__setattr__(self, "opacity", float(self.opacity))
        if self.font_size is not None:
            object.__setattr__(self, "font_size", float(self.font_size))

    def validate(self) -> None:
        if self.stroke_width < 0:
            raise ModelValidationError("stroke_width must be nonnegative")
        if not 0 <= self.opacity <= 1:
            raise ModelValidationError("opacity must be within [0, 1]")
        if self.font_size is not None and self.font_size < 0:
            raise ModelValidationError("font_size must be nonnegative")


@dataclass(frozen=True, slots=True)
class ProvenanceReference:
    evidence_refs: tuple[str, ...] = ()
    producer: str = ""
    operation: str = ""

    def validate(self) -> None:
        require_nonempty(self.producer, "provenance producer")
        require_nonempty(self.operation, "provenance operation")


@dataclass(frozen=True, slots=True)
class UncertaintyRecord:
    level: UncertaintyLevel
    reasons: tuple[str, ...] = ()
    review_required: bool = False


@dataclass(frozen=True, slots=True)
class Component:
    component_id: str
    component_type: ComponentType
    semantic_role: str
    geometry: Geometry
    style: Style
    representation: RepresentationType
    representation_decision_id: str
    parent_component_id: str | None = None
    asset_ref: str | None = None
    uncertainty: UncertaintyRecord = field(
        default_factory=lambda: UncertaintyRecord(UncertaintyLevel.NONE)
    )
    review_status: ReviewStatus = ReviewStatus.NOT_REQUIRED
    coverage_status: CoverageStatus = CoverageStatus.UNKNOWN
    renderer_realizations: tuple[str, ...] = ()
    provenance: ProvenanceReference = field(
        default_factory=lambda: ProvenanceReference((), "canonical-author", "declare")
    )

    def validate(self) -> None:
        require_nonempty(self.component_id, "component_id")
        require_nonempty(self.semantic_role, "semantic_role")
        require_nonempty(self.representation_decision_id, "representation_decision_id")
        self.geometry.validate()
        self.style.validate()
        self.provenance.validate()


@dataclass(frozen=True, slots=True)
class SemanticGroup:
    group_id: str
    parent_group_id: str | None
    member_component_ids: tuple[str, ...]
    semantic_role: str
    confidence: ConfidenceLevel
    provenance: ProvenanceReference
    review_status: ReviewStatus

    def __post_init__(self) -> None:
        object.__setattr__(self, "member_component_ids", tuple(sorted(self.member_component_ids)))

    def validate(self) -> None:
        require_nonempty(self.group_id, "group_id")
        require_nonempty(self.semantic_role, "group semantic_role")
        if len(set(self.member_component_ids)) != len(self.member_component_ids):
            raise ModelValidationError(f"duplicate membership in group {self.group_id}")
        self.provenance.validate()


@dataclass(frozen=True, slots=True)
class Relation:
    relation_id: str
    source_component_id: str
    target_component_id: str
    direction: RelationDirection
    relation_type: RelationType
    route_type: RouteType
    attachment_intent: AttachmentIntent
    visual_style: Style
    confidence: ConfidenceLevel
    representation: RepresentationType
    route_points: tuple[Point, ...] = ()
    renderer_realizations: tuple[str, ...] = ()
    runtime_verification: RuntimeVerification = RuntimeVerification.NOT_RUN
    review_status: ReviewStatus = ReviewStatus.NOT_REQUIRED
    evidence_refs: tuple[str, ...] = ()

    def validate(self) -> None:
        require_nonempty(self.relation_id, "relation_id")
        require_nonempty(self.source_component_id, "source_component_id")
        require_nonempty(self.target_component_id, "target_component_id")
        self.visual_style.validate()


@dataclass(frozen=True, slots=True)
class ScientificReviewChecks:
    scientific_content_review: CheckStatus
    component_count_check: CheckStatus
    topology_check: CheckStatus
    symbol_check: CheckStatus
    orientation_check: CheckStatus
    color_encoding_check: CheckStatus
    review_status: ReviewStatus

    def has_deviation(self) -> bool:
        return CheckStatus.FAIL in {
            self.scientific_content_review,
            self.component_count_check,
            self.topology_check,
            self.symbol_check,
            self.orientation_check,
            self.color_encoding_check,
        }


@dataclass(frozen=True, slots=True)
class AssetReference:
    asset_id: str
    asset_type: AssetType
    source_type: AssetSourceType
    file_ref: str | None
    content_hash: str | None
    source_region: BBox | None
    alpha_state: AlphaState
    provenance: ProvenanceReference
    scientific_fidelity_status: FidelityStatus
    replaceability: Replaceability
    representation_type: RepresentationType
    backend_requirement: str | None
    scientific_review: ScientificReviewChecks | None = None

    def validate(self) -> None:
        require_nonempty(self.asset_id, "asset_id")
        if self.file_ref is not None:
            normalized = self.file_ref.replace("\\", "/")
            if normalized.startswith("/") or ":/" in normalized:
                raise ModelValidationError("asset file_ref must not be an absolute machine path")
        if self.source_region is not None:
            self.source_region.validate()
        self.provenance.validate()


@dataclass(frozen=True, slots=True)
class RepresentationDecision:
    decision_id: str
    component_id: str
    selected_representation: RepresentationType
    candidate_representations: tuple[RepresentationType, ...]
    decision_reason: str
    confidence: ConfidenceLevel
    scientific_risk: str
    editability_need: str
    complexity_estimate: CostClass
    execution_cost_estimate: CostClass
    review_cost_estimate: CostClass
    required_backend_capabilities: tuple[str, ...]
    fallback_policy: FallbackPolicy
    evidence_refs: tuple[str, ...]
    status: DecisionStatus

    def validate(self) -> None:
        require_nonempty(self.decision_id, "decision_id")
        require_nonempty(self.component_id, "decision component_id")
        require_nonempty(self.decision_reason, "decision_reason")
        if self.selected_representation not in self.candidate_representations:
            raise ModelValidationError(
                f"decision {self.decision_id} selected representation is not a candidate"
            )


@dataclass(frozen=True, slots=True)
class ExplicitFallbackRecord:
    fallback_id: str
    component_id: str
    original_decision_id: str
    fallback_decision_id: str
    fallback_reason: str
    fallback_target: RepresentationType
    backend_capability_evidence_refs: tuple[str, ...]
    provenance: ProvenanceReference

    def validate(self) -> None:
        require_nonempty(self.fallback_id, "fallback_id")
        require_nonempty(self.fallback_reason, "fallback_reason")
        if self.original_decision_id == self.fallback_decision_id:
            raise ModelValidationError("fallback must create a new decision")
        if not self.backend_capability_evidence_refs:
            raise ModelValidationError("fallback requires backend capability evidence")
        self.provenance.validate()


@dataclass(frozen=True, slots=True)
class CoverageRecord:
    coverage_id: str
    dimension: CoverageDimension
    status: CoverageStatus
    mandatory: bool
    target_ids: tuple[str, ...]
    evidence_refs: tuple[str, ...]
    review_status: ReviewStatus
    notes: str = ""

    def validate(self) -> None:
        require_nonempty(self.coverage_id, "coverage_id")


@dataclass(frozen=True, slots=True)
class ReviewItem:
    review_id: str
    target_type: TargetType
    target_id: str
    reason_type: ReviewReason
    severity: Severity
    evidence_refs: tuple[str, ...]
    candidate_actions: tuple[str, ...]
    candidate_representations: tuple[RepresentationType, ...]
    status: ReviewStatus
    disposition: ReviewDisposition
    created_by: str
    created_at: str

    def validate(self) -> None:
        require_nonempty(self.review_id, "review_id")
        require_nonempty(self.target_id, "review target_id")
        require_nonempty(self.created_by, "review created_by")
        require_nonempty(self.created_at, "review created_at")


@dataclass(frozen=True, slots=True)
class FailClosedRecord:
    failure_id: str
    reason: FailureReason
    target_id: str
    message: str
    recoverable: bool
    reviewable: bool
    fallback_eligible: bool
    state: FailureState
    evidence_refs: tuple[str, ...] = ()

    def validate(self) -> None:
        require_nonempty(self.failure_id, "failure_id")
        require_nonempty(self.target_id, "failure target_id")
        require_nonempty(self.message, "failure message")


@dataclass(frozen=True, slots=True)
class RendererRealization:
    realization_id: str
    canonical_object_id: str
    contributing_canonical_object_ids: tuple[str, ...]
    backend: str
    backend_object_id: str
    representation_used: RepresentationType
    artifact_ref: str | None
    status: RealizationStatus
    runtime_verification: RuntimeVerification
    warnings: tuple[str, ...] = ()

    def validate(self) -> None:
        require_nonempty(self.realization_id, "realization_id")
        require_nonempty(self.canonical_object_id, "canonical_object_id")
        require_nonempty(self.backend, "backend")
        require_nonempty(self.backend_object_id, "backend_object_id")


@dataclass(frozen=True, slots=True)
class EvidenceRecord:
    evidence_id: str
    source_type: EvidenceSourceType
    source_hash: str
    producer: str
    operation: str
    input_refs: tuple[str, ...]
    output_refs: tuple[str, ...]
    run_index: int
    tool_runtime_info: dict[str, str]
    notes: str = ""

    def validate(self) -> None:
        require_nonempty(self.evidence_id, "evidence_id")
        require_nonempty(self.source_hash, "evidence source_hash")
        require_nonempty(self.producer, "evidence producer")
        require_nonempty(self.operation, "evidence operation")
        if self.run_index < 0:
            raise ModelValidationError("run_index must be nonnegative")


@dataclass(frozen=True, slots=True)
class RevisionRecord:
    revision_id: str
    parent_revision_id: str | None
    canonical_state_hash: str
    change_manifest: tuple[str, ...]
    evidence_refs: tuple[str, ...]
    status: RevisionStatus

    def validate(self) -> None:
        require_nonempty(self.revision_id, "revision_id")


CostValue: TypeAlias = int | float | str


@dataclass(frozen=True, slots=True)
class CostMetrics:
    analysis_calls: CostValue = "NOT_EXPOSED"
    vision_calls: CostValue = "NOT_EXPOSED"
    input_tokens: CostValue = "NOT_EXPOSED"
    output_tokens: CostValue = "NOT_EXPOSED"
    imagegen_calls: CostValue = "NOT_EXPOSED"
    renderer_calls: CostValue = "NOT_EXPOSED"
    external_backend_calls: CostValue = "NOT_EXPOSED"
    full_image_reviews: CostValue = "NOT_EXPOSED"
    roi_reviews: CostValue = "NOT_EXPOSED"
    manual_corrections: CostValue = "NOT_EXPOSED"
    wall_time_ms: CostValue = "NOT_EXPOSED"

    def validate(self) -> None:
        for name in self.__dataclass_fields__:
            value = getattr(self, name)
            if isinstance(value, str):
                if value != "NOT_EXPOSED":
                    raise ModelValidationError(f"{name} string value must be NOT_EXPOSED")
            elif isinstance(value, bool) or value < 0:
                raise ModelValidationError(f"{name} must be nonnegative or NOT_EXPOSED")


@dataclass(frozen=True, slots=True)
class CostTelemetry:
    estimated_cost: CostMetrics
    observed_cost: CostMetrics
    stage_wall_time_ms: dict[str, CostValue] = field(default_factory=dict)

    def validate(self) -> None:
        self.estimated_cost.validate()
        self.observed_cost.validate()
        for stage, value in self.stage_wall_time_ms.items():
            require_nonempty(stage, "cost stage")
            if isinstance(value, str):
                if value != "NOT_EXPOSED":
                    raise ModelValidationError("stage cost string must be NOT_EXPOSED")
            elif isinstance(value, bool) or value < 0:
                raise ModelValidationError("stage cost must be nonnegative or NOT_EXPOSED")
