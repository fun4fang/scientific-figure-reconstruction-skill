from __future__ import annotations

from dataclasses import replace

from unified_figure.adapters.contracts import BackendCapabilityProfile
from unified_figure.model.enums import (
    AssetSourceType,
    ComponentType,
    ConfidenceLevel,
    CostClass,
    DecisionStatus,
    FallbackPolicy,
    RepresentationType,
    ReviewStatus,
)
from unified_figure.model.figure import make_stable_id
from unified_figure.model.records import AssetReference, Component, RepresentationDecision

from .contracts import CostPolicy, EditingRequirements, RepresentationRouter, SemanticContext


ROUTER_VERSION = "representation-router-v0.1"


_CAPABILITY_FOR = {
    RepresentationType.NATIVE_TEXT: "supports_native_text",
    RepresentationType.NATIVE_SIMPLE_SHAPE: "supports_simple_shape",
    RepresentationType.NATIVE_RELATION: "supports_native_relation",
    RepresentationType.COMPOSITE_NATIVE: "supports_simple_shape",
    RepresentationType.EDITABLE_CHART: "supports_editable_chart",
    RepresentationType.VECTOR_ASSET: "supports_vector_asset",
    RepresentationType.EXACT_RASTER_ASSET: "supports_exact_raster",
    RepresentationType.RASTER_PRESERVE_COMPLEX: "supports_exact_raster",
    RepresentationType.GENERATED_COMPLEX_ASSET: "supports_generated_asset",
    RepresentationType.REVIEW_REQUIRED: "localized_review",
    RepresentationType.UNSUPPORTED: "none",
}


def _supported(
    representation: RepresentationType,
    profiles: tuple[BackendCapabilityProfile, ...],
) -> bool:
    return any(profile.supports(representation) for profile in profiles)


