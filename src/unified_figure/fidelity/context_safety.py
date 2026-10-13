"""Local scientific-context protection from existing canonical evidence.

No case IDs, labels, expected outputs or model calls. A crop is not a coverage
certificate: charts/photos/mechanisms need their surrounding panel context.
Native evidence remains canonical; protected pixels must not be overpainted.
"""
from __future__ import annotations

from dataclasses import dataclass
import math

from unified_figure.adapters._layout import fit_text_to_bbox
from unified_figure.model.enums import ComponentType, RepresentationType
from unified_figure.model.geometry import BBox

POLICY_VERSION = 'scientific-context-preservation-v0.1'
SCIENTIFIC_TYPES = {ComponentType.CHART, ComponentType.COMPLEX_VISUAL, ComponentType.IMAGE_ASSET}


def covers(outer: BBox, inner: BBox) -> bool:
    return (outer.x <= inner.x and outer.y <= inner.y
            and outer.x + outer.width >= inner.x + inner.width
            and outer.y + outer.height >= inner.y + inner.height)


def overlap(a: BBox, b: BBox) -> bool:
    return (min(a.x+a.width, b.x+b.width) > max(a.x, b.x)
            and min(a.y+a.height, b.y+b.height) > max(a.y, b.y))


def integer_bounds(box: BBox, width: int, height: int) -> BBox:
    """Outward pixel rounding, with placement identical to extracted pixels."""
    if not all(math.isfinite(v) for v in (box.x, box.y, box.width, box.height)):
        raise ValueError('Non-finite source bounds')
    if box.x < 0 or box.y < 0 or box.width <= 0 or box.height <= 0:
        raise ValueError('Invalid source bounds')
    if box.x + box.width > width or box.y + box.height > height:
        raise ValueError('Out-of-bounds source region; do not silently truncate science')
    x, y = math.floor(box.x), math.floor(box.y)
    right, bottom = math.ceil(box.x+box.width), math.ceil(box.y+box.height)
    return BBox(float(x), float(y), float(right-x), float(bottom-y))


def context_envelope(box: BBox, peers: tuple[BBox, ...], width: int, height: int) -> BBox:
    """Extend context into inter-panel margins without entering peer cores.

    Mid-gap separators are deterministic geometry, not guessed semantic labels.
    Ambiguous overlapping cores retain their original bounds rather than invent
    a partition. This cannot certify an inaccurate analysis panel boundary.
    """
    if any(overlap(box, p) and not covers(box, p) and not covers(p, box) for p in peers):
        return integer_bounds(box, width, height)
    left, top, right, bottom = 0., 0., float(width), float(height)
    for p in peers:
        if covers(box, p) or covers(p, box):
            continue
        vertical = min(box.y+box.height, p.y+p.height) > max(box.y, p.y)
        horizontal = min(box.x+box.width, p.x+p.width) > max(box.x, p.x)
        if vertical and p.x+p.width <= box.x:
            left = max(left, (p.x+p.width+box.x)/2)
        if vertical and p.x >= box.x+box.width:
            right = min(right, (box.x+box.width+p.x)/2)
        if horizontal and p.y+p.height <= box.y:
            top = max(top, (p.y+p.height+box.y)/2)
        if horizontal and p.y >= box.y+box.height:
            bottom = min(bottom, (box.y+box.height+p.y)/2)
    return integer_bounds(BBox(left, top, right-left, bottom-top), width, height)


@dataclass(frozen=True)
class ContextRegion:
    component_id: str
    bbox: BBox
    reason: str
    review_required: bool = False


def derive_context_regions(figure, existing_regions=()) -> tuple[ContextRegion, ...]:
    scientific = [c for c in figure.components if c.component_type in SCIENTIFIC_TYPES and c.asset_ref]
    panels = [c for c in figure.components if c.component_type is ComponentType.PANEL]
    width, height = figure.input_geometry.decoded_width_px, figure.input_geometry.decoded_height_px
    existing = [r.bbox for r in existing_regions] + [c.geometry.bbox for c in figure.components
        if c.asset_ref and c.provenance.operation == 'route-coherent-panel-to-source-preservation']
    # Avoid a redundant parent raster when a nested scientific panel completely
    # owns that parent's science. Do not suppress a parent containing other data.
    anchors = [p for p in panels if any(covers(p.geometry.bbox, c.geometry.bbox) for c in scientific)]
    anchors = [p for p in anchors if not any(
        q.component_id != p.component_id and covers(p.geometry.bbox, q.geometry.bbox)
        and all(not covers(p.geometry.bbox, c.geometry.bbox) or covers(q.geometry.bbox, c.geometry.bbox)
                for c in scientific) for q in anchors)]
    anchors += [c for c in scientific if not any(covers(p.geometry.bbox, c.geometry.bbox) for p in anchors)]
    # Already-preserved coherent regions keep their prior decision. New peers
    # constrain context extension, including panels whose science was rejected.
    peer_boxes = tuple(p.geometry.bbox for p in panels) + tuple(c.geometry.bbox for c in anchors)
    result = []
    for c in anchors:
        box = c.geometry.bbox
        if any(covers(b, box) for b in existing):
            continue
        ambiguous = any(overlap(box, p) and not covers(box, p) and not covers(p, box) for p in peer_boxes)
        isolated_object = c.component_type is not ComponentType.PANEL and len(anchors) == 1
        envelope = integer_bounds(box, width, height) if isolated_object else context_envelope(box, peer_boxes, width, height)
        result.append(ContextRegion(c.component_id, envelope,
            'scientific-context: preserve data with axes/legend/labels in its local panel envelope'
            + ('; UNRESOLVED_CONTEXT_BOUNDS_REVIEW_REQUIRED' if ambiguous or isolated_object else ''),
            ambiguous or isolated_object))
    protected = existing + [r.bbox for r in result]
    for c in figure.components:
        if any(covers(b, c.geometry.bbox) for b in protected):
            continue
        text = figure.metadata.get('text.'+c.component_id, '')
        if c.representation is RepresentationType.NATIVE_TEXT:
            fit = fit_text_to_bbox(text, c.geometry.bbox, c.style.font_size)
            aggregate = ';' in text  # spatial list has no individual label geometry
            asset_conflict = any(overlap(c.geometry.bbox, a.geometry.bbox) for a in scientific)
            unsafe = aggregate or not fit.fits_bbox or fit.font_size_px < 5 or asset_conflict
            if not unsafe:
                continue
            reason = 'scientific-context: uncertain text geometry/readability or unvalidated asset overlay'
        elif c.representation is RepresentationType.NATIVE_SIMPLE_SHAPE and text:
            # Keep text evidence, but do not invent label placement inside a box.
            reason = 'scientific-context: label-bearing shape has no independent native text geometry'
        else:
            continue
        result.append(ContextRegion(c.component_id, integer_bounds(c.geometry.bbox, width, height), reason))
    return tuple(result)
