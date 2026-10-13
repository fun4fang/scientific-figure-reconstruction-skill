from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, replace
from hashlib import sha256
import json
from math import isfinite
from typing import Any

from unified_figure.model.enums import ComponentType, CoverageDimension, CoverageStatus, RepresentationType
from unified_figure.serialization.canonical import to_primitive

from .geometry_policy_v0_2 import (
    DEFAULT_GEOMETRY_NORMALIZATION_POLICY_V0_2,
    GeometryNormalizationPolicyV0_2,
    GeometryNormalizationRecord,
)
from .proposal import AnalysisProposal, PROPOSAL_VERSION


PROPOSAL_VALIDATION_POLICY_VERSION_V0_2 = "proposal-validation-policy-v0.2"
PROPOSAL_QUARANTINE_POLICY_VERSION_V0_2 = "proposal-item-quarantine-v0.2"
DEPENDENCY_LOCALIZATION_POLICY_VERSION_V0_2 = "proposal-dependency-localization-v0.2"

_CONFIDENCE = {"HIGH", "MEDIUM", "LOW"}
_UNCERTAINTY = {"NONE", "LOW", "MEDIUM", "HIGH", "AMBIGUOUS", "REVIEW_REQUIRED"}


@dataclass(frozen=True, slots=True)
class ProposalItemRef:
    kind: str
    original_id: str
    normalized_id: str
    occurrence_index: int


@dataclass(frozen=True, slots=True)
class ProposalIdRemap:
    kind: str
    original_id: str
    normalized_id: str
    occurrence_index: int
    reason: str = "DUPLICATE_ID_REMAP"


@dataclass(frozen=True, slots=True)
class ProposalItemDisposition:
    item: ProposalItemRef
    reason_codes: tuple[str, ...]
    raw_item_json: str
    dependency_refs: tuple[str, ...]
    evidence_ref: str


@dataclass(frozen=True, slots=True)
class ProposalDependencyImpact:
    dependency_kind: str
    dependency_id: str
    dependent_kind: str
    dependent_id: str
    reason: str


@dataclass(frozen=True, slots=True)
class ProposalReviewCandidateV0_2:
    target_kind: str
    target_id: str
    reason_code: str
    candidate_actions: tuple[str, ...]
    evidence_ref: str


@dataclass(frozen=True, slots=True)
class ProposalValidationEvidence:
    evidence_ref: str
    code: str
    target_kind: str
    target_id: str
    message: str


@dataclass(frozen=True, slots=True)
class ProposalValidationResult:
    accepted_items: tuple[ProposalItemRef, ...]
    quarantined_items: tuple[ProposalItemDisposition, ...]
    rejected_items: tuple[ProposalItemDisposition, ...]
    dependency_impacts: tuple[ProposalDependencyImpact, ...]
    global_errors: tuple[ProposalValidationEvidence, ...]
    recoverable_errors: tuple[ProposalValidationEvidence, ...]
    fatal_errors: tuple[ProposalValidationEvidence, ...]
    review_candidates: tuple[ProposalReviewCandidateV0_2, ...]
    validation_evidence: tuple[ProposalValidationEvidence, ...]
    geometry_normalizations: tuple[GeometryNormalizationRecord, ...]
    id_remaps: tuple[ProposalIdRemap, ...]
    normalized_proposal: AnalysisProposal | None
    policy_version: str = PROPOSAL_VALIDATION_POLICY_VERSION_V0_2

    @property
    def fatal(self) -> bool:
        return bool(self.fatal_errors)

    @property
    def valid(self) -> bool:
        return not (
            self.fatal
            or self.global_errors
            or self.recoverable_errors
            or self.quarantined_items
            or self.rejected_items
        )

    @property
    def rejected_proposal_ids(self) -> tuple[str, ...]:
        return tuple(
            sorted(
                {
                    item.item.normalized_id
                    for item in (*self.quarantined_items, *self.rejected_items)
                }
            )
        )

    def policy_hash(self) -> str:
        payload = {
            "validation": self.policy_version,
            "quarantine": PROPOSAL_QUARANTINE_POLICY_VERSION_V0_2,
            "dependency": DEPENDENCY_LOCALIZATION_POLICY_VERSION_V0_2,
        }
        return "sha256:" + sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()


