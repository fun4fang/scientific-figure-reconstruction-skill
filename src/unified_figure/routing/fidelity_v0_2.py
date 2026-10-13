from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import Enum

from unified_figure.model.enums import ComponentType, RepresentationType

from .policy_v0_1 import RepresentationRoutingPolicyV0_1
from .quality_profiles import (
    GenerationCandidacyLevel,
    LatencyPreference,
    ProfessionalBackendCandidacy,
    QualityProfilePolicy,
)


FIDELITY_AWARE_ROUTING_POLICY_VERSION = "fidelity-aware-routing-policy-v0.1"
ROUTER_VERSION = "representation-router-v0.2"


class EvidenceRating(str, Enum):
    UNKNOWN = "UNKNOWN"
    INADEQUATE = "INADEQUATE"
    ADEQUATE = "ADEQUATE"
    HIGH = "HIGH"
    SOURCE_EXACT = "SOURCE_EXACT"


class ComponentComplexity(str, Enum):
    SIMPLE = "SIMPLE"
    MODERATE = "MODERATE"
    HIGH = "HIGH"


class ScientificSensitivity(str, Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


class EditabilityValue(str, Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


class GeneratedCandidateValue(str, Enum):
    NONE = "NONE"
    UNCERTAIN = "UNCERTAIN"
    CLEAR = "CLEAR"


class ScientificCompatibility(str, Enum):
    NOT_EVALUATED = "NOT_EVALUATED"
    PASS = "PASS"
    FAIL = "FAIL"


class ExpectedProfessionalValue(str, Enum):
    NONE = "NONE"
    UNCERTAIN = "UNCERTAIN"
    CLEAR = "CLEAR"


@dataclass(frozen=True, slots=True)
class BackendAvailabilitySnapshot:
    pptx_native: bool = True
    svg_native: bool = True
    exact_source_preserve: bool = True
    image_generation: bool = False
    photoshop_refinement: bool = False

    def to_dict(self) -> dict[str, bool]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class FidelityRoutingInput:
    component_id: str
    component_type: ComponentType
    component_complexity: ComponentComplexity
    scientific_sensitivity: ScientificSensitivity
    source_available: bool
    source_asset_quality: EvidenceRating
    source_preservation_fidelity: EvidenceRating
    native_realization_fidelity: EvidenceRating
    editability_value: EditabilityValue
    generated_asset_candidate_value: GeneratedCandidateValue
    generated_scientific_compatibility: ScientificCompatibility
    reference_guidance_available: bool
    professional_refinement_expected_value: ExpectedProfessionalValue
    repairable_defects: tuple[str, ...]
    backend_availability: BackendAvailabilitySnapshot
    source_preservation_ready: bool = True
    unknown_fields: tuple[str, ...] = ()
    decision_critical_unknowns: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.component_id:
            raise ValueError("component_id must be non-empty")
        object.__setattr__(self, "repairable_defects", tuple(sorted(set(self.repairable_defects))))
        object.__setattr__(self, "unknown_fields", tuple(sorted(set(self.unknown_fields))))
        object.__setattr__(
            self,
            "decision_critical_unknowns",
            tuple(sorted(set(self.decision_critical_unknowns))),
        )
        if not set(self.decision_critical_unknowns).issubset(self.unknown_fields):
            raise ValueError("decision_critical_unknowns must be a subset of unknown_fields")


@dataclass(frozen=True, slots=True)
class FallbackStep:
    representation: RepresentationType
    backend_ids: tuple[str, ...]
    reason: str

    def to_dict(self) -> dict[str, object]:
        return {
            "representation": self.representation.value,
            "backend_ids": list(self.backend_ids),
            "reason": self.reason,
        }


@dataclass(frozen=True, slots=True)
class FidelityRouteDecision:
    component_id: str
    profile: str
    selected_representation: RepresentationType
    selected_backend_ids: tuple[str, ...]
    representation_candidates: tuple[RepresentationType, ...]
    backend_candidates: tuple[str, ...]
    fallback_graph: tuple[FallbackStep, ...]
    generation_requested: bool
    photoshop_requested: bool
    review_required: bool
    reasons: tuple[str, ...]
    scientific_safety_policy: str
    unknown_field_count: int
    decision_critical_unknown_count: int
    decision_critical_unknowns: tuple[str, ...]
    policy_version: str = FIDELITY_AWARE_ROUTING_POLICY_VERSION
    router_version: str = ROUTER_VERSION
    base_router_version: str = RepresentationRoutingPolicyV0_1.version

    def to_dict(self) -> dict[str, object]:
        return {
            "component_id": self.component_id,
            "profile": self.profile,
            "selected_representation": self.selected_representation.value,
            "selected_backend_ids": list(self.selected_backend_ids),
            "representation_candidates": [item.value for item in self.representation_candidates],
            "backend_candidates": list(self.backend_candidates),
            "fallback_graph": [item.to_dict() for item in self.fallback_graph],
            "generation_requested": self.generation_requested,
            "photoshop_requested": self.photoshop_requested,
            "review_required": self.review_required,
            "reasons": list(self.reasons),
            "scientific_safety_policy": self.scientific_safety_policy,
            "unknown_field_count": self.unknown_field_count,
            "decision_critical_unknown_count": self.decision_critical_unknown_count,
            "decision_critical_unknowns": list(self.decision_critical_unknowns),
            "policy_version": self.policy_version,
            "router_version": self.router_version,
            "base_router_version": self.base_router_version,
        }


_ADEQUATE = {EvidenceRating.ADEQUATE, EvidenceRating.HIGH, EvidenceRating.SOURCE_EXACT}
_NATIVE_TYPES = {
    ComponentType.TEXT: RepresentationType.NATIVE_TEXT,
    ComponentType.SIMPLE_SHAPE: RepresentationType.NATIVE_SIMPLE_SHAPE,
    ComponentType.RELATION_ANCHOR: RepresentationType.NATIVE_SIMPLE_SHAPE,
    ComponentType.PANEL: RepresentationType.NATIVE_SIMPLE_SHAPE,
}
_REPAIRABLE_DEFECTS = {
    "ALPHA_BACKGROUND",
    "EDGE_CLEANUP",
    "BOUNDED_COMPOSITION",
    "BOUNDED_GEOMETRY",
}


class FidelityAwareRoutingPolicyV0_2(RepresentationRoutingPolicyV0_1):
    """Interpretable fidelity-aware extension of representation-router-v0.1.

    The base router remains available for canonical routing. This layer plans a
    representation and backend realization without adding backend identity to
    canonical semantics.
    """

    version = ROUTER_VERSION

    def decide_fidelity(
        self,
        evidence: FidelityRoutingInput,
        profile: QualityProfilePolicy,
    ) -> FidelityRouteDecision:
        reasons: list[str] = [
            "scientific safety evaluated before fidelity, editability, cost, or enhancement",
            f"target_fidelity={profile.target_fidelity.value}",
            f"source_asset_quality={evidence.source_asset_quality.value}",
            f"latency_preference={profile.latency_preference.value}",
            f"optional_backend_call_budget={profile.call_budget.max_optional_backend_calls}",
            f"unknown_field_count={len(evidence.unknown_fields)}",
            f"decision_critical_unknown_count={len(evidence.decision_critical_unknowns)}",
        ]
        candidates: list[RepresentationType] = []
        backend_candidates: list[str] = []

        native_representation = _NATIVE_TYPES.get(evidence.component_type)
        native_available = evidence.backend_availability.pptx_native or evidence.backend_availability.svg_native
        source_adequate = (
            evidence.source_available
            and evidence.source_preservation_ready
            and evidence.backend_availability.exact_source_preserve
            and evidence.source_preservation_fidelity in _ADEQUATE
            and (
                evidence.source_asset_quality in _ADEQUATE
                or evidence.source_preservation_fidelity is EvidenceRating.SOURCE_EXACT
            )
        )
        native_adequate = (
            native_available
            and evidence.native_realization_fidelity in _ADEQUATE
            and evidence.editability_value in {EditabilityValue.MEDIUM, EditabilityValue.HIGH}
        )
        critical_unknown = bool(evidence.decision_critical_unknowns)
        conservative_source_due_unknown = (
            critical_unknown
            and evidence.source_available
            and evidence.source_preservation_ready
            and evidence.backend_availability.exact_source_preserve
            and evidence.component_type not in {ComponentType.TEXT, ComponentType.RELATION_ANCHOR}
        )

        if native_representation is not None:
            candidates.append(native_representation)
        if evidence.source_available and evidence.source_preservation_ready:
            candidates.append(RepresentationType.EXACT_RASTER_ASSET)
        if evidence.component_type not in {
            ComponentType.TEXT,
            ComponentType.SIMPLE_SHAPE,
            ComponentType.RELATION_ANCHOR,
            ComponentType.PANEL,
            ComponentType.CHART,
        }:
            candidates.append(RepresentationType.GENERATED_COMPLEX_ASSET)
        candidates.append(RepresentationType.REVIEW_REQUIRED)

        generation_allowed = self._generation_allowed(evidence, profile)
        photoshop_allowed = self._photoshop_allowed(evidence, profile)

        selected: RepresentationType
        backends: tuple[str, ...]
        if conservative_source_due_unknown:
            selected = RepresentationType.EXACT_RASTER_ASSET
            backends = ("EXACT_SOURCE_PRESERVE",)
            reasons.append(
                "decision-critical UNKNOWN prevents optimistic native decomposition; exact source is the safe route"
            )
        elif critical_unknown and not evidence.source_preservation_ready:
            selected = RepresentationType.REVIEW_REQUIRED
            backends = ()
            reasons.append(
                "decision-critical UNKNOWN has no authoritative source fallback; actionable review is required"
            )
        elif native_representation is not None and native_adequate:
            selected = native_representation
            backends = self._native_backends(evidence.backend_availability)
            reasons.append("native realization is fidelity-adequate and meaningfully editable")
        elif evidence.component_type is ComponentType.CHART and source_adequate:
            selected = RepresentationType.EXACT_RASTER_ASSET
            backends = ("EXACT_SOURCE_PRESERVE",)
            reasons.append("chart pixels are source-authoritative; no numerical values are inferred")
        elif photoshop_allowed and source_adequate:
            selected = RepresentationType.EXACT_RASTER_ASSET
            backends = ("PHOTOSHOP_REFINEMENT",)
            reasons.append("specific evidence-supported source refinement defect has clear value")
        elif generation_allowed:
            selected = RepresentationType.GENERATED_COMPLEX_ASSET
            backends = ("IMAGE_GENERATION",)
            reasons.append("complex content has inadequate native/source fidelity and generation has evidence-supported value")
        elif source_adequate:
            selected = RepresentationType.EXACT_RASTER_ASSET
            backends = ("EXACT_SOURCE_PRESERVE",)
            reasons.append("authoritative source preservation is safer and more faithful than native decomposition")
        elif (
            evidence.source_available
            and evidence.source_preservation_ready
            and evidence.backend_availability.exact_source_preserve
        ):
            selected = RepresentationType.EXACT_RASTER_ASSET
            backends = ("EXACT_SOURCE_PRESERVE",)
            reasons.append("source preservation is the safest available recognizability floor despite known limitations")
        elif native_adequate and native_representation is not None:
            selected = native_representation
            backends = self._native_backends(evidence.backend_availability)
            reasons.append("source route unavailable; fidelity-adequate native realization selected")
        else:
            selected = RepresentationType.REVIEW_REQUIRED
            backends = ()
            reasons.append("no available route proves the minimum recognizable fidelity floor")

        if evidence.generated_scientific_compatibility is ScientificCompatibility.FAIL:
            reasons.append("scientifically incompatible generation is rejected before professional refinement")
        if profile.profile.value == "FAST" and selected is RepresentationType.EXACT_RASTER_ASSET:
            reasons.append(
                "FAST preserves source pixels as its recognizability floor instead of emitting a low-fidelity semantic skeleton"
            )
        if not evidence.backend_availability.image_generation:
            reasons.append("ImageGen unavailable; base route remains available")
        if not evidence.backend_availability.photoshop_refinement:
            reasons.append("Photoshop unavailable; base route remains available")

        fallback_graph = self._fallback_graph(evidence, selected, backends, native_representation, native_adequate)
        for backend in (
            *self._native_backends(evidence.backend_availability),
            *(('EXACT_SOURCE_PRESERVE',) if evidence.backend_availability.exact_source_preserve else ()),
            *(('IMAGE_GENERATION',) if evidence.backend_availability.image_generation else ()),
            *(('PHOTOSHOP_REFINEMENT',) if evidence.backend_availability.photoshop_refinement else ()),
        ):
            if backend not in backend_candidates:
                backend_candidates.append(backend)

        return FidelityRouteDecision(
            component_id=evidence.component_id,
            profile=profile.profile.value,
            selected_representation=selected,
            selected_backend_ids=backends,
            representation_candidates=tuple(dict.fromkeys(candidates)),
            backend_candidates=tuple(backend_candidates),
            fallback_graph=fallback_graph,
            generation_requested=backends == ("IMAGE_GENERATION",),
            photoshop_requested=backends == ("PHOTOSHOP_REFINEMENT",),
            review_required=selected is RepresentationType.REVIEW_REQUIRED,
            reasons=tuple(reasons),
            scientific_safety_policy=profile.scientific_safety_policy,
            unknown_field_count=len(evidence.unknown_fields),
            decision_critical_unknown_count=len(evidence.decision_critical_unknowns),
            decision_critical_unknowns=evidence.decision_critical_unknowns,
        )

    @staticmethod
    def _native_backends(availability: BackendAvailabilitySnapshot) -> tuple[str, ...]:
        values: list[str] = []
        if availability.pptx_native:
            values.append("PPTX_NATIVE")
        if availability.svg_native:
            values.append("SVG_NATIVE")
        return tuple(values)

    @staticmethod
    def _generation_allowed(
        evidence: FidelityRoutingInput,
        profile: QualityProfilePolicy,
    ) -> bool:
        if profile.generation_candidacy is GenerationCandidacyLevel.DISABLED:
            return False
        if profile.latency_preference is LatencyPreference.LOW:
            return False
        if profile.call_budget.max_imagegen_calls < 1:
            return False
        if not evidence.backend_availability.image_generation or not evidence.reference_guidance_available:
            return False
        if evidence.component_complexity is not ComponentComplexity.HIGH:
            return False
        if evidence.native_realization_fidelity is not EvidenceRating.INADEQUATE:
            return False
        if evidence.source_preservation_fidelity in {EvidenceRating.HIGH, EvidenceRating.SOURCE_EXACT}:
            return False
        if evidence.generated_scientific_compatibility is ScientificCompatibility.FAIL:
            return False
        if evidence.scientific_sensitivity is ScientificSensitivity.HIGH and (
            evidence.generated_scientific_compatibility is not ScientificCompatibility.PASS
        ):
            return False
        if evidence.generated_asset_candidate_value is GeneratedCandidateValue.CLEAR:
            return True
        return (
            evidence.generated_asset_candidate_value is GeneratedCandidateValue.UNCERTAIN
            and profile.generation_candidacy is GenerationCandidacyLevel.PERMISSIVE_WITH_REVIEW
        )

    @staticmethod
    def _photoshop_allowed(
        evidence: FidelityRoutingInput,
        profile: QualityProfilePolicy,
    ) -> bool:
        if profile.professional_backend_candidacy is ProfessionalBackendCandidacy.DISABLED:
            return False
        if profile.latency_preference is LatencyPreference.LOW:
            return False
        if profile.call_budget.max_photoshop_calls < 1:
            return False
        if not evidence.backend_availability.photoshop_refinement:
            return False
        if evidence.generated_scientific_compatibility is ScientificCompatibility.FAIL:
            return False
        if evidence.professional_refinement_expected_value is not ExpectedProfessionalValue.CLEAR:
            return False
        return bool(set(evidence.repairable_defects) & _REPAIRABLE_DEFECTS)

    def _fallback_graph(
        self,
        evidence: FidelityRoutingInput,
        selected: RepresentationType,
        backends: tuple[str, ...],
        native_representation: RepresentationType | None,
        native_adequate: bool,
    ) -> tuple[FallbackStep, ...]:
        steps: list[FallbackStep] = []

        def add(representation: RepresentationType, backend_ids: tuple[str, ...], reason: str) -> None:
            key = (representation, backend_ids)
            if key not in {(item.representation, item.backend_ids) for item in steps}:
                steps.append(FallbackStep(representation, backend_ids, reason))

        if backends == ("PHOTOSHOP_REFINEMENT",):
            add(
                RepresentationType.EXACT_RASTER_ASSET,
                ("EXACT_SOURCE_PRESERVE",),
                "professional refinement unavailable, invalid, or not materially better",
            )
        if selected is RepresentationType.GENERATED_COMPLEX_ASSET:
            if (
                evidence.source_available
                and evidence.source_preservation_ready
                and evidence.backend_availability.exact_source_preserve
            ):
                add(
                    RepresentationType.EXACT_RASTER_ASSET,
                    ("EXACT_SOURCE_PRESERVE",),
                    "generation unavailable, rejected, or scientifically incompatible",
                )
        if selected not in {RepresentationType.EXACT_RASTER_ASSET, RepresentationType.GENERATED_COMPLEX_ASSET}:
            if (
                evidence.source_available
                and evidence.source_preservation_ready
                and evidence.backend_availability.exact_source_preserve
            ):
                add(
                    RepresentationType.EXACT_RASTER_ASSET,
                    ("EXACT_SOURCE_PRESERVE",),
                    "native realization fails the minimum visual-fidelity floor",
                )
        if (
            native_representation is not None
            and native_adequate
            and selected is not native_representation
        ):
            add(
                native_representation,
                self._native_backends(evidence.backend_availability),
                "source/optional asset path is unavailable while native fidelity remains adequate",
            )
        add(
            RepresentationType.REVIEW_REQUIRED,
            (),
            "no remaining realization can satisfy scientific safety and minimum fidelity",
        )
        return tuple(steps)
