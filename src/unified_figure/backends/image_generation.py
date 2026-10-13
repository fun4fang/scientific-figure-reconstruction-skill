from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import Enum
from hashlib import sha256
import json
from pathlib import Path
from typing import Iterable

from PIL import Image, ImageStat


IMAGE_GENERATION_CANDIDACY_POLICY_VERSION = "image-generation-candidacy-policy-v0.1"
IMAGE_GENERATION_PROMPT_POLICY_VERSION = "component-image-generation-prompt-v0.1"
GENERATED_ASSET_ACCEPTANCE_POLICY_VERSION = "generated-asset-acceptance-v0.1"


class ImageGenerationCandidacy(str, Enum):
    NOT_CANDIDATE = "NOT_CANDIDATE"
    CANDIDATE = "CANDIDATE"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"


class SourceQuality(str, Enum):
    HIGH = "HIGH"
    ADEQUATE = "ADEQUATE"
    LOW = "LOW"
    INCOMPLETE = "INCOMPLETE"


class ScientificSensitivity(str, Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


class Assessment(str, Enum):
    PASS = "PASS"
    FAIL = "FAIL"
    NOT_VERIFIABLE = "NOT_VERIFIABLE"


@dataclass(frozen=True, slots=True)
class ImageGenerationCandidacyInput:
    component_type: str
    complexity: str
    source_quality: SourceQuality
    native_fidelity: str
    source_preservation_quality: str
    scientific_sensitivity: ScientificSensitivity
    editability_value: str
    backend_available: bool
    reference_guidance_available: bool


@dataclass(frozen=True, slots=True)
class ImageGenerationCandidacyDecision:
    status: ImageGenerationCandidacy
    reasons: tuple[str, ...]
    policy_version: str = IMAGE_GENERATION_CANDIDACY_POLICY_VERSION


def assess_image_generation_candidacy(
    evidence: ImageGenerationCandidacyInput,
) -> ImageGenerationCandidacyDecision:
    if not evidence.backend_available:
        return ImageGenerationCandidacyDecision(
            ImageGenerationCandidacy.NOT_CANDIDATE, ("backend unavailable; use base fallback",)
        )
    if evidence.component_type in {"TEXT", "RELATION", "SIMPLE_SHAPE", "CHART"}:
        return ImageGenerationCandidacyDecision(
            ImageGenerationCandidacy.NOT_CANDIDATE,
            (f"component type {evidence.component_type} is excluded from generation",),
        )
    if (
        evidence.source_quality is SourceQuality.HIGH
        and evidence.source_preservation_quality == "HIGH"
    ):
        return ImageGenerationCandidacyDecision(
            ImageGenerationCandidacy.NOT_CANDIDATE,
            ("high-quality source preservation already satisfies fidelity target",),
        )
    if evidence.scientific_sensitivity is ScientificSensitivity.HIGH:
        return ImageGenerationCandidacyDecision(
            ImageGenerationCandidacy.REVIEW_REQUIRED,
            ("high scientific sensitivity requires an explicit human candidacy decision",),
        )
    if not evidence.reference_guidance_available:
        return ImageGenerationCandidacyDecision(
            ImageGenerationCandidacy.NOT_CANDIDATE,
            ("reference-guided generation unavailable",),
        )
    if evidence.complexity == "HIGH" and evidence.native_fidelity == "LOW":
        if evidence.source_quality in {SourceQuality.LOW, SourceQuality.INCOMPLETE}:
            return ImageGenerationCandidacyDecision(
                ImageGenerationCandidacy.CANDIDATE,
                (
                    "high-complexity content is poorly served by native realization",
                    "source quality is insufficient",
                ),
            )
    return ImageGenerationCandidacyDecision(
        ImageGenerationCandidacy.REVIEW_REQUIRED,
        ("evidence does not prove a material advantage over exact source preservation",),
    )


def build_component_generation_prompt(
    *,
    semantic_role: str,
    must_preserve: Iterable[str],
    may_improve: Iterable[str],
    must_not_invent: Iterable[str],
    transparent_background: bool,
) -> str:
    def section(name: str, values: Iterable[str]) -> str:
        normalized = tuple(item.strip() for item in values if item.strip())
        if not normalized:
            raise ValueError(f"{name} must contain at least one constraint")
        return name + ":\n- " + "\n- ".join(normalized)

    background = (
        "Output a component-only PNG with a real transparent alpha background."
        if transparent_background
        else "Keep the source background treatment; do not add a scene or frame."
    )
    return "\n\n".join(
        (
            "Create one bounded scientific visual asset for use inside a larger first-party figure composition.",
            f"COMPONENT ROLE:\n{semantic_role}",
            section("MUST PRESERVE", must_preserve),
            section("MAY IMPROVE", may_improve),
            section("MUST NOT INVENT", must_not_invent),
            "COMPOSITION:\n"
            + background
            + " Do not include text, labels, arrows, legends, panel letters, borders, or a whole figure. "
            + "Preserve the reference viewpoint, orientation, silhouette, and relative part arrangement.",
        )
    )


def prompt_hash(prompt: str) -> str:
    return "sha256:" + sha256(prompt.encode("utf-8")).hexdigest()


def generation_cache_identity(
    *, source_crop_hash: str, prompt: str, backend_identity: str, generation_parameters: dict[str, object]
) -> str:
    payload = {
        "contract_version": IMAGE_GENERATION_PROMPT_POLICY_VERSION,
        "source_crop_hash": source_crop_hash,
        "prompt_hash": prompt_hash(prompt),
        "backend_identity": backend_identity,
        "generation_parameters": generation_parameters,
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return "sha256:" + sha256(encoded).hexdigest()


@dataclass(frozen=True, slots=True)
class LocalAssetChecks:
    valid_file: Assessment
    nonempty: Assessment
    alpha_valid: Assessment
    geometry_compatible: Assessment
    width: int
    height: int
    aspect_ratio_error: float
    channel_variance: tuple[float, ...]
    alpha_extrema: tuple[int, int] | None

    def to_dict(self) -> dict[str, object]:
        result = asdict(self)
        result["valid_file"] = self.valid_file.value
        result["nonempty"] = self.nonempty.value
        result["alpha_valid"] = self.alpha_valid.value
        result["geometry_compatible"] = self.geometry_compatible.value
        return result


def inspect_generated_asset(
    path: Path,
    *,
    expected_width: int,
    expected_height: int,
    alpha_required: bool,
    maximum_aspect_ratio_error: float = 0.35,
) -> LocalAssetChecks:
    try:
        with Image.open(path) as source:
            source.load()
            image = source.convert("RGBA")
    except Exception:
        return LocalAssetChecks(
            Assessment.FAIL,
            Assessment.NOT_VERIFIABLE,
            Assessment.NOT_VERIFIABLE,
            Assessment.NOT_VERIFIABLE,
            0,
            0,
            1.0,
            (),
            None,
        )
    variance = tuple(round(value, 6) for value in ImageStat.Stat(image).var)
    nonempty = Assessment.PASS if max(variance[:3], default=0.0) > 1.0 else Assessment.FAIL
    extrema = image.getchannel("A").getextrema()
    if alpha_required:
        alpha_valid = Assessment.PASS if extrema[0] < 250 and extrema[1] > 5 else Assessment.FAIL
    else:
        alpha_valid = Assessment.PASS
    expected_ratio = expected_width / max(expected_height, 1)
    actual_ratio = image.width / max(image.height, 1)
    ratio_error = abs(actual_ratio - expected_ratio) / max(expected_ratio, 1e-9)
    geometry = Assessment.PASS if ratio_error <= maximum_aspect_ratio_error else Assessment.FAIL
    return LocalAssetChecks(
        Assessment.PASS,
        nonempty,
        alpha_valid,
        geometry,
        image.width,
        image.height,
        round(ratio_error, 6),
        variance,
        extrema,
    )


@dataclass(frozen=True, slots=True)
class GeneratedAssetAcceptance:
    local_checks: LocalAssetChecks
    placement_compatible: Assessment
    visual_reference_compatible: Assessment
    scientific_content_compatible: Assessment
    accepted: bool
    fallback_required: bool
    reasons: tuple[str, ...]
    policy_version: str = GENERATED_ASSET_ACCEPTANCE_POLICY_VERSION

    def to_dict(self) -> dict[str, object]:
        return {
            "local_checks": self.local_checks.to_dict(),
            "placement_compatible": self.placement_compatible.value,
            "visual_reference_compatible": self.visual_reference_compatible.value,
            "scientific_content_compatible": self.scientific_content_compatible.value,
            "accepted": self.accepted,
            "fallback_required": self.fallback_required,
            "reasons": list(self.reasons),
            "policy_version": self.policy_version,
        }


def decide_generated_asset_acceptance(
    *,
    local_checks: LocalAssetChecks,
    placement_compatible: Assessment,
    visual_reference_compatible: Assessment,
    scientific_content_compatible: Assessment,
) -> GeneratedAssetAcceptance:
    checks = {
        "VALID_FILE": local_checks.valid_file,
        "NONEMPTY": local_checks.nonempty,
        "ALPHA_VALID": local_checks.alpha_valid,
        "GEOMETRY_COMPATIBLE": local_checks.geometry_compatible,
        "PLACEMENT_COMPATIBLE": placement_compatible,
        "VISUAL_REFERENCE_COMPATIBLE": visual_reference_compatible,
        "SCIENTIFIC_CONTENT_COMPATIBLE": scientific_content_compatible,
    }
    failed = tuple(name for name, status in checks.items() if status is not Assessment.PASS)
    return GeneratedAssetAcceptance(
        local_checks=local_checks,
        placement_compatible=placement_compatible,
        visual_reference_compatible=visual_reference_compatible,
        scientific_content_compatible=scientific_content_compatible,
        accepted=not failed,
        fallback_required=bool(failed),
        reasons=tuple(f"{name}={checks[name].value}" for name in failed),
    )
