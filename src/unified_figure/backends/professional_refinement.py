from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from hashlib import sha256
import json
from pathlib import Path

from PIL import Image, ImageStat


PROFESSIONAL_REFINEMENT_CANDIDACY_POLICY_VERSION = (
    "professional-refinement-candidacy-policy-v0.1"
)
PROFESSIONAL_REFINEMENT_ACCEPTANCE_POLICY_VERSION = (
    "professional-refinement-acceptance-policy-v0.1"
)


class ProfessionalRefinementCandidacy(str, Enum):
    NOT_CANDIDATE = "NOT_CANDIDATE"
    CANDIDATE = "CANDIDATE"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"


class QualityState(str, Enum):
    PASS = "PASS"
    LIMITED = "LIMITED"
    FAIL = "FAIL"
    NOT_VERIFIABLE = "NOT_VERIFIABLE"


class ExpectedRefinementValue(str, Enum):
    CLEAR = "CLEAR"
    MARGINAL = "MARGINAL"
    NONE = "NONE"
    UNKNOWN = "UNKNOWN"


class ScientificSafetyClass(str, Enum):
    SOURCE_ONLY_REFINEMENT = "SOURCE_ONLY_REFINEMENT"
    GENERATED_VISUAL_REFINEMENT = "GENERATED_VISUAL_REFINEMENT"
    SOURCE_GENERATED_HYBRID = "SOURCE_GENERATED_HYBRID"
    SCIENTIFIC_STRUCTURE_MODIFICATION_REQUIRED = "SCIENTIFIC_STRUCTURE_MODIFICATION_REQUIRED"


@dataclass(frozen=True, slots=True)
class ProfessionalRefinementCandidacyInput:
    scientific_acceptance: QualityState
    source_quality: QualityState
    generated_asset_quality: QualityState
    alpha_quality: QualityState
    edge_quality: QualityState
    geometry_mismatch: bool
    composition_mismatch: bool
    editability_value: str
    backend_available: bool
    expected_value: ExpectedRefinementValue


@dataclass(frozen=True, slots=True)
class ProfessionalRefinementCandidacyDecision:
    status: ProfessionalRefinementCandidacy
    reasons: tuple[str, ...]
    policy_version: str = PROFESSIONAL_REFINEMENT_CANDIDACY_POLICY_VERSION


def assess_professional_refinement_candidacy(
    evidence: ProfessionalRefinementCandidacyInput,
) -> ProfessionalRefinementCandidacyDecision:
    reasons: list[str] = []
    if evidence.scientific_acceptance is QualityState.FAIL:
        return ProfessionalRefinementCandidacyDecision(
            ProfessionalRefinementCandidacy.NOT_CANDIDATE,
            ("reject-before-refine: scientific content deviation failed",),
        )
    if not evidence.backend_available:
        return ProfessionalRefinementCandidacyDecision(
            ProfessionalRefinementCandidacy.NOT_CANDIDATE,
            ("Photoshop refinement backend unavailable; retain safe fallback",),
        )
    repairable = (
        evidence.geometry_mismatch
        or evidence.composition_mismatch
        or evidence.alpha_quality in {QualityState.FAIL, QualityState.LIMITED}
        or evidence.edge_quality in {QualityState.FAIL, QualityState.LIMITED}
    )
    if not repairable:
        reasons.append("no specific repairable refinement defect")
    if evidence.source_quality is QualityState.PASS and not repairable:
        reasons.append("source preservation already satisfies the fidelity target")
    if evidence.expected_value is ExpectedRefinementValue.NONE:
        reasons.append("expected incremental professional value is none")
    if reasons:
        return ProfessionalRefinementCandidacyDecision(
            ProfessionalRefinementCandidacy.NOT_CANDIDATE, tuple(reasons)
        )
    if evidence.scientific_acceptance is QualityState.NOT_VERIFIABLE:
        return ProfessionalRefinementCandidacyDecision(
            ProfessionalRefinementCandidacy.REVIEW_REQUIRED,
            ("scientific acceptance is not verifiable",),
        )
    if evidence.expected_value is ExpectedRefinementValue.UNKNOWN:
        return ProfessionalRefinementCandidacyDecision(
            ProfessionalRefinementCandidacy.REVIEW_REQUIRED,
            ("repairable defect exists but expected refinement value is unknown",),
        )
    return ProfessionalRefinementCandidacyDecision(
        ProfessionalRefinementCandidacy.CANDIDATE,
        ("specific repairable local defect with bounded scientific-safe operation",),
    )


@dataclass(frozen=True, slots=True)
class RefinedAssetChecks:
    valid_file: QualityState
    nonempty: QualityState
    alpha_valid: QualityState
    geometry_compatible: QualityState
    edge_matte: QualityState
    width: int
    height: int
    alpha_extrema: tuple[int, int] | None
    content_hash: str


