from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import TYPE_CHECKING

from unified_figure.model.enums import AnalysisMode, FailureReason, FailureState
from unified_figure.model.figure import make_stable_id
from unified_figure.model.geometry import AuthoritativeInputGeometry, BBox, ModelValidationError, require_nonempty
from unified_figure.model.records import (
    Component,
    CoverageRecord,
    EvidenceRecord,
    FailClosedRecord,
    Relation,
    ReviewItem,
    SemanticGroup,
)

if TYPE_CHECKING:
    from .proposal import AnalysisProposal


@dataclass(frozen=True, slots=True)
class AuthoritativeSourceReference:
    source_id: str
    source_hash: str
    repository_relative_ref: str

    def validate(self) -> None:
        require_nonempty(self.source_id, "source_id")
        require_nonempty(self.source_hash, "source_hash")
        require_nonempty(self.repository_relative_ref, "repository_relative_ref")
        normalized = self.repository_relative_ref.replace("\\", "/")
        if normalized.startswith("/") or ":/" in normalized:
            raise ModelValidationError("source reference must be repository-relative")


@dataclass(frozen=True, slots=True)
class AnalysisPolicy:
    mode: AnalysisMode
    roi: BBox | None
    max_full_image_calls: int = 1
    max_roi_calls: int = 0
    linked_uncertainty_id: str | None = None
    linked_review_id: str | None = None
    target_proposal_ids: tuple[str, ...] = ()

    def validate(self) -> None:
        if self.max_full_image_calls < 0 or self.max_roi_calls < 0:
            raise ModelValidationError("analysis call budgets must be nonnegative")
        if self.mode is AnalysisMode.FULL_IMAGE and self.roi is not None:
            raise ModelValidationError("full-image analysis must not declare an ROI")
        if self.mode is AnalysisMode.ROI:
            if self.roi is None:
                raise ModelValidationError("ROI analysis requires an ROI")
            self.roi.validate()

    def validate_for_execution(self) -> None:
        self.validate()
        if self.mode is AnalysisMode.ROI and not (
            self.linked_uncertainty_id or self.linked_review_id
        ):
            raise ModelValidationError(
                "ROI execution must bind to an AnalysisUncertainty or ReviewItem"
            )
        if self.mode is AnalysisMode.FULL_IMAGE and (
            self.linked_uncertainty_id or self.linked_review_id or self.target_proposal_ids
        ):
            raise ModelValidationError("full-image analysis cannot declare ROI bindings")


@dataclass(frozen=True, slots=True)
class ProposedInputGeometry:
    width_px: int
    height_px: int
    source_hash: str


@dataclass(frozen=True, slots=True)
class CanonicalAnalysisProposal:
    proposal_id: str
    reported_geometry: ProposedInputGeometry | None
    components: tuple[Component, ...]
    groups: tuple[SemanticGroup, ...]
    relations: tuple[Relation, ...]
    coverage: tuple[CoverageRecord, ...]
    review_items: tuple[ReviewItem, ...]
    evidence: tuple[EvidenceRecord, ...]


class FigureAnalysisProvider(ABC):
    """Provider-neutral analysis contract; it has no authority over input geometry."""

    @abstractmethod
    def analyze(
        self,
        source: AuthoritativeSourceReference,
        input_geometry: AuthoritativeInputGeometry,
        policy: AnalysisPolicy,
    ) -> "AnalysisProposal":
        raise NotImplementedError


def preserve_authoritative_geometry(
    figure_id: str,
    authoritative: AuthoritativeInputGeometry,
    proposal: CanonicalAnalysisProposal,
) -> tuple[AuthoritativeInputGeometry, FailClosedRecord | None]:
    """Keep deterministic geometry and record, rather than apply, conflicts."""
    authoritative.validate()
    reported = proposal.reported_geometry
    if reported is None:
        return authoritative, None
    conflicts = []
    if reported.width_px != authoritative.decoded_width_px:
        conflicts.append(
            f"analysis width {reported.width_px} != decoded width {authoritative.decoded_width_px}"
        )
    if reported.height_px != authoritative.decoded_height_px:
        conflicts.append(
            f"analysis height {reported.height_px} != decoded height {authoritative.decoded_height_px}"
        )
    if reported.source_hash != authoritative.source_hash:
        conflicts.append("analysis source hash differs from authoritative source hash")
    if not conflicts:
        return authoritative, None
    return authoritative, FailClosedRecord(
        failure_id=make_stable_id(figure_id, "geometry-conflict", 0, proposal.proposal_id),
        reason=FailureReason.INPUT_GEOMETRY_CONFLICT,
        target_id=figure_id,
        message="; ".join(conflicts),
        recoverable=True,
        reviewable=True,
        fallback_eligible=False,
        state=FailureState.REVIEW_REQUIRED,
        evidence_refs=tuple(item.evidence_id for item in proposal.evidence),
    )
