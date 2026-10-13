from __future__ import annotations

from dataclasses import dataclass, replace
from hashlib import sha256
import math
from pathlib import Path

from PIL import Image, ImageFilter, ImageStat

from unified_figure.model.enums import (
    AlphaState,
    AssetSourceType,
    AssetType,
    ComponentType,
    ConfidenceLevel,
    CostClass,
    DecisionStatus,
    EvidenceSourceType,
    FallbackPolicy,
    FidelityStatus,
    Replaceability,
    RepresentationType,
    RevisionStatus,
    ReviewStatus,
    ReviewDisposition,
    ReviewReason,
    TargetType,
    Severity,
    RouteType,
)
from unified_figure.model.figure import CanonicalFigure, make_stable_id
from unified_figure.model.geometry import BBox
from unified_figure.model.records import (
    AssetReference,
    EvidenceRecord,
    ExplicitFallbackRecord,
    ProvenanceReference,
    ReviewItem,
)
from .context_safety import derive_context_regions, integer_bounds
from .content_gaps import scientific_content_gaps


POLICY_VERSION = "coherent-source-preservation-v0.4-content-completeness"


@dataclass(frozen=True, slots=True)
class CoherentPreservationRegion:
    panel_component_id: str
    bbox: BBox
    preservation_scale: str
    reason: str
    contained_asset_area_fraction: float
    native_shape_count: int
    native_text_count: int
    coherent_group_count: int
    source_color_entropy: float | None = None
    source_edge_density: float | None = None
    source_variance: float | None = None


@dataclass(frozen=True, slots=True)
class SourcePreservationResult:
    figure: CanonicalFigure
    regions: tuple[CoherentPreservationRegion, ...]
    created_asset_paths: tuple[str, ...]


def _contains(outer: BBox, inner: BBox) -> bool:
    center_x = inner.x + inner.width / 2
    center_y = inner.y + inner.height / 2
    return outer.x <= center_x <= outer.x + outer.width and outer.y <= center_y <= outer.y + outer.height


@dataclass(frozen=True, slots=True)
class _SourceRegionMetrics:
    color_entropy: float
    edge_density: float
    variance: float


def _source_region_metrics(source: Image.Image, bbox: BBox) -> _SourceRegionMetrics | None:
    x0 = max(0, round(bbox.x))
    y0 = max(0, round(bbox.y))
    x1 = min(source.width, round(bbox.x + bbox.width))
    y1 = min(source.height, round(bbox.y + bbox.height))
    if x1 <= x0 or y1 <= y0:
        return None
    crop = source.convert("RGB").crop((x0, y0, x1, y1))
    variance = sum(ImageStat.Stat(crop).var) / 3.0
    quantized = crop.quantize(colors=16)
    counts = quantized.getcolors() or []
    total = max(1, sum(count for count, _ in counts))
    entropy = -sum(
        (count / total) * math.log2(count / total)
        for count, _ in counts
        if count
    )
    edges = crop.convert("L").filter(ImageFilter.FIND_EDGES)
    getter = getattr(edges, "get_flattened_data", edges.getdata)
    edge_density = sum(value > 24 for value in getter()) / max(1, crop.width * crop.height)
    return _SourceRegionMetrics(entropy, edge_density, variance)


def _preservation_scale(panel_fraction: float, *, expanded_to_canvas: bool = False) -> str:
    if expanded_to_canvas:
        return "COMPLEX_REGION"
    if panel_fraction < 0.2:
        return "SUBPANEL"
    return "PANEL"


