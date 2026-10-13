from __future__ import annotations

from hashlib import sha256

from unified_figure.model.enums import RepresentationType

from .contracts import BackendAvailability, BackendCandidateEvidence, BackendDecision
from .registry import BackendCapabilityRegistry


BACKEND_DECISION_POLICY_VERSION = "backend-decision-policy-v0.1"


def select_backend(
    *,
    figure_id: str,
    component_id: str,
    representation: RepresentationType,
    registry: BackendCapabilityRegistry,
    preferred_backend_ids: tuple[str, ...],
    fallback_backend_id: str,
    fallback_representation: RepresentationType,
) -> BackendDecision:
    candidates: list[BackendCandidateEvidence] = []
    chosen = ""
    realized_representation = representation
    for backend_id in tuple(dict.fromkeys((*preferred_backend_ids, fallback_backend_id))):
        capability = registry.get(backend_id)
        candidate_representation = (
            fallback_representation if backend_id == fallback_backend_id else representation
        )
        reasons: list[str] = []
        if capability.availability is not BackendAvailability.AVAILABLE:
            reasons.append(f"availability={capability.availability.value}")
        if not capability.supports(candidate_representation):
            reasons.append(f"unsupported_representation={candidate_representation.value}")
        accepted = not reasons
        candidates.append(
            BackendCandidateEvidence(
                backend_id=backend_id,
                representation=candidate_representation,
                availability=capability.availability,
                accepted_for_selection=accepted,
                rejection_reasons=tuple(reasons),
            )
        )
        if accepted and not chosen:
            chosen = backend_id
            realized_representation = candidate_representation
    if not chosen:
        fallback = registry.get(fallback_backend_id)
        if fallback.availability is not BackendAvailability.AVAILABLE:
            raise ValueError("fallback backend must be available")
        if not fallback.supports(fallback_representation):
            raise ValueError("fallback backend must support fallback representation")
        chosen = fallback_backend_id
        realized_representation = fallback_representation
    digest = sha256(
        f"{figure_id}\x1f{component_id}\x1f{representation.value}\x1f{chosen}".encode("utf-8")
    ).hexdigest()[:10]
    chosen_capability = registry.get(chosen)
    return BackendDecision(
        decision_id=f"backend-decision-0000-{digest}",
        component_id=component_id,
        requested_representation=representation,
        realized_representation=realized_representation,
        candidate_backends=tuple(candidates),
        chosen_backend=chosen,
        fidelity_rationale=f"selected capability fidelity={chosen_capability.fidelity_potential.value}",
        editability_rationale=f"selected editability={chosen_capability.editability_level.value}",
        cost_rationale=f"selected cost={chosen_capability.cost_class.value}",
        scientific_risk_rationale=(
            f"selected scientific risk={chosen_capability.scientific_content_risk.value}; "
            f"generated output is never authoritative"
        ),
        fallback_backend=fallback_backend_id,
        fallback_representation=fallback_representation,
        decision_policy_version=BACKEND_DECISION_POLICY_VERSION,
    )