class AnalysisProposalValidatorV0_2:
    """Recover valid proposal items while preserving every rejected observation."""

    def __init__(
        self,
        geometry_policy: GeometryNormalizationPolicyV0_2 = DEFAULT_GEOMETRY_NORMALIZATION_POLICY_V0_2,
    ) -> None:
        self.geometry_policy = geometry_policy

    def validate(
        self,
        proposal: AnalysisProposal,
        *,
        authoritative_width: int,
        authoritative_height: int,
    ) -> ProposalValidationResult:
        evidence: list[ProposalValidationEvidence] = []
        global_errors: list[ProposalValidationEvidence] = []
        recoverable: list[ProposalValidationEvidence] = []
        fatal: list[ProposalValidationEvidence] = []
        impacts: list[ProposalDependencyImpact] = []
        reviews: list[ProposalReviewCandidateV0_2] = []
        geometry_records: list[GeometryNormalizationRecord] = []
        remaps: list[ProposalIdRemap] = []
        quarantined: list[ProposalItemDisposition] = []
        rejected: list[ProposalItemDisposition] = []

        def add_evidence(
            code: str,
            kind: str,
            target: str,
            message: str,
            *,
            scope: str = "RECOVERABLE",
        ) -> str:
            ref = "validation:" + sha256(
                f"{code}|{kind}|{target}|{message}".encode("utf-8")
            ).hexdigest()[:20]
            record = ProposalValidationEvidence(ref, code, kind, target, message)
            evidence.append(record)
            if scope == "GLOBAL":
                global_errors.append(record)
            elif scope == "FATAL":
                fatal.append(record)
            else:
                recoverable.append(record)
            return ref

        if proposal.proposal_version != PROPOSAL_VERSION:
            ref = add_evidence(
                "UNSUPPORTED_PROPOSAL_VERSION",
                "proposal",
                "proposal",
                f"expected {PROPOSAL_VERSION}, got {proposal.proposal_version}",
                scope="FATAL",
            )
            return ProposalValidationResult(
                (), (), (), (), (), (), tuple(fatal), (), tuple(evidence), (), (), None
            )
        if authoritative_width <= 0 or authoritative_height <= 0:
            add_evidence(
                "INVALID_AUTHORITATIVE_GEOMETRY",
                "proposal",
                "proposal",
                "authoritative dimensions must be positive",
                scope="FATAL",
            )
            return ProposalValidationResult(
                (), (), (), (), (), (), tuple(fatal), (), tuple(evidence), (), (), None
            )

        collections: tuple[tuple[str, tuple[Any, ...]], ...] = (
            ("panel", proposal.panels),
            ("component", proposal.component_proposals),
            ("group", proposal.group_proposals),
            ("relation", proposal.relation_proposals),
            ("asset", proposal.asset_proposals),
            ("chart", proposal.chart_proposals),
        )
        all_ids = [getattr(item, "proposal_id", "") for _, values in collections for item in values]
        counts = Counter(all_ids)
        references = self._all_references(proposal)
        ambiguous_ids = {item for item, count in counts.items() if count > 1 and item in references}
        occurrences: defaultdict[str, int] = defaultdict(int)
        originals: dict[tuple[str, int], Any] = {}
        normalized_collections: dict[str, list[Any]] = {kind: [] for kind, _ in collections}

        for kind, values in collections:
            for index, item in enumerate(values):
                originals[(kind, index)] = item
                original_id = getattr(item, "proposal_id", "")
                occurrence = occurrences[original_id]
                occurrences[original_id] += 1
                normalized_id = original_id
                if counts[original_id] > 1:
                    normalized_id = f"{kind}:{original_id}#{occurrence + 1}"
                    remaps.append(
                        ProposalIdRemap(kind, original_id, normalized_id, occurrence)
                    )
                    add_evidence(
                        "DUPLICATE_ID_REMAP",
                        kind,
                        normalized_id,
                        f"temporary ID {original_id!r} remapped deterministically",
                    )
                normalized_collections[kind].append(replace(item, proposal_id=normalized_id))

        disposition_by_id: dict[str, ProposalItemDisposition] = {}

        def dispose(
            *,
            kind: str,
            original: Any,
            normalized: Any,
            occurrence_index: int,
            reasons: tuple[str, ...],
            dependency_refs: tuple[str, ...] = (),
            hard_reject: bool = False,
        ) -> None:
            normalized_id = getattr(normalized, "proposal_id", "")
            ref = add_evidence(
                reasons[0],
                kind,
                normalized_id,
                "; ".join(reasons),
            )
            item_ref = ProposalItemRef(
                kind,
                getattr(original, "proposal_id", ""),
                normalized_id,
                occurrence_index,
            )
            record = ProposalItemDisposition(
                item_ref,
                reasons,
                json.dumps(to_primitive(original), sort_keys=True, separators=(",", ":")),
                dependency_refs,
                ref,
            )
            disposition_by_id[normalized_id] = record
            (rejected if hard_reject else quarantined).append(record)
            reviews.append(
                ProposalReviewCandidateV0_2(
                    kind,
                    normalized_id,
                    reasons[0],
                    (
                        "inspect preserved proposal evidence",
                        "resolve dependency or mark unsupported",
                    ),
                    ref,
                )
            )

        def normalize_geometry(kind: str, index: int, item: Any, fields: tuple[str, ...]) -> Any | None:
            current = item
            reasons: list[str] = []
            hard = False
            for field_name in fields:
                record = self.geometry_policy.normalize(
                    getattr(current, field_name),
                    target_id=getattr(current, "proposal_id", ""),
                    field_name=field_name,
                    authoritative_width=authoritative_width,
                    authoritative_height=authoritative_height,
                )
                geometry_records.append(record)
                if record.disposition == "NORMALIZED":
                    current = replace(current, **{field_name: record.normalized})
                    add_evidence(
                        record.reason,
                        kind,
                        getattr(current, "proposal_id", ""),
                        f"{field_name} clipped to authoritative canvas",
                    )
                elif record.normalized is None:
                    reasons.append(record.reason)
                    hard = hard or record.disposition == "REJECTED"
            if reasons:
                original = originals[(kind, index)]
                dispose(
                    kind=kind,
                    original=original,
                    normalized=current,
                    occurrence_index=index,
                    reasons=tuple(sorted(set(reasons))),
                    hard_reject=hard,
                )
                return None
            if getattr(current, "confidence", "HIGH") not in _CONFIDENCE or getattr(
                current, "uncertainty", "NONE"
            ) not in _UNCERTAINTY:
                original = originals[(kind, index)]
                dispose(
                    kind=kind,
                    original=original,
                    normalized=current,
                    occurrence_index=index,
                    reasons=("MISSING_CONFIDENCE_OR_UNCERTAINTY",),
                    hard_reject=True,
                )
                return None
            return current

        panels: list[Any] = []
        for index, item in enumerate(normalized_collections["panel"]):
            normalized = normalize_geometry("panel", index, item, ("bbox",))
            if normalized is not None:
                panels.append(normalized)
        panel_ids = {item.proposal_id for item in panels}

        components: list[Any] = []
        for index, item in enumerate(normalized_collections["component"]):
            normalized = normalize_geometry("component", index, item, ("bbox", "evidence_region"))
            if normalized is None:
                continue
            if normalized.proposed_type not in {item.value for item in ComponentType}:
                add_evidence(
                    "UNSUPPORTED_COMPONENT_VOCABULARY",
                    "component",
                    normalized.proposal_id,
                    f"{normalized.proposed_type} normalized to UNKNOWN during canonicalization",
                )
            unknown_hints = sorted(
                set(normalized.representation_hints) - {item.value for item in RepresentationType}
            )
            if unknown_hints:
                add_evidence(
                    "UNSUPPORTED_REPRESENTATION_HINT",
                    "component",
                    normalized.proposal_id,
                    ",".join(unknown_hints),
                )
            panel_ref = normalized.panel_proposal_id
            if panel_ref and (panel_ref in ambiguous_ids or panel_ref not in panel_ids):
                impacts.append(
                    ProposalDependencyImpact("panel", panel_ref, "component", normalized.proposal_id, "UNKNOWN_OR_AMBIGUOUS_PANEL_REFERENCE")
                )
                ref = add_evidence(
                    "UNKNOWN_PANEL_REFERENCE",
                    "component",
                    normalized.proposal_id,
                    panel_ref,
                )
                reviews.append(
                    ProposalReviewCandidateV0_2(
                        "component",
                        normalized.proposal_id,
                        "UNKNOWN_PANEL_REFERENCE",
                        ("retain component without asserted panel", "review panel membership"),
                        ref,
                    )
                )
                normalized = replace(normalized, panel_proposal_id=None)
            components.append(normalized)
        component_ids = {item.proposal_id for item in components}

        assets: list[Any] = []
        for index, item in enumerate(normalized_collections["asset"]):
            normalized = normalize_geometry("asset", index, item, ("source_region",))
            if normalized is None:
                continue
            component_ref = normalized.component_proposal_id
            if component_ref in ambiguous_ids or component_ref not in component_ids:
                impacts.append(
                    ProposalDependencyImpact("component", component_ref, "asset", normalized.proposal_id, "INVALID_ASSET_ASSOCIATION")
                )
                dispose(
                    kind="asset",
                    original=originals[("asset", index)],
                    normalized=normalized,
                    occurrence_index=index,
                    reasons=("MALFORMED_ASSET_PROPOSAL",),
                    dependency_refs=(component_ref,),
                )
                continue
            assets.append(normalized)
        asset_ids = {item.proposal_id for item in assets}

        normalized_components: list[Any] = []
        for component in components:
            asset_ref = component.asset_proposal_id
            if asset_ref and (asset_ref in ambiguous_ids or asset_ref not in asset_ids):
                impacts.append(
                    ProposalDependencyImpact("asset", asset_ref, "component", component.proposal_id, "INVALID_ASSET_ASSOCIATION")
                )
                ref = add_evidence(
                    "MALFORMED_ASSET_PROPOSAL",
                    "component",
                    component.proposal_id,
                    asset_ref,
                )
                reviews.append(
                    ProposalReviewCandidateV0_2(
                        "component",
                        component.proposal_id,
                        "MALFORMED_ASSET_PROPOSAL",
                        ("retain source region", "supply valid asset association"),
                        ref,
                    )
                )
                component = replace(component, asset_proposal_id=None)
            normalized_components.append(component)
        components = normalized_components

        groups: list[Any] = []
        tentative_group_ids = {item.proposal_id for item in normalized_collections["group"]}
        for index, item in enumerate(normalized_collections["group"]):
            normalized = normalize_geometry("group", index, item, ("evidence_region",))
            if normalized is None:
                continue
            valid_members: list[str] = []
            invalid_members: list[str] = []
            for member in normalized.member_proposal_ids:
                if member in ambiguous_ids or member not in component_ids:
                    invalid_members.append(member)
                    impacts.append(
                        ProposalDependencyImpact("component", member, "group", normalized.proposal_id, "INVALID_GROUP_MEMBERSHIP")
                    )
                else:
                    valid_members.append(member)
            parent = normalized.parent_group_proposal_id
            if parent and (parent in ambiguous_ids or parent not in tentative_group_ids):
                impacts.append(
                    ProposalDependencyImpact("group", parent, "group", normalized.proposal_id, "INVALID_GROUP_PARENT")
                )
                invalid_members.append(parent)
                parent = None
            if invalid_members:
                ref = add_evidence(
                    "INVALID_GROUP_MEMBERSHIP",
                    "group",
                    normalized.proposal_id,
                    ",".join(sorted(set(invalid_members))),
                )
                reviews.append(
                    ProposalReviewCandidateV0_2(
                        "group",
                        normalized.proposal_id,
                        "INVALID_GROUP_MEMBERSHIP",
                        ("retain valid members", "review rejected membership"),
                        ref,
                    )
                )
            if not valid_members:
                dispose(
                    kind="group",
                    original=originals[("group", index)],
                    normalized=normalized,
                    occurrence_index=index,
                    reasons=("NO_VALID_GROUP_MEMBERS",),
                    dependency_refs=tuple(sorted(set(invalid_members))),
                )
                continue
            groups.append(
                replace(
                    normalized,
                    parent_group_proposal_id=parent,
                    member_proposal_ids=tuple(valid_members),
                )
            )
        accepted_group_ids = {item.proposal_id for item in groups}
        groups = [
            replace(item, parent_group_proposal_id=None)
            if item.parent_group_proposal_id and item.parent_group_proposal_id not in accepted_group_ids
            else item
            for item in groups
        ]

        relations: list[Any] = []
        for index, item in enumerate(normalized_collections["relation"]):
            normalized = normalize_geometry("relation", index, item, ("evidence_region",))
            if normalized is None:
                continue
            invalid_refs = [
                value
                for value in (normalized.source_candidate_id, normalized.target_candidate_id)
                if not value or value in ambiguous_ids or value not in component_ids
            ]
            route_valid = all(
                len(point) == 2
                and all(
                    isinstance(value, (int, float)) and isfinite(float(value))
                    for value in point
                )
                for point in normalized.route_points
            ) and normalized.coordinate_space in {"SOURCE_PIXELS", "NORMALIZED"}
            if invalid_refs or not route_valid:
                for value in invalid_refs:
                    impacts.append(
                        ProposalDependencyImpact("component", value or "MISSING", "relation", normalized.proposal_id, "DANGLING_RELATION_REFERENCE")
                    )
                dispose(
                    kind="relation",
                    original=originals[("relation", index)],
                    normalized=normalized,
                    occurrence_index=index,
                    reasons=("DANGLING_RELATION_REFERENCE" if invalid_refs else "IMPOSSIBLE_GEOMETRY",),
                    dependency_refs=tuple(str(item) for item in invalid_refs),
                )
                continue
            relations.append(normalized)

        charts: list[Any] = []
        for index, item in enumerate(normalized_collections["chart"]):
            component_ref = item.component_proposal_id
            if component_ref in ambiguous_ids or component_ref not in component_ids:
                impacts.append(
                    ProposalDependencyImpact("component", component_ref, "chart", item.proposal_id, "MALFORMED_CHART_PROPOSAL")
                )
                dispose(
                    kind="chart",
                    original=originals[("chart", index)],
                    normalized=item,
                    occurrence_index=index,
                    reasons=("MALFORMED_CHART_PROPOSAL",),
                    dependency_refs=(component_ref,),
                )
                continue
            if item.proposed_values and not (
                item.authoritative_data_available and item.authoritative_data_ref
            ):
                ref = add_evidence(
                    "NONAUTHORITATIVE_CHART_VALUES",
                    "chart",
                    item.proposal_id,
                    "pixel-derived values removed from authoritative proposal state",
                )
                reviews.append(
                    ProposalReviewCandidateV0_2(
                        "chart",
                        item.proposal_id,
                        "NONAUTHORITATIVE_CHART_VALUES",
                        ("retain source-preserving chart asset", "supply authoritative data"),
                        ref,
                    )
                )
                item = replace(item, proposed_values=())
            charts.append(item)

        accepted_ids = {
            item.proposal_id
            for values in (panels, components, groups, relations, assets, charts)
            for item in values
        }
        coverage = []
        accepted_coverage_refs: list[ProposalItemRef] = []
        seen_coverage_dimensions: set[str] = set()
        for index, observation in enumerate(proposal.coverage_observations):
            coverage_id = f"coverage:{observation.dimension}#{index + 1}"
            item_ref = ProposalItemRef(
                "coverage",
                observation.dimension,
                coverage_id,
                index,
            )
            valid_targets = tuple(
                item for item in observation.target_proposal_ids if item in accepted_ids
            )
            invalid_targets = tuple(
                item for item in observation.target_proposal_ids if item not in accepted_ids
            )
            if invalid_targets:
                add_evidence(
                    "DANGLING_COVERAGE_REFERENCE",
                    "coverage",
                    observation.dimension,
                    ",".join(invalid_targets),
                )
            invalid_vocabulary = observation.dimension not in {
                item.value for item in CoverageDimension
            } or observation.status not in {
                item.value for item in CoverageStatus
            }
            duplicate_dimension = observation.dimension in seen_coverage_dimensions
            if invalid_vocabulary or duplicate_dimension:
                code = (
                    "UNSUPPORTED_COVERAGE_VOCABULARY"
                    if invalid_vocabulary
                    else "DUPLICATE_COVERAGE_DIMENSION"
                )
                ref = add_evidence(
                    code,
                    "coverage",
                    coverage_id,
                    observation.status,
                )
                record = ProposalItemDisposition(
                    item_ref,
                    (code,),
                    json.dumps(
                        to_primitive(observation),
                        sort_keys=True,
                        separators=(",", ":"),
                    ),
                    invalid_targets,
                    ref,
                )
                rejected.append(record)
                reviews.append(
                    ProposalReviewCandidateV0_2(
                        "coverage",
                        coverage_id,
                        code,
                        ("inspect coverage observation", "retain conservative UNKNOWN"),
                        ref,
                    )
                )
                continue
            seen_coverage_dimensions.add(observation.dimension)
            coverage.append(replace(observation, target_proposal_ids=valid_targets))
            accepted_coverage_refs.append(item_ref)

        uncertainties = tuple(
            item
            for item in proposal.uncertainties
            if item.target_proposal_id in accepted_ids
        )
        review_candidates = tuple(
            item
            for item in proposal.review_candidates
            if item.target_proposal_id in accepted_ids
        )
        model_evidence = tuple(
            replace(
                item,
                target_proposal_ids=tuple(
                    target for target in item.target_proposal_ids if target in accepted_ids
                ),
            )
            for item in proposal.model_evidence
        )
        normalized_proposal = replace(
            proposal,
            panels=tuple(panels),
            component_proposals=tuple(components),
            group_proposals=tuple(groups),
            relation_proposals=tuple(relations),
            asset_proposals=tuple(assets),
            chart_proposals=tuple(charts),
            coverage_observations=tuple(coverage),
            uncertainties=uncertainties,
            review_candidates=review_candidates,
            model_evidence=model_evidence,
        )

        accepted: list[ProposalItemRef] = []
        for kind, values in (
            ("panel", normalized_proposal.panels),
            ("component", normalized_proposal.component_proposals),
            ("group", normalized_proposal.group_proposals),
            ("relation", normalized_proposal.relation_proposals),
            ("asset", normalized_proposal.asset_proposals),
            ("chart", normalized_proposal.chart_proposals),
        ):
            for index, item in enumerate(values):
                original = next(
                    (
                        remap.original_id
                        for remap in remaps
                        if remap.kind == kind and remap.normalized_id == item.proposal_id
                    ),
                    item.proposal_id,
                )
                accepted.append(ProposalItemRef(kind, original, item.proposal_id, index))
        accepted.extend(accepted_coverage_refs)

        if not components and not panels:
            add_evidence(
                "NO_RECOVERABLE_FIGURE_STATE",
                "proposal",
                "proposal",
                "no valid component or panel remains after localized validation",
                scope="FATAL",
            )
            normalized_proposal = None

        return ProposalValidationResult(
            accepted_items=tuple(sorted(accepted, key=lambda item: (item.kind, item.normalized_id))),
            quarantined_items=tuple(sorted(quarantined, key=lambda item: (item.item.kind, item.item.normalized_id))),
            rejected_items=tuple(sorted(rejected, key=lambda item: (item.item.kind, item.item.normalized_id))),
            dependency_impacts=tuple(sorted(impacts, key=lambda item: (item.dependent_kind, item.dependent_id, item.dependency_id))),
            global_errors=tuple(global_errors),
            recoverable_errors=tuple(recoverable),
            fatal_errors=tuple(fatal),
            review_candidates=tuple(sorted(reviews, key=lambda item: (item.target_kind, item.target_id, item.reason_code))),
            validation_evidence=tuple(sorted(evidence, key=lambda item: item.evidence_ref)),
            geometry_normalizations=tuple(geometry_records),
            id_remaps=tuple(remaps),
            normalized_proposal=normalized_proposal,
        )

    @staticmethod
    def _all_references(proposal: AnalysisProposal) -> set[str]:
        refs: set[str] = set()
        for component in proposal.component_proposals:
            refs.update(
                item
                for item in (component.panel_proposal_id, component.asset_proposal_id)
                if item
            )
        for group in proposal.group_proposals:
            refs.update(group.member_proposal_ids)
            if group.parent_group_proposal_id:
                refs.add(group.parent_group_proposal_id)
        for relation in proposal.relation_proposals:
            refs.update(
                item
                for item in (relation.source_candidate_id, relation.target_candidate_id)
                if item
            )
        refs.update(item.component_proposal_id for item in proposal.asset_proposals)
        refs.update(item.component_proposal_id for item in proposal.chart_proposals)
        for coverage in proposal.coverage_observations:
            refs.update(coverage.target_proposal_ids)
        refs.update(item.target_proposal_id for item in proposal.uncertainties)
        refs.update(item.target_proposal_id for item in proposal.review_candidates)
        for item in proposal.model_evidence:
            refs.update(item.target_proposal_ids)
        return refs


def validation_policy_hash_v0_2() -> str:
    payload = {
        "validation": PROPOSAL_VALIDATION_POLICY_VERSION_V0_2,
        "quarantine": PROPOSAL_QUARANTINE_POLICY_VERSION_V0_2,
        "dependency": DEPENDENCY_LOCALIZATION_POLICY_VERSION_V0_2,
        "geometry": DEFAULT_GEOMETRY_NORMALIZATION_POLICY_V0_2.policy_hash(),
    }
    return "sha256:" + sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
