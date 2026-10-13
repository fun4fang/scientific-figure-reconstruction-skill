from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import Enum
from hashlib import sha256
import json


QUALITY_PROFILE_POLICY_VERSION = "quality-profile-policy-v0.1"


class QualityProfile(str, Enum):
    FAST = "FAST"
    BALANCED = "BALANCED"
    HIGH = "HIGH"
    SHOWCASE = "SHOWCASE"


class TargetFidelity(str, Enum):
    RECOGNIZABLE = "RECOGNIZABLE"
    STRONG = "STRONG"
    HIGH = "HIGH"
    HIGHEST_AVAILABLE = "HIGHEST_AVAILABLE"


class EditabilityPreference(str, Enum):
    SECONDARY = "SECONDARY"
    BALANCED = "BALANCED"
    HIGH_WHEN_FAITHFUL = "HIGH_WHEN_FAITHFUL"


class GenerationCandidacyLevel(str, Enum):
    DISABLED = "DISABLED"
    CLEAR_VALUE_ONLY = "CLEAR_VALUE_ONLY"
    EVIDENCE_SUPPORTED = "EVIDENCE_SUPPORTED"
    PERMISSIVE_WITH_REVIEW = "PERMISSIVE_WITH_REVIEW"


class ProfessionalBackendCandidacy(str, Enum):
    DISABLED = "DISABLED"
    CLEAR_VALUE_ONLY = "CLEAR_VALUE_ONLY"


class LatencyPreference(str, Enum):
    LOW = "LOW"
    BOUNDED = "BOUNDED"
    QUALITY_PRIORITY = "QUALITY_PRIORITY"


@dataclass(frozen=True, slots=True)
class BackendCallBudget:
    max_model_calls: int
    max_imagegen_calls: int
    max_photoshop_calls: int
    max_optional_backend_calls: int

    def __post_init__(self) -> None:
        values = (
            self.max_model_calls,
            self.max_imagegen_calls,
            self.max_photoshop_calls,
            self.max_optional_backend_calls,
        )
        if any(value < 0 for value in values):
            raise ValueError("profile call budgets must be non-negative")
        if self.max_imagegen_calls > self.max_model_calls:
            raise ValueError("ImageGen budget cannot exceed total model-call budget")
        if self.max_imagegen_calls + self.max_photoshop_calls > self.max_optional_backend_calls:
            raise ValueError("per-backend budgets cannot exceed the optional-backend budget")


@dataclass(frozen=True, slots=True)
class QualityProfilePolicy:
    profile: QualityProfile
    target_fidelity: TargetFidelity
    editability_preference: EditabilityPreference
    generation_candidacy: GenerationCandidacyLevel
    professional_backend_candidacy: ProfessionalBackendCandidacy
    latency_preference: LatencyPreference
    call_budget: BackendCallBudget
    prefer_source_preservation: bool
    fast_fidelity_floor_required: bool
    scientific_safety_policy: str = "PROFILE_INVARIANT_FAIL_CLOSED_V0_1"
    policy_version: str = QUALITY_PROFILE_POLICY_VERSION

    def to_dict(self) -> dict[str, object]:
        payload = asdict(self)
        return {
            "profile": self.profile.value,
            "target_fidelity": self.target_fidelity.value,
            "editability_preference": self.editability_preference.value,
            "generation_candidacy": self.generation_candidacy.value,
            "professional_backend_candidacy": self.professional_backend_candidacy.value,
            "latency_preference": self.latency_preference.value,
            "call_budget": payload["call_budget"],
            "prefer_source_preservation": self.prefer_source_preservation,
            "fast_fidelity_floor_required": self.fast_fidelity_floor_required,
            "scientific_safety_policy": self.scientific_safety_policy,
            "policy_version": self.policy_version,
        }

    def fingerprint(self) -> str:
        encoded = json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":")).encode("utf-8")
        return "sha256:" + sha256(encoded).hexdigest()


_PROFILES = {
    QualityProfile.FAST: QualityProfilePolicy(
        profile=QualityProfile.FAST,
        target_fidelity=TargetFidelity.RECOGNIZABLE,
        editability_preference=EditabilityPreference.SECONDARY,
        generation_candidacy=GenerationCandidacyLevel.DISABLED,
        professional_backend_candidacy=ProfessionalBackendCandidacy.DISABLED,
        latency_preference=LatencyPreference.LOW,
        call_budget=BackendCallBudget(0, 0, 0, 0),
        prefer_source_preservation=True,
        fast_fidelity_floor_required=True,
    ),
    QualityProfile.BALANCED: QualityProfilePolicy(
        profile=QualityProfile.BALANCED,
        target_fidelity=TargetFidelity.STRONG,
        editability_preference=EditabilityPreference.BALANCED,
        generation_candidacy=GenerationCandidacyLevel.CLEAR_VALUE_ONLY,
        professional_backend_candidacy=ProfessionalBackendCandidacy.DISABLED,
        latency_preference=LatencyPreference.BOUNDED,
        call_budget=BackendCallBudget(1, 1, 0, 1),
        prefer_source_preservation=True,
        fast_fidelity_floor_required=False,
    ),
    QualityProfile.HIGH: QualityProfilePolicy(
        profile=QualityProfile.HIGH,
        target_fidelity=TargetFidelity.HIGH,
        editability_preference=EditabilityPreference.HIGH_WHEN_FAITHFUL,
        generation_candidacy=GenerationCandidacyLevel.EVIDENCE_SUPPORTED,
        professional_backend_candidacy=ProfessionalBackendCandidacy.CLEAR_VALUE_ONLY,
        latency_preference=LatencyPreference.QUALITY_PRIORITY,
        call_budget=BackendCallBudget(2, 2, 1, 3),
        prefer_source_preservation=True,
        fast_fidelity_floor_required=False,
    ),
    QualityProfile.SHOWCASE: QualityProfilePolicy(
        profile=QualityProfile.SHOWCASE,
        target_fidelity=TargetFidelity.HIGHEST_AVAILABLE,
        editability_preference=EditabilityPreference.HIGH_WHEN_FAITHFUL,
        generation_candidacy=GenerationCandidacyLevel.PERMISSIVE_WITH_REVIEW,
        professional_backend_candidacy=ProfessionalBackendCandidacy.CLEAR_VALUE_ONLY,
        latency_preference=LatencyPreference.QUALITY_PRIORITY,
        call_budget=BackendCallBudget(3, 3, 1, 4),
        prefer_source_preservation=True,
        fast_fidelity_floor_required=False,
    ),
}


def get_quality_profile(profile: QualityProfile | str) -> QualityProfilePolicy:
    key = profile if isinstance(profile, QualityProfile) else QualityProfile(profile.upper())
    return _PROFILES[key]


def quality_profile_registry() -> tuple[QualityProfilePolicy, ...]:
    return tuple(_PROFILES[item] for item in QualityProfile)