def detect_coherent_preservation_regions(
    figure: CanonicalFigure,
    *,
    reference_path: str | Path | None = None,
) -> tuple[CoherentPreservationRegion, ...]:
    """Detect panel-scale safety-net regions from generic canonical evidence."""

    panels = [item for item in figure.components if item.component_type is ComponentType.PANEL]
    canvas_area = figure.input_geometry.decoded_width_px * figure.input_geometry.decoded_height_px
    source: Image.Image | None = None
    if reference_path is not None:
        reference = Path(reference_path)
        if (
            reference.is_file()
            and "sha256:" + sha256(reference.read_bytes()).hexdigest()
            == figure.input_geometry.source_hash
        ):
            source = Image.open(reference).convert("RGB")
    regions: list[CoherentPreservationRegion] = []
    for panel in panels:
        bbox = panel.geometry.bbox
        nested_panel = any(
            other.component_id != panel.component_id
            and _contains(bbox, other.geometry.bbox)
            and other.geometry.bbox.width * other.geometry.bbox.height < bbox.width * bbox.height * 0.95
            for other in panels
        )
        # A root-sized workflow canvas can legitimately contain subpanels.  Do
        # not let those subpanels suppress the dense-native safety net; only
        # skip nested containers that are not themselves the dominant canvas.
        if nested_panel and (bbox.width * bbox.height) / max(1.0, canvas_area) < 0.75:
            continue
        contained = [
            item
            for item in figure.components
            if item.component_id != panel.component_id and _contains(bbox, item.geometry.bbox)
        ]
        assets = [item for item in contained if item.asset_ref]
        asset_area = sum(item.geometry.bbox.width * item.geometry.bbox.height for item in assets)
        panel_area = max(1.0, bbox.width * bbox.height)
        asset_fraction = min(1.0, asset_area / panel_area)
        shapes = [item for item in contained if item.representation is RepresentationType.NATIVE_SIMPLE_SHAPE]
        texts = [item for item in contained if item.representation is RepresentationType.NATIVE_TEXT]
        default_shapes = [
            item
            for item in shapes
            if (item.style.fill or "#ffffff").lower() in {"#ffffff", "white"}
            and (item.style.stroke or "#111111").lower() in {"#111111", "#000000", "black"}
        ]
        component_ids = {item.component_id for item in contained}
        coherent_groups = [
            group
            for group in figure.groups
            if len(group.member_component_ids) >= 2
            and set(group.member_component_ids).issubset(component_ids)
        ]
        advanced_relations = [
            relation
            for relation in figure.relations
            if relation.source_component_id in component_ids
            and relation.target_component_id in component_ids
            and relation.route_type in {RouteType.CURVED, RouteType.BRANCH, RouteType.SELF_LOOP}
        ]
        reason = ""
        preservation_bbox = bbox
        expanded_to_canvas = False
        source_metrics = _source_region_metrics(source, bbox) if source is not None else None
        if (
            panel_area / max(1.0, canvas_area) >= 0.75
            and not assets
            and len(shapes) >= 8
            and len(texts) < len(shapes) * 0.6
        ):
            reason = "dense native panel lacks source assets and sufficient editable labels"
        elif (
            panel_area / max(1.0, canvas_area) >= 0.2
            and assets
            and asset_fraction < 0.25
            and len(default_shapes) >= 2
        ):
            reason = "complex panel has sparse preserved coverage and default native placeholders"
        elif (
            panel_area / max(1.0, canvas_area) >= 0.45
            and asset_fraction < 0.25
            and len(advanced_relations) >= 2
        ):
            reason = "relation-dense scientific panel requires unsupported advanced route realizations"
            # Routes and labels may intentionally cross the detected panel edge.
            # Preserve the complete coherent figure rather than clipping those
            # dependencies to an analysis-proposed panel boundary.
            preservation_bbox = BBox(
                0.0,
                0.0,
                float(figure.input_geometry.decoded_width_px),
                float(figure.input_geometry.decoded_height_px),
            )
            expanded_to_canvas = True
        elif (
            source_metrics is not None
            and panel_area / max(1.0, canvas_area) >= 0.07
            and not assets
            and 1 <= len(shapes) <= 4
            and len(texts) <= 4
            and coherent_groups
            and source_metrics.color_entropy >= 3.2
            and source_metrics.edge_density >= 0.04
            and source_metrics.variance >= 800.0
        ):
            reason = (
                "source-rich scientific panel is represented by sparse low-detail native "
                "primitives despite coherent group evidence"
            )
        if reason:
            panel_fraction = panel_area / max(1.0, canvas_area)
            regions.append(
                CoherentPreservationRegion(
                    panel_component_id=panel.component_id,
                    bbox=preservation_bbox,
                    preservation_scale=_preservation_scale(
                        panel_fraction,
                        expanded_to_canvas=expanded_to_canvas,
                    ),
                    reason=reason,
                    contained_asset_area_fraction=round(asset_fraction, 6),
                    native_shape_count=len(shapes),
                    native_text_count=len(texts),
                    coherent_group_count=len(coherent_groups),
                    source_color_entropy=(
                        round(source_metrics.color_entropy, 6) if source_metrics is not None else None
                    ),
                    source_edge_density=(
                        round(source_metrics.edge_density, 6) if source_metrics is not None else None
                    ),
                    source_variance=(
                        round(source_metrics.variance, 6) if source_metrics is not None else None
                    ),
                )
            )
    if source is not None:
        source.close()
    return tuple(regions)


