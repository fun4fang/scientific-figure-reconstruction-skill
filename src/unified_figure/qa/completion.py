from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from unified_figure.adapters.contracts import AdapterRealizationResult, AdapterValidationResult
from unified_figure.model.enums import (
    CoverageStatus,
    FailureState,
    RealizationStatus,
    ReconstructionStatus,
    RepresentationType,
    ReviewStatus,
)
from unified_figure.model.figure import CanonicalFigure


class CompletionStatus(str, Enum):
    COMPLETE = "COMPLETE"
    COMPLETE_WITH_LIMITATIONS = "COMPLETE_WITH_LIMITATIONS"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"
    PARTIAL = "PARTIAL"
    FAILED = "FAILED"


@dataclass(frozen=True, slots=True)
class CompletionEvaluation:
    status: CompletionStatus
    canonical_status: ReconstructionStatus
    reasons: tuple[str, ...]


class CompletionEvaluator:
    """Coverage-aware, review-aware, renderer-aware completion policy."""

    def evaluate(
        self,
        figure: CanonicalFigure,
        result: AdapterRealizationResult | None,
        output_validation: AdapterValidationResult,
    ) -> CompletionEvaluation:
        reasons: list[str] = []
        try:
            figure.validate()
        except ValueError as exc:
            return CompletionEvaluation(
                CompletionStatus.FAILED,
                ReconstructionStatus.FAILED,
                (f"structural validation failed: {exc}",),
            )
        if not output_validation.valid:
            return CompletionEvaluation(
                CompletionStatus.FAILED,
                ReconstructionStatus.FAILED,
                tuple(output_validation.messages) or ("renderer output validation failed",),
            )
        if any(item.state in {FailureState.ACTIVE, FailureState.TERMINAL} for item in figure.failures):
            return CompletionEvaluation(
                CompletionStatus.FAILED,
                ReconstructionStatus.FAILED,
                ("active non-review fail-closed record",),
            )

        blocking_coverage = [
            item
            for item in figure.coverage
            if item.mandatory
            and item.status in {CoverageStatus.MISSING, CoverageStatus.ALTERED, CoverageStatus.UNKNOWN}
        ]
        unresolved_reviews = [
            item for item in figure.review_items if item.status in {ReviewStatus.REQUIRED, ReviewStatus.PENDING}
        ]
        review_components = [
            item
            for item in figure.components
            if item.representation in {RepresentationType.REVIEW_REQUIRED, RepresentationType.UNSUPPORTED}
        ]
        expected_ids = {
            item.component_id
            for item in figure.components
            if item.representation not in {RepresentationType.REVIEW_REQUIRED, RepresentationType.UNSUPPORTED}
        } | {
            item.relation_id
            for item in figure.relations
            if item.representation not in {RepresentationType.REVIEW_REQUIRED, RepresentationType.UNSUPPORTED}
        }
        realized_ids = (
            {
                item.canonical_object_id
                for item in result.realizations
                if item.status in {RealizationStatus.REALIZED, RealizationStatus.VALIDATED}
            }
            if result is not None
            else set()
        )
        unsupported_realization_ids = (
            {
                item.canonical_object_id
                for item in result.realizations
                if item.status is RealizationStatus.UNSUPPORTED
            }
            if result is not None
            else set()
        )
        # An explicit unsupported realization is a localized capability result,
        # not a missing mapping.  It must still prevent COMPLETE, but it must not
        # turn otherwise valid partial work into a global renderer failure.
        missing_realizations = expected_ids - realized_ids - unsupported_realization_ids

        if missing_realizations and not (unresolved_reviews or review_components):
            return CompletionEvaluation(
                CompletionStatus.FAILED,
                ReconstructionStatus.FAILED,
                ("missing renderer realizations: " + ",".join(sorted(missing_realizations)),),
            )
        if unresolved_reviews or review_components:
            if unresolved_reviews:
                reasons.append("unresolved localized review items")
            if review_components:
                reasons.append("review-required or unsupported representations remain")
            if blocking_coverage:
                reasons.append("mandatory coverage unresolved")
            return CompletionEvaluation(
                CompletionStatus.REVIEW_REQUIRED,
                ReconstructionStatus.REVIEW_REQUIRED,
                tuple(reasons),
            )
        if unsupported_realization_ids:
            reasons.append(
                "unsupported localized renderer realizations: "
                + ",".join(sorted(unsupported_realization_ids))
            )
            if blocking_coverage:
                reasons.append("mandatory coverage unresolved")
            return CompletionEvaluation(
                CompletionStatus.REVIEW_REQUIRED,
                ReconstructionStatus.REVIEW_REQUIRED,
                tuple(reasons),
            )
        if blocking_coverage:
            reasons.extend(
                f"{item.dimension.value}={item.status.value}" for item in blocking_coverage
            )
            return CompletionEvaluation(
                CompletionStatus.PARTIAL,
                ReconstructionStatus.COMPLETE_WITH_LIMITATIONS,
                tuple(reasons),
            )
        partial_coverage = [item for item in figure.coverage if item.mandatory and item.status is CoverageStatus.PARTIAL]
        warnings = tuple(result.warnings) if result is not None else ()
        if partial_coverage or warnings:
            reasons.extend(f"{item.dimension.value}=PARTIAL" for item in partial_coverage)
            reasons.extend(warnings)
            return CompletionEvaluation(
                CompletionStatus.COMPLETE_WITH_LIMITATIONS,
                ReconstructionStatus.COMPLETE_WITH_LIMITATIONS,
                tuple(reasons),
            )
        return CompletionEvaluation(CompletionStatus.COMPLETE, ReconstructionStatus.SUCCESS, ())


@dataclass(frozen=True, slots=True)
class SemanticParityResult:
    valid: bool
    messages: tuple[str, ...]


def validate_cross_backend_parity(
    left: CanonicalFigure,
    right: CanonicalFigure,
) -> SemanticParityResult:
    messages: list[str] = []
    left_components = {item.component_id for item in left.components}
    right_components = {item.component_id for item in right.components}
    if left_components != right_components:
        messages.append("canonical component coverage differs")
    if {item.relation_id for item in left.relations} != {item.relation_id for item in right.relations}:
        messages.append("canonical relation coverage differs")
    for label, figure in (("left", left), ("right", right)):
        mapped = {item.canonical_object_id for item in figure.renderer_realizations}
        expected = {
            item.component_id
            for item in figure.components
            if item.representation not in {RepresentationType.REVIEW_REQUIRED, RepresentationType.UNSUPPORTED}
        } | {
            item.relation_id
            for item in figure.relations
            if item.representation not in {RepresentationType.REVIEW_REQUIRED, RepresentationType.UNSUPPORTED}
        }
        if not expected.issubset(mapped):
            messages.append(f"{label} backend mapping is incomplete")
        asset_components = {item.component_id for item in figure.components if item.asset_ref}
        if not asset_components.issubset(mapped | {item.component_id for item in figure.components if item.representation is RepresentationType.REVIEW_REQUIRED}):
            messages.append(f"{label} backend asset presence is incomplete")
    if left.reconstruction_status != right.reconstruction_status:
        messages.append("completion status differs")
    left_pending = {item.target_id for item in left.review_items if item.status is ReviewStatus.PENDING}
    right_pending = {item.target_id for item in right.review_items if item.status is ReviewStatus.PENDING}
    if left_pending != right_pending:
        messages.append("review state differs")
    return SemanticParityResult(not messages, tuple(messages))
