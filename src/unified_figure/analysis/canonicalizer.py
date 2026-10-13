from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from hashlib import sha256
import json
from pathlib import Path
import re
from typing import Iterable

from unified_figure.model.enums import (
    AlphaState,
    AssetSourceType,
    AssetType,
    AttachmentIntent,
    ComponentType,
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
    Severity,
    TargetType,
    UncertaintyLevel,
)
from unified_figure.model.figure import CanonicalFigure, SCHEMA_VERSION, make_stable_id
from unified_figure.model.geometry import (
    AuthoritativeInputGeometry,
    BBox,
    CoordinateSpace,
    Geometry,
    Point,
)
from unified_figure.model.records import (
    AssetReference,
    Component,
    CostMetrics,
    CostTelemetry,
    CoverageRecord,
    EvidenceRecord,
    FailClosedRecord,
    FontStyle,
    ProvenanceReference,
    Relation,
    RepresentationDecision,
    ReviewItem,
    RevisionRecord,
    SemanticGroup,
    Style,
    UncertaintyRecord,
)

from .contracts import AuthoritativeSourceReference
from unified_figure.model.text_authoring import encode_text_authoring
from .proposal import (
    AnalysisProposal,
    AssetProposal,
    ComponentProposal,
    ProposalBBox,
    RelationProposal,
    StyleHint,
)
from .provider import AnalysisRunReceipt
from .validator import (
    AnalysisProposalValidator,
    ProposalValidationIssue,
    ProposalValidationResult,
)


CANONICALIZATION_POLICY_VERSION = "proposal-canonicalization-v0.1"


