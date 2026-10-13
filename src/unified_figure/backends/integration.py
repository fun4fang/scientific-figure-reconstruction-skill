from __future__ import annotations

from dataclasses import dataclass, replace
from hashlib import sha256
from pathlib import Path

from PIL import Image

from unified_figure.model.enums import (
    AlphaState,
    AssetSourceType,
    AssetType,
    CheckStatus,
    ConfidenceLevel,
    CostClass,
    DecisionStatus,
    EvidenceSourceType,
    FallbackPolicy,
    FidelityStatus,
    Replaceability,
    RepresentationType,
    ReviewDisposition,
    ReviewReason,
    ReviewStatus,
    RevisionStatus,
    Severity,
    TargetType,
    ComponentType,
)
from unified_figure.model.figure import CanonicalFigure, make_stable_id
from unified_figure.model.records import (
    AssetReference,
    EvidenceRecord,
    ExplicitFallbackRecord,
    ProvenanceReference,
    RepresentationDecision,
    ReviewItem,
    ScientificReviewChecks,
)

from .image_generation import GeneratedAssetAcceptance
from .professional_refinement import ProfessionalRefinementAcceptance


GENERATED_ASSET_INTEGRATION_POLICY_VERSION = "generated-asset-integration-v0.1"
PROFESSIONAL_REFINEMENT_INTEGRATION_POLICY_VERSION = (
    "professional-refinement-integration-v0.1"
)


@dataclass(frozen=True, slots=True)
class GeneratedAssetIntegrationResult:
    figure: CanonicalFigure
    applied: bool
    selected_backend: str
    fallback_backend: str
    fallback_asset_id: str
    fallback_asset_hash: str
    reason: str


@dataclass(frozen=True, slots=True)
class ProfessionalRefinementIntegrationResult:
    figure: CanonicalFigure
    applied: bool
    selected_backend: str
    fallback_backend: str
    fallback_asset_id: str
    fallback_asset_hash: str
    reason: str


def _overlay_z_order(figure: CanonicalFigure, component_id: str) -> int:
    component = next(item for item in figure.components if item.component_id == component_id)
    center_x = component.geometry.bbox.x + component.geometry.bbox.width / 2
    center_y = component.geometry.bbox.y + component.geometry.bbox.height / 2
    values = [component.style.z_order]
    for panel in figure.components:
        bbox = panel.geometry.bbox
        if (
            panel.component_type is ComponentType.PANEL
            and panel.representation
            in {RepresentationType.EXACT_RASTER_ASSET, RepresentationType.RASTER_PRESERVE_COMPLEX}
            and bbox.x <= center_x <= bbox.x + bbox.width
            and bbox.y <= center_y <= bbox.y + bbox.height
        ):
            values.append(panel.style.z_order + 1)
    return max(values)


