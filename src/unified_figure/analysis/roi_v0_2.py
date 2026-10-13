from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from hashlib import sha256
import json

from unified_figure.model.enums import (
    EvidenceSourceType,
    ReconstructionStatus,
    ReviewDisposition,
    ReviewStatus,
    RevisionStatus,
)
from unified_figure.model.figure import CanonicalFigure, make_stable_id
from unified_figure.model.records import EvidenceRecord


ROI_PROPOSAL_VERSION_V0_2 = "roi-analysis-proposal-v0.2"
ROI_PROMPT_VERSION_V0_2 = "UNIFIED_FIGURE_ROI_PROMPT_V0_2"
ROI_QUESTION_TYPES_V0_2 = frozenset(
    {
        "TEXT_DISAMBIGUATION",
        "RELATION_ENDPOINT_DISAMBIGUATION",
        "GROUP_MEMBERSHIP_DISAMBIGUATION",
        "ASSET_BOUNDARY_DISAMBIGUATION",
        "COMPONENT_TYPE_DISAMBIGUATION",
    }
)

UNIFIED_FIGURE_ROI_PROMPT_V0_2 = """You inspect one cropped scientific-figure region for one typed question.
Use only the crop and supplied local target context. Do not re-analyze the full figure.
Do not invent hidden content or unrelated components. Return one ROIAnalysisProposalV0_2.
State the local observation, candidate resolution, confidence, and remaining uncertainty.
If the crop is insufficient, preserve uncertainty instead of guessing."""

ROI_PROMPT_HASH_V0_2 = "sha256:" + sha256(
    UNIFIED_FIGURE_ROI_PROMPT_V0_2.encode("utf-8")
).hexdigest()


@dataclass(frozen=True, slots=True)
class ROILocalObservationV0_2:
    observation: str
    evidence_ref: str


@dataclass(frozen=True, slots=True)
class ROIAnalysisProposalV0_2:
    roi_version: str
    target_review_id: str
    target_canonical_refs: tuple[str, ...]
    question_type: str
    local_observations: tuple[ROILocalObservationV0_2, ...]
    candidate_resolution: str
    confidence: str
    remaining_uncertainty: str
    local_evidence: tuple[str, ...]

    def validate(self) -> None:
        if self.roi_version != ROI_PROPOSAL_VERSION_V0_2:
            raise ValueError("unsupported ROI proposal version")
        if self.question_type not in ROI_QUESTION_TYPES_V0_2:
            raise ValueError("unsupported ROI question type")
        if not self.target_review_id or not self.target_canonical_refs:
            raise ValueError("ROI proposal must bind to review and canonical target scope")
        if self.confidence not in {"HIGH", "MEDIUM", "LOW"}:
            raise ValueError("invalid ROI confidence")
        if self.remaining_uncertainty not in {
            "NONE",
            "LOW",
            "MEDIUM",
            "HIGH",
            "AMBIGUOUS",
            "REVIEW_REQUIRED",
        }:
            raise ValueError("invalid ROI uncertainty")
        if not self.local_observations or not self.local_evidence:
            raise ValueError("ROI proposal requires local observable evidence")

    def to_json(self) -> str:
        return json.dumps(asdict(self), sort_keys=True, separators=(",", ":"))

    def content_hash(self) -> str:
        return "sha256:" + sha256(self.to_json().encode("utf-8")).hexdigest()


def roi_analysis_proposal_schema_v0_2() -> dict[str, object]:
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": "ROIAnalysisProposalV0_2",
        "type": "object",
        "additionalProperties": False,
        "required": [
            "roi_version",
            "target_review_id",
            "target_canonical_refs",
            "question_type",
            "local_observations",
            "candidate_resolution",
            "confidence",
            "remaining_uncertainty",
            "local_evidence",
        ],
        "properties": {
            "roi_version": {"const": ROI_PROPOSAL_VERSION_V0_2},
            "target_review_id": {"type": "string", "minLength": 1},
            "target_canonical_refs": {
                "type": "array",
                "minItems": 1,
                "items": {"type": "string"},
            },
            "question_type": {
                "type": "string",
                "enum": sorted(ROI_QUESTION_TYPES_V0_2),
            },
            "local_observations": {
                "type": "array",
                "minItems": 1,
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["observation", "evidence_ref"],
                    "properties": {
                        "observation": {"type": "string"},
                        "evidence_ref": {"type": "string"},
                    },
                },
            },
            "candidate_resolution": {"type": "string"},
            "confidence": {"enum": ["HIGH", "MEDIUM", "LOW"]},
            "remaining_uncertainty": {
                "enum": [
                    "NONE",
                    "LOW",
                    "MEDIUM",
                    "HIGH",
                    "AMBIGUOUS",
                    "REVIEW_REQUIRED",
                ]
            },
            "local_evidence": {
                "type": "array",
                "minItems": 1,
                "items": {"type": "string"},
            },
        },
    }