@dataclass(frozen=True, slots=True)
class CanonicalizationPolicy:
    policy_version: str = CANONICALIZATION_POLICY_VERSION
    coordinate_policy: str = "reject-negative-and-out-of-bounds;epsilon-clamp-only"
    identity_policy: str = "figure-scope+semantic-kind+stable-sequence;exclude-text-and-raw-bbox"
    grouping_policy: str = "group-members-authoritative;ambiguous-membership-review"
    chart_policy: str = "pixel-values-nonauthoritative;preserve-source-region"
    representation_policy: str = "prepare-candidates-only;router-owns-final-selection"
    invalid_item_policy: str = "drop-invalid-item;emit-localized-review-and-fail-closed-record"

    def policy_hash(self) -> str:
        payload = json.dumps(asdict(self), sort_keys=True, separators=(",", ":"))
        return "sha256:" + sha256(payload.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class CanonicalizationReceipt:
    receipt_version: str
    figure_id: str
    source_hash: str
    proposal_hash: str
    proposal_validation_valid: bool
    validation_issue_codes: tuple[str, ...]
    rejected_proposal_ids: tuple[str, ...]
    canonicalization_policy_version: str
    canonicalization_policy_hash: str
    canonical_state_hash: str
    proposal_to_canonical: tuple[tuple[str, str], ...]
    ignored_representation_hints: tuple[str, ...]
    nonauthoritative_chart_value_count: int
    review_item_ids: tuple[str, ...]
    failure_ids: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class CanonicalizationResult:
    figure: CanonicalFigure
    validation: ProposalValidationResult
    proposal_to_canonical: tuple[tuple[str, str], ...]
    receipt: CanonicalizationReceipt
    proposal: AnalysisProposal

    def canonical_id_for(self, proposal_id: str) -> str | None:
        return dict(self.proposal_to_canonical).get(proposal_id)


class ProposalCanonicalizer:
    """Deterministic first-party owner of proposal-to-canonical normalization."""

    def __init__(
        self,
        *,
        repository_root: str | Path,
        asset_output_dir: str | Path,
        policy: CanonicalizationPolicy = CanonicalizationPolicy(),
    ) -> None:
        self.repository_root = Path(repository_root).resolve()
        from unified_figure.file_boundary import checked_target
        self.asset_output_dir = checked_target(repository_root, asset_output_dir)
        self.policy = policy
        if self.repository_root not in self.asset_output_dir.parents:
            raise ValueError("asset output directory must remain inside repository root")

    def canonicalize(
        self,
        *,
        figure_id: str,
        source: AuthoritativeSourceReference,
        authoritative_geometry: AuthoritativeInputGeometry,
        proposal: AnalysisProposal,
        analysis_receipt: AnalysisRunReceipt | None = None,
        validation: ProposalValidationResult | None = None,
        write_receipt_to: str | Path | None = None,
    ) -> CanonicalizationResult:
        authoritative_geometry.validate()
        validation = validation or AnalysisProposalValidator().validate(
            proposal,
            authoritative_width=authoritative_geometry.decoded_width_px,
            authoritative_height=authoritative_geometry.decoded_height_px,
        )
        if validation.fatal:
            raise ValueError("fatal AnalysisProposal validation failure")

        proposal_hash = proposal.content_hash()
        input_evidence = EvidenceRecord(
            evidence_id=make_stable_id(figure_id, "input-evidence", 0, source.source_hash),
            source_type=EvidenceSourceType.INPUT,
            source_hash=source.source_hash,
            producer="deterministic-image-decoder",
            operation="decode-authoritative-reference-image",
            input_refs=(source.repository_relative_ref,),
            output_refs=(
                f"{authoritative_geometry.decoded_width_px}x{authoritative_geometry.decoded_height_px}",
            ),
            run_index=0,
            tool_runtime_info={"geometry_authority": authoritative_geometry.geometry_source},
            notes="Decoded input dimensions are authoritative over model observations.",
        )
        analysis_evidence = EvidenceRecord(
            evidence_id=make_stable_id(figure_id, "analysis-evidence", 0, proposal_hash),
            source_type=EvidenceSourceType.ANALYSIS,
            source_hash=proposal_hash,
            producer=proposal.runtime_metadata.provider,
            operation="produce-untrusted-analysis-proposal",
            input_refs=(input_evidence.evidence_id,),
            output_refs=(proposal.proposal_version,),
            run_index=0,
            tool_runtime_info={
                "model": proposal.runtime_metadata.model,
                "prompt_hash": proposal.runtime_metadata.prompt_hash,
                "proposal_schema_hash": proposal.runtime_metadata.proposal_schema_hash,
                "response_id": proposal.runtime_metadata.response_id,
            },
            notes="Observable model output only; no hidden reasoning is retained.",
        )
        validation_payload = json.dumps(
            {
                "valid": validation.valid,
                "issues": [asdict(item) for item in validation.issues],
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        canonicalization_evidence = EvidenceRecord(
            evidence_id=make_stable_id(
                figure_id, "canonicalization-evidence", 0, self.policy.policy_hash()
            ),
            source_type=EvidenceSourceType.REVISION,
            source_hash="sha256:" + sha256(validation_payload.encode("utf-8")).hexdigest(),
            producer=self.policy.policy_version,
            operation="validate-normalize-and-canonicalize-analysis-proposal",
            input_refs=(analysis_evidence.evidence_id,),
            output_refs=(),
            run_index=0,
            tool_runtime_info={"policy_hash": self.policy.policy_hash(), "model_calls": "0"},
            notes="Deterministic canonicalization owns IDs, vocabulary, geometry, review, and coverage.",
        )
        evidence = (input_evidence, analysis_evidence, canonicalization_evidence)
        evidence_ids = tuple(item.evidence_id for item in evidence)

        mapping: dict[str, str] = {}
        reviews: list[ReviewItem] = []
        failures: list[FailClosedRecord] = []
        ignored_hints: set[str] = set()
        rejected = set(validation.rejected_proposal_ids)

        geometry_conflict = (
            proposal.observed_canvas.width != authoritative_geometry.decoded_width_px
            or proposal.observed_canvas.height != authoritative_geometry.decoded_height_px
            or proposal.observed_canvas.orientation != authoritative_geometry.orientation.value
        )
        if geometry_conflict:
            failures.append(
                FailClosedRecord(
                    failure_id=make_stable_id(figure_id, "input-geometry-conflict", 0),
                    reason=FailureReason.INPUT_GEOMETRY_CONFLICT,
                    target_id=figure_id,
                    message=(
                        f"model canvas={proposal.observed_canvas.width}x{proposal.observed_canvas.height}/"
                        f"{proposal.observed_canvas.orientation}; authoritative="
                        f"{authoritative_geometry.decoded_width_px}x{authoritative_geometry.decoded_height_px}/"
                        f"{authoritative_geometry.orientation.value}; model dimensions ignored"
                    ),
                    recoverable=True,
                    reviewable=True,
                    fallback_eligible=False,
                    state=FailureState.REVIEW_REQUIRED,
                    evidence_refs=(analysis_evidence.evidence_id,),
                )
            )
            reviews.append(
                self._review(
                    figure_id,
                    0,
                    TargetType.FIGURE,
                    figure_id,
                    ReviewReason.GEOMETRY_CONFLICT,
                    Severity.ERROR,
                    (analysis_evidence.evidence_id,),
                    ("retain decoded geometry", "inspect analysis canvas report"),
                    (),
                    "authoritative-geometry-guard",
                )
            )

        candidates: list[tuple[str, ComponentProposal | object, ComponentType]] = []
        for panel in proposal.panels:
            if panel.proposal_id not in rejected:
                candidates.append((panel.proposal_id, panel, ComponentType.PANEL))
        for component in proposal.component_proposals:
            if component.proposal_id not in rejected:
                try:
                    component_type = ComponentType(component.proposed_type)
                except ValueError:
                    component_type = ComponentType.UNKNOWN
                candidates.append((component.proposal_id, component, component_type))
            ignored_hints.update(
                hint
                for hint in component.representation_hints
                if hint not in {item.value for item in RepresentationType}
            )

        def identity_key(item: tuple[str, object, ComponentType]) -> tuple[object, ...]:
            _, candidate, component_type = item
            bbox = getattr(candidate, "bbox")
            normalized = self._normalize_bbox(
                bbox,
                authoritative_geometry.decoded_width_px,
                authoritative_geometry.decoded_height_px,
            )
            role = self._normalize_role(getattr(candidate, "semantic_role"), component_type, candidate)
            return (
                component_type.value,
                role,
                round(normalized.y / max(1.0, authoritative_geometry.decoded_height_px * 0.05)),
                round(normalized.x / max(1.0, authoritative_geometry.decoded_width_px * 0.05)),
            )

        ordered_candidates = sorted(candidates, key=identity_key)
        components: list[Component] = []
        decisions: list[RepresentationDecision] = []
        assets: list[AssetReference] = []
        proposal_components = {item.proposal_id: item for item in proposal.component_proposals}
        asset_proposals = {item.proposal_id: item for item in proposal.asset_proposals}
        charts_by_component = {item.component_proposal_id: item for item in proposal.chart_proposals}
        source_path = (self.repository_root / source.repository_relative_ref).resolve()

        for sequence, (proposal_id, item, component_type) in enumerate(ordered_candidates):
            role = self._normalize_role(getattr(item, "semantic_role"), component_type, item)
            component_id = make_stable_id(
                figure_id,
                "component",
                sequence,
                f"{component_type.value}|{role}",
            )
            mapping[proposal_id] = component_id
            bbox = self._normalize_bbox(
                getattr(item, "bbox"),
                authoritative_geometry.decoded_width_px,
                authoritative_geometry.decoded_height_px,
            )
            confidence = self._confidence(getattr(item, "confidence"))
            uncertainty = self._uncertainty(getattr(item, "uncertainty"))
            asset_ref: str | None = None
            representation = self._initial_representation(component_type)
            candidate_representations = [representation]

            if isinstance(item, ComponentProposal) and item.asset_proposal_id:
                asset_proposal = asset_proposals.get(item.asset_proposal_id)
                if asset_proposal and asset_proposal.proposal_id not in rejected:
                    if asset_proposal.source_type == AssetSourceType.SOURCE_GENERATED.value:
                        representation = RepresentationType.REVIEW_REQUIRED
                        candidate_representations = [
                            RepresentationType.GENERATED_COMPLEX_ASSET,
                            RepresentationType.REVIEW_REQUIRED,
                        ]
                        reviews.append(
                            self._review(
                                figure_id,
                                len(reviews),
                                TargetType.COMPONENT,
                                component_id,
                                ReviewReason.SCIENTIFIC_GENERATION_RISK,
                                Severity.CRITICAL,
                                (analysis_evidence.evidence_id,),
                                ("supply an exact source asset", "mark unsupported"),
                                tuple(candidate_representations),
                                self.policy.policy_version,
                            )
                        )
                    else:
                        asset, normalized_representation = self._materialize_source_asset(
                            figure_id=figure_id,
                            sequence=len(assets),
                            component_id=component_id,
                            proposal=asset_proposal,
                            source_path=source_path,
                            authoritative_geometry=authoritative_geometry,
                            evidence_refs=(input_evidence.evidence_id, analysis_evidence.evidence_id),
                        )
                        assets.append(asset)
                        asset_ref = asset.asset_id
                        mapping[asset_proposal.proposal_id] = asset.asset_id
                        representation = normalized_representation
                        candidate_representations = [normalized_representation]
                        if asset_proposal.source_type == AssetSourceType.SOURCE_VECTOR.value:
                            candidate_representations.insert(0, RepresentationType.VECTOR_ASSET)
                            reviews.append(
                                self._review(
                                    figure_id,
                                    len(reviews),
                                    TargetType.COMPONENT,
                                    component_id,
                                    ReviewReason.REPRESENTATION_AMBIGUITY,
                                    Severity.WARNING,
                                    (analysis_evidence.evidence_id,),
                                    ("supply authoritative vector bytes", "retain exact raster crop"),
                                    tuple(candidate_representations),
                                    self.policy.policy_version,
                                )
                            )

            if (
                isinstance(item, ComponentProposal)
                and component_type in {ComponentType.COMPLEX_VISUAL, ComponentType.IMAGE_ASSET, ComponentType.CHART}
                and not asset_ref
            ):
                implicit_asset = AssetProposal(
                    proposal_id=f"implicit-source-region:{item.proposal_id}",
                    component_proposal_id=item.proposal_id,
                    asset_type=AssetType.RASTER.value,
                    source_type=AssetSourceType.SOURCE_PRESERVED_RASTER.value,
                    source_region=item.bbox,
                    separable=False,
                    has_alpha="UNKNOWN",
                    authoritative_data_ref=None,
                    representation_hints=(RepresentationType.RASTER_PRESERVE_COMPLEX.value,),
                    confidence=item.confidence,
                    uncertainty=item.uncertainty,
                    notes="deterministic source-preserving crop from authoritative reference image",
                )
                asset, normalized_representation = self._materialize_source_asset(
                    figure_id=figure_id,
                    sequence=len(assets),
                    component_id=component_id,
                    proposal=implicit_asset,
                    source_path=source_path,
                    authoritative_geometry=authoritative_geometry,
                    evidence_refs=(input_evidence.evidence_id, analysis_evidence.evidence_id),
                )
                assets.append(asset)
                asset_ref = asset.asset_id
                mapping[implicit_asset.proposal_id] = asset.asset_id
                representation = normalized_representation
                candidate_representations = [normalized_representation]
                if component_type is ComponentType.CHART and item.proposal_id in charts_by_component:
                    chart = charts_by_component[item.proposal_id]
                    if not (chart.authoritative_data_available and chart.authoritative_data_ref):
                        reviews.append(
                            self._review(
                                figure_id,
                                len(reviews),
                                TargetType.COMPONENT,
                                component_id,
                                ReviewReason.REPRESENTATION_AMBIGUITY,
                                Severity.WARNING,
                                (analysis_evidence.evidence_id,),
                                ("retain source-preserving chart image", "supply authoritative chart data"),
                                (RepresentationType.RASTER_PRESERVE_COMPLEX, RepresentationType.EDITABLE_CHART),
                                self.policy.policy_version,
                            )
                        )

            if component_type in {ComponentType.COMPLEX_VISUAL, ComponentType.IMAGE_ASSET, ComponentType.CHART} and not asset_ref:
                representation = RepresentationType.REVIEW_REQUIRED
                candidate_representations = [
                    RepresentationType.EXACT_RASTER_ASSET,
                    RepresentationType.RASTER_PRESERVE_COMPLEX,
                    RepresentationType.REVIEW_REQUIRED,
                ]
                reviews.append(
                    self._review(
                        figure_id,
                        len(reviews),
                        TargetType.COMPONENT,
                        component_id,
                        ReviewReason.EXTRACTION_LIMITATION,
                        Severity.ERROR,
                        (analysis_evidence.evidence_id,),
                        ("provide a valid source region", "perform localized review"),
                        tuple(candidate_representations),
                        self.policy.policy_version,
                    )
                )
            if component_type is ComponentType.UNKNOWN:
                representation = RepresentationType.REVIEW_REQUIRED
                candidate_representations = [RepresentationType.REVIEW_REQUIRED]
                reviews.append(
                    self._review(
                        figure_id,
                        len(reviews),
                        TargetType.COMPONENT,
                        component_id,
                        ReviewReason.UNSUPPORTED_FEATURE,
                        Severity.ERROR,
                        (analysis_evidence.evidence_id,),
                        ("classify component", "mark unsupported"),
                        (RepresentationType.REVIEW_REQUIRED, RepresentationType.UNSUPPORTED),
                        self.policy.policy_version,
                    )
                )
            if component_type is ComponentType.TEXT and (
                confidence is ConfidenceLevel.LOW
                or uncertainty in {UncertaintyLevel.HIGH, UncertaintyLevel.AMBIGUOUS, UncertaintyLevel.REVIEW_REQUIRED}
            ):
                reviews.append(
                    self._review(
                        figure_id,
                        len(reviews),
                        TargetType.COMPONENT,
                        component_id,
                        ReviewReason.EXTRACTION_LIMITATION,
                        Severity.WARNING,
                        (analysis_evidence.evidence_id,),
                        ("perform bound ROI analysis", "accept observed transcription"),
                        (RepresentationType.NATIVE_TEXT,),
                        self.policy.policy_version,
                    )
                )

            decision_id = make_stable_id(figure_id, "analysis-representation-candidate", sequence, component_id)
            decision = RepresentationDecision(
                decision_id=decision_id,
                component_id=component_id,
                selected_representation=representation,
                candidate_representations=tuple(dict.fromkeys((*candidate_representations, representation))),
                decision_reason=(
                    f"{self.policy.policy_version} prepared provider-neutral candidates from "
                    f"component_type={component_type.value}; model hints are non-authoritative"
                ),
                confidence=confidence,
                scientific_risk="HIGH" if component_type in {ComponentType.CHART, ComponentType.COMPLEX_VISUAL} else "LOW",
                editability_need="COMPONENT_LEVEL" if component_type in {ComponentType.TEXT, ComponentType.SIMPLE_SHAPE} else "ASSET_LEVEL",
                complexity_estimate=CostClass.HIGH if component_type in {ComponentType.CHART, ComponentType.COMPLEX_VISUAL} else CostClass.LOW,
                execution_cost_estimate=CostClass.LOW,
                review_cost_estimate=CostClass.HIGH if representation is RepresentationType.REVIEW_REQUIRED else CostClass.LOW,
                required_backend_capabilities=(self._capability_name(representation),),
                fallback_policy=FallbackPolicy.REQUIRE_REVIEW if representation is RepresentationType.REVIEW_REQUIRED else FallbackPolicy.ALLOW_EXPLICIT,
                evidence_refs=(analysis_evidence.evidence_id, canonicalization_evidence.evidence_id),
                status=DecisionStatus.REVIEW_REQUIRED if representation is RepresentationType.REVIEW_REQUIRED else DecisionStatus.PROPOSED,
            )
            decisions.append(decision)
            component = Component(
                component_id=component_id,
                component_type=component_type,
                semantic_role=role,
                geometry=Geometry(bbox, CoordinateSpace.SOURCE_PIXELS),
                style=self._style(getattr(item, "style_hints", ()), component_type),
                representation=representation,
                representation_decision_id=decision_id,
                asset_ref=asset_ref,
                uncertainty=UncertaintyRecord(
                    uncertainty,
                    (f"analysis confidence={confidence.value}",) if uncertainty is not UncertaintyLevel.NONE else (),
                    any(review.target_id == component_id for review in reviews),
                ),
                review_status=ReviewStatus.PENDING if any(review.target_id == component_id for review in reviews) else ReviewStatus.NOT_REQUIRED,
                coverage_status=CoverageStatus.PRESENT,
                provenance=ProvenanceReference(
                    (analysis_evidence.evidence_id, canonicalization_evidence.evidence_id),
                    self.policy.policy_version,
                    "canonicalize-component-proposal",
                ),
            )
            components.append(component)

        for issue in validation.issues:
            if not issue.blocks_item:
                continue
            reason = (
                FailureReason.UNCERTAIN_RELATION
                if "RELATION" in issue.code
                else FailureReason.INVALID_STRUCTURE
            )
            failures.append(
                FailClosedRecord(
                    failure_id=make_stable_id(figure_id, "proposal-validation-failure", len(failures), f"{issue.target_proposal_id}|{issue.code}"),
                    reason=reason,
                    target_id=figure_id,
                    message=f"{issue.code}: {issue.message}; malformed item was not canonicalized",
                    recoverable=True,
                    reviewable=True,
                    fallback_eligible=False,
                    state=FailureState.REVIEW_REQUIRED,
                    evidence_refs=(analysis_evidence.evidence_id, canonicalization_evidence.evidence_id),
                )
            )
            reviews.append(
                self._review(
                    figure_id,
                    len(reviews),
                    TargetType.FIGURE,
                    figure_id,
                    ReviewReason.UNCERTAIN_RELATION if "RELATION" in issue.code else ReviewReason.GEOMETRY_CONFLICT,
                    Severity.ERROR,
                    (canonicalization_evidence.evidence_id,),
                    ("correct the rejected proposal item", "perform localized re-analysis"),
                    (),
                    self.policy.policy_version,
                )
            )

        groups: list[SemanticGroup] = []
        for sequence, group in enumerate(sorted(proposal.group_proposals, key=lambda item: (item.semantic_role, item.proposal_id))):
            if group.proposal_id in rejected:
                continue
            group_id = make_stable_id(figure_id, "semantic-group", sequence, self._slug(group.semantic_role))
            mapping[group.proposal_id] = group_id
            members = tuple(sorted(mapping[item] for item in group.member_proposal_ids if item in mapping))
            ambiguous = group.uncertainty in {"HIGH", "AMBIGUOUS", "REVIEW_REQUIRED"} or group.confidence == "LOW"
            groups.append(
                SemanticGroup(
                    group_id=group_id,
                    parent_group_id=None,
                    member_component_ids=members if not ambiguous else (),
                    semantic_role=self._slug(group.semantic_role) or "semantic_group",
                    confidence=self._confidence(group.confidence),
                    provenance=ProvenanceReference(
                        (analysis_evidence.evidence_id, canonicalization_evidence.evidence_id),
                        self.policy.policy_version,
                        "normalize-group-membership",
                    ),
                    review_status=ReviewStatus.PENDING if ambiguous else ReviewStatus.NOT_REQUIRED,
                )
            )
            if ambiguous:
                reviews.append(
                    self._review(
                        figure_id,
                        len(reviews),
                        TargetType.GROUP,
                        group_id,
                        ReviewReason.UNCERTAIN_GROUPING,
                        Severity.WARNING,
                        (analysis_evidence.evidence_id,),
                        ("perform bound ROI analysis", "select authoritative members"),
                        (),
                        self.policy.policy_version,
                    )
                )
        groups = [
            replace(
                group,
                parent_group_id=mapping.get(
                    next(
                        (
                            candidate.parent_group_proposal_id
                            for candidate in proposal.group_proposals
                            if mapping.get(candidate.proposal_id) == group.group_id
                        ),
                        None,
                    )
                ),
            )
            for group in groups
        ]

        relations: list[Relation] = []
        for sequence, relation in enumerate(sorted(proposal.relation_proposals, key=lambda item: item.proposal_id)):
            if relation.proposal_id in rejected:
                continue
            source_id = mapping.get(relation.source_candidate_id or "")
            target_id = mapping.get(relation.target_candidate_id or "")
            ambiguous = (
                not source_id
                or not target_id
                or relation.confidence == "LOW"
                or relation.uncertainty in {"HIGH", "AMBIGUOUS", "REVIEW_REQUIRED"}
                or relation.direction == "UNKNOWN"
            )
            if ambiguous:
                failures.append(
                    FailClosedRecord(
                        failure_id=make_stable_id(figure_id, "uncertain-relation", len(failures), relation.proposal_id),
                        reason=FailureReason.UNCERTAIN_RELATION,
                        target_id=figure_id,
                        message=f"relation proposal {relation.proposal_id} lacks trusted endpoints/direction",
                        recoverable=True,
                        reviewable=True,
                        fallback_eligible=False,
                        state=FailureState.REVIEW_REQUIRED,
                        evidence_refs=(analysis_evidence.evidence_id,),
                    )
                )
                reviews.append(
                    self._review(
                        figure_id,
                        len(reviews),
                        TargetType.FIGURE,
                        figure_id,
                        ReviewReason.UNCERTAIN_RELATION,
                        Severity.ERROR,
                        (analysis_evidence.evidence_id,),
                        ("perform bound ROI analysis", "mark relation unsupported"),
                        (RepresentationType.NATIVE_RELATION, RepresentationType.REVIEW_REQUIRED),
                        self.policy.policy_version,
                    )
                )
                continue
            relation_id = make_stable_id(
                figure_id,
                "relation",
                sequence,
                f"{source_id}|{target_id}|{relation.relation_type}|{relation.direction}",
            )
            mapping[relation.proposal_id] = relation_id
            relations.append(
                self._canonical_relation(
                    relation_id,
                    relation,
                    source_id,
                    target_id,
                    authoritative_geometry,
                    analysis_evidence.evidence_id,
                )
            )

        review_keys = {(item.target_id, item.reason_type.value) for item in reviews}
        for uncertainty in proposal.uncertainties:
            target = mapping.get(uncertainty.target_proposal_id, figure_id)
            reason = self._review_reason(uncertainty.reason_type)
            if (target, reason.value) not in review_keys:
                reviews.append(
                    self._review(
                        figure_id,
                        len(reviews),
                        self._target_type(target, components, groups, relations, assets),
                        target,
                        reason,
                        self._severity(uncertainty.severity),
                        (analysis_evidence.evidence_id,),
                        ("perform bound ROI analysis", "accept uncertainty", "mark unsupported"),
                        (),
                        self.policy.policy_version,
                    )
                )
                review_keys.add((target, reason.value))
        for candidate in proposal.review_candidates:
            target = mapping.get(candidate.target_proposal_id, figure_id)
            reason = self._review_reason(candidate.reason_type)
            if (target, reason.value) in review_keys:
                continue
            representations = tuple(
                RepresentationType(item)
                for item in candidate.candidate_representations
                if item in {value.value for value in RepresentationType}
            )
            reviews.append(
                self._review(
                    figure_id,
                    len(reviews),
                    self._target_type(target, components, groups, relations, assets),
                    target,
                    reason,
                    self._severity(candidate.severity),
                    (analysis_evidence.evidence_id,),
                    candidate.candidate_actions or ("perform localized review",),
                    representations,
                    self.policy.policy_version,
                )
            )
            review_keys.add((target, reason.value))

        coverage = self._coverage(
            figure_id,
            proposal,
            mapping,
            analysis_evidence.evidence_id,
            reviews,
        )
        status = ReconstructionStatus.REVIEW_REQUIRED if reviews or failures else ReconstructionStatus.IN_PROGRESS
        observed_cost = self._analysis_cost(analysis_receipt)
        revision_id = make_stable_id(figure_id, "revision-analysis-root", 0, proposal_hash)
        figure = CanonicalFigure(
            schema_version=SCHEMA_VERSION,
            figure_id=figure_id,
            input_geometry=authoritative_geometry,
            components=tuple(components),
            groups=tuple(groups),
            relations=tuple(relations),
            assets=tuple(assets),
            representation_decisions=tuple(decisions),
            fallbacks=(),
            coverage=tuple(coverage),
            review_items=tuple(self._deduplicate_reviews(reviews)),
            failures=tuple(failures),
            revision=RevisionRecord(
                revision_id=revision_id,
                parent_revision_id=None,
                canonical_state_hash="",
                change_manifest=(
                    "canonicalize untrusted AnalysisProposal",
                    f"preserve authoritative geometry {authoritative_geometry.decoded_width_px}x{authoritative_geometry.decoded_height_px}",
                    "initialize coverage and localized review state",
                ),
                evidence_refs=evidence_ids,
                status=RevisionStatus.ROOT,
            ),
            evidence=evidence,
            cost_telemetry=CostTelemetry(
                estimated_cost=CostMetrics(
                    analysis_calls=1,
                    vision_calls=1,
                    input_tokens="NOT_EXPOSED",
                    output_tokens="NOT_EXPOSED",
                    imagegen_calls=0,
                    renderer_calls=0,
                    external_backend_calls=0,
                    full_image_reviews=0,
                    roi_reviews=0,
                    manual_corrections=0,
                    wall_time_ms="NOT_EXPOSED",
                ),
                observed_cost=observed_cost,
                stage_wall_time_ms={"analysis": observed_cost.wall_time_ms, "canonicalization": "NOT_EXPOSED"},
            ),
            renderer_realizations=(),
            reconstruction_status=status,
            metadata={
                "analysis.prompt_hash": proposal.runtime_metadata.prompt_hash,
                "analysis.proposal_hash": proposal_hash,
                "analysis.proposal_map": json.dumps(mapping, sort_keys=True, separators=(",", ":")),
                "canonicalization.policy_hash": self.policy.policy_hash(),
                "source.class": "PHASE5C_NON_BENCHMARK_REFERENCE",
                **{
                    f"text.{mapping[item.proposal_id]}": item.text_content or ""
                    for item in proposal.component_proposals
                    if item.proposal_id in mapping and item.text_content
                },
                **{
                    'authoring.text.'+mapping[item.proposal_id]: encode_text_authoring(json.loads(hint.value),authoritative_geometry)
                    for item in proposal.component_proposals
                    if item.proposal_id in mapping and item.proposed_type == 'TEXT'
                    for hint in item.style_hints if hint.key.lower() == 'text_authoring'
                },
            },
        ).with_computed_revision_hash()
        figure.validate()

        nonauthoritative_chart_values = sum(
            len(item.proposed_values)
            for item in proposal.chart_proposals
            if not (item.authoritative_data_available and item.authoritative_data_ref)
        )
        receipt = CanonicalizationReceipt(
            receipt_version="phase5c-canonicalization-receipt-v0.1",
            figure_id=figure_id,
            source_hash=source.source_hash,
            proposal_hash=proposal_hash,
            proposal_validation_valid=validation.valid,
            validation_issue_codes=tuple(sorted({item.code for item in validation.issues})),
            rejected_proposal_ids=validation.rejected_proposal_ids,
            canonicalization_policy_version=self.policy.policy_version,
            canonicalization_policy_hash=self.policy.policy_hash(),
            canonical_state_hash=figure.state_hash(),
            proposal_to_canonical=tuple(sorted(mapping.items())),
            ignored_representation_hints=tuple(sorted(ignored_hints)),
            nonauthoritative_chart_value_count=nonauthoritative_chart_values,
            review_item_ids=tuple(item.review_id for item in figure.review_items),
            failure_ids=tuple(item.failure_id for item in figure.failures),
        )
        if write_receipt_to is not None:
            Path(write_receipt_to).write_text(
                json.dumps(receipt.to_dict(), indent=2, sort_keys=True) + "\n", encoding="utf-8"
            )
        return CanonicalizationResult(
            figure=figure,
            validation=validation,
            proposal_to_canonical=tuple(sorted(mapping.items())),
            receipt=receipt,
            proposal=proposal,
        )

    def apply_roi_revision(
        self,
        base: CanonicalizationResult,
        *,
        roi_proposal: AnalysisProposal,
        roi_receipt: AnalysisRunReceipt,
        linked_uncertainty_id: str,
        write_receipt_to: str | Path | None = None,
    ) -> CanonicalizationResult:
        """Apply only the bound target and preserve all unrelated canonical objects verbatim."""

        uncertainty = next(
            (
                item
                for item in base.proposal.uncertainties
                if item.uncertainty_id == linked_uncertainty_id
            ),
            None,
        )
        if uncertainty is None:
            raise ValueError("ROI revision must bind to an existing typed uncertainty")
        mapping = dict(base.proposal_to_canonical)
        target_id = mapping.get(uncertainty.target_proposal_id)
        if target_id is None:
            raise ValueError("ROI uncertainty target was not canonicalized")
        roi_validation = AnalysisProposalValidator().validate(
            roi_proposal,
            authoritative_width=base.figure.input_geometry.decoded_width_px,
            authoritative_height=base.figure.input_geometry.decoded_height_px,
        )
        if roi_validation.fatal:
            raise ValueError("fatal ROI proposal validation failure")
        roi_evidence = EvidenceRecord(
            evidence_id=make_stable_id(base.figure.figure_id, "roi-analysis-evidence", len(base.figure.evidence), linked_uncertainty_id),
            source_type=EvidenceSourceType.ANALYSIS,
            source_hash=roi_proposal.content_hash(),
            producer=roi_proposal.runtime_metadata.provider,
            operation="bound-roi-analysis",
            input_refs=(base.figure.revision.revision_id, linked_uncertainty_id),
            output_refs=(target_id,),
            run_index=1,
            tool_runtime_info={
                "model": roi_proposal.runtime_metadata.model,
                "prompt_hash": roi_proposal.runtime_metadata.prompt_hash,
                "analysis_mode": "ROI",
                "cache_hit": str(roi_receipt.cache_hit).lower(),
            },
            notes="ROI evidence may update only its bound canonical target.",
        )
        components = list(base.figure.components)
        metadata = dict(base.figure.metadata)
        target_component = next((item for item in components if item.component_id == target_id), None)
        changed = False
        if target_component is not None:
            candidates = [
                item
                for item in roi_proposal.component_proposals
                if item.proposed_type == target_component.component_type.value
            ]
            if candidates:
                best = sorted(
                    candidates,
                    key=lambda item: (
                        {"HIGH": 0, "MEDIUM": 1, "LOW": 2}.get(item.confidence, 3),
                        item.proposal_id,
                    ),
                )[0]
                if target_component.component_type is ComponentType.TEXT and best.text_content:
                    metadata[f"text.{target_id}"] = best.text_content
                if best.confidence in {"HIGH", "MEDIUM"} and best.uncertainty in {"NONE", "LOW"}:
                    index = components.index(target_component)
                    components[index] = replace(
                        target_component,
                        uncertainty=UncertaintyRecord(UncertaintyLevel.LOW, ("resolved by bound ROI evidence",), False),
                        review_status=ReviewStatus.NOT_REQUIRED,
                        provenance=replace(
                            target_component.provenance,
                            evidence_refs=tuple(dict.fromkeys((*target_component.provenance.evidence_refs, roi_evidence.evidence_id))),
                            operation="resolve-component-with-bound-roi",
                        ),
                    )
                    changed = True
        reviews = tuple(
            item
            for item in base.figure.review_items
            if not (changed and item.target_id == target_id and item.status is ReviewStatus.PENDING)
        )
        failures = base.figure.failures
        previous = base.figure.cost_telemetry.observed_cost
        current = self._analysis_cost(roi_receipt, roi=True)
        observed = CostMetrics(
            analysis_calls=self._sum_cost(previous.analysis_calls, current.analysis_calls),
            vision_calls=self._sum_cost(previous.vision_calls, current.vision_calls),
            input_tokens=self._sum_cost(previous.input_tokens, current.input_tokens),
            output_tokens=self._sum_cost(previous.output_tokens, current.output_tokens),
            imagegen_calls=0,
            renderer_calls=previous.renderer_calls,
            external_backend_calls=0,
            full_image_reviews=0,
            roi_reviews=self._sum_cost(previous.roi_reviews, current.roi_reviews),
            manual_corrections=0,
            wall_time_ms=self._sum_cost(previous.wall_time_ms, current.wall_time_ms),
        )
        revision_id = make_stable_id(
            base.figure.figure_id, "revision-roi-candidate", 0, linked_uncertainty_id
        )
        figure = replace(
            base.figure,
            components=tuple(components),
            review_items=reviews,
            failures=failures,
            evidence=(*base.figure.evidence, roi_evidence),
            cost_telemetry=replace(
                base.figure.cost_telemetry,
                observed_cost=observed,
                stage_wall_time_ms={
                    **base.figure.cost_telemetry.stage_wall_time_ms,
                    "roi_analysis": current.wall_time_ms,
                },
            ),
            revision=RevisionRecord(
                revision_id=revision_id,
                parent_revision_id=base.figure.revision.revision_id,
                canonical_state_hash="",
                change_manifest=(
                    f"bind ROI evidence to uncertainty {linked_uncertainty_id}",
                    f"update only canonical target {target_id}",
                    "preserve unrelated canonical objects and prior evidence",
                ),
                evidence_refs=tuple(dict.fromkeys((*base.figure.revision.evidence_refs, roi_evidence.evidence_id))),
                status=RevisionStatus.CANDIDATE,
            ),
            reconstruction_status=ReconstructionStatus.REVIEW_REQUIRED if reviews else ReconstructionStatus.IN_PROGRESS,
            metadata=metadata,
        ).with_computed_revision_hash()
        figure.validate()
        receipt = replace(
            base.receipt,
            receipt_version="phase5c-roi-canonicalization-receipt-v0.1",
            proposal_hash=roi_proposal.content_hash(),
            proposal_validation_valid=roi_validation.valid,
            validation_issue_codes=tuple(sorted({item.code for item in roi_validation.issues})),
            rejected_proposal_ids=roi_validation.rejected_proposal_ids,
            canonical_state_hash=figure.state_hash(),
            review_item_ids=tuple(item.review_id for item in figure.review_items),
            failure_ids=tuple(item.failure_id for item in figure.failures),
        )
        if write_receipt_to is not None:
            Path(write_receipt_to).write_text(
                json.dumps(receipt.to_dict(), indent=2, sort_keys=True) + "\n", encoding="utf-8"
            )
        return CanonicalizationResult(
            figure,
            roi_validation,
            base.proposal_to_canonical,
            receipt,
            base.proposal,
        )

    def _materialize_source_asset(
        self,
        *,
        figure_id: str,
        sequence: int,
        component_id: str,
        proposal,
        source_path: Path,
        authoritative_geometry: AuthoritativeInputGeometry,
        evidence_refs: tuple[str, ...],
    ) -> tuple[AssetReference, RepresentationType]:
        try:
            from PIL import Image
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError("source asset extraction requires Pillow") from exc
        region = self._normalize_bbox(
            proposal.source_region,
            authoritative_geometry.decoded_width_px,
            authoritative_geometry.decoded_height_px,
        )
        left, top = int(region.x), int(region.y)
        right = max(left + 1, int(round(region.x + region.width)))
        bottom = max(top + 1, int(round(region.y + region.height)))
        from unified_figure.file_boundary import save_asset
        asset_id = make_stable_id(figure_id, "source-asset", sequence, component_id)
        with Image.open(source_path) as image:
            crop = image.crop((left, top, right, bottom))
            alpha = "A" in crop.getbands()
            output = save_asset(crop, self.asset_output_dir, asset_id, root=self.repository_root, optimize=False, compress_level=9)
        digest = "sha256:" + sha256(output.read_bytes()).hexdigest()
        file_ref = output.relative_to(self.repository_root).as_posix()
        preserved = not proposal.separable or proposal.source_type == AssetSourceType.SOURCE_PRESERVED_RASTER.value
        representation = (
            RepresentationType.RASTER_PRESERVE_COMPLEX
            if preserved
            else RepresentationType.EXACT_RASTER_ASSET
        )
        source_type = (
            AssetSourceType.SOURCE_PRESERVED_RASTER if preserved else AssetSourceType.SOURCE_EXACT
        )
        return (
            AssetReference(
                asset_id=asset_id,
                asset_type=AssetType.RASTER,
                source_type=source_type,
                file_ref=file_ref,
                content_hash=digest,
                source_region=region,
                alpha_state=AlphaState.HAS_ALPHA if alpha else AlphaState.OPAQUE,
                provenance=ProvenanceReference(
                    evidence_refs,
                    self.policy.policy_version,
                    "deterministic-source-region-extraction",
                ),
                scientific_fidelity_status=FidelityStatus.VERIFIED_EXACT if not preserved else FidelityStatus.PRESERVED,
                replaceability=Replaceability.REPLACEABLE,
                representation_type=representation,
                backend_requirement=None,
            ),
            representation,
        )

    def _coverage(
        self,
        figure_id: str,
        proposal: AnalysisProposal,
        mapping: dict[str, str],
        evidence_id: str,
        reviews: list[ReviewItem],
    ) -> list[CoverageRecord]:
        by_dimension = {item.dimension: item for item in proposal.coverage_observations}
        result: list[CoverageRecord] = []
        for sequence, dimension in enumerate(CoverageDimension):
            observation = by_dimension.get(dimension.value)
            status = (
                CoverageStatus(observation.status)
                if observation and observation.status in {item.value for item in CoverageStatus}
                else CoverageStatus.UNKNOWN
            )
            coverage_id = make_stable_id(figure_id, "coverage", sequence, dimension.value)
            targets = (
                tuple(sorted(mapping[item] for item in observation.target_proposal_ids if item in mapping))
                if observation
                else ()
            )
            review_required = status in {
                CoverageStatus.MISSING,
                CoverageStatus.ALTERED,
                CoverageStatus.UNKNOWN,
            }
            result.append(
                CoverageRecord(
                    coverage_id=coverage_id,
                    dimension=dimension,
                    status=status,
                    mandatory=True,
                    target_ids=targets,
                    evidence_refs=(evidence_id,),
                    review_status=ReviewStatus.PENDING if review_required else ReviewStatus.NOT_REQUIRED,
                    notes=observation.notes if observation else "coverage observation missing; initialized UNKNOWN",
                )
            )
            if review_required:
                reviews.append(
                    self._review(
                        figure_id,
                        len(reviews),
                        TargetType.COVERAGE,
                        coverage_id,
                        ReviewReason.MISSING_CONTENT if status is CoverageStatus.MISSING else ReviewReason.ALTERED_CONTENT if status is CoverageStatus.ALTERED else ReviewReason.EXTRACTION_LIMITATION,
                        Severity.ERROR,
                        (evidence_id,),
                        ("inspect localized coverage evidence", "request targeted analysis"),
                        (),
                        self.policy.policy_version,
                    )
                )
        return result

    def _canonical_relation(
        self,
        relation_id: str,
        proposal: RelationProposal,
        source_id: str,
        target_id: str,
        geometry: AuthoritativeInputGeometry,
        evidence_id: str,
    ) -> Relation:
        points = tuple(
            self._normalize_point(point, proposal.coordinate_space, geometry)
            for point in proposal.route_points
        )
        style = self._style(proposal.visual_style_hints, ComponentType.RELATION_ANCHOR)
        return Relation(
            relation_id=relation_id,
            source_component_id=source_id,
            target_component_id=target_id,
            direction=RelationDirection(proposal.direction),
            relation_type=RelationType(proposal.relation_type),
            route_type=RouteType(proposal.route_type),
            attachment_intent=AttachmentIntent.NATIVE_ATTACHMENT if proposal.route_type == RouteType.STRAIGHT.value else AttachmentIntent.VISUAL_ROUTE_ONLY,
            visual_style=replace(style, fill=None, arrowhead_end="triangle" if proposal.direction == RelationDirection.SOURCE_TO_TARGET.value else style.arrowhead_end),
            confidence=self._confidence(proposal.confidence),
            representation=RepresentationType.NATIVE_RELATION,
            route_points=points,
            review_status=ReviewStatus.NOT_REQUIRED,
            evidence_refs=(evidence_id,),
        )

    @staticmethod
    def _normalize_bbox(bbox: ProposalBBox, width: int, height: int) -> BBox:
        if bbox.coordinate_space == CoordinateSpace.NORMALIZED.value:
            x, y, box_width, box_height = (
                bbox.x * width,
                bbox.y * height,
                bbox.width * width,
                bbox.height * height,
            )
        else:
            x, y, box_width, box_height = bbox.x, bbox.y, bbox.width, bbox.height
        epsilon = 1e-6
        x = 0.0 if -epsilon <= x < 0 else x
        y = 0.0 if -epsilon <= y < 0 else y
        box_width = min(box_width, width - x) if box_width + x <= width + epsilon else box_width
        box_height = min(box_height, height - y) if box_height + y <= height + epsilon else box_height
        return BBox(x, y, box_width, box_height)

    @staticmethod
    def _normalize_point(
        point: tuple[float, float], coordinate_space: str, geometry: AuthoritativeInputGeometry
    ) -> Point:
        if coordinate_space == CoordinateSpace.NORMALIZED.value:
            return Point(point[0] * geometry.decoded_width_px, point[1] * geometry.decoded_height_px)
        return Point(*point)

    @staticmethod
    def _normalize_role(role: str, component_type: ComponentType, item: object) -> str:
        normalized = ProposalCanonicalizer._slug(role)
        if component_type in {ComponentType.SIMPLE_SHAPE, ComponentType.PANEL, ComponentType.RELATION_ANCHOR}:
            hints = {
                hint.key.lower(): hint.value.lower()
                for hint in getattr(item, "style_hints", ())
                if isinstance(hint, StyleHint)
            }
            shape = hints.get("shape", normalized)
            if shape in {"rectangle", "rect", "box", "process", "node", "panel"}:
                return "rectangle" if component_type is not ComponentType.PANEL else "panel"
            if shape in {"rounded_rectangle", "rounded rectangle", "roundrect"}:
                return "rounded_rectangle"
            if shape in {"ellipse", "circle", "oval"}:
                return "ellipse"
            if shape == "line":
                return "line"
            return "rectangle" if component_type is not ComponentType.PANEL else "panel"
        return normalized or component_type.value.lower()

    @staticmethod
    def _style(hints: Iterable[StyleHint], component_type: ComponentType) -> Style:
        values = {item.key.lower(): item.value for item in hints}
        stroke_width = ProposalCanonicalizer._float(values.get("stroke_width"), 1.5)
        font_size = ProposalCanonicalizer._float(values.get("font_size"), 18.0)
        opacity = ProposalCanonicalizer._float(values.get("opacity"), 1.0)
        text_color=values.get('color',values.get('fill','#111111'))
        # Literal CSS names from text evidence only; do not change shape colors.
        text_color={'green':'#008000','blue':'#0000FF','orange':'#FFA500'}.get(text_color.lower(),text_color)
        return Style(
            fill=values.get("fill", "#ffffff") if component_type is not ComponentType.TEXT else text_color,
            stroke=values.get("stroke", "#111111"),
            stroke_width=max(0.0, stroke_width),
            opacity=max(0.0, min(1.0, opacity)),
            font=FontStyle(values.get("font_family", "Arial"),values.get('font_weight',values.get('weight','normal')),
                values.get('font_style','').lower() == 'italic') if component_type is ComponentType.TEXT else None,
            font_size=max(0.0, font_size) if component_type is ComponentType.TEXT else None,
            text_alignment=values.get('text_alignment',values.get('alignment','center')) if component_type is ComponentType.TEXT else values.get('text_alignment'),
            z_order=int(ProposalCanonicalizer._float(values.get("z_order"), 0)),
            arrowhead_start=values.get("arrowhead_start"),
            arrowhead_end=values.get("arrowhead_end"),
        )

    @staticmethod
    def _initial_representation(component_type: ComponentType) -> RepresentationType:
        if component_type is ComponentType.TEXT:
            return RepresentationType.NATIVE_TEXT
        if component_type in {ComponentType.SIMPLE_SHAPE, ComponentType.PANEL, ComponentType.RELATION_ANCHOR}:
            return RepresentationType.NATIVE_SIMPLE_SHAPE
        return RepresentationType.REVIEW_REQUIRED

    @staticmethod
    def _capability_name(representation: RepresentationType) -> str:
        return {
            RepresentationType.NATIVE_TEXT: "supports_native_text",
            RepresentationType.NATIVE_SIMPLE_SHAPE: "supports_simple_shape",
            RepresentationType.NATIVE_RELATION: "supports_native_relation",
            RepresentationType.VECTOR_ASSET: "supports_vector_asset",
            RepresentationType.EXACT_RASTER_ASSET: "supports_exact_raster",
            RepresentationType.RASTER_PRESERVE_COMPLEX: "supports_exact_raster",
            RepresentationType.REVIEW_REQUIRED: "localized_review",
        }.get(representation, "explicit-capability-negotiation")

    @staticmethod
    def _confidence(value: str) -> ConfidenceLevel:
        try:
            return ConfidenceLevel(value)
        except ValueError:
            return ConfidenceLevel.LOW

    @staticmethod
    def _uncertainty(value: str) -> UncertaintyLevel:
        try:
            return UncertaintyLevel(value)
        except ValueError:
            return UncertaintyLevel.REVIEW_REQUIRED

    @staticmethod
    def _review_reason(value: str) -> ReviewReason:
        try:
            return ReviewReason(value)
        except ValueError:
            return ReviewReason.UNSUPPORTED_FEATURE

    @staticmethod
    def _severity(value: str) -> Severity:
        try:
            return Severity(value)
        except ValueError:
            return Severity.ERROR

    @staticmethod
    def _target_type(
        target_id: str,
        components: Iterable[Component],
        groups: Iterable[SemanticGroup],
        relations: Iterable[Relation],
        assets: Iterable[AssetReference],
    ) -> TargetType:
        if any(item.component_id == target_id for item in components):
            return TargetType.COMPONENT
        if any(item.group_id == target_id for item in groups):
            return TargetType.GROUP
        if any(item.relation_id == target_id for item in relations):
            return TargetType.RELATION
        if any(item.asset_id == target_id for item in assets):
            return TargetType.ASSET
        return TargetType.FIGURE

    @staticmethod
    def _review(
        figure_id: str,
        sequence: int,
        target_type: TargetType,
        target_id: str,
        reason: ReviewReason,
        severity: Severity,
        evidence_refs: tuple[str, ...],
        actions: tuple[str, ...],
        representations: tuple[RepresentationType, ...],
        producer: str,
    ) -> ReviewItem:
        return ReviewItem(
            review_id=make_stable_id(figure_id, "analysis-review", sequence, f"{target_id}|{reason.value}"),
            target_type=target_type,
            target_id=target_id,
            reason_type=reason,
            severity=severity,
            evidence_refs=evidence_refs,
            candidate_actions=actions,
            candidate_representations=representations,
            status=ReviewStatus.PENDING,
            disposition=ReviewDisposition.PENDING,
            created_by=producer,
            created_at="run:phase5c:0000",
        )

    @staticmethod
    def _deduplicate_reviews(reviews: Iterable[ReviewItem]) -> tuple[ReviewItem, ...]:
        seen: set[tuple[str, ReviewReason]] = set()
        result: list[ReviewItem] = []
        for review in reviews:
            key = (review.target_id, review.reason_type)
            if key not in seen:
                seen.add(key)
                result.append(review)
        return tuple(result)

    @staticmethod
    def _analysis_cost(receipt: AnalysisRunReceipt | None, *, roi: bool = False) -> CostMetrics:
        if receipt is None:
            return CostMetrics(
                analysis_calls=0,
                vision_calls=0,
                input_tokens="NOT_EXPOSED",
                output_tokens="NOT_EXPOSED",
                imagegen_calls=0,
                renderer_calls=0,
                external_backend_calls=0,
                full_image_reviews=0,
                roi_reviews=0,
                manual_corrections=0,
                wall_time_ms="NOT_EXPOSED",
            )
        actual_call = 0 if receipt.cache_hit else 1
        return CostMetrics(
            analysis_calls=actual_call,
            vision_calls=actual_call,
            input_tokens=receipt.input_tokens,
            output_tokens=receipt.output_tokens,
            imagegen_calls=0,
            renderer_calls=0,
            external_backend_calls=0,
            full_image_reviews=0,
            roi_reviews=actual_call if roi else 0,
            manual_corrections=0,
            wall_time_ms=receipt.wall_time_ms,
        )

    @staticmethod
    def _sum_cost(left, right):
        if isinstance(left, (int, float)) and not isinstance(left, bool) and isinstance(right, (int, float)) and not isinstance(right, bool):
            return left + right
        return "NOT_EXPOSED"

    @staticmethod
    def _slug(value: str) -> str:
        return re.sub(r"[^a-z0-9]+", "_", value.strip().lower()).strip("_")

    @staticmethod
    def _float(value: str | None, default: float) -> float:
        try:
            return float(value) if value is not None else default
        except ValueError:
            return default
