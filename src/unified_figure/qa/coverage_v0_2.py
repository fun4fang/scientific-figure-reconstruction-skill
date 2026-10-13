from __future__ import annotations

from dataclasses import asdict, dataclass
from hashlib import sha256
import json

from unified_figure.analysis.proposal import AnalysisProposal
from unified_figure.analysis.validator_v0_2 import ProposalValidationResult
from unified_figure.model.enums import (
    ComponentType,
    CoverageDimension,
    CoverageStatus,
    RealizationStatus,
    ReviewStatus,
    TargetType,
)
from unified_figure.model.figure import CanonicalFigure


COVERAGE_RECONCILIATION_POLICY_VERSION = "COVERAGE_RECONCILIATION_V0_2"


@dataclass(frozen=True, slots=True)
class CoverageReconciliationRecord:
    dimension: CoverageDimension
    observed_state: CoverageStatus
    reconciled_state: CoverageStatus
    reason: str
    evidence_refs: tuple[str, ...]
    confidence: str
    authority_source: str = "SYSTEM_RECONCILED_COVERAGE"


@dataclass(frozen=True, slots=True)
class CoverageReconciliationResult:
    records: tuple[CoverageReconciliationRecord, ...]
    policy_version: str = COVERAGE_RECONCILIATION_POLICY_VERSION

    def policy_hash(self) -> str:
        payload = {
            "policy_version": self.policy_version,
            "principle": "conservative-downgrade-only;never-invent-present",
            "inputs": [
                "observed-coverage",
                "proposal-inventory",
                "canonical-inventory",
                "quarantined-and-rejected-inventory",
                "group-and-object-review-state",
                "renderer-realization-state-when-available",
            ],
        }
        return "sha256:" + sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()

    def evidence_hash(self) -> str:
        payload = json.dumps(
            [asdict(item) for item in self.records],
            default=lambda item: item.value,
            sort_keys=True,
            separators=(",", ":"),
        )
        return "sha256:" + sha256(payload.encode("utf-8")).hexdigest()