def roi_proposal_schema_hash_v0_2() -> str:
    payload = json.dumps(
        roi_analysis_proposal_schema_v0_2(), sort_keys=True, separators=(",", ":")
    )
    return "sha256:" + sha256(payload.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class LowContextROIRequestV0_2:
    prompt_version: str
    prompt_hash: str
    prompt: str
    question_type: str
    target_review_id: str
    target_canonical_refs: tuple[str, ...]
    local_context: tuple[tuple[str, str], ...]
    crop_hash: str
    output_schema_hash: str

    def canonical_json(self) -> str:
        return json.dumps(asdict(self), sort_keys=True, separators=(",", ":"))

    def payload_bytes(self) -> int:
        return len(self.canonical_json().encode("utf-8"))


def build_low_context_roi_request_v0_2(
    *,
    question_type: str,
    target_review_id: str,
    target_canonical_refs: tuple[str, ...],
    local_context: dict[str, str],
    crop_hash: str,
) -> LowContextROIRequestV0_2:
    if question_type not in ROI_QUESTION_TYPES_V0_2:
        raise ValueError("unsupported ROI question type")
    if not target_review_id or not target_canonical_refs or not crop_hash:
        raise ValueError("ROI request must be review-, target-, and crop-bound")
    return LowContextROIRequestV0_2(
        ROI_PROMPT_VERSION_V0_2,
        ROI_PROMPT_HASH_V0_2,
        UNIFIED_FIGURE_ROI_PROMPT_V0_2,
        question_type,
        target_review_id,
        target_canonical_refs,
        tuple(sorted(local_context.items())),
        crop_hash,
        roi_proposal_schema_hash_v0_2(),
    )


class ROIRevisionApplierV0_2:
    """Create a revision-local candidate without rewriting unrelated canonical state."""

    def apply(
        self,
        figure: CanonicalFigure,
        proposal: ROIAnalysisProposalV0_2,
        *,
        accept_resolution: bool = False,
    ) -> CanonicalFigure:
        proposal.validate()
        review = next(
            (item for item in figure.review_items if item.review_id == proposal.target_review_id),
            None,
        )
        if review is None:
            raise ValueError("ROI proposal targets an unknown ReviewItem")
        valid_targets = {
            figure.figure_id,
            *(item.component_id for item in figure.components),
            *(item.group_id for item in figure.groups),
            *(item.relation_id for item in figure.relations),
            *(item.asset_id for item in figure.assets),
        }
        if not set(proposal.target_canonical_refs).issubset(valid_targets):
            raise ValueError("ROI proposal exceeds its canonical target scope")
        if review.target_id not in set(proposal.target_canonical_refs):
            raise ValueError("ROI target scope does not include the bound review target")

        evidence = EvidenceRecord(
            evidence_id=make_stable_id(
                figure.figure_id,
                "roi-v0.2-evidence",
                len(figure.evidence),
                proposal.content_hash(),
            ),
            source_type=EvidenceSourceType.ANALYSIS,
            source_hash=proposal.content_hash(),
            producer=ROI_PROMPT_VERSION_V0_2,
            operation="record-local-roi-resolution-candidate",
            input_refs=(proposal.target_review_id, *proposal.target_canonical_refs),
            output_refs=(proposal.candidate_resolution,),
            run_index=0,
            tool_runtime_info={
                "prompt_hash": ROI_PROMPT_HASH_V0_2,
                "schema_hash": roi_proposal_schema_hash_v0_2(),
                "scope": "LOCAL_ONLY",
            },
            notes="ROI evidence cannot rewrite unrelated canonical objects or global coverage.",
        )
        reviews = tuple(
            replace(
                item,
                status=ReviewStatus.RESOLVED,
                disposition=ReviewDisposition.RESOLVED,
            )
            if accept_resolution
            and item.review_id == proposal.target_review_id
            and proposal.remaining_uncertainty == "NONE"
            else item
            for item in figure.review_items
        )
        metadata = dict(figure.metadata)
        metadata[f"roi_candidate.{proposal.target_review_id}"] = proposal.candidate_resolution
        revision_id = make_stable_id(
            figure.figure_id,
            "revision-roi-v0.2-candidate",
            len(figure.evidence),
            proposal.target_review_id,
        )
        result = replace(
            figure,
            review_items=reviews,
            evidence=(*figure.evidence, evidence),
            metadata=metadata,
            revision=replace(
                figure.revision,
                revision_id=revision_id,
                parent_revision_id=figure.revision.revision_id,
                canonical_state_hash="",
                change_manifest=(
                    *figure.revision.change_manifest,
                    f"record local ROI candidate for {proposal.target_review_id}",
                ),
                evidence_refs=tuple(
                    dict.fromkeys((*figure.revision.evidence_refs, evidence.evidence_id))
                ),
                status=RevisionStatus.CANDIDATE,
            ),
            reconstruction_status=(
                ReconstructionStatus.REVIEW_REQUIRED
                if any(item.status in {ReviewStatus.PENDING, ReviewStatus.REQUIRED} for item in reviews)
                else figure.reconstruction_status
            ),
        ).with_computed_revision_hash()
        result.validate()
        return result
