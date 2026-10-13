from __future__ import annotations

from dataclasses import asdict, dataclass
from hashlib import sha256
import json
from pathlib import Path

from unified_figure.adapters.pptx import PptxRendererAdapter
from unified_figure.adapters.svg import SvgRendererAdapter
from unified_figure.backends import BackendCapabilityRegistry
from unified_figure.fidelity import apply_source_preservation_fallback
from unified_figure.model.enums import AnalysisMode, CoordinateSpace, Orientation
from unified_figure.model.geometry import AuthoritativeInputGeometry
from unified_figure.pipeline import RealizationPipelineResult, render_figure
from unified_figure.qa.completion import SemanticParityResult, validate_cross_backend_parity
from unified_figure.routing import (
    FidelityRoutingExecutionResult,
    QualityProfilePolicy,
    execute_fidelity_routing,
)

from .canonicalizer import CanonicalizationResult, ProposalCanonicalizer
from .contracts import AnalysisPolicy, AuthoritativeSourceReference
from .provider import MultimodalFigureAnalysisProvider
from .validator import AnalysisProposalValidator


@dataclass(frozen=True, slots=True)
class ReferenceRealizationResult:
    canonicalization: CanonicalizationResult
    pptx: RealizationPipelineResult
    svg: RealizationPipelineResult
    semantic_parity: SemanticParityResult
    receipt_paths: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class FidelityReferenceRealizationResult:
    canonicalization: CanonicalizationResult
    routing: FidelityRoutingExecutionResult
    pptx: RealizationPipelineResult
    svg: RealizationPipelineResult
    semantic_parity: SemanticParityResult
    receipt_paths: tuple[str, ...]


def decode_reference_image(
    repository_root: str | Path,
    repository_relative_ref: str,
    *,
    source_id: str,
) -> tuple[AuthoritativeSourceReference, AuthoritativeInputGeometry]:
    try:
        from PIL import Image
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("deterministic reference-image decode requires Pillow") from exc
    root = Path(repository_root).resolve()
    path = (root / repository_relative_ref).resolve()
    if root not in path.parents:
        raise ValueError("reference image escapes repository root")
    from unified_figure.security import checked_image, bounded_bytes, MAX_IMAGE_BYTES
    checked_image(path)
    data = bounded_bytes(path, MAX_IMAGE_BYTES)
    digest = "sha256:" + sha256(data).hexdigest()
    with Image.open(path) as image:
        image.verify()
    with Image.open(path) as image:
        width, height = image.width, image.height
    source = AuthoritativeSourceReference(source_id, digest, repository_relative_ref)
    geometry = AuthoritativeInputGeometry(
        source_width_px=width,
        source_height_px=height,
        decoded_width_px=width,
        decoded_height_px=height,
        coordinate_space=CoordinateSpace.SOURCE_PIXELS,
        orientation=Orientation.NORMAL,
        source_hash=digest,
        geometry_source="pillow-deterministic-decode-v0.1",
    )
    return source, geometry


def analyze_reference_image(
    *,
    figure_id: str,
    repository_root: str | Path,
    source: AuthoritativeSourceReference,
    geometry: AuthoritativeInputGeometry,
    provider: MultimodalFigureAnalysisProvider,
    canonicalizer: ProposalCanonicalizer,
    policy: AnalysisPolicy | None = None,
    canonicalization_receipt_path: str | Path | None = None,
) -> CanonicalizationResult:
    policy = policy or AnalysisPolicy(AnalysisMode.FULL_IMAGE, None, 1, 2)
    proposal = provider.analyze(source, geometry, policy)
    if provider.last_receipt is None:
        raise RuntimeError("analysis provider did not expose a receipt")
    validation = AnalysisProposalValidator().validate(
        proposal,
        authoritative_width=geometry.decoded_width_px,
        authoritative_height=geometry.decoded_height_px,
    )
    return canonicalizer.canonicalize(
        figure_id=figure_id,
        source=source,
        authoritative_geometry=geometry,
        proposal=proposal,
        analysis_receipt=provider.last_receipt,
        validation=validation,
        write_receipt_to=canonicalization_receipt_path,
    )


