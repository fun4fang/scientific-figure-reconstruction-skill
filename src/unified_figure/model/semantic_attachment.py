"""Optional semantic sites in canonical metadata; legacy relations unchanged."""
import json
import math
import re

from .geometry import BBox, Point, ModelValidationError
from .text_authoring import get_text_authoring
from .enums import AssetSourceType, AssetType, ComponentType, ConfidenceLevel, CoordinateSpace, RepresentationType

SITES = {"top_center": 0, "left_center": 1, "bottom_center": 2, "right_center": 3}
KEY = "authoring.attachment."
ASSET_KEY = "authoring.asset_endpoint."
VALID_NATIVE_ENDPOINT = "VALID_NATIVE_ENDPOINT"
VALID_PRESERVED_ASSET_ENDPOINT = "VALID_PRESERVED_ASSET_ENDPOINT"
UNRESOLVED_ENDPOINT = "UNRESOLVED_ENDPOINT"


def encode_asset_endpoint(component, asset, source_hash, proposal_hash):
    """Bind an exact source crop to its object, input and inference provenance.

    A crop is a movable picture, not a claim about an internal scientific model.
    No invisible anchor/rectangle is introduced.
    """
    b = component.geometry.bbox
    return json.dumps({"version": "preserved-asset-endpoint-v0.1",
        "component_id": component.component_id, "asset_id": asset.asset_id,
        "asset_hash": asset.content_hash, "source_hash": source_hash,
        "proposal_hash": proposal_hash, "coordinate_space": "SOURCE_PIXELS",
        "bbox": dict(x=b.x, y=b.y, width=b.width, height=b.height)},
        sort_keys=True, separators=(",", ":"))


def endpoint_status(figure, component):
    """Type/provenance eligibility only; relation confidence is checked separately."""
    authoring = get_text_authoring(figure, component)
    preset = authoring.get("container", {}).get("preset") if authoring else None
    if component.representation in {RepresentationType.NATIVE_TEXT, RepresentationType.NATIVE_SIMPLE_SHAPE}:
        if preset in {"rect", "roundRect"} or component.semantic_role in {"rectangle", "rounded_rectangle", "rect", "roundRect"}:
            return VALID_NATIVE_ENDPOINT
        return UNRESOLVED_ENDPOINT
    if component.component_type is not ComponentType.IMAGE_ASSET or component.representation is not RepresentationType.EXACT_RASTER_ASSET:
        return UNRESOLVED_ENDPOINT
    raw = figure.metadata.get(ASSET_KEY + component.component_id)
    if raw is None:
        return UNRESOLVED_ENDPOINT
    try:
        data = json.loads(raw)
        asset = next(a for a in figure.assets if a.asset_id == component.asset_ref)
        box = BBox(**data["bbox"])
        expected = {"version", "component_id", "asset_id", "asset_hash", "source_hash", "proposal_hash", "coordinate_space", "bbox"}
        if set(data) != expected or data["version"] != "preserved-asset-endpoint-v0.1":
            raise ValueError("invalid asset endpoint fields")
        if (data["component_id"] != component.component_id or data["asset_id"] != asset.asset_id
                or data["source_hash"] != figure.input_geometry.source_hash
                or data["asset_hash"] != asset.content_hash or box != component.geometry.bbox
                or box != asset.source_region or data["coordinate_space"] != "SOURCE_PIXELS"
                or component.geometry.coordinate_space is not CoordinateSpace.SOURCE_PIXELS
                or asset.source_type is not AssetSourceType.SOURCE_EXACT or asset.asset_type is not AssetType.RASTER
                or not asset.file_ref or not re.fullmatch(r"sha256:[0-9a-f]{64}", asset.content_hash or "")
                or not re.fullmatch(r"[0-9a-f]{64}", data["proposal_hash"])
                or data["proposal_hash"] != figure.metadata.get("visual.reauthoring.proposal_hash")):
            raise ValueError("asset endpoint identity/bounds mismatch")
        if not any(e.evidence_id in component.provenance.evidence_refs
                   and e.evidence_id in asset.provenance.evidence_refs
                   and e.source_hash == "sha256:" + data["proposal_hash"]
                   and e.tool_runtime_info.get("source_hash") == data["source_hash"] for e in figure.evidence):
            raise ValueError("asset endpoint lacks bound source/proposal evidence")
        if (not all(math.isfinite(v) for v in (box.x, box.y, box.width, box.height))
                or min(box.width, box.height) <= 0 or min(box.x, box.y) < 0
                or box.x + box.width > figure.input_geometry.decoded_width_px
                or box.y + box.height > figure.input_geometry.decoded_height_px):
            raise ValueError("invalid asset endpoint geometry")
    except (ValueError, KeyError, TypeError, StopIteration) as exc:
        raise ModelValidationError("invalid preserved asset endpoint: " + str(exc)) from exc
    return VALID_PRESERVED_ASSET_ENDPOINT