def inspect_refined_asset(
    path: str | Path,
    *,
    expected_width: int,
    expected_height: int,
    alpha_required: bool,
    geometry_tolerance: float = 0.02,
) -> RefinedAssetChecks:
    asset = Path(path)
    content_hash = "sha256:" + sha256(asset.read_bytes()).hexdigest() if asset.is_file() else ""
    try:
        with Image.open(asset) as opened:
            image = opened.convert("RGBA")
            image.load()
    except Exception:
        return RefinedAssetChecks(
            QualityState.FAIL,
            QualityState.FAIL,
            QualityState.FAIL,
            QualityState.FAIL,
            QualityState.NOT_VERIFIABLE,
            0,
            0,
            None,
            content_hash,
        )
    alpha = image.getchannel("A")
    extrema = tuple(int(value) for value in alpha.getextrema())
    variance = sum(ImageStat.Stat(image).var)
    expected_ratio = expected_width / max(1, expected_height)
    observed_ratio = image.width / max(1, image.height)
    ratio_error = abs(observed_ratio - expected_ratio) / max(expected_ratio, 1e-9)
    alpha_ok = extrema[1] > 0 and (not alpha_required or extrema[0] == 0)
    edge_pixels = []
    rgba = image.load()
    for x in range(image.width):
        edge_pixels.extend((rgba[x, 0], rgba[x, image.height - 1]))
    for y in range(image.height):
        edge_pixels.extend((rgba[0, y], rgba[image.width - 1, y]))
    matte_fail = any(pixel[3] > 8 and max(pixel[:3]) < 12 for pixel in edge_pixels)
    return RefinedAssetChecks(
        QualityState.PASS,
        QualityState.PASS if variance > 0 and extrema[1] > 0 else QualityState.FAIL,
        QualityState.PASS if alpha_ok else QualityState.FAIL,
        QualityState.PASS if ratio_error <= geometry_tolerance else QualityState.FAIL,
        QualityState.FAIL if matte_fail else QualityState.PASS,
        image.width,
        image.height,
        extrema,
        content_hash,
    )


@dataclass(frozen=True, slots=True)
class ProfessionalRefinementAcceptance:
    accepted: bool
    selected_backend: str
    fallback_backend: str
    reasons: tuple[str, ...]
    policy_version: str = PROFESSIONAL_REFINEMENT_ACCEPTANCE_POLICY_VERSION


def decide_professional_refinement_acceptance(
    *,
    local_checks: RefinedAssetChecks,
    scientific_content: QualityState,
    visual_gain: QualityState,
    placement: QualityState,
    fallback_backend: str,
) -> ProfessionalRefinementAcceptance:
    failures: list[str] = []
    for name, value in (
        ("VALID_FILE", local_checks.valid_file),
        ("NONEMPTY", local_checks.nonempty),
        ("ALPHA_VALID", local_checks.alpha_valid),
        ("GEOMETRY_COMPATIBLE", local_checks.geometry_compatible),
        ("EDGE_MATTE", local_checks.edge_matte),
        ("SCIENTIFIC_CONTENT", scientific_content),
        ("VISUAL_GAIN", visual_gain),
        ("PLACEMENT", placement),
    ):
        if value is not QualityState.PASS:
            failures.append(f"{name}={value.value}")
    return ProfessionalRefinementAcceptance(
        accepted=not failures,
        selected_backend="PHOTOSHOP_REFINEMENT" if not failures else fallback_backend,
        fallback_backend=fallback_backend,
        reasons=tuple(failures),
    )


def refinement_cache_identity(
    *, input_hash: str, operation_plan: dict[str, object], backend_identity: str
) -> str:
    payload = json.dumps(
        {
            "backend_identity": backend_identity,
            "input_hash": input_hash,
            "operation_plan": operation_plan,
            "policy_version": PROFESSIONAL_REFINEMENT_ACCEPTANCE_POLICY_VERSION,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return "sha256:" + sha256(payload).hexdigest()


def build_transparent_png_export_jsx(output_path: str | Path) -> str:
    path = Path(output_path).resolve()
    escaped = str(path).replace("\\", "/").replace("'", "\\'")
    return (
        f"var f=new File('{escaped}');"
        "var o=new ExportOptionsSaveForWeb();"
        "o.format=SaveDocumentType.PNG;o.PNG8=false;o.transparency=true;"
        "o.interlaced=false;o.includeProfile=false;"
        "activeDocument.exportDocument(f,ExportType.SAVEFORWEB,o);"
        "'transparent-png-exported';"
    )