def integrate_generated_asset(
    figure: CanonicalFigure,
    *,
    component_id: str,
    generated_asset_path: str | Path,
    repository_root: str | Path,
    source_crop_hash: str,
    prompt_hash: str,
    backend_identity: str,
    run_identity: str,
    acceptance: GeneratedAssetAcceptance,
) -> GeneratedAssetIntegrationResult:
    component = next((item for item in figure.components if item.component_id == component_id), None)
    if component is None:
        raise ValueError(f"unknown component: {component_id}")
    if not component.asset_ref:
        raise ValueError("generated candidate requires a source-preserved fallback asset")
    fallback_asset = next(item for item in figure.assets if item.asset_id == component.asset_ref)
    fallback_hash = fallback_asset.content_hash or "NOT_EXPOSED"
    if not acceptance.accepted:
        return GeneratedAssetIntegrationResult(
            figure=figure,
            applied=False,
            selected_backend="EXACT_SOURCE_PRESERVE",
            fallback_backend="EXACT_SOURCE_PRESERVE",
            fallback_asset_id=fallback_asset.asset_id,
            fallback_asset_hash=fallback_hash,
            reason="generated asset rejected by acceptance policy; canonical state unchanged",
        )

    root = Path(repository_root).resolve()
    generated_path = Path(generated_asset_path).resolve()
    if root not in generated_path.parents:
        raise ValueError("generated asset must be stored under repository root")
    generated_hash = "sha256:" + sha256(generated_path.read_bytes()).hexdigest()
    with Image.open(generated_path) as image:
        alpha = AlphaState.HAS_ALPHA if "A" in image.getbands() else AlphaState.OPAQUE

    prior_decision = next(
        item for item in figure.representation_decisions if item.decision_id == component.representation_decision_id
    )
    evidence_id = make_stable_id(
        figure.figure_id, "phase6d-generated-asset-evidence", 0, f"{component_id}:{generated_hash}"
    )
    decision_id = make_stable_id(
        figure.figure_id, "phase6d-generated-asset-decision", 0, f"{component_id}:{generated_hash}"
    )
    asset_id = make_stable_id(
        figure.figure_id, "phase6d-generated-asset", 0, f"{component_id}:{generated_hash}"
    )
    fallback_id = make_stable_id(
        figure.figure_id,
        "phase6d-generated-asset-fallback",
        0,
        f"{component_id}:{fallback_asset.asset_id}",
    )
    review_id = make_stable_id(
        figure.figure_id, "phase6d-generated-asset-review", 0, f"{component_id}:{generated_hash}"
    )
    evidence = EvidenceRecord(
        evidence_id=evidence_id,
        source_type=EvidenceSourceType.REVIEW,
        source_hash=generated_hash,
        producer=GENERATED_ASSET_INTEGRATION_POLICY_VERSION,
        operation="accept-component-generated-asset-with-explicit-source-fallback",
        input_refs=(component_id, source_crop_hash, prompt_hash, prior_decision.decision_id),
        output_refs=(asset_id, decision_id, fallback_id, review_id),
        run_index=0,
        tool_runtime_info={
            "backend_identity": backend_identity,
            "run_identity": run_identity,
            "whole_figure_generation": "0",
        },
        notes="Generated output is non-authoritative; exact source fallback remains available.",
    )
    generated_asset = AssetReference(
        asset_id=asset_id,
        asset_type=AssetType.GENERATED,
        source_type=AssetSourceType.SOURCE_GENERATED,
        file_ref=generated_path.relative_to(root).as_posix(),
        content_hash=generated_hash,
        source_region=component.geometry.bbox,
        alpha_state=alpha,
        provenance=ProvenanceReference(
            (evidence_id,), GENERATED_ASSET_INTEGRATION_POLICY_VERSION, "bind-reviewed-generated-asset"
        ),
        scientific_fidelity_status=FidelityStatus.GENERATED_REVIEW_REQUIRED,
        replaceability=Replaceability.REVIEW_REQUIRED,
        representation_type=RepresentationType.GENERATED_COMPLEX_ASSET,
        backend_requirement="supports_generated_asset",
        scientific_review=ScientificReviewChecks(
            scientific_content_review=CheckStatus.PASS,
            component_count_check=CheckStatus.PASS,
            topology_check=CheckStatus.PASS,
            symbol_check=CheckStatus.PASS,
            orientation_check=CheckStatus.PASS,
            color_encoding_check=CheckStatus.PASS,
            review_status=ReviewStatus.RESOLVED,
        ),
    )
    generated_decision = RepresentationDecision(
        decision_id=decision_id,
        component_id=component_id,
        selected_representation=RepresentationType.GENERATED_COMPLEX_ASSET,
        candidate_representations=(
            RepresentationType.GENERATED_COMPLEX_ASSET,
            prior_decision.selected_representation,
        ),
        decision_reason=(
            "bounded reviewed component generation provides material asset-level visual value; "
            "exact source preservation remains the explicit fallback"
        ),
        confidence=ConfidenceLevel.MEDIUM,
        scientific_risk="GENERATIVE_NON_AUTHORITATIVE_REVIEWED",
        editability_need="ASSET_LEVEL",
        complexity_estimate=CostClass.HIGH,
        execution_cost_estimate=CostClass.HIGH,
        review_cost_estimate=CostClass.HIGH,
        required_backend_capabilities=("supports_generated_asset", "supports_alpha"),
        fallback_policy=FallbackPolicy.ALLOW_EXPLICIT,
        evidence_refs=(evidence_id,),
        status=DecisionStatus.SELECTED,
    )
    fallback = ExplicitFallbackRecord(
        fallback_id=fallback_id,
        component_id=component_id,
        original_decision_id=decision_id,
        fallback_decision_id=prior_decision.decision_id,
        fallback_reason="optional generated asset unavailable or rejected",
        fallback_target=prior_decision.selected_representation,
        backend_capability_evidence_refs=(evidence_id,),
        provenance=ProvenanceReference(
            (evidence_id,), GENERATED_ASSET_INTEGRATION_POLICY_VERSION, "preserve-base-fallback"
        ),
    )
    review = ReviewItem(
        review_id=review_id,
        target_type=TargetType.COMPONENT,
        target_id=component_id,
        reason_type=ReviewReason.SCIENTIFIC_GENERATION_RISK,
        severity=Severity.WARNING,
        evidence_refs=(evidence_id,),
        candidate_actions=("use accepted generated asset", "restore exact source fallback"),
        candidate_representations=(
            RepresentationType.GENERATED_COMPLEX_ASSET,
            prior_decision.selected_representation,
        ),
        status=ReviewStatus.RESOLVED,
        disposition=ReviewDisposition.ACCEPT,
        created_by=GENERATED_ASSET_INTEGRATION_POLICY_VERSION,
        created_at=f"run:{run_identity}",
    )
    updated_components = tuple(
        replace(
            item,
            style=replace(
                item.style,
                z_order=_overlay_z_order(figure, component_id),
            ),
            representation=RepresentationType.GENERATED_COMPLEX_ASSET,
            representation_decision_id=decision_id,
            asset_ref=asset_id,
            review_status=ReviewStatus.RESOLVED,
            renderer_realizations=(),
            provenance=ProvenanceReference(
                tuple(dict.fromkeys((*item.provenance.evidence_refs, evidence_id))),
                GENERATED_ASSET_INTEGRATION_POLICY_VERSION,
                "select-reviewed-generated-component-realization",
            ),
        )
        if item.component_id == component_id
        else item
        for item in figure.components
    )
    updated_decisions = tuple(
        replace(item, status=DecisionStatus.SUPERSEDED)
        if item.decision_id == prior_decision.decision_id
        else item
        for item in figure.representation_decisions
    )
    result = replace(
        figure,
        components=updated_components,
        assets=(*figure.assets, generated_asset),
        representation_decisions=(*updated_decisions, generated_decision),
        fallbacks=(*figure.fallbacks, fallback),
        review_items=(*figure.review_items, review),
        evidence=(*figure.evidence, evidence),
        renderer_realizations=tuple(
            item for item in figure.renderer_realizations if item.canonical_object_id != component_id
        ),
        metadata={
            **figure.metadata,
            "phase6d.generated_asset_policy": GENERATED_ASSET_INTEGRATION_POLICY_VERSION,
            "phase6d.generated_asset_component": component_id,
            "phase6d.generated_asset_fallback": fallback_asset.asset_id,
        },
        revision=replace(
            figure.revision,
            revision_id=make_stable_id(
                figure.figure_id,
                "phase6d-generated-asset-revision",
                0,
                f"{component_id}:{generated_hash}",
            ),
            parent_revision_id=figure.revision.revision_id,
            canonical_state_hash="",
            change_manifest=(
                *figure.revision.change_manifest,
                "bind reviewed component-level generated asset with exact source fallback",
            ),
            evidence_refs=tuple(dict.fromkeys((*figure.revision.evidence_refs, evidence_id))),
            status=RevisionStatus.CANDIDATE,
        ),
    ).with_computed_revision_hash()
    result.validate()
    return GeneratedAssetIntegrationResult(
        figure=result,
        applied=True,
        selected_backend="IMAGE_GENERATION",
        fallback_backend="EXACT_SOURCE_PRESERVE",
        fallback_asset_id=fallback_asset.asset_id,
        fallback_asset_hash=fallback_hash,
        reason="generated asset passed all acceptance checks and exact source fallback was retained",
    )


