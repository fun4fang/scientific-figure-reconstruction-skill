from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from hashlib import sha256
import json
from pathlib import Path

from unified_figure.model.enums import (
    CoverageStatus,
    EvidenceSourceType,
    FailureReason,
    FailureState,
    ReconstructionStatus,
    ReviewDisposition,
    ReviewReason,
    ReviewStatus,
    Severity,
    TargetType,
)
from unified_figure.model.figure import CanonicalFigure, make_stable_id
from unified_figure.model.geometry import AuthoritativeInputGeometry
from unified_figure.model.records import EvidenceRecord, FailClosedRecord, ReviewItem
from unified_figure.qa.coverage_v0_2 import (
    CoverageReconcilerV0_2,
    CoverageReconciliationRecord,
)

from .canonicalizer import (
    CanonicalizationResult,
    ProposalCanonicalizer,
)
from .contracts import AuthoritativeSourceReference
from .proposal import AnalysisProposal
from .provider import AnalysisRunReceipt
from .validator import ProposalValidationResult as LegacyProposalValidationResult
from .validator_v0_2 import (
    AnalysisProposalValidatorV0_2,
    ProposalReviewCandidateV0_2,
    ProposalValidationResult,
)


STABILIZED_CANONICALIZATION_POLICY_VERSION = "proposal-canonicalization-v0.2"


@dataclass(frozen=True, slots=True)
class StabilizedCanonicalizationReceipt:
    receipt_version: str
    figure_id: str
    source_hash: str
    original_proposal_hash: str
    normalized_proposal_hash: str
    validation_policy_hash: str
    accepted_item_count: int
    quarantined_item_count: int
    rejected_item_count: int
    dependency_impact_count: int
    id_remap_count: int
    coverage_records: tuple[CoverageReconciliationRecord, ...]
    canonical_state_hash: str

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class StabilizedCanonicalizationResult:
    figure: CanonicalFigure
    validation: ProposalValidationResult
    base_result: CanonicalizationResult
    receipt: StabilizedCanonicalizationReceipt
    original_proposal: AnalysisProposal

    @property
    def proposal_to_canonical(self) -> tuple[tuple[str, str], ...]:
        return self.base_result.proposal_to_canonical

    def canonical_id_for(self, proposal_id: str) -> str | None:
        return self.base_result.canonical_id_for(proposal_id)