class RepresentationRoutingPolicyV0_1(RepresentationRouter):
    """Deterministic MVP policy driven by semantics, evidence, cost, and capabilities."""

    version = ROUTER_VERSION

    def decide(
        self,
        component: Component,
        semantic_context: SemanticContext,
        editing_requirements: EditingRequirements,
        backend_capabilities: tuple[BackendCapabilityProfile, ...],
        cost_policy: CostPolicy,
    ) -> RepresentationDecision:
        return self.decide_component(
            component,
            semantic_context,
            editing_requirements,
            backend_capabilities,
            cost_policy,
            asset=None,
            prior_decision=None,
        )

    def decide_component(
        self,
        component: Component,
        semantic_context: SemanticContext,
        editing_requirements: EditingRequirements,
        backend_capabilities: tuple[BackendCapabilityProfile, ...],
        cost_policy: CostPolicy,
        *,
        asset: AssetReference | None,
        prior_decision: RepresentationDecision | None,
    ) -> RepresentationDecision:
        if not backend_capabilities:
            raise ValueError("at least one BackendCapabilityProfile is required")

        complexity = prior_decision.complexity_estimate if prior_decision else self._complexity(component)
        candidates = self._candidates(component, asset, editing_requirements, prior_decision)
        selected = next(
            (
                item
                for item in candidates
                if item not in {RepresentationType.REVIEW_REQUIRED, RepresentationType.UNSUPPORTED}
                and _supported(item, backend_capabilities)
                and self._cost_within(
                    self._execution_cost(item, complexity), cost_policy.maximum_execution_cost
                )
            ),
            None,
        )
        rejected: list[str] = []
        for item in candidates:
            if item is RepresentationType.REVIEW_REQUIRED:
                if selected is not None:
                    rejected.append("REVIEW_REQUIRED:not required by current evidence")
                continue
            if item is RepresentationType.UNSUPPORTED:
                if selected is not None:
                    rejected.append("UNSUPPORTED:a supported representation is available")
                continue
            if item is not selected:
                item_cost = self._execution_cost(item, complexity)
                rejected.append(
                    f"{item.value}:"
                    + (
                        "capability unavailable"
                        if not _supported(item, backend_capabilities)
                        else f"execution cost {item_cost.value} exceeds policy"
                        if not self._cost_within(item_cost, cost_policy.maximum_execution_cost)
                        else "lower policy priority"
                    )
                )

        generated_without_backend = (
            asset is not None
            and asset.source_type is AssetSourceType.SOURCE_GENERATED
            and (
                not cost_policy.optional_backends_allowed
                or not _supported(RepresentationType.GENERATED_COMPLEX_ASSET, backend_capabilities)
            )
        )
        if selected is None:
            selected = RepresentationType.REVIEW_REQUIRED
            status = DecisionStatus.REVIEW_REQUIRED
        elif selected is RepresentationType.GENERATED_COMPLEX_ASSET and (
            component.review_status in {ReviewStatus.REQUIRED, ReviewStatus.PENDING}
            or generated_without_backend
        ):
            selected = RepresentationType.REVIEW_REQUIRED
            status = DecisionStatus.REVIEW_REQUIRED
        else:
            status = DecisionStatus.SELECTED

        execution_cost = self._execution_cost(selected, complexity)
        review_cost = CostClass.HIGH if selected is RepresentationType.REVIEW_REQUIRED else CostClass.LOW
        reason_parts = [
            f"{ROUTER_VERSION} selected {selected.value}",
            f"semantic_type={component.component_type.value}",
            f"editability={editing_requirements.minimum_editability}",
            f"fidelity_priority={editing_requirements.fidelity_priority}",
            f"scientific_risk={semantic_context.scientific_criticality}",
            f"complexity={complexity.value}",
            f"execution_cost={execution_cost.value}",
            f"review_cost={review_cost.value}",
            f"maximum_execution_cost={cost_policy.maximum_execution_cost.value}",
            f"maximum_review_cost={cost_policy.maximum_review_cost.value}",
        ]
        if asset is not None:
            reason_parts.append(f"asset_source={asset.source_type.value}")
        if rejected:
            reason_parts.append("alternatives_rejected=" + ",".join(rejected))
        if generated_without_backend:
            reason_parts.append("generated backend unavailable or disallowed")

        complete_candidates = tuple(dict.fromkeys((*candidates, selected)))
        return RepresentationDecision(
            decision_id=make_stable_id(
                semantic_context.figure_id,
                "representation-decision-v0-1",
                0,
                component.component_id,
            ),
            component_id=component.component_id,
            selected_representation=selected,
            candidate_representations=complete_candidates,
            decision_reason="; ".join(reason_parts),
            confidence=(
                ConfidenceLevel.HIGH
                if asset is not None or component.component_type in {ComponentType.TEXT, ComponentType.SIMPLE_SHAPE}
                else ConfidenceLevel.MEDIUM
            ),
            scientific_risk=semantic_context.scientific_criticality,
            editability_need=editing_requirements.minimum_editability,
            complexity_estimate=complexity,
            execution_cost_estimate=execution_cost,
            review_cost_estimate=review_cost,
            required_backend_capabilities=(_CAPABILITY_FOR[selected],),
            fallback_policy=(
                FallbackPolicy.REQUIRE_REVIEW
                if selected is RepresentationType.REVIEW_REQUIRED
                else FallbackPolicy.ALLOW_EXPLICIT
            ),
            evidence_refs=semantic_context.evidence_refs,
            status=status,
        )

    @staticmethod
    def _complexity(component: Component) -> CostClass:
        if component.component_type in {ComponentType.COMPLEX_VISUAL, ComponentType.CHART}:
            return CostClass.HIGH
        if component.component_type is ComponentType.UNKNOWN:
            return CostClass.MEDIUM
        return CostClass.LOW

    @staticmethod
    def _execution_cost(representation: RepresentationType, complexity: CostClass) -> CostClass:
        if representation in {RepresentationType.REVIEW_REQUIRED, RepresentationType.UNSUPPORTED}:
            return CostClass.LOW
        if representation in {RepresentationType.EXACT_RASTER_ASSET, RepresentationType.RASTER_PRESERVE_COMPLEX, RepresentationType.VECTOR_ASSET}:
            return CostClass.LOW
        return complexity

    @staticmethod
    def _cost_within(value: CostClass, maximum: CostClass) -> bool:
        rank = {CostClass.LOW: 0, CostClass.MEDIUM: 1, CostClass.HIGH: 2, CostClass.NOT_EXPOSED: 3}
        return rank[value] <= rank[maximum]

    @staticmethod
    def _candidates(
        component: Component,
        asset: AssetReference | None,
        editing: EditingRequirements,
        prior: RepresentationDecision | None,
    ) -> tuple[RepresentationType, ...]:
        if component.component_type is ComponentType.TEXT:
            return (RepresentationType.NATIVE_TEXT, RepresentationType.REVIEW_REQUIRED)
        if component.component_type in {ComponentType.SIMPLE_SHAPE, ComponentType.PANEL, ComponentType.RELATION_ANCHOR}:
            if (
                asset is not None
                and prior is not None
                and prior.complexity_estimate is CostClass.HIGH
                and editing.minimum_editability.upper() in {"LOW", "ASSET_LEVEL", "NONE"}
            ):
                preserved = (
                    RepresentationType.VECTOR_ASSET
                    if asset.source_type is AssetSourceType.SOURCE_VECTOR
                    else RepresentationType.RASTER_PRESERVE_COMPLEX
                    if asset.source_type is AssetSourceType.SOURCE_PRESERVED_RASTER
                    else RepresentationType.EXACT_RASTER_ASSET
                )
                return (preserved, RepresentationType.NATIVE_SIMPLE_SHAPE, RepresentationType.REVIEW_REQUIRED)
            return (RepresentationType.NATIVE_SIMPLE_SHAPE, RepresentationType.REVIEW_REQUIRED)
        if asset is not None:
            if asset.source_type is AssetSourceType.SOURCE_VECTOR:
                return (RepresentationType.VECTOR_ASSET, RepresentationType.REVIEW_REQUIRED)
            if asset.source_type is AssetSourceType.SOURCE_EXACT:
                return (RepresentationType.EXACT_RASTER_ASSET, RepresentationType.REVIEW_REQUIRED)
            if asset.source_type is AssetSourceType.SOURCE_PRESERVED_RASTER:
                return (RepresentationType.RASTER_PRESERVE_COMPLEX, RepresentationType.REVIEW_REQUIRED)
            if asset.source_type is AssetSourceType.SOURCE_GENERATED:
                return (RepresentationType.GENERATED_COMPLEX_ASSET, RepresentationType.REVIEW_REQUIRED)
        if component.component_type is ComponentType.CHART:
            return (RepresentationType.REVIEW_REQUIRED,)
        if component.component_type in {ComponentType.COMPLEX_VISUAL, ComponentType.IMAGE_ASSET, ComponentType.UNKNOWN}:
            return (RepresentationType.REVIEW_REQUIRED,)
        return (RepresentationType.UNSUPPORTED,)