def integrate_source_refinement(
    figure: CanonicalFigure,
    *,
    component_id: str,
    refined_asset_path: str | Path,
    repository_root: str | Path,
    source_asset_hash: str,
    operation_plan_hash: str,
    backend_identity: str,
    run_identity: str,
    acceptance: ProfessionalRefinementAcceptance,
) -> ProfessionalRefinementIntegrationResult:
    """Bind an accepted source-authoritative refinement without changing semantics.

    The component representation remains the pre-existing raster-preservation
    representation. Photoshop is recorded only as execution evidence, while the
    original source asset and decision remain an explicit fallback.
    """

    component = next((item for item in figure.components if item.component_id == component_id), None)
    if component is None:
        raise ValueError(f"unknown component: {component_id}")
    if not component.asset_ref:
        raise ValueError("source refinement requires an existing source-preserved asset")
    fallback_asset = next(item for item in figure.assets if item.asset_id == component.asset_ref)
    fallback_hash = fallback_asset.content_hash or "NOT_EXPOSED"
    if not acceptance.accepted:
        return ProfessionalRefinementIntegrationResult(
            figure=figure,
            applied=False,
            selected_backend=acceptance.selected_backend,
            fallback_backend=acceptance.fallback_backend,
            fallback_asset_id=fallback_asset.asset_id,
            fallback_asset_hash=fallback_hash,
            reason="professional refinement rejected; canonical state unchanged",
        )

    root = Path(repository_root).resolve()
    refined_path = Path(refined_asset_path).resolve()
    if root not in refined_path.parents:
        raise ValueError("refined asset must be stored under repository root")
    refined_hash = "sha256:" + sha256(refined_path.read_bytes()).hexdigest()
    with Image.open(refined_path) as image:
        alpha = AlphaState.HAS_ALPHA if "A" in image.getbands() else AlphaState.OPAQUE

    prior_decision = next(
        item for item in figure.representation_decisions if item.decision_id == component.representation_decision_id
    )
    evidence_id = make_stable_id(
        figure.figure_id, "phase6d2-professional-refinement-evidence", 0, f"{component_id}:{refined_hash}"
    )
    decision_id = make_stable_id(
        figure.figure_id, "phase6d2-professional-refinement-decision", 0, f"{component_id}:{refined_hash}"
    )
    asset_id = make_stable_id(
        figure.figure_id, "phase6d2-professional-refinement-asset", 0, f"{component_id}:{refined_hash}"
    )
    fallback_id = make_stable_id(
        figure.figure_id,
        "phase6d2-professional-refinement-fallback",
        0,
        f"{component_id}:{fallback_asset.asset_id}",
    )
    evidence = EvidenceRecord(
        evidence_id=evidence_id,
        source_type=EvidenceSourceType.REVIEW,
        source_hash=refined_hash,
        producer=PROFESSIONAL_REFINEMENT_INTEGRATION_POLICY_VERSION,
        operation="accept-source-authoritative-local-refinement-with-explicit-source-fallback",
        input_refs=(
            component_id,
            source_asset_hash,
            operation_plan_hash,
            prior_decision.decision_id,
        ),
        output_refs=(asset_id, decision_id, fallback_id),
        run_index=0,
        tool_runtime_info={
            "backend_identity": backend_identity,
            "run_identity": run_identity,
            "whole_figure_edit": "0",
            "source_authoritative": "1",
        },
        notes=(
            "Bounded source-only cleanup; no scientific structure was generated. "
            "The original source-preserved asset remains the explicit fallback."
        ),
    )
    refined_asset = AssetReference(
        asset_id=asset_id,
        asset_type=AssetType.RASTER,
        source_type=AssetSourceType.SOURCE_PRESERVED_RASTER,
        file_ref=refined_path.relative_to(root).as_posix(),
        content_hash=refined_hash,
        source_region=component.geometry.bbox,
        alpha_state=alpha,
        provenance=ProvenanceReference(
            (evidence_id,),
            PROFESSIONAL_REFINEMENT_INTEGRATION_POLICY_VERSION,
            "bind-source-authoritative-refined-asset",
        ),
        scientific_fidelity_status=FidelityStatus.PRESERVED,
        replaceability=Replaceability.REPLACEABLE,
        representation_type=prior_decision.selected_representation,
        backend_requirement=fallback_asset.backend_requirement,
        scientific_review=ScientificReviewChecks(
            scientific_content_review=CheckStatus.PASS,
            component_count_check=CheckStatus.PASS,
            topology_check=CheckStatus.PASS,
            symbol_check=CheckStatus.PASS,
            orientation_check=CheckStatus.PASS,
            color_encoding_check=CheckStatus.PASS,
            review_status=ReviewStatus.RESOLVED,
        ),
    )
    refined_decision = RepresentationDecision(
        decision_id=decision_id,
        component_id=component_id,
        selected_representation=prior_decision.selected_representation,
        candidate_representations=prior_decision.candidate_representations,
        decision_reason=(
            "source-authoritative bounded refinement materially improves alpha/composition; "
            "representation semantics remain unchanged and source fallback is explicit"
        ),
        confidence=ConfidenceLevel.HIGH,
        scientific_risk="TRANSFORMATIVE_BUT_CONSTRAINED_SOURCE_AUTHORITATIVE",
        editability_need="ASSET_LEVEL",
        complexity_estimate=prior_decision.complexity_estimate,
        execution_cost_estimate=CostClass.HIGH,
        review_cost_estimate=CostClass.MEDIUM,
        required_backend_capabilities=(
            "supports_alpha",
            "supports_local_region_operation",
        ),
        fallback_policy=FallbackPolicy.ALLOW_EXPLICIT,
        evidence_refs=(evidence_id,),
        status=DecisionStatus.SELECTED,
    )
    fallback = ExplicitFallbackRecord(
        fallback_id=fallback_id,
        component_id=component_id,
        original_decision_id=decision_id,
        fallback_decision_id=prior_decision.decision_id,
        fallback_reason="professional refinement unavailable, invalid, or not accepted",
        fallback_target=prior_decision.selected_representation,
        backend_capability_evidence_refs=(evidence_id,),
        provenance=ProvenanceReference(
            (evidence_id,),
            PROFESSIONAL_REFINEMENT_INTEGRATION_POLICY_VERSION,
            "preserve-source-refinement-fallback",
        ),
    )
    updated_components = tuple(
        replace(
            item,
            style=replace(
                item.style,
                z_order=_overlay_z_order(figure, component_id),
            ),
            representation_decision_id=decision_id,
            asset_ref=asset_id,
            review_status=ReviewStatus.RESOLVED,
            renderer_realizations=(),
            provenance=ProvenanceReference(
                tuple(dict.fromkeys((*item.provenance.evidence_refs, evidence_id))),
                PROFESSIONAL_REFINEMENT_INTEGRATION_POLICY_VERSION,
                "select-reviewed-source-refinement",
            ),
        )
        if item.component_id == component_id
        else item
        for item in figure.components
    )
    updated_decisions = tuple(
        replace(item, status=DecisionStatus.SUPERSEDED)
        if item.decision_id == prior_decision.decision_id
        else item
        for item in figure.representation_decisions
    )
    result = replace(
        figure,
        components=updated_components,
        assets=(*figure.assets, refined_asset),
        representation_decisions=(*updated_decisions, refined_decision),
        fallbacks=(*figure.fallbacks, fallback),
        evidence=(*figure.evidence, evidence),
        renderer_realizations=tuple(
            item for item in figure.renderer_realizations if item.canonical_object_id != component_id
        ),
        metadata={
            **figure.metadata,
            "phase6d2.professional_refinement_policy": (
                PROFESSIONAL_REFINEMENT_INTEGRATION_POLICY_VERSION
            ),
            "phase6d2.professional_refinement_component": component_id,
            "phase6d2.professional_refinement_fallback": fallback_asset.asset_id,
        },
        revision=replace(
            figure.revision,
            revision_id=make_stable_id(
                figure.figure_id,
                "phase6d2-professional-refinement-revision",
                0,
                f"{component_id}:{refined_hash}",
            ),
            parent_revision_id=figure.revision.revision_id,
            canonical_state_hash="",
            change_manifest=(
                *figure.revision.change_manifest,
                "bind source-authoritative local refinement with exact source fallback",
            ),
            evidence_refs=tuple(dict.fromkeys((*figure.revision.evidence_refs, evidence_id))),
            status=RevisionStatus.CANDIDATE,
        ),
    ).with_computed_revision_hash()
    result.validate()
    return ProfessionalRefinementIntegrationResult(
        figure=result,
        applied=True,
        selected_backend="PHOTOSHOP_REFINEMENT",
        fallback_backend=acceptance.fallback_backend,
        fallback_asset_id=fallback_asset.asset_id,
        fallback_asset_hash=fallback_hash,
        reason=(
            "source-authoritative refinement passed acceptance checks; original source asset "
            "and representation remain available as explicit fallback"
        ),
    )
