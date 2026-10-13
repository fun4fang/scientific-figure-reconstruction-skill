"""Convert bounded visual authoring proposals to the existing canonical state.

No new Router/Writer, OCR gate bypass or fabricated authoring-truth provenance.
Explicit content/geometry/style validation precedes the unchanged Router.
"""
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import re

from PIL import Image

from unified_figure.model import enums as E
from unified_figure.model.figure import CanonicalFigure
from unified_figure.model.geometry import BBox, Geometry, Point, ModelValidationError
from unified_figure.model.records import (Component, Style, FontStyle, ProvenanceReference,
    RepresentationDecision, RevisionRecord, EvidenceRecord, Relation, AssetReference, CoverageRecord)
from unified_figure.model.text_authoring import encode_text_authoring
from unified_figure.model.semantic_attachment import encode_attachment, encode_asset_endpoint, site_point
from .visual_authoring import VERSION, validate_proposal, relation_readiness


def _overlap(a, b):
    return min(a.x+a.width, b.x+b.width) > max(a.x, b.x) and min(a.y+a.height, b.y+b.height) > max(a.y, b.y)


def _decision(cid, representation):
    return RepresentationDecision("decision-"+cid, cid, representation, (representation,),
        "Validated functional visual-authoring proposal; inference, not ground truth", E.ConfidenceLevel.HIGH,
        "MEDIUM", "HIGH", E.CostClass.LOW, E.CostClass.LOW, E.CostClass.LOW, (),
        E.FallbackPolicy.FAIL_CLOSED, ("visual-authoring",), E.DecisionStatus.SELECTED)


