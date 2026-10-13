from __future__ import annotations

from unified_figure.adapters.contracts import BackendCapabilityProfile
from unified_figure.model.enums import (
    ComponentType,
    ConfidenceLevel,
    CostClass,
    DecisionStatus,
    FallbackPolicy,
    RepresentationType,
)
from unified_figure.model.figure import make_stable_id
from unified_figure.model.records import Component, RepresentationDecision

from .contracts import CostPolicy, EditingRequirements, RepresentationRouter, SemanticContext


class DeterministicSyntheticRouter(RepresentationRouter):
    """Small generic policy for synthetic contract tests, not a production router."""

    def decide(
        self,
        component: Component,
        semantic_context: SemanticContext,
        editing_requirements: EditingRequirements,
        backend_capabilities: tuple[BackendCapabilityProfile, ...],
        cost_policy: CostPolicy,
    ) -> RepresentationDecision:
        support = {representation for profile in backend_capabilities for representation in profile.supported_representations()}
        preferred = {
            ComponentType.TEXT: RepresentationType.NATIVE_TEXT,
            ComponentType.SIMPLE_SHAPE: RepresentationType.NATIVE_SIMPLE_SHAPE,
            ComponentType.RELATION_ANCHOR: RepresentationType.NATIVE_SIMPLE_SHAPE,
            ComponentType.COMPLEX_VISUAL: RepresentationType.RASTER_PRESERVE_COMPLEX,
            ComponentType.IMAGE_ASSET: RepresentationType.EXACT_RASTER_ASSET,
            ComponentType.PANEL: RepresentationType.NATIVE_SIMPLE_SHAPE,
            ComponentType.CHART: RepresentationType.REVIEW_REQUIRED,
            ComponentType.UNKNOWN: RepresentationType.REVIEW_REQUIRED,
        }[component.component_type]
        selected = preferred if preferred in support or preferred is RepresentationType.REVIEW_REQUIRED else RepresentationType.UNSUPPORTED
        status = (
            DecisionStatus.REVIEW_REQUIRED
            if selected is RepresentationType.REVIEW_REQUIRED
            else DecisionStatus.UNSUPPORTED
            if selected is RepresentationType.UNSUPPORTED
            else DecisionStatus.SELECTED
        )
        candidates = (preferred,) if selected is preferred else (preferred, selected)
        return RepresentationDecision(
            decision_id=make_stable_id(
                semantic_context.figure_id,
                "representation-decision",
                0,
                component.component_id,
            ),
            component_id=component.component_id,
            selected_representation=selected,
            candidate_representations=candidates,
            decision_reason="deterministic synthetic policy based on semantic type and declared capabilities",
            confidence=ConfidenceLevel.HIGH,
            scientific_risk=semantic_context.scientific_criticality,
            editability_need=editing_requirements.minimum_editability,
            complexity_estimate=CostClass.LOW,
            execution_cost_estimate=CostClass.LOW,
            review_cost_estimate=CostClass.LOW,
            required_backend_capabilities=(f"supports_{selected.value.lower()}",),
            fallback_policy=FallbackPolicy.FAIL_CLOSED,
            evidence_refs=semantic_context.evidence_refs,
            status=status,
        )
