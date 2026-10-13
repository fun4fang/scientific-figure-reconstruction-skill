from __future__ import annotations

from dataclasses import dataclass, replace
from hashlib import sha256
import json
from time import perf_counter_ns

from unified_figure.adapters.contracts import (
    AdapterRealizationResult,
    AdapterValidationResult,
    RendererAdapter,
)
from unified_figure.model.enums import EvidenceSourceType, ReconstructionStatus, RevisionStatus
from unified_figure.model.figure import CanonicalFigure, make_stable_id
from unified_figure.model.records import CostMetrics, CostTelemetry, EvidenceRecord
from unified_figure.qa.completion import CompletionEvaluation, CompletionEvaluator
from unified_figure.routing.contracts import CostPolicy, EditingRequirements
from unified_figure.routing.orchestration import route_figure
from unified_figure.routing.policy_v0_1 import RepresentationRoutingPolicyV0_1


def _add_cost(left, right):
    if (
        isinstance(left, (int, float))
        and not isinstance(left, bool)
        and isinstance(right, (int, float))
        and not isinstance(right, bool)
    ):
        return left + right
    return "NOT_EXPOSED"


@dataclass(frozen=True, slots=True)
class RealizationRunReceipt:
    receipt_version: str
    figure_id: str
    root_revision_id: str
    candidate_revision_id: str
    validated_revision_id: str
    adapter_config_hash: str
    artifact_hashes: tuple[tuple[str, str], ...]
    completion_status: str
    renderer_calls: int
    elapsed_ms: float


@dataclass(frozen=True, slots=True)
class RealizationPipelineResult:
    root_state: CanonicalFigure
    realization_candidate: CanonicalFigure
    validated_state: CanonicalFigure
    adapter_result: AdapterRealizationResult | None
    completion: CompletionEvaluation
    receipt: RealizationRunReceipt


def validate_realization(
    figure: CanonicalFigure,
    adapter: RendererAdapter,
    result: AdapterRealizationResult,
) -> tuple[AdapterValidationResult, CompletionEvaluation]:
    validation = adapter.validate_output(result)
    completion = CompletionEvaluator().evaluate(figure, result, validation)
    return validation, completion