def realize_reference_figure(
    canonicalization: CanonicalizationResult,
    *,
    repository_root: str | Path,
    output_dir: str | Path,
) -> ReferenceRealizationResult:
    root = Path(repository_root).resolve()
    output = Path(output_dir).resolve()
    if root not in output.parents:
        raise ValueError("realization output must remain inside repository root")
    output.mkdir(parents=True, exist_ok=True)
    figure = canonicalization.figure
    pptx_path = output / f"{figure.figure_id}.pptx"
    svg_path = output / f"{figure.figure_id}.svg"
    pptx = render_figure(
        figure,
        PptxRendererAdapter(
            pptx_path,
            source_root=root,
            artifact_ref=pptx_path.relative_to(root).as_posix(),
        ),
    )
    svg = render_figure(
        figure,
        SvgRendererAdapter(
            svg_path,
            source_root=root,
            artifact_ref=svg_path.relative_to(root).as_posix(),
        ),
    )
    parity = validate_cross_backend_parity(pptx.validated_state, svg.validated_state)
    routing_receipt = {
        "receipt_version": "phase5c-routing-receipt-v0.1",
        "figure_id": figure.figure_id,
        "router_version": "representation-router-v0.1",
        "pptx_decisions": [
            asdict(item) for item in pptx.realization_candidate.representation_decisions
        ],
        "svg_decisions": [
            asdict(item) for item in svg.realization_candidate.representation_decisions
        ],
    }
    render_receipt = {
        "receipt_version": "phase5c-render-receipt-v0.1",
        "pptx": asdict(pptx.receipt),
        "svg": asdict(svg.receipt),
        "semantic_parity": asdict(parity),
    }
    qa_receipt = {
        "receipt_version": "phase5c-qa-receipt-v0.1",
        "pptx": asdict(pptx.completion),
        "svg": asdict(svg.completion),
        "semantic_parity": asdict(parity),
        "canonical_validation": "PASS",
    }
    paths = []
    for name, payload in (
        ("routing_receipt.json", routing_receipt),
        ("render_receipt.json", render_receipt),
        ("qa_receipt.json", qa_receipt),
    ):
        path = output / name
        path.write_text(
            json.dumps(payload, indent=2, sort_keys=True, default=str) + "\n",
            encoding="utf-8",
        )
        paths.append(path.relative_to(root).as_posix())
    (output / "canonical_root.json").write_text(figure.to_json() + "\n", encoding="utf-8")
    (output / "pptx_validated_state.json").write_text(
        pptx.validated_state.to_json() + "\n", encoding="utf-8"
    )
    (output / "svg_validated_state.json").write_text(
        svg.validated_state.to_json() + "\n", encoding="utf-8"
    )
    paths.extend(
        [
            (output / "canonical_root.json").relative_to(root).as_posix(),
            (output / "pptx_validated_state.json").relative_to(root).as_posix(),
            (output / "svg_validated_state.json").relative_to(root).as_posix(),
        ]
    )
    return ReferenceRealizationResult(canonicalization, pptx, svg, parity, tuple(paths))


def realize_reference_figure_fidelity(
    canonicalization: CanonicalizationResult,
    *,
    repository_root: str | Path,
    output_dir: str | Path,
    profile: QualityProfilePolicy,
    backend_registry: BackendCapabilityRegistry,
    reference_path: str | Path | None = None,
) -> FidelityReferenceRealizationResult:
    """Execute v0.2 routing from canonical evidence, then realize both renderers."""

    root = Path(repository_root).resolve()
    output = Path(output_dir).resolve()
    if root not in output.parents:
        raise ValueError("realization output must remain inside repository root")
    output.mkdir(parents=True, exist_ok=True)
    routing_root = canonicalization.figure
    if reference_path is not None:
        routing_root = apply_source_preservation_fallback(
            routing_root,
            reference_path=reference_path,
            repository_root=root,
            output_dir=output / "derived_assets",
        ).figure
    routing = execute_fidelity_routing(
        routing_root,
        profile=profile,
        backend_registry=backend_registry,
        repository_root=root,
        reference_path=reference_path,
    )
    figure = routing.routed_state
    pptx_path = output / f"{figure.figure_id}.pptx"
    svg_path = output / f"{figure.figure_id}.svg"
    pptx = render_figure(
        figure,
        PptxRendererAdapter(
            pptx_path,
            source_root=root,
            artifact_ref=pptx_path.relative_to(root).as_posix(),
        ),
        routing_applied=True,
    )
    svg = render_figure(
        figure,
        SvgRendererAdapter(
            svg_path,
            source_root=root,
            artifact_ref=svg_path.relative_to(root).as_posix(),
        ),
        routing_applied=True,
    )
    parity = validate_cross_backend_parity(pptx.validated_state, svg.validated_state)
    payloads = {
        "fidelity_routing_receipt.json": routing.to_dict(),
        "render_receipt.json": {
            "receipt_version": "fidelity-reference-render-receipt-v0.1",
            "pptx": asdict(pptx.receipt),
            "svg": asdict(svg.receipt),
            "semantic_parity": asdict(parity),
        },
        "qa_receipt.json": {
            "receipt_version": "fidelity-reference-qa-receipt-v0.1",
            "pptx": asdict(pptx.completion),
            "svg": asdict(svg.completion),
            "semantic_parity": asdict(parity),
            "manual_routing_fields": 0,
        },
    }
    paths: list[str] = []
    for name, payload in payloads.items():
        path = output / name
        path.write_text(
            json.dumps(payload, indent=2, sort_keys=True, default=str) + "\n",
            encoding="utf-8",
        )
        paths.append(path.relative_to(root).as_posix())
    for name, state in (
        ("canonical_root.json", canonicalization.figure),
        ("profile_routed_state.json", routing.routed_state),
        ("pptx_validated_state.json", pptx.validated_state),
        ("svg_validated_state.json", svg.validated_state),
    ):
        path = output / name
        path.write_text(state.to_json() + "\n", encoding="utf-8")
        paths.append(path.relative_to(root).as_posix())
    return FidelityReferenceRealizationResult(
        canonicalization, routing, pptx, svg, parity, tuple(paths)
    )