def site_point(box, site):
    x, y, w, h = box.x, box.y, box.width, box.height
    return {"top_center": Point(x+w/2, y), "left_center": Point(x, y+h/2),
            "bottom_center": Point(x+w/2, y+h), "right_center": Point(x+w, y+h/2)}[site]


def outer_bounds(figure, component):
    authoring = get_text_authoring(figure, component)
    if authoring and authoring.get("container"):
        return BBox(**authoring["container"]["bbox"])
    return component.geometry.bbox


def encode_attachment(relation, source_site, target_site, source_hash):
    if source_site not in SITES or target_site not in SITES:
        raise ModelValidationError("unsupported attachment site")
    data = {"version": "semantic-attachment-v0.1", "source": relation.source_component_id,
            "target": relation.target_component_id, "source_site": source_site,
            "target_site": target_site, "source_hash": source_hash}
    return json.dumps(data, sort_keys=True, separators=(",", ":"))


def get_attachment(figure, relation):
    raw = figure.metadata.get(KEY + relation.relation_id)
    if raw is None:
        if figure.metadata.get('visual.reauthoring.version') and any(
                c.component_id in {relation.source_component_id, relation.target_component_id}
                and c.component_type is ComponentType.IMAGE_ASSET for c in figure.components):
            raise ModelValidationError('UNRESOLVED_ENDPOINT: hybrid relation lacks semantic attachment evidence')
        return None
    data = json.loads(raw)
    if set(data) != {"version", "source", "target", "source_site", "target_site", "source_hash"} or data["version"] != "semantic-attachment-v0.1":
        raise ModelValidationError("invalid attachment evidence")
    if data["source"] != relation.source_component_id or data["target"] != relation.target_component_id or data["source_hash"] != figure.input_geometry.source_hash:
        raise ModelValidationError("semantic attachment source/identity mismatch")
    components = {c.component_id: c for c in figure.components}
    points = []
    for side in ("source", "target"):
        c = components[data[side]]
        status = endpoint_status(figure, c)
        if data[side+"_site"] not in SITES or status == UNRESOLVED_ENDPOINT:
            raise ModelValidationError("UNRESOLVED_ENDPOINT: attachment requires a real container or bound exact source asset")
        if status == VALID_PRESERVED_ASSET_ENDPOINT and (
                relation.confidence is not ConfidenceLevel.HIGH
                or not set(relation.evidence_refs).intersection(c.provenance.evidence_refs)):
            raise ModelValidationError('uncertain/unbound hybrid relation evidence')
        p = site_point(outer_bounds(figure, c), data[side+"_site"])
        if not all(math.isfinite(v) for v in (p.x, p.y)):
            raise ModelValidationError("non-finite attachment")
        points.append(p)
    if len(relation.route_points) < 2 or any(abs(a.x-b.x) > 0.01 or abs(a.y-b.y) > 0.01 for a, b in zip((relation.route_points[0], relation.route_points[-1]), points)):
        raise ModelValidationError("canonical route contradicts semantic attachment")
    return data