def render_figure(
    figure: CanonicalFigure,
    adapter: RendererAdapter,
    *,
    router: RepresentationRoutingPolicyV0_1 | None = None,
    editing_requirements: dict[str, EditingRequirements] | None = None,
    cost_policy: CostPolicy | None = None,
    routing_applied: bool = False,
) -> RealizationPipelineResult:
    """Route, realize, validate, and freeze one synthetic/developer realization run."""

    started = perf_counter_ns()
    root = figure
    routed = root if routing_applied else route_figure(
        root,
        adapter.capabilities(),
        router=router,
        editing_requirements=editing_requirements,
        cost_policy=cost_policy,
    )
    input_validation = adapter.validate_input(routed)
    adapter_result: AdapterRealizationResult | None = None
    renderer_evidence: tuple[EvidenceRecord, ...] = ()
    renderer_calls = 0
    output_validation = AdapterValidationResult(True)
    if input_validation.valid:
        adapter_result = adapter.realize(routed)
        renderer_calls = 1
        output_validation = adapter.validate_output(adapter_result)
        renderer_evidence = adapter.collect_runtime_evidence(adapter_result)
    else:
        output_validation = AdapterValidationResult(
            valid=all("REVIEW_REQUIRED" in message or "unsupported representation" in message for message in input_validation.messages),
            messages=input_validation.messages,
        )

    realizations = adapter_result.realizations if adapter_result else ()
    refs_by_object: dict[str, list[str]] = {}
    for realization in realizations:
        refs_by_object.setdefault(realization.canonical_object_id, []).append(realization.realization_id)
    components = tuple(
        replace(item, renderer_realizations=tuple(sorted(refs_by_object.get(item.component_id, ()))))
        for item in routed.components
    )
    relations = tuple(
        replace(item, renderer_realizations=tuple(sorted(refs_by_object.get(item.relation_id, ()))))
        for item in routed.relations
    )
    renderer_evidence_ids = tuple(item.evidence_id for item in renderer_evidence)
    prior_observed = routed.cost_telemetry.observed_cost
    observed = CostMetrics(
        analysis_calls=prior_observed.analysis_calls,
        vision_calls=prior_observed.vision_calls,
        input_tokens=prior_observed.input_tokens,
        output_tokens=prior_observed.output_tokens,
        imagegen_calls=prior_observed.imagegen_calls,
        renderer_calls=_add_cost(prior_observed.renderer_calls, renderer_calls),
        external_backend_calls=prior_observed.external_backend_calls,
        full_image_reviews=prior_observed.full_image_reviews,
        roi_reviews=prior_observed.roi_reviews,
        manual_corrections=prior_observed.manual_corrections,
        wall_time_ms=prior_observed.wall_time_ms,
    )
    candidate = replace(
        routed,
        components=components,
        relations=relations,
        renderer_realizations=realizations,
        evidence=(*routed.evidence, *renderer_evidence),
        cost_telemetry=CostTelemetry(
            estimated_cost=routed.cost_telemetry.estimated_cost,
            observed_cost=observed,
            stage_wall_time_ms={
                **routed.cost_telemetry.stage_wall_time_ms,
                "phase5b_route_realize_validate": "NOT_EXPOSED",
            },
        ),
        revision=replace(
            routed.revision,
            canonical_state_hash="",
            change_manifest=(*routed.revision.change_manifest, "record renderer realizations and artifact evidence"),
            evidence_refs=tuple(dict.fromkeys((*routed.revision.evidence_refs, *renderer_evidence_ids))),
        ),
    ).with_computed_revision_hash()
    candidate.validate()

    completion = CompletionEvaluator().evaluate(candidate, adapter_result, output_validation)
    qa_payload = json.dumps(
        {"status": completion.status.value, "reasons": completion.reasons},
        sort_keys=True,
        separators=(",", ":"),
    )
    qa_evidence = EvidenceRecord(
        evidence_id=make_stable_id(root.figure_id, "qa-evidence", 0, adapter.capabilities().backend_id),
        source_type=EvidenceSourceType.REVISION,
        source_hash="sha256:" + sha256(qa_payload.encode("utf-8")).hexdigest(),
        producer="CompletionEvaluator-v0.1",
        operation="coverage-review-realization-qa",
        input_refs=(candidate.revision.revision_id, *renderer_evidence_ids),
        output_refs=(completion.status.value,),
        run_index=0,
        tool_runtime_info={"external_calls": "0", "full_image_reviews": "0"},
        notes="Structural/runtime QA and coverage-aware completion result.",
    )
    final_revision_id = make_stable_id(
        root.figure_id, "revision-validated", 0, adapter.capabilities().backend_id
    )
    final_revision_status = (
        RevisionStatus.REJECTED
        if completion.canonical_status is ReconstructionStatus.FAILED
        else RevisionStatus.CANDIDATE
        if completion.canonical_status is ReconstructionStatus.REVIEW_REQUIRED
        else RevisionStatus.ACCEPTED
    )
    validated = replace(
        candidate,
        evidence=(*candidate.evidence, qa_evidence),
        reconstruction_status=completion.canonical_status,
        revision=replace(
            candidate.revision,
            revision_id=final_revision_id,
            parent_revision_id=candidate.revision.revision_id,
            canonical_state_hash="",
            change_manifest=(
                *candidate.revision.change_manifest,
                f"completion={completion.status.value}",
            ),
            evidence_refs=tuple(dict.fromkeys((*candidate.revision.evidence_refs, qa_evidence.evidence_id))),
            status=final_revision_status,
        ),
    ).with_computed_revision_hash()
    validated.validate()
    elapsed_ms = (perf_counter_ns() - started) / 1_000_000
    receipt = RealizationRunReceipt(
        receipt_version="phase5b-e2e-receipt-v0.1",
        figure_id=root.figure_id,
        root_revision_id=root.revision.revision_id,
        candidate_revision_id=candidate.revision.revision_id,
        validated_revision_id=validated.revision.revision_id,
        adapter_config_hash=adapter_result.adapter_config_hash if adapter_result else "NOT_APPLICABLE",
        artifact_hashes=adapter_result.artifact_hashes if adapter_result else (),
        completion_status=completion.status.value,
        renderer_calls=renderer_calls,
        elapsed_ms=elapsed_ms,
    )
    return RealizationPipelineResult(root, candidate, validated, adapter_result, completion, receipt)
