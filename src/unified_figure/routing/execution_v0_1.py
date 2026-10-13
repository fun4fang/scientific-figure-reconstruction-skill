from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from hashlib import sha256
import json
from pathlib import Path

from unified_figure.backends import BackendDecision, BackendCapabilityRegistry, select_backend
from unified_figure.model.enums import (
    ConfidenceLevel,
    CostClass,
    DecisionStatus,
    EvidenceSourceType,
    FallbackPolicy,
    RepresentationType,
    ReviewDisposition,
    ReviewReason,
    ReviewStatus,
    RevisionStatus,
    Severity,
    TargetType,
)
from unified_figure.model.figure import CanonicalFigure, make_stable_id
from unified_figure.model.records import EvidenceRecord, RepresentationDecision, ReviewItem
from unified_figure.model.records import ExplicitFallbackRecord, ProvenanceReference

from .evidence_v0_1 import BuiltRoutingEvidence, RoutingEvidenceBuilder
from .fidelity_v0_2 import FidelityAwareRoutingPolicyV0_2, FidelityRouteDecision
from .quality_profiles import QualityProfilePolicy


FIDELITY_ROUTING_EXECUTION_BRIDGE_VERSION = "fidelity-routing-execution-bridge-v0.1"


@dataclass(frozen=True, slots=True)
class FidelityRoutingExecutionResult:
    root_state: CanonicalFigure
    routed_state: CanonicalFigure
    profile: str
    evidence: tuple[BuiltRoutingEvidence, ...]
    route_decisions: tuple[FidelityRouteDecision, ...]
    backend_decisions: tuple[BackendDecision, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "bridge_version": FIDELITY_ROUTING_EXECUTION_BRIDGE_VERSION,
            "profile": self.profile,
            "root_state_hash": self.root_state.state_hash(),
            "routed_state_hash": self.routed_state.state_hash(),
            "evidence": [item.to_dict() for item in self.evidence],
            "route_decisions": [item.to_dict() for item in self.route_decisions],
            "backend_decisions": [item.to_dict() for item in self.backend_decisions],
        }


