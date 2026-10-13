from __future__ import annotations

from dataclasses import asdict, replace
from hashlib import sha256
import json

from unified_figure.adapters.contracts import BackendCapabilityProfile
from unified_figure.model.enums import (
    DecisionStatus,
    EvidenceSourceType,
    RepresentationType,
    ReviewDisposition,
    ReviewReason,
    ReviewStatus,
    RevisionStatus,
    Severity,
    TargetType,
    UncertaintyLevel,
)
from unified_figure.model.figure import CanonicalFigure, make_stable_id
from unified_figure.model.records import (
    EvidenceRecord,
    ExplicitFallbackRecord,
    ProvenanceReference,
    ReviewItem,
    UncertaintyRecord,
)

from .contracts import CostPolicy, EditingRequirements, SemanticContext
from .policy_v0_1 import RepresentationRoutingPolicyV0_1


def capability_profile_hash(profile: BackendCapabilityProfile) -> str:
    payload = json.dumps(asdict(profile), default=str, sort_keys=True, separators=(",", ":"))
    return "sha256:" + sha256(payload.encode("utf-8")).hexdigest()


def route_figure(
    figure: CanonicalFigure,
    profile: BackendCapabilityProfile,
    *,
    router: RepresentationRoutingPolicyV0_1 | None = None,
    editing_requirements: dict[str, EditingRequirements] | None = None,
    cost_policy: CostPolicy | None = None,
) -> CanonicalFigure:
    """Create a capability-bound candidate state without backend-name branching."""

    figure.validate()
    router = router or RepresentationRoutingPolicyV0_1()
    editing_requirements = editing_requirements or {}
    cost_policy = cost_policy or CostPolicy(
        maximum_execution_cost=profile.cost_class,
        maximum_review_cost=profile.cost_class,
        optional_backends_allowed=False,
    )
    capability_evidence_id = make_stable_id(
        figure.figure_id, "routing-capability-evidence", 0, capability_profile_hash(profile)
    )
    capability_evidence = EvidenceRecord(
        evidence_id=capability_evidence_id,
        source_type=EvidenceSourceType.ROUTING,
        source_hash=capability_profile_hash(profile),
        producer=router.version,
        operation="negotiate-backend-capabilities",
        input_refs=(figure.revision.revision_id,),
        output_refs=(),
        run_index=0,
        tool_runtime_info={
            "router_version": router.version,
            "capability_profile_version": "phase5b-v0.1",
            "model_calls": "0",
        },
        notes="Capability-driven; backend identity is evidence, not a routing branch.",
    )
    assets = {item.asset_id: item for item in figure.assets}
    decisions = {item.decision_id: item for item in figure.representation_decisions}
    routed_decisions = list(figure.representation_decisions)
    routed_components = []
    fallbacks = list(figure.fallbacks)
    reviews = list(figure.review_items)

    for sequence, component in enumerate(figure.components):
        prior = decisions[component.representation_decision_id]
        requirements = editing_requirements.get(
            component.component_id,
            EditingRequirements(("preserve_semantics",), prior.editability_need, "HIGH"),
        )
        context = SemanticContext(
            figure_id=figure.figure_id,
            group_ids=tuple(
                group.group_id for group in figure.groups if component.component_id in group.member_component_ids
            ),
            relation_ids=tuple(
                relation.relation_id
                for relation in figure.relations
                if component.component_id in {relation.source_component_id, relation.target_component_id}
            ),
            scientific_criticality=prior.scientific_risk,
            evidence_refs=(capability_evidence_id, *prior.evidence_refs),
        )
        asset = assets.get(component.asset_ref) if component.asset_ref else None
        decision = router.decide_component(
            component,
            context,
            requirements,
            (profile,),
            cost_policy,
            asset=asset,
            prior_decision=prior,
        )
        selected_asset_ref = component.asset_ref

        if decision.selected_representation is RepresentationType.REVIEW_REQUIRED:
            review_id = make_stable_id(figure.figure_id, "routing-review", sequence, component.component_id)
            if not any(item.review_id == review_id for item in reviews):
                reviews.append(
                    ReviewItem(
                        review_id=review_id,
                        target_type=TargetType.COMPONENT,
                        target_id=component.component_id,
                        reason_type=(
                            ReviewReason.SCIENTIFIC_GENERATION_RISK
                            if component.representation is RepresentationType.GENERATED_COMPLEX_ASSET
                            else ReviewReason.BACKEND_CAPABILITY_LIMIT
                        ),
                        severity=Severity.ERROR,
                        evidence_refs=(capability_evidence_id,),
                        candidate_actions=("choose a supported source-preserving representation", "perform localized review"),
                        candidate_representations=decision.candidate_representations,
                        status=ReviewStatus.PENDING,
                        disposition=ReviewDisposition.PENDING,
                        created_by=router.version,
                        created_at="run:phase5b:0000",
                    )
                )

        if decision.selected_representation is prior.selected_representation:
            decision = replace(decision, decision_id=prior.decision_id)
            routed_decisions = [decision if item.decision_id == prior.decision_id else item for item in routed_decisions]
        else:
            fallback_asset_id = figure.metadata.get(f"fallback_asset.{component.component_id}")
            if (
                decision.selected_representation is RepresentationType.REVIEW_REQUIRED
                and fallback_asset_id
                and fallback_asset_id in assets
            ):
                fallback_asset = assets[fallback_asset_id]
                if profile.supports(fallback_asset.representation_type):
                    decision = replace(
                        decision,
                        selected_representation=fallback_asset.representation_type,
                        candidate_representations=tuple(
                            dict.fromkeys((*decision.candidate_representations, fallback_asset.representation_type))
                        ),
                        decision_reason=decision.decision_reason
                        + f"; explicit fallback asset={fallback_asset.asset_id}",
                        required_backend_capabilities=("supports_exact_raster",),
                        status=DecisionStatus.FALLBACK_SELECTED,
                    )
                    selected_asset_ref = fallback_asset.asset_id
            fallback_decision_id = make_stable_id(
                figure.figure_id, "fallback-decision-v0-1", sequence, component.component_id
            )
            decision = replace(
                decision,
                decision_id=fallback_decision_id,
                status=(
                    DecisionStatus.REVIEW_REQUIRED
                    if decision.selected_representation is RepresentationType.REVIEW_REQUIRED
                    else DecisionStatus.FALLBACK_SELECTED
                ),
            )
            routed_decisions = [
                replace(item, status=DecisionStatus.SUPERSEDED)
                if item.decision_id == prior.decision_id
                else item
                for item in routed_decisions
            ]
            routed_decisions.append(decision)
            fallback_id = make_stable_id(figure.figure_id, "explicit-fallback", sequence, component.component_id)
            fallbacks.append(
                ExplicitFallbackRecord(
                    fallback_id=fallback_id,
                    component_id=component.component_id,
                    original_decision_id=prior.decision_id,
                    fallback_decision_id=decision.decision_id,
                    fallback_reason=decision.decision_reason,
                    fallback_target=decision.selected_representation,
                    backend_capability_evidence_refs=(capability_evidence_id,),
                    provenance=ProvenanceReference(
                        evidence_refs=(capability_evidence_id,),
                        producer=router.version,
                        operation="explicit-capability-fallback",
                    ),
                )
            )

        if decision.selected_representation is not RepresentationType.REVIEW_REQUIRED:
            reviews = [
                item
                for item in reviews
                if item.review_id
                != make_stable_id(figure.figure_id, "routing-review", sequence, component.component_id)
            ]

        review_required = decision.selected_representation is RepresentationType.REVIEW_REQUIRED
        routed_components.append(
            replace(
                component,
                representation=decision.selected_representation,
                representation_decision_id=decision.decision_id,
                asset_ref=selected_asset_ref,
                review_status=ReviewStatus.PENDING if review_required else component.review_status,
                uncertainty=(
                    UncertaintyRecord(
                        UncertaintyLevel.REVIEW_REQUIRED,
                        ("representation routing requires review",),
                        True,
                    )
                    if review_required
                    else component.uncertainty
                ),
                renderer_realizations=(),
            )
        )

    candidate_revision_id = make_stable_id(
        figure.figure_id, "revision-realization-candidate", 0, profile.backend_id
    )
    routed = replace(
        figure,
        components=tuple(routed_components),
        representation_decisions=tuple(routed_decisions),
        fallbacks=tuple(fallbacks),
        review_items=tuple(reviews),
        evidence=(*figure.evidence, capability_evidence),
        renderer_realizations=(),
        revision=replace(
            figure.revision,
            revision_id=candidate_revision_id,
            parent_revision_id=figure.revision.revision_id,
            canonical_state_hash="",
            change_manifest=(
                f"route with {router.version}",
                f"bind capability evidence {capability_evidence_id}",
            ),
            evidence_refs=tuple(dict.fromkeys((*figure.revision.evidence_refs, capability_evidence_id))),
            status=RevisionStatus.CANDIDATE,
        ),
    ).with_computed_revision_hash()
    routed.validate()
    return routed