def canonical_from_visual_authoring(data, source, geometry, *, repository_root, asset_dir):
    validate_proposal(data, (geometry.decoded_width_px, geometry.decoded_height_px))
    from unified_figure.file_boundary import checked_absolute, checked_target
    root = checked_absolute(repository_root); path = checked_target(root, root / source.repository_relative_ref)
    if "sha256:"+hashlib.sha256(path.read_bytes()).hexdigest() != geometry.source_hash:
        raise ModelValidationError("source identity changed")
    if data["background"].upper() != "#FFFFFF":
        raise ModelValidationError("this bounded workflow slice requires a clear white background")
    blocked = [r for r in relation_readiness(data) if r["status"] != "READY"]
    if blocked:
        raise ModelValidationError("hybrid relation REVIEW_REQUIRED before asset extraction: " +
            json.dumps(blocked, sort_keys=True))
    figure = CanonicalFigure.empty("visual-reauthoring-"+geometry.source_hash[-12:], geometry,
        RevisionRecord("reauthoring-root", None, "", (), ("visual-authoring",), E.RevisionStatus.ROOT))
    proposal_hash = hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()
    evidence = EvidenceRecord("visual-authoring", E.EvidenceSourceType.ANALYSIS, "sha256:"+proposal_hash,
        VERSION, "image-only-functional-authoring", (source.repository_relative_ref,), (), 0,
        {"source_hash": geometry.source_hash}, "Model visual inference; scientific correctness requires rendered review.")
    provenance = ProvenanceReference(("visual-authoring",), VERSION, "functional-re-authoring")
    components, decisions, assets, relations = [], [], [], []
    metadata = {"visual.reauthoring.version": VERSION,
                "visual.reauthoring.proposal_hash": proposal_hash,
                "visual.reauthoring.uncertainties": json.dumps(data["uncertainties"]),
                "visual.reauthoring.content_accounted_claim": str(data["all_visible_content_accounted_for"])}
    native_nodes = []
    preserve = list(data["preserve_regions"])
    by_id = {v["id"]: v for v in data["nodes"]}
    for node in data["nodes"]:
        if node["confidence"] != "HIGH":
            preserve.append({"id": node["id"], "bbox": node["bbox"], "reason": "uncertain model node: " + node["evidence"]})
            continue
        b, text_b = BBox(**node["bbox"]), BBox(**node["text_bbox"])
        s = node["style"]
        if not node["evidence"].strip() or not 0 < s["font_size_px"] <= geometry.decoded_height_px:
            raise ModelValidationError("missing visual evidence or invalid font geometry")
        for color in ("fill", "stroke", "text_color"):
            if not re.fullmatch(r"#[0-9a-fA-F]{6}|none", s[color]):
                raise ModelValidationError("non-explicit visual color")
        if s["stroke_width_px"] < 0:
            raise ModelValidationError("negative stroke")
        cid = node["id"]
        is_label = bool(node["content"])
        rep = E.RepresentationType.NATIVE_TEXT if is_label else E.RepresentationType.NATIVE_SIMPLE_SHAPE
        c = Component(cid, E.ComponentType.TEXT if is_label else E.ComponentType.SIMPLE_SHAPE,
            node["kind"] if not is_label else (node["role"] or "node-label"), Geometry(b),
            Style(fill=s["text_color"] if is_label else (s["fill"] if s["fill"] != "none" else None),
                  stroke=s["stroke"] if s["stroke"] != "none" else None, stroke_width=s["stroke_width_px"],
                  font=FontStyle(s["font_family"], s["font_weight"]), font_size=s["font_size_px"],
                  text_alignment=s["alignment"], z_order=1 if is_label else -1), rep, "decision-"+cid,
            provenance=provenance)
        if is_label:
            payload = {"version": "text-authoring-v0.1", "evidence_refs": ["visual-authoring"],
                       "text_bounds": node["text_bbox"], "vertical_anchor": "ctr",
                       "wrap": "NO_WRAP", "autofit": "NONE"}
            if node["kind"] != "text":
                payload["container"] = {"bbox": node["bbox"],
                    "preset": "rect" if node["kind"] == "rectangle" else "roundRect",
                    "fill": s["fill"], "stroke": s["stroke"], "stroke_width_px": s["stroke_width_px"]}
            if node.get('paragraphs'):
                if ''.join(node['content'].split()) != ''.join(''.join(p['content'].split()) for p in node['paragraphs']):
                    raise ModelValidationError('Rich blocks omit or duplicate canonical scientific text')
                payload['paragraphs'] = node['paragraphs']
                # An aggregate hint may be smaller than its explicit child blocks.
                # Derive bounds only from supplied geometry, never from raster/GT.
                boxes = [text_b] + [BBox(**p['bbox']) for p in node['paragraphs']]
                left, top = min(v.x for v in boxes), min(v.y for v in boxes)
                right, bottom = max(v.x+v.width for v in boxes), max(v.y+v.height for v in boxes)
                payload['text_bounds'] = dict(x=left, y=top, width=right-left, height=bottom-top)
                metadata['visual.reauthoring.text_bounds_union.'+cid] = json.dumps({
                    'original_aggregate': node['text_bbox'], 'explicit_block_union': payload['text_bounds']})
            metadata["authoring.text."+cid] = encode_text_authoring(payload, geometry)
            metadata["text."+cid] = node["content"]
        components.append(c); decisions.append(_decision(cid, rep)); native_nodes.append(node)
    from unified_figure.file_boundary import checked_target, save_asset
    asset_folder = checked_target(root, asset_dir)
    with Image.open(path) as image:
        for region in preserve:
            b = BBox(**region["bbox"])
            if any(_overlap(b, BBox(**n["bbox"])) for n in native_nodes):
                raise ModelValidationError("source/native overlap: no scientific masking or duplicate overlay allowed")
            cid = region["id"]; asset_id = "asset-"+cid
            crop = image.crop((round(b.x), round(b.y), round(b.x+b.width), round(b.y+b.height)))
            p = save_asset(crop, asset_folder, asset_id, root=root)
            rep = E.RepresentationType.EXACT_RASTER_ASSET
            asset = AssetReference(asset_id, E.AssetType.RASTER, E.AssetSourceType.SOURCE_EXACT,
                p.relative_to(root).as_posix(), "sha256:"+hashlib.sha256(p.read_bytes()).hexdigest(), b,
                E.AlphaState.OPAQUE, provenance, E.FidelityStatus.PRESERVED, E.Replaceability.FIXED, rep, None)
            assets.append(asset)
            component = Component(cid, E.ComponentType.IMAGE_ASSET, "uncertain-source-region", Geometry(b),
                Style(z_order=0), rep, "decision-"+cid, asset_ref=asset_id,
                review_status=E.ReviewStatus.REQUIRED, provenance=provenance)
            components.append(component)
            # Only explicit regions have eligible endpoint evidence. An
            # uncertain node's crop is not promoted to a certain attachment.
            if cid in {v['id'] for v in data['preserve_regions']}:
                metadata['authoring.asset_endpoint.'+cid] = encode_asset_endpoint(component, asset,
                    geometry.source_hash, proposal_hash)
            decisions.append(_decision(cid, rep))
    realized_ids = {v.component_id for v in components}
    by_id.update({v['id']: v for v in data['preserve_regions']})
    for r in data["connectors"]:
        if r["confidence"] != "HIGH" or r["source"] not in realized_ids or r["target"] not in realized_ids:
            raise ModelValidationError("uncertain/unrealized relation requires explicit source region or review, not invented attachment")
        src, dst = by_id[r["source"]], by_id[r["target"]]
        start, end = site_point(BBox(**src["bbox"]), r["source_site"]), site_point(BBox(**dst["bbox"]), r["target_site"])
        points = list(Point(**p) for p in r["points"])
        if len(points) < 2:
            raise ModelValidationError("connector lacks visual route evidence")
        points[0], points[-1] = start, end
        relation = Relation(r["id"], r["source"], r["target"], E.RelationDirection.SOURCE_TO_TARGET,
            E.RelationType.FLOW, E.RouteType.STRAIGHT if r["routing"] == "straight" else E.RouteType.ELBOW,
            E.AttachmentIntent.NATIVE_ATTACHMENT, Style(stroke=r["stroke"], stroke_width=r["stroke_width_px"],
            arrowhead_end="triangle"), E.ConfidenceLevel.HIGH, E.RepresentationType.NATIVE_RELATION,
            route_points=tuple(points), evidence_refs=("visual-authoring",))
        metadata["authoring.attachment."+r["id"]] = encode_attachment(relation, r["source_site"], r["target_site"], geometry.source_hash)
        relations.append(relation)
    figure = replace(figure, components=tuple(components), assets=tuple(assets), relations=tuple(relations),
        representation_decisions=tuple(decisions), evidence=(evidence,), metadata=metadata,
        coverage=tuple(CoverageRecord("coverage-"+d.value, d, E.CoverageStatus.UNKNOWN, False, (),
                       ("visual-authoring",), E.ReviewStatus.REQUIRED,
                       "Model inventory is not a verified scientific coverage pass") for d in E.CoverageDimension)).with_computed_revision_hash()
    figure.validate()
    return figure
