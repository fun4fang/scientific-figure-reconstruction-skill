from __future__ import annotations

from dataclasses import dataclass
from math import isfinite

from unified_figure.model.enums import (
    ComponentType,
    CoverageDimension,
    CoverageStatus,
    RepresentationType,
)

from .proposal import AnalysisProposal, ProposalBBox, PROPOSAL_VERSION


_CONFIDENCE = {"HIGH", "MEDIUM", "LOW"}
_UNCERTAINTY = {"NONE", "LOW", "MEDIUM", "HIGH", "AMBIGUOUS", "REVIEW_REQUIRED"}
_COORDINATES = {"SOURCE_PIXELS", "NORMALIZED"}


@dataclass(frozen=True, slots=True)
class ProposalValidationIssue:
    code: str
    severity: str
    target_proposal_id: str
    message: str
    blocks_item: bool
    blocks_proposal: bool = False


@dataclass(frozen=True, slots=True)
class ProposalValidationResult:
    valid: bool
    issues: tuple[ProposalValidationIssue, ...]
    rejected_proposal_ids: tuple[str, ...]

    @property
    def fatal(self) -> bool:
        return any(item.blocks_proposal for item in self.issues)


class AnalysisProposalValidator:
    """Fail-closed validation for untrusted model-facing observations."""

    def validate(
        self,
        proposal: AnalysisProposal,
        *,
        authoritative_width: int,
        authoritative_height: int,
    ) -> ProposalValidationResult:
        issues: list[ProposalValidationIssue] = []
        rejected: set[str] = set()

        def issue(
            code: str,
            target: str,
            message: str,
            *,
            severity: str = "ERROR",
            blocks_item: bool = True,
            blocks_proposal: bool = False,
        ) -> None:
            issues.append(
                ProposalValidationIssue(
                    code, severity, target, message, blocks_item, blocks_proposal
                )
            )
            if blocks_item and target:
                rejected.add(target)

        if proposal.proposal_version != PROPOSAL_VERSION:
            issue(
                "UNSUPPORTED_PROPOSAL_VERSION",
                "proposal",
                f"expected {PROPOSAL_VERSION}, got {proposal.proposal_version}",
                blocks_proposal=True,
            )
        if proposal.observed_canvas.coordinate_space not in _COORDINATES:
            issue(
                "INVALID_COORDINATE_SYSTEM",
                "observed_canvas",
                proposal.observed_canvas.coordinate_space,
                blocks_proposal=True,
            )
        if proposal.observed_canvas.width <= 0 or proposal.observed_canvas.height <= 0:
            issue(
                "INVALID_CANVAS",
                "observed_canvas",
                "observed canvas dimensions must be positive",
                blocks_proposal=True,
            )

        collections = (
            proposal.panels,
            proposal.component_proposals,
            proposal.group_proposals,
            proposal.relation_proposals,
            proposal.asset_proposals,
            proposal.chart_proposals,
        )
        ids: list[str] = []
        for collection in collections:
            for item in collection:
                proposal_id = getattr(item, "proposal_id", "")
                if not proposal_id.strip():
                    issue("MISSING_PROPOSAL_ID", "proposal", "proposal ID is empty", blocks_proposal=True)
                ids.append(proposal_id)
        duplicates = sorted({item for item in ids if ids.count(item) > 1})
        for duplicate in duplicates:
            issue(
                "DUPLICATE_PROPOSAL_ID",
                duplicate,
                "proposal IDs must be globally unique",
                blocks_proposal=True,
            )

        panel_ids = {item.proposal_id for item in proposal.panels}
        component_ids = {item.proposal_id for item in proposal.component_proposals}
        group_ids = {item.proposal_id for item in proposal.group_proposals}
        asset_ids = {item.proposal_id for item in proposal.asset_proposals}
        target_ids = panel_ids | component_ids | group_ids | {
            item.proposal_id for item in proposal.relation_proposals
        } | asset_ids | {item.proposal_id for item in proposal.chart_proposals}

        for panel in proposal.panels:
            self._validate_bbox(
                panel.bbox, panel.proposal_id, authoritative_width, authoritative_height, issue
            )
            self._validate_confidence(panel.confidence, panel.uncertainty, panel.proposal_id, issue)

        for component in proposal.component_proposals:
            self._validate_bbox(
                component.bbox,
                component.proposal_id,
                authoritative_width,
                authoritative_height,
                issue,
            )
            self._validate_bbox(
                component.evidence_region,
                component.proposal_id,
                authoritative_width,
                authoritative_height,
                issue,
            )
            self._validate_confidence(
                component.confidence, component.uncertainty, component.proposal_id, issue
            )
            if component.panel_proposal_id and component.panel_proposal_id not in panel_ids:
                issue(
                    "UNKNOWN_PANEL_REFERENCE",
                    component.proposal_id,
                    f"unknown panel {component.panel_proposal_id}",
                )
            if component.proposed_type not in {item.value for item in ComponentType}:
                issue(
                    "UNSUPPORTED_COMPONENT_VOCABULARY",
                    component.proposal_id,
                    component.proposed_type,
                    blocks_item=False,
                    severity="WARNING",
                )
            unknown_hints = sorted(
                set(component.representation_hints) - {item.value for item in RepresentationType}
            )
            if unknown_hints:
                issue(
                    "UNSUPPORTED_REPRESENTATION_HINT",
                    component.proposal_id,
                    ",".join(unknown_hints),
                    blocks_item=False,
                    severity="WARNING",
                )
            if component.asset_proposal_id and component.asset_proposal_id not in asset_ids:
                issue(
                    "MALFORMED_ASSET_PROPOSAL",
                    component.proposal_id,
                    f"unknown asset proposal {component.asset_proposal_id}",
                )

        for group in proposal.group_proposals:
            self._validate_bbox(
                group.evidence_region,
                group.proposal_id,
                authoritative_width,
                authoritative_height,
                issue,
            )
            self._validate_confidence(group.confidence, group.uncertainty, group.proposal_id, issue)
            if group.parent_group_proposal_id and group.parent_group_proposal_id not in group_ids:
                issue(
                    "INVALID_GROUP_MEMBERSHIP",
                    group.proposal_id,
                    f"unknown parent group {group.parent_group_proposal_id}",
                )
            missing = sorted(set(group.member_proposal_ids) - component_ids)
            if missing:
                issue(
                    "INVALID_GROUP_MEMBERSHIP",
                    group.proposal_id,
                    "unknown members: " + ",".join(missing),
                )

        for relation in proposal.relation_proposals:
            self._validate_bbox(
                relation.evidence_region,
                relation.proposal_id,
                authoritative_width,
                authoritative_height,
                issue,
            )
            self._validate_confidence(
                relation.confidence, relation.uncertainty, relation.proposal_id, issue
            )
            if not relation.source_candidate_id or relation.source_candidate_id not in component_ids:
                issue("DANGLING_RELATION_REFERENCE", relation.proposal_id, "missing/unknown source")
            if not relation.target_candidate_id or relation.target_candidate_id not in component_ids:
                issue("DANGLING_RELATION_REFERENCE", relation.proposal_id, "missing/unknown target")
            if relation.coordinate_space not in _COORDINATES:
                issue("INVALID_COORDINATE_SYSTEM", relation.proposal_id, relation.coordinate_space)
            for point in relation.route_points:
                if len(point) != 2 or not all(isfinite(float(value)) for value in point):
                    issue("IMPOSSIBLE_GEOMETRY", relation.proposal_id, "invalid route point")

        for asset in proposal.asset_proposals:
            self._validate_bbox(
                asset.source_region,
                asset.proposal_id,
                authoritative_width,
                authoritative_height,
                issue,
            )
            self._validate_confidence(asset.confidence, asset.uncertainty, asset.proposal_id, issue)
            if asset.component_proposal_id not in component_ids:
                issue(
                    "MALFORMED_ASSET_PROPOSAL",
                    asset.proposal_id,
                    f"unknown component {asset.component_proposal_id}",
                )
            if asset.source_type == "SOURCE_GENERATED":
                issue(
                    "SCIENTIFIC_GENERATION_RISK",
                    asset.proposal_id,
                    "analysis cannot authorize generated scientific content",
                    severity="WARNING",
                    blocks_item=False,
                )

        for chart in proposal.chart_proposals:
            self._validate_confidence(chart.confidence, chart.uncertainty, chart.proposal_id, issue)
            if chart.component_proposal_id not in component_ids:
                issue(
                    "MALFORMED_CHART_PROPOSAL",
                    chart.proposal_id,
                    f"unknown component {chart.component_proposal_id}",
                )
            if chart.proposed_values and not (
                chart.authoritative_data_available and chart.authoritative_data_ref
            ):
                issue(
                    "NONAUTHORITATIVE_CHART_VALUES",
                    chart.proposal_id,
                    "pixel-derived values may not become authoritative chart data",
                    severity="WARNING",
                    blocks_item=False,
                )

        coverage_dimensions = [item.dimension for item in proposal.coverage_observations]
        for observation in proposal.coverage_observations:
            if observation.dimension not in {item.value for item in CoverageDimension}:
                issue(
                    "UNSUPPORTED_COVERAGE_VOCABULARY",
                    observation.dimension,
                    observation.dimension,
                    blocks_item=False,
                    severity="WARNING",
                )
            if observation.status not in {item.value for item in CoverageStatus}:
                issue(
                    "UNSUPPORTED_COVERAGE_VOCABULARY",
                    observation.dimension,
                    observation.status,
                    blocks_item=False,
                    severity="WARNING",
                )
            if observation.confidence not in _CONFIDENCE:
                issue(
                    "MISSING_CONFIDENCE",
                    observation.dimension,
                    observation.confidence,
                    blocks_item=False,
                )
            missing = sorted(set(observation.target_proposal_ids) - target_ids)
            if missing:
                issue(
                    "DANGLING_COVERAGE_REFERENCE",
                    observation.dimension,
                    ",".join(missing),
                    blocks_item=False,
                    severity="WARNING",
                )
        for dimension in CoverageDimension:
            if coverage_dimensions.count(dimension.value) != 1:
                issue(
                    "COVERAGE_DIMENSION_CARDINALITY",
                    dimension.value,
                    "each coverage dimension must appear exactly once",
                    blocks_item=False,
                    severity="WARNING",
                )

        uncertainty_ids: set[str] = set()
        for uncertainty in proposal.uncertainties:
            if uncertainty.uncertainty_id in uncertainty_ids:
                issue(
                    "DUPLICATE_UNCERTAINTY_ID",
                    uncertainty.uncertainty_id,
                    "duplicate uncertainty ID",
                    blocks_item=False,
                )
            uncertainty_ids.add(uncertainty.uncertainty_id)
            if uncertainty.target_proposal_id not in target_ids:
                issue(
                    "DANGLING_UNCERTAINTY_REFERENCE",
                    uncertainty.uncertainty_id,
                    uncertainty.target_proposal_id,
                    blocks_item=False,
                )
            self._validate_bbox(
                uncertainty.region,
                uncertainty.uncertainty_id,
                authoritative_width,
                authoritative_height,
                issue,
            )
        for candidate in proposal.review_candidates:
            if candidate.target_proposal_id not in target_ids:
                issue(
                    "DANGLING_REVIEW_REFERENCE",
                    candidate.candidate_id,
                    candidate.target_proposal_id,
                    blocks_item=False,
                )

        ordered = tuple(
            sorted(issues, key=lambda item: (item.target_proposal_id, item.code, item.message))
        )
        return ProposalValidationResult(
            valid=not any(item.severity in {"ERROR", "CRITICAL"} for item in ordered),
            issues=ordered,
            rejected_proposal_ids=tuple(sorted(rejected)),
        )

    @staticmethod
    def _validate_confidence(confidence: str, uncertainty: str, target: str, issue) -> None:
        if confidence not in _CONFIDENCE:
            issue("MISSING_CONFIDENCE", target, confidence)
        if uncertainty not in _UNCERTAINTY:
            issue("MISSING_UNCERTAINTY", target, uncertainty)

    @staticmethod
    def _validate_bbox(
        bbox: ProposalBBox,
        target: str,
        authoritative_width: int,
        authoritative_height: int,
        issue,
    ) -> None:
        values = (bbox.x, bbox.y, bbox.width, bbox.height)
        if not all(isfinite(float(value)) for value in values):
            issue("IMPOSSIBLE_GEOMETRY", target, "geometry contains a non-finite value")
            return
        if bbox.coordinate_space not in _COORDINATES:
            issue("INVALID_COORDINATE_SYSTEM", target, bbox.coordinate_space)
            return
        if bbox.width < 0 or bbox.height < 0:
            issue("NEGATIVE_BBOX", target, "negative width/height is forbidden")
            return
        limit_x = 1.0 if bbox.coordinate_space == "NORMALIZED" else float(authoritative_width)
        limit_y = 1.0 if bbox.coordinate_space == "NORMALIZED" else float(authoritative_height)
        epsilon = 1e-6
        if (
            bbox.x < -epsilon
            or bbox.y < -epsilon
            or bbox.x + bbox.width > limit_x + epsilon
            or bbox.y + bbox.height > limit_y + epsilon
        ):
            issue("OUT_OF_BOUNDS_GEOMETRY", target, "bbox exceeds authoritative canvas")