class CoverageReconcilerV0_2:
    """Conservatively reconcile untrusted observations with accepted inventory."""

    def reconcile(
        self,
        *,
        proposal: AnalysisProposal,
        figure: CanonicalFigure,
        validation: ProposalValidationResult,
        evidence_refs: tuple[str, ...],
    ) -> CoverageReconciliationResult:
        observed = {
            item.dimension: item
            for item in proposal.coverage_observations
            if item.dimension in {dimension.value for dimension in CoverageDimension}
        }
        proposed_counts = self._proposal_counts(proposal)
        accepted_counts = self._accepted_counts(figure)
        affected_counts: dict[CoverageDimension, int] = {
            dimension: 0 for dimension in CoverageDimension
        }
        for item in (*validation.quarantined_items, *validation.rejected_items):
            dimension = self._dimension_for_kind(item.item.kind, item.raw_item_json)
            if dimension is not None:
                affected_counts[dimension] += 1
        for dimension in self._review_affected_dimensions(figure):
            affected_counts[dimension] += 1
        for dimension in self._realization_affected_dimensions(figure):
            affected_counts[dimension] += 1

        records: list[CoverageReconciliationRecord] = []
        for dimension in CoverageDimension:
            observation = observed.get(dimension.value)
            try:
                observed_state = (
                    CoverageStatus(observation.status)
                    if observation is not None
                    else CoverageStatus.UNKNOWN
                )
            except ValueError:
                observed_state = CoverageStatus.UNKNOWN
            accepted = accepted_counts[dimension]
            proposed = proposed_counts[dimension]
            affected = affected_counts[dimension]
            reconciled, reason, confidence = self._reconcile_dimension(
                observed_state=observed_state,
                proposed_count=proposed,
                accepted_count=accepted,
                affected_count=affected,
            )
            records.append(
                CoverageReconciliationRecord(
                    dimension=dimension,
                    observed_state=observed_state,
                    reconciled_state=reconciled,
                    reason=reason,
                    evidence_refs=evidence_refs,
                    confidence=confidence,
                )
            )
        return CoverageReconciliationResult(tuple(records))

    @staticmethod
    def _reconcile_dimension(
        *,
        observed_state: CoverageStatus,
        proposed_count: int,
        accepted_count: int,
        affected_count: int,
    ) -> tuple[CoverageStatus, str, str]:
        if affected_count:
            if accepted_count:
                return (
                    CoverageStatus.PARTIAL,
                    f"{affected_count} system issue(s) affect the dimension; {accepted_count} accepted",
                    "HIGH",
                )
            return (
                CoverageStatus.UNKNOWN,
                f"{affected_count} system issue(s) affect a dimension without accepted inventory",
                "HIGH",
            )
        if observed_state is CoverageStatus.PRESENT:
            if proposed_count == 0 and accepted_count == 0:
                return (
                    CoverageStatus.UNKNOWN,
                    "model claimed PRESENT without inventory or authoritative expected-count evidence",
                    "MEDIUM",
                )
            if accepted_count < proposed_count:
                return (
                    CoverageStatus.PARTIAL,
                    f"accepted inventory {accepted_count} is below observed proposal inventory {proposed_count}",
                    "HIGH",
                )
            return CoverageStatus.PRESENT, "observed state is consistent with accepted inventory", "MEDIUM"
        if observed_state is CoverageStatus.MISSING and accepted_count:
            return (
                CoverageStatus.UNKNOWN,
                "model MISSING conflicts with accepted canonical inventory",
                "HIGH",
            )
        if observed_state is CoverageStatus.UNKNOWN:
            return CoverageStatus.UNKNOWN, "no authority supports an optimistic upgrade", "HIGH"
        return observed_state, "conservative observed state retained", "MEDIUM"

    @staticmethod
    def _proposal_counts(proposal: AnalysisProposal) -> dict[CoverageDimension, int]:
        return {
            CoverageDimension.TEXT_COVERAGE: sum(
                item.proposed_type == ComponentType.TEXT.value
                for item in proposal.component_proposals
            ),
            CoverageDimension.STRUCTURAL_OBJECT_COVERAGE: sum(
                item.proposed_type
                in {
                    ComponentType.SIMPLE_SHAPE.value,
                    ComponentType.RELATION_ANCHOR.value,
                }
                for item in proposal.component_proposals
            ),
            CoverageDimension.RELATION_COVERAGE: len(proposal.relation_proposals),
            CoverageDimension.COMPLEX_ASSET_COVERAGE: max(
                len(proposal.asset_proposals),
                sum(
                    item.proposed_type
                    in {ComponentType.COMPLEX_VISUAL.value, ComponentType.IMAGE_ASSET.value}
                    for item in proposal.component_proposals
                ),
            ),
            CoverageDimension.CHART_COVERAGE: max(
                len(proposal.chart_proposals),
                sum(
                    item.proposed_type == ComponentType.CHART.value
                    for item in proposal.component_proposals
                ),
            ),
            CoverageDimension.PANEL_COVERAGE: max(
                len(proposal.panels),
                sum(
                    item.proposed_type == ComponentType.PANEL.value
                    for item in proposal.component_proposals
                ),
            ),
        }

    @staticmethod
    def _accepted_counts(figure: CanonicalFigure) -> dict[CoverageDimension, int]:
        return {
            CoverageDimension.TEXT_COVERAGE: sum(
                item.component_type is ComponentType.TEXT for item in figure.components
            ),
            CoverageDimension.STRUCTURAL_OBJECT_COVERAGE: sum(
                item.component_type in {ComponentType.SIMPLE_SHAPE, ComponentType.RELATION_ANCHOR}
                for item in figure.components
            ),
            CoverageDimension.RELATION_COVERAGE: len(figure.relations),
            CoverageDimension.COMPLEX_ASSET_COVERAGE: max(
                len(figure.assets),
                sum(
                    item.component_type in {ComponentType.COMPLEX_VISUAL, ComponentType.IMAGE_ASSET}
                    for item in figure.components
                ),
            ),
            CoverageDimension.CHART_COVERAGE: sum(
                item.component_type is ComponentType.CHART for item in figure.components
            ),
            CoverageDimension.PANEL_COVERAGE: sum(
                item.component_type is ComponentType.PANEL for item in figure.components
            ),
        }

    @staticmethod
    def _dimension_for_kind(kind: str, raw_item_json: str) -> CoverageDimension | None:
        if kind == "relation":
            return CoverageDimension.RELATION_COVERAGE
        if kind == "asset":
            return CoverageDimension.COMPLEX_ASSET_COVERAGE
        if kind == "chart":
            return CoverageDimension.CHART_COVERAGE
        if kind == "panel":
            return CoverageDimension.PANEL_COVERAGE
        if kind == "component":
            try:
                component_type = json.loads(raw_item_json).get("proposed_type")
            except (json.JSONDecodeError, AttributeError):
                return CoverageDimension.STRUCTURAL_OBJECT_COVERAGE
            if component_type == ComponentType.TEXT.value:
                return CoverageDimension.TEXT_COVERAGE
            if component_type == ComponentType.CHART.value:
                return CoverageDimension.CHART_COVERAGE
            if component_type in {ComponentType.COMPLEX_VISUAL.value, ComponentType.IMAGE_ASSET.value}:
                return CoverageDimension.COMPLEX_ASSET_COVERAGE
            if component_type == ComponentType.PANEL.value:
                return CoverageDimension.PANEL_COVERAGE
            return CoverageDimension.STRUCTURAL_OBJECT_COVERAGE
        return None

    @staticmethod
    def _review_affected_dimensions(figure: CanonicalFigure) -> set[CoverageDimension]:
        pending = {
            item.target_id: item.target_type
            for item in figure.review_items
            if item.status in {ReviewStatus.PENDING, ReviewStatus.REQUIRED}
        }
        dimensions: set[CoverageDimension] = set()
        relation_ids = {item.relation_id for item in figure.relations}
        asset_ids = {item.asset_id for item in figure.assets}
        group_ids = {item.group_id for item in figure.groups}
        coverage_by_id = {item.coverage_id: item.dimension for item in figure.coverage}
        components = {item.component_id: item for item in figure.components}
        for target_id, target_type in pending.items():
            if target_type is TargetType.COVERAGE and target_id in coverage_by_id:
                dimensions.add(coverage_by_id[target_id])
            elif target_type is TargetType.RELATION or target_id in relation_ids:
                dimensions.add(CoverageDimension.RELATION_COVERAGE)
            elif target_type is TargetType.GROUP or target_id in group_ids:
                dimensions.add(CoverageDimension.STRUCTURAL_OBJECT_COVERAGE)
            elif target_type is TargetType.ASSET or target_id in asset_ids:
                dimensions.add(CoverageDimension.COMPLEX_ASSET_COVERAGE)
            elif target_id in components:
                component = components[target_id]
                dimensions.add(CoverageReconcilerV0_2._dimension_for_component(component.component_type))
        return dimensions

    @staticmethod
    def _realization_affected_dimensions(figure: CanonicalFigure) -> set[CoverageDimension]:
        affected_ids = {
            item.canonical_object_id
            for item in figure.renderer_realizations
            if item.status in {RealizationStatus.UNSUPPORTED, RealizationStatus.FAILED}
        }
        dimensions: set[CoverageDimension] = set()
        relations = {item.relation_id for item in figure.relations}
        components = {item.component_id: item for item in figure.components}
        for target_id in affected_ids:
            if target_id in relations:
                dimensions.add(CoverageDimension.RELATION_COVERAGE)
            elif target_id in components:
                dimensions.add(
                    CoverageReconcilerV0_2._dimension_for_component(
                        components[target_id].component_type
                    )
                )
        return dimensions

    @staticmethod
    def _dimension_for_component(component_type: ComponentType) -> CoverageDimension:
        if component_type is ComponentType.TEXT:
            return CoverageDimension.TEXT_COVERAGE
        if component_type is ComponentType.CHART:
            return CoverageDimension.CHART_COVERAGE
        if component_type in {ComponentType.COMPLEX_VISUAL, ComponentType.IMAGE_ASSET}:
            return CoverageDimension.COMPLEX_ASSET_COVERAGE
        if component_type is ComponentType.PANEL:
            return CoverageDimension.PANEL_COVERAGE
        return CoverageDimension.STRUCTURAL_OBJECT_COVERAGE


def coverage_reconciliation_policy_hash_v0_2() -> str:
    return CoverageReconciliationResult(()).policy_hash()