class StabilizedProposalCanonicalizerV0_2:
    """V0.2 recovery layer over the frozen canonical schema and v0.1 builder."""

    def __init__(
        self,
        *,
        repository_root: str | Path,
        asset_output_dir: str | Path,
        validator: AnalysisProposalValidatorV0_2 | None = None,
        coverage_reconciler: CoverageReconcilerV0_2 | None = None,
    ) -> None:
        self.validator = validator or AnalysisProposalValidatorV0_2()
        self.coverage_reconciler = coverage_reconciler or CoverageReconcilerV0_2()
        self.base = ProposalCanonicalizer(
            repository_root=repository_root,
            asset_output_dir=asset_output_dir,
        )

    def canonicalize(
        self,
        *,
        figure_id: str,
        source: AuthoritativeSourceReference,
        authoritative_geometry: AuthoritativeInputGeometry,
        proposal: AnalysisProposal,
        analysis_receipt: AnalysisRunReceipt | None = None,
        write_receipt_to: str | Path | None = None,
    ) -> StabilizedCanonicalizationResult:
        authoritative_geometry.validate()
        validation = self.validator.validate(
            proposal,
            authoritative_width=authoritative_geometry.decoded_width_px,
            authoritative_height=authoritative_geometry.decoded_height_px,
        )
        if validation.fatal or validation.normalized_proposal is None:
            codes = ",".join(item.code for item in validation.fatal_errors)
            raise ValueError(f"fatal AnalysisProposal v0.2 validation failure: {codes}")

        normalized = validation.normalized_proposal
        base_result = self.base.canonicalize(
            figure_id=figure_id,
            source=source,
            authoritative_geometry=authoritative_geometry,
            proposal=normalized,
            analysis_receipt=analysis_receipt,
            validation=LegacyProposalValidationResult(True, (), ()),
        )
        figure = base_result.figure
        validation_payload = json.dumps(
            {
                "accepted": [asdict(item) for item in validation.accepted_items],
                "quarantined": [asdict(item) for item in validation.quarantined_items],
                "rejected": [asdict(item) for item in validation.rejected_items],
                "impacts": [asdict(item) for item in validation.dependency_impacts],
                "remaps": [asdict(item) for item in validation.id_remaps],
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        recovery_evidence = EvidenceRecord(
            evidence_id=make_stable_id(
                figure_id,
                "proposal-recovery-evidence",
                0,
                validation.policy_hash(),
            ),
            source_type=EvidenceSourceType.REVISION,
            source_hash="sha256:" + sha256(validation_payload.encode("utf-8")).hexdigest(),
            producer=STABILIZED_CANONICALIZATION_POLICY_VERSION,
            operation="quarantine-localize-and-preserve-valid-proposal-items",
            input_refs=tuple(item.evidence_id for item in figure.evidence),
            output_refs=tuple(
                item.item.normalized_id
                for item in (*validation.quarantined_items, *validation.rejected_items)
            ),
            run_index=0,
            tool_runtime_info={
                "validation_policy_hash": validation.policy_hash(),
                "model_calls": "0",
            },
            notes="Raw invalid items and dependency impacts are preserved outside canonical assertions.",
        )
        evidence = (*figure.evidence, recovery_evidence)
        mapping = dict(base_result.proposal_to_canonical)
        reviews = list(figure.review_items)
        failures = list(figure.failures)

        for candidate in validation.review_candidates:
            target_id, target_type = self._resolve_review_target(
                candidate,
                figure,
                mapping,
                validation,
            )
            reason = self._review_reason(candidate.reason_code)
            if not any(
                item.target_id == target_id and item.reason_type is reason
                for item in reviews
            ):
                reviews.append(
                    ReviewItem(
                        review_id=make_stable_id(
                            figure_id,
                            "stabilization-review",
                            len(reviews),
                            f"{target_id}|{reason.value}|{candidate.target_id}",
                        ),
                        target_type=target_type,
                        target_id=target_id,
                        reason_type=reason,
                        severity=Severity.ERROR,
                        evidence_refs=(recovery_evidence.evidence_id,),
                        candidate_actions=candidate.candidate_actions,
                        candidate_representations=(),
                        status=ReviewStatus.PENDING,
                        disposition=ReviewDisposition.PENDING,
                        created_by=STABILIZED_CANONICALIZATION_POLICY_VERSION,
                        created_at="run:phase5f:0000",
                    )
                )

        for item in (*validation.quarantined_items, *validation.rejected_items):
            candidate = ProposalReviewCandidateV0_2(
                item.item.kind,
                item.item.normalized_id,
                item.reason_codes[0],
                ("inspect preserved proposal evidence",),
                item.evidence_ref,
            )
            target_id, _ = self._resolve_review_target(
                candidate,
                figure,
                mapping,
                validation,
            )
            failures.append(
                FailClosedRecord(
                    failure_id=make_stable_id(
                        figure_id,
                        "quarantined-proposal-item",
                        len(failures),
                        item.item.normalized_id,
                    ),
                    reason=(
                        FailureReason.UNCERTAIN_RELATION
                        if item.item.kind == "relation"
                        else FailureReason.EXTRACTION_LIMITATION
                    ),
                    target_id=target_id,
                    message=(
                        f"{item.item.kind} {item.item.original_id!r} was not asserted: "
                        + ",".join(item.reason_codes)
                    ),
                    recoverable=True,
                    reviewable=True,
                    fallback_eligible=False,
                    state=FailureState.REVIEW_REQUIRED,
                    evidence_refs=(recovery_evidence.evidence_id,),
                )
            )

        provisional = replace(
            figure,
            evidence=evidence,
            review_items=tuple(self._deduplicate_reviews(reviews)),
            failures=tuple(failures),
            revision=replace(
                figure.revision,
                canonical_state_hash="",
                change_manifest=(
                    *figure.revision.change_manifest,
                    "apply item-level proposal quarantine and dependency localization",
                ),
                evidence_refs=tuple(
                    dict.fromkeys(
                        (*figure.revision.evidence_refs, recovery_evidence.evidence_id)
                    )
                ),
            ),
            reconstruction_status=(
                ReconstructionStatus.REVIEW_REQUIRED
                if reviews or failures
                else ReconstructionStatus.IN_PROGRESS
            ),
        )
        reconciliation = self.coverage_reconciler.reconcile(
            proposal=proposal,
            figure=provisional,
            validation=validation,
            evidence_refs=(recovery_evidence.evidence_id,),
        )
        coverage_evidence = EvidenceRecord(
            evidence_id=make_stable_id(
                figure_id,
                "coverage-reconciliation-evidence",
                0,
                reconciliation.policy_hash(),
            ),
            source_type=EvidenceSourceType.REVISION,
            source_hash=reconciliation.evidence_hash(),
            producer=reconciliation.policy_version,
            operation="reconcile-observed-and-system-coverage",
            input_refs=(recovery_evidence.evidence_id,),
            output_refs=tuple(
                f"{item.dimension.value}={item.reconciled_state.value}"
                for item in reconciliation.records
            ),
            run_index=0,
            tool_runtime_info={"authority": "SYSTEM_RECONCILED_COVERAGE"},
            notes="Completion consumes reconciled coverage; model observations remain evidence only.",
        )
        records_by_dimension = {item.dimension: item for item in reconciliation.records}
        coverage = tuple(
            replace(
                item,
                status=records_by_dimension[item.dimension].reconciled_state,
                evidence_refs=tuple(
                    dict.fromkeys((*item.evidence_refs, coverage_evidence.evidence_id))
                ),
                review_status=(
                    ReviewStatus.PENDING
                    if records_by_dimension[item.dimension].reconciled_state
                    in {
                        CoverageStatus.MISSING,
                        CoverageStatus.ALTERED,
                        CoverageStatus.UNKNOWN,
                    }
                    else item.review_status
                ),
                notes=(
                    f"observed={records_by_dimension[item.dimension].observed_state.value}; "
                    f"reconciled={records_by_dimension[item.dimension].reconciled_state.value}; "
                    f"{records_by_dimension[item.dimension].reason}"
                ),
            )
            for item in provisional.coverage
        )
        final = replace(
            provisional,
            coverage=coverage,
            evidence=(*provisional.evidence, coverage_evidence),
            revision=replace(
                provisional.revision,
                canonical_state_hash="",
                change_manifest=(
                    *provisional.revision.change_manifest,
                    "replace observed coverage authority with deterministic reconciliation",
                ),
                evidence_refs=tuple(
                    dict.fromkeys(
                        (*provisional.revision.evidence_refs, coverage_evidence.evidence_id)
                    )
                ),
            ),
        ).with_computed_revision_hash()
        final.validate()
        receipt = StabilizedCanonicalizationReceipt(
            receipt_version="phase5f-stabilized-canonicalization-v0.2",
            figure_id=figure_id,
            source_hash=source.source_hash,
            original_proposal_hash=proposal.content_hash(),
            normalized_proposal_hash=normalized.content_hash(),
            validation_policy_hash=validation.policy_hash(),
            accepted_item_count=len(validation.accepted_items),
            quarantined_item_count=len(validation.quarantined_items),
            rejected_item_count=len(validation.rejected_items),
            dependency_impact_count=len(validation.dependency_impacts),
            id_remap_count=len(validation.id_remaps),
            coverage_records=reconciliation.records,
            canonical_state_hash=final.state_hash(),
        )
        if write_receipt_to is not None:
            Path(write_receipt_to).write_text(
                json.dumps(receipt.to_dict(), default=lambda item: item.value, indent=2, sort_keys=True)
                + "\n",
                encoding="utf-8",
            )
        return StabilizedCanonicalizationResult(
            final,
            validation,
            base_result,
            receipt,
            proposal,
        )

    @staticmethod
    def _resolve_review_target(
        candidate: ProposalReviewCandidateV0_2,
        figure: CanonicalFigure,
        mapping: dict[str, str],
        validation: ProposalValidationResult,
    ) -> tuple[str, TargetType]:
        target = mapping.get(candidate.target_id)
        if target is None:
            impacts = [
                item
                for item in validation.dependency_impacts
                if item.dependent_id == candidate.target_id
            ]
            for impact in impacts:
                target = mapping.get(impact.dependency_id)
                if target is not None:
                    break
        if target is None:
            return figure.figure_id, TargetType.FIGURE
        if any(item.component_id == target for item in figure.components):
            return target, TargetType.COMPONENT
        if any(item.group_id == target for item in figure.groups):
            return target, TargetType.GROUP
        if any(item.relation_id == target for item in figure.relations):
            return target, TargetType.RELATION
        if any(item.asset_id == target for item in figure.assets):
            return target, TargetType.ASSET
        return figure.figure_id, TargetType.FIGURE

    @staticmethod
    def _review_reason(code: str) -> ReviewReason:
        if "RELATION" in code:
            return ReviewReason.UNCERTAIN_RELATION
        if "GROUP" in code:
            return ReviewReason.UNCERTAIN_GROUPING
        if "ASSET" in code or "CHART" in code:
            return ReviewReason.REPRESENTATION_AMBIGUITY
        if "GEOMETRY" in code or "BOUND" in code or "SIZE" in code:
            return ReviewReason.GEOMETRY_CONFLICT
        return ReviewReason.EXTRACTION_LIMITATION

    @staticmethod
    def _deduplicate_reviews(reviews: list[ReviewItem]) -> list[ReviewItem]:
        seen: set[tuple[str, ReviewReason]] = set()
        result: list[ReviewItem] = []
        for item in reviews:
            key = (item.target_id, item.reason_type)
            if key not in seen:
                seen.add(key)
                result.append(item)
        return result