def apply_source_preservation_fallback(
    figure: CanonicalFigure,
    *,
    reference_path: str | Path,
    repository_root: str | Path,
    output_dir: str | Path,
) -> SourcePreservationResult:
    """Bind exact coherent source crops as explicit panel-level fallbacks."""

    root = Path(repository_root).resolve()
    reference = Path(reference_path).resolve()
    from unified_figure.file_boundary import checked_target, save_asset
    output = checked_target(repository_root, output_dir)
    if root not in output.parents:
        raise ValueError("source-preservation output must remain inside repository root")
    source_hash = "sha256:" + sha256(reference.read_bytes()).hexdigest()
    if source_hash != figure.input_geometry.source_hash:
        raise ValueError("reference hash does not match authoritative input geometry")
    regions = detect_coherent_preservation_regions(figure, reference_path=reference)
    context = derive_context_regions(figure, regions)
    regions = (*regions, *(
        CoherentPreservationRegion(r.component_id, r.bbox, "LOCAL_SCIENTIFIC_CONTEXT", r.reason,
                                   0.0, 0, 0, 0) for r in context
    ))
    if not regions:
        return SourcePreservationResult(figure, (), ())

    # The analysis panel box is not evidence that labels just outside it can
    # be discarded. Complete the disjoint complement, not the whole canvas.
    with Image.open(reference) as source:
        gaps = scientific_content_gaps(figure, regions, source)
    if gaps:
        donor = next(c for c in figure.components if c.component_id == regions[0].panel_component_id)
        prior = next(d for d in figure.representation_decisions if d.decision_id == donor.representation_decision_id)
        additions=[]; gap_decisions=[]
        for index, bbox in enumerate(gaps):
            cid=make_stable_id(figure.figure_id,'scientific-content-gap',index,str(bbox))
            did=make_stable_id(figure.figure_id,'scientific-content-gap-decision',index,cid)
            additions.append(replace(donor,component_id=cid,component_type=ComponentType.IMAGE_ASSET,
                semantic_role='unresolved scientific context complement',
                geometry=replace(donor.geometry,bbox=bbox),asset_ref=None,parent_component_id=None,
                representation_decision_id=did,renderer_realizations=(),
                provenance=ProvenanceReference(donor.provenance.evidence_refs,POLICY_VERSION,'scientific-context-complement')))
            gap_decisions.append(replace(prior,decision_id=did,component_id=cid))
            regions=(*regions,CoherentPreservationRegion(cid,bbox,'CONTEXT_COMPLEMENT',
                'uncovered source context is not proven expendable',0.,0,0,0))
        figure=replace(figure,components=(*figure.components,*additions),
            representation_decisions=(*figure.representation_decisions,*gap_decisions))

    output.mkdir(parents=True, exist_ok=True)
    components = list(figure.components)
    decisions = list(figure.representation_decisions)
    assets = list(figure.assets)
    fallbacks = list(figure.fallbacks)
    created: list[str] = []
    reviews = list(figure.review_items)
    panel_index = {item.component_id: index for index, item in enumerate(components)}
    decision_index = {item.decision_id: index for index, item in enumerate(decisions)}
    with Image.open(reference) as source_image:
        source = source_image.convert("RGBA")
        for sequence, region in enumerate(regions):
            component = components[panel_index[region.panel_component_id]]
            prior = decisions[decision_index[component.representation_decision_id]]
            bounded = integer_bounds(region.bbox, source.width, source.height)
            region = replace(region, bbox=bounded)
            x0, y0 = int(bounded.x), int(bounded.y)
            x1, y1 = int(bounded.x + bounded.width), int(bounded.y + bounded.height)
            crop = source.crop((x0, y0, x1, y1))
            opaque = crop.getchannel("A").getextrema() == (255, 255)
            save_image = crop.convert("RGB") if opaque else crop
            asset_id = make_stable_id(figure.figure_id, "phase6c-source-preserve-asset", sequence, component.component_id)
            crop_path = save_asset(save_image, output, asset_id, root=root)
            crop_hash = "sha256:" + sha256(crop_path.read_bytes()).hexdigest()
            evidence_refs = tuple(dict.fromkeys((*component.provenance.evidence_refs, *prior.evidence_refs)))
            assets.append(
                AssetReference(
                    asset_id=asset_id,
                    asset_type=AssetType.RASTER,
                    source_type=AssetSourceType.SOURCE_PRESERVED_RASTER,
                    file_ref=crop_path.relative_to(root).as_posix(),
                    content_hash=crop_hash,
                    source_region=region.bbox,
                    alpha_state=AlphaState.OPAQUE if opaque else AlphaState.HAS_ALPHA,
                    provenance=ProvenanceReference(evidence_refs, POLICY_VERSION, "exact-coherent-source-crop"),
                    scientific_fidelity_status=FidelityStatus.PRESERVED,
                    replaceability=Replaceability.FIXED,
                    representation_type=RepresentationType.RASTER_PRESERVE_COMPLEX,
                    backend_requirement="supports_exact_raster",
                )
            )
            new_decision_id = make_stable_id(
                figure.figure_id, "phase6c-source-preserve-decision", sequence, component.component_id
            )
            decisions[decision_index[prior.decision_id]] = replace(prior, status=DecisionStatus.SUPERSEDED)
            new_decision = replace(
                prior,
                decision_id=new_decision_id,
                selected_representation=RepresentationType.RASTER_PRESERVE_COMPLEX,
                candidate_representations=tuple(
                    dict.fromkeys(
                        (
                            RepresentationType.RASTER_PRESERVE_COMPLEX,
                            RepresentationType.NATIVE_SIMPLE_SHAPE,
                            *prior.candidate_representations,
                        )
                    )
                ),
                decision_reason=f"{POLICY_VERSION}: {region.reason}; explicit coherent source-preservation fallback",
                confidence=ConfidenceLevel.HIGH,
                scientific_risk="HIGH_VISUAL_OR_SCIENTIFIC_CONTENT_LOSS_IF_NATIVE_ONLY",
                editability_need="ASSET_LEVEL",
                complexity_estimate=CostClass.HIGH,
                execution_cost_estimate=CostClass.LOW,
                review_cost_estimate=CostClass.LOW,
                required_backend_capabilities=("supports_exact_raster",),
                fallback_policy=FallbackPolicy.ALLOW_EXPLICIT,
                status=DecisionStatus.FALLBACK_SELECTED,
            )
            decisions.append(new_decision)
            fallbacks.append(
                ExplicitFallbackRecord(
                    fallback_id=make_stable_id(
                        figure.figure_id, "phase6c-source-preserve-fallback", sequence, component.component_id
                    ),
                    component_id=component.component_id,
                    original_decision_id=prior.decision_id,
                    fallback_decision_id=new_decision_id,
                    fallback_reason=region.reason,
                    fallback_target=RepresentationType.RASTER_PRESERVE_COMPLEX,
                    backend_capability_evidence_refs=evidence_refs,
                    provenance=ProvenanceReference(
                        evidence_refs,
                        POLICY_VERSION,
                        "explicit-source-preservation-fallback",
                    ),
                )
            )
            components[panel_index[component.component_id]] = replace(
                component,
                geometry=replace(component.geometry, bbox=region.bbox),
                representation=RepresentationType.RASTER_PRESERVE_COMPLEX,
                representation_decision_id=new_decision_id,
                asset_ref=asset_id,
                provenance=ProvenanceReference(
                    evidence_refs,
                    POLICY_VERSION,
                    "route-coherent-panel-to-source-preservation",
                ),
            )
            created.append(crop_path.relative_to(root).as_posix())

    regions = tuple(replace(r, bbox=integer_bounds(r.bbox, source.width, source.height)) for r in regions)
    evidence = EvidenceRecord(
        evidence_id=make_stable_id(figure.figure_id, "phase6c-source-preserve-evidence", 0, source_hash),
        source_type=EvidenceSourceType.ROUTING,
        source_hash=source_hash,
        producer=POLICY_VERSION,
        operation="detect-and-bind-coherent-source-preservation",
        input_refs=(figure.revision.revision_id,),
        output_refs=tuple(item.panel_component_id for item in regions),
        run_index=0,
        tool_runtime_info={"model_calls": "0", "local_image_crops": str(len(regions))},
        notes="Fidelity safety net; source crops are exact and the fallback is explicit.",
    )
    for region in context:
        if region.review_required:
            reviews.append(ReviewItem(
                review_id=make_stable_id(figure.figure_id, 'context-boundary-review', len(reviews), region.component_id),
                target_type=TargetType.COMPONENT, target_id=region.component_id,
                reason_type=ReviewReason.GEOMETRY_CONFLICT, severity=Severity.ERROR,
                evidence_refs=(evidence.evidence_id,),
                candidate_actions=('verify complete axes/legend context and overlapping source bounds',),
                candidate_representations=(RepresentationType.EXACT_RASTER_ASSET,),
                status=ReviewStatus.PENDING, disposition=ReviewDisposition.PENDING,
                created_by=POLICY_VERSION, created_at='deterministic:context-review'))
    result = replace(
        figure,
        components=tuple(components),
        assets=tuple(assets),
        representation_decisions=tuple(decisions),
        fallbacks=tuple(fallbacks),
        evidence=(*figure.evidence, evidence),
        review_items=tuple(reviews),
        metadata={
            **figure.metadata,
            "phase6c.source_preservation_policy": POLICY_VERSION,
            "phase6c.source_preservation_regions": ",".join(item.panel_component_id for item in regions),
            "phase6f.source_preservation_scales": ",".join(
                f"{item.panel_component_id}:{item.preservation_scale}" for item in regions
            ),
        },
        revision=replace(
            figure.revision,
            revision_id=make_stable_id(figure.figure_id, "phase6c-source-preserve-revision", 0, source_hash),
            parent_revision_id=figure.revision.revision_id,
            canonical_state_hash="",
            change_manifest=(
                *figure.revision.change_manifest,
                "bind explicit coherent source-preservation fallback",
            ),
            evidence_refs=tuple(dict.fromkeys((*figure.revision.evidence_refs, evidence.evidence_id))),
            status=RevisionStatus.CANDIDATE,
        ),
    ).with_computed_revision_hash()
    result.validate()
    return SourcePreservationResult(result, regions, tuple(created))