def execute_fidelity_routing(
    figure: CanonicalFigure,
    *,
    profile: QualityProfilePolicy,
    backend_registry: BackendCapabilityRegistry,
    repository_root: str | Path,
    reference_path: str | Path | None = None,
    router: FidelityAwareRoutingPolicyV0_2 | None = None,
) -> FidelityRoutingExecutionResult:
    """Apply profile-aware representation decisions without changing semantics."""

    figure.validate()
    builder = RoutingEvidenceBuilder(
        repository_root=repository_root,
        backend_registry=backend_registry,
        reference_path=reference_path,
    )
    built = builder.build_all(figure)
    policy = router or FidelityAwareRoutingPolicyV0_2()
    route_decisions = tuple(policy.decide_fidelity(item.routing_input, profile) for item in built)
    route_by_component = {item.component_id: item for item in route_decisions}
    evidence_by_component = {item.routing_input.component_id: item for item in built}
    prior_by_id = {item.decision_id: item for item in figure.representation_decisions}
    decisions = list(figure.representation_decisions)
    fallbacks = list(figure.fallbacks)
    reviews = list(figure.review_items)
    components = []
    backend_decisions: list[BackendDecision] = []

    for sequence, component in enumerate(figure.components):
        route = route_by_component[component.component_id]
        prior = prior_by_id[component.representation_decision_id]
        complexity = {
            "SIMPLE": CostClass.LOW,
            "MODERATE": CostClass.MEDIUM,
            "HIGH": CostClass.HIGH,
        }[evidence_by_component[component.component_id].routing_input.component_complexity.value]
        selected = route.selected_representation
        asset_ref = component.asset_ref
        source_fallback_id = builder.source_fallback_asset_id(figure, component)
        if selected in {RepresentationType.EXACT_RASTER_ASSET, RepresentationType.RASTER_PRESERVE_COMPLEX}:
            asset_ref = source_fallback_id or asset_ref
            source_asset = next((item for item in figure.assets if item.asset_id == asset_ref), None)
            if source_asset is not None:
                selected = source_asset.representation_type
        elif selected is RepresentationType.GENERATED_COMPLEX_ASSET:
            current_asset = next((item for item in figure.assets if item.asset_id == component.asset_ref), None)
            if current_asset is None or current_asset.representation_type is not RepresentationType.GENERATED_COMPLEX_ASSET:
                # Planning may request generation, but realization remains on
                # the explicit source fallback until an accepted generated
                # asset is integrated.
                asset_ref = source_fallback_id or asset_ref
                source_asset = next((item for item in figure.assets if item.asset_id == asset_ref), None)
                selected = (
                    source_asset.representation_type
                    if source_asset is not None
                    else RepresentationType.REVIEW_REQUIRED
                )
        candidates = tuple(dict.fromkeys((*route.representation_candidates, selected)))
        changed = selected is not prior.selected_representation
        decision_id = (
            make_stable_id(
                figure.figure_id,
                "fidelity-representation-decision",
                sequence,
                f"{component.component_id}:{profile.profile.value}",
            )
            if changed
            else prior.decision_id
        )
        if changed:
            decisions = [
                replace(item, status=DecisionStatus.SUPERSEDED)
                if item.decision_id == prior.decision_id else item
                for item in decisions
            ]
        required = tuple(
            dict.fromkeys(
                "supports_" + backend.lower()
                for backend in route.selected_backend_ids
            )
        ) or ("localized_review",)
        canonical_decision = RepresentationDecision(
                decision_id=decision_id,
                component_id=component.component_id,
                selected_representation=selected,
                candidate_representations=candidates,
                decision_reason="; ".join(
                    (*route.reasons,)
                    if selected is route.selected_representation
                    else (*route.reasons, "optional realization pending/rejected; explicit source fallback realized")
                ),
                confidence=ConfidenceLevel.HIGH if not route.review_required else ConfidenceLevel.LOW,
                scientific_risk=evidence_by_component[component.component_id].routing_input.scientific_sensitivity.value,
                editability_need=evidence_by_component[component.component_id].routing_input.editability_value.value,
                complexity_estimate=complexity,
                execution_cost_estimate=(CostClass.HIGH if route.generation_requested or route.photoshop_requested else CostClass.LOW),
                review_cost_estimate=CostClass.HIGH if route.review_required else CostClass.LOW,
                required_backend_capabilities=required,
                fallback_policy=FallbackPolicy.REQUIRE_REVIEW if route.review_required else FallbackPolicy.ALLOW_EXPLICIT,
                evidence_refs=prior.evidence_refs,
                status=(
                    DecisionStatus.REVIEW_REQUIRED
                    if route.review_required
                    else DecisionStatus.FALLBACK_SELECTED
                    if changed
                    else DecisionStatus.SELECTED
                ),
            )
        if changed:
            decisions.append(canonical_decision)
            fallbacks.append(
                ExplicitFallbackRecord(
                    fallback_id=make_stable_id(
                        figure.figure_id,
                        "fidelity-routing-transition",
                        sequence,
                        f"{component.component_id}:{profile.profile.value}",
                    ),
                    component_id=component.component_id,
                    original_decision_id=prior.decision_id,
                    fallback_decision_id=decision_id,
                    fallback_reason="profile-aware automatic routing selected a different safe realization",
                    fallback_target=selected,
                    backend_capability_evidence_refs=prior.evidence_refs,
                    provenance=ProvenanceReference(
                        prior.evidence_refs,
                        FIDELITY_ROUTING_EXECUTION_BRIDGE_VERSION,
                        "explicit-profile-routing-transition",
                    ),
                )
            )
        else:
            decisions = [
                canonical_decision if item.decision_id == prior.decision_id else item
                for item in decisions
            ]
        if route.review_required:
            review_id = make_stable_id(
                figure.figure_id,
                "fidelity-routing-unknown-review",
                sequence,
                f"{component.component_id}:{profile.profile.value}",
            )
            if not any(item.review_id == review_id for item in reviews):
                reviews.append(
                    ReviewItem(
                        review_id=review_id,
                        target_type=TargetType.COMPONENT,
                        target_id=component.component_id,
                        reason_type=ReviewReason.REPRESENTATION_AMBIGUITY,
                        severity=Severity.ERROR,
                        evidence_refs=prior.evidence_refs,
                        candidate_actions=(
                            "bind an authoritative source-preserving realization",
                            "perform localized representation review",
                        ),
                        candidate_representations=route.representation_candidates,
                        status=ReviewStatus.PENDING,
                        disposition=ReviewDisposition.PENDING,
                        created_by=FIDELITY_ROUTING_EXECUTION_BRIDGE_VERSION,
                        created_at="run:fidelity-routing:deterministic",
                    )
                )
        components.append(
            replace(
                component,
                representation=selected,
                representation_decision_id=decision_id,
                asset_ref=asset_ref,
                review_status=(
                    ReviewStatus.REQUIRED if route.review_required else component.review_status
                ),
                renderer_realizations=(),
            )
        )
        backend_decisions.extend(
            _backend_decisions(
                figure.figure_id, component.component_id, route, backend_registry,
                source_fallback_available=bool(source_fallback_id),
            )
        )

    evidence_payload = {
        "bridge_version": FIDELITY_ROUTING_EXECUTION_BRIDGE_VERSION,
        "profile": profile.to_dict(),
        "evidence_hashes": [item.evidence_hash for item in built],
        "route_decisions": [item.to_dict() for item in route_decisions],
        "backend_decisions": [item.to_dict() for item in backend_decisions],
    }
    encoded = json.dumps(evidence_payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    evidence_id = make_stable_id(
        figure.figure_id, "fidelity-routing-execution-evidence", 0,
        "sha256:" + sha256(encoded).hexdigest(),
    )
    evidence_record = EvidenceRecord(
        evidence_id=evidence_id,
        source_type=EvidenceSourceType.ROUTING,
        source_hash="sha256:" + sha256(encoded).hexdigest(),
        producer=FIDELITY_ROUTING_EXECUTION_BRIDGE_VERSION,
        operation="build-profile-independent-evidence-and-apply-profile-routing",
        input_refs=(figure.revision.revision_id,),
        output_refs=tuple(item.decision_id for item in decisions if item.decision_id not in prior_by_id),
        run_index=0,
        tool_runtime_info={
            "router_version": policy.version,
            "profile": profile.profile.value,
            "model_calls": "0",
            "manual_routing_fields": "0",
        },
        notes="Backend identity remains outside canonical semantics; unknown evidence fails to a safe base route or review.",
    )
    obsolete_runtime_evidence = {
        item.evidence_id
        for item in figure.evidence
        if item.source_type is EvidenceSourceType.RENDERER
        or item.operation == "coverage-review-realization-qa"
    }
    routed = replace(
        figure,
        components=tuple(components),
        relations=tuple(replace(item, renderer_realizations=()) for item in figure.relations),
        representation_decisions=tuple(decisions),
        fallbacks=tuple(fallbacks),
        review_items=tuple(reviews),
        renderer_realizations=(),
        evidence=(
            *(item for item in figure.evidence if item.evidence_id not in obsolete_runtime_evidence),
            evidence_record,
        ),
        metadata={
            **figure.metadata,
            "fidelity.routing_bridge": FIDELITY_ROUTING_EXECUTION_BRIDGE_VERSION,
            "fidelity.routing_profile": profile.profile.value,
        },
        revision=replace(
            figure.revision,
            revision_id=make_stable_id(
                figure.figure_id, "fidelity-routed-revision", 0, profile.profile.value
            ),
            parent_revision_id=figure.revision.revision_id,
            canonical_state_hash="",
            change_manifest=(
                *figure.revision.change_manifest,
                f"route automatically with {policy.version} profile={profile.profile.value}",
            ),
            evidence_refs=tuple(
                dict.fromkeys(
                    (
                        *(
                            item for item in figure.revision.evidence_refs
                            if item not in obsolete_runtime_evidence
                        ),
                        evidence_id,
                    )
                )
            ),
            status=RevisionStatus.CANDIDATE,
        ),
    ).with_computed_revision_hash()
    routed.validate()
    if reference_path is not None and profile.editability_preference.value != 'SECONDARY':
        from unified_figure.fidelity.selective_native import apply_selective_native
        output=Path(repository_root)/'.runtime/selective_native_v0_1'/routed.state_hash().split(':')[-1]
        routed=apply_selective_native(routed,reference_path,repository_root,output)
        original_ids={c.component_id for c in figure.components}
        extra=tuple(e for e in builder.build_all(routed) if e.routing_input.component_id not in original_ids)
        extra_routes=tuple(policy.decide_fidelity(e.routing_input,profile) for e in extra)
        if any(r.selected_representation is not RepresentationType.NATIVE_TEXT for r in extra_routes):
            raise ValueError('Verified label was not accepted by production representation policy')
        built=(*built,*extra)
        route_decisions=(*route_decisions,*extra_routes)
        for route in extra_routes:
            backend_decisions.extend(_backend_decisions(figure.figure_id,route.component_id,route,backend_registry,
                                     source_fallback_available=True))
    return FidelityRoutingExecutionResult(
        root_state=figure,
        routed_state=routed,
        profile=profile.profile.value,
        evidence=built,
        route_decisions=route_decisions,
        backend_decisions=tuple(backend_decisions),
    )


def _backend_decisions(
    figure_id: str,
    component_id: str,
    route: FidelityRouteDecision,
    registry: BackendCapabilityRegistry,
    *,
    source_fallback_available: bool,
) -> tuple[BackendDecision, ...]:
    if not route.selected_backend_ids:
        return ()
    fallback_backend = "EXACT_SOURCE_PRESERVE" if source_fallback_available else route.selected_backend_ids[0]
    fallback_representation = (
        RepresentationType.EXACT_RASTER_ASSET if source_fallback_available else route.selected_representation
    )
    if set(route.selected_backend_ids) == {"PPTX_NATIVE", "SVG_NATIVE"}:
        return tuple(
            select_backend(
                figure_id=figure_id,
                component_id=component_id,
                representation=route.selected_representation,
                registry=registry,
                preferred_backend_ids=(backend,),
                fallback_backend_id=backend,
                fallback_representation=route.selected_representation,
            )
            for backend in route.selected_backend_ids
        )
    return (
        select_backend(
            figure_id=figure_id,
            component_id=component_id,
            representation=route.selected_representation,
            registry=registry,
            preferred_backend_ids=route.selected_backend_ids,
            fallback_backend_id=fallback_backend,
            fallback_representation=fallback_representation,
        ),
    )
