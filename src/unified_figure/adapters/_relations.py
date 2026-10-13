from __future__ import annotations

from dataclasses import dataclass
from math import hypot

from unified_figure.model.enums import RouteType
from unified_figure.model.geometry import BBox, Point
from unified_figure.model.records import Relation


@dataclass(frozen=True, slots=True)
class RelationPath:
    points: tuple[Point, ...]
    route_type: RouteType
    derived_from: str


def _edge_point(source: BBox, target: BBox) -> Point:
    source_center = Point(source.x + source.width / 2, source.y + source.height / 2)
    target_center = Point(target.x + target.width / 2, target.y + target.height / 2)
    dx = target_center.x - source_center.x
    dy = target_center.y - source_center.y
    if dx == 0 and dy == 0:
        return source_center
    scale_x = source.width / 2 / abs(dx) if dx else float("inf")
    scale_y = source.height / 2 / abs(dy) if dy else float("inf")
    scale = min(scale_x, scale_y)
    return Point(source_center.x + dx * scale, source_center.y + dy * scale)


def relation_path(relation: Relation, source: BBox, target: BBox, *, align_orthogonal: bool = False) -> RelationPath:
    if len(relation.route_points) >= 2:
        points = list(relation.route_points)
        if align_orthogonal and relation.route_type is RouteType.ELBOW:
            # Intake binds endpoints to exact sites; proposal corners may have
            # integer-pixel rounding. Propagate only that <=1px discrepancy to
            # adjoining corners, never invent a new relation/site/corridor.
            for i, j in ((0, 1), (len(points)-1, len(points)-2)):
                a, b = points[i], points[j]
                if abs(a.x-b.x) <= 1:
                    points[j] = Point(a.x, b.y)
                elif abs(a.y-b.y) <= 1:
                    points[j] = Point(b.x, a.y)
        return RelationPath(tuple(points), relation.route_type, "CANONICAL_ROUTE_POINTS")
    start = _edge_point(source, target)
    end = _edge_point(target, source)
    if relation.route_type is RouteType.ELBOW:
        horizontal_first = abs(end.x - start.x) >= abs(end.y - start.y)
        turn = Point(end.x, start.y) if horizontal_first else Point(start.x, end.y)
        return RelationPath((start, turn, end), relation.route_type, "BBOX_SIDE_ORTHOGONAL")
    return RelationPath((start, end), relation.route_type, "BBOX_SIDE_STRAIGHT")


def orthogonal_connector_preset(points: tuple[Point, ...], scale: float):
    """Faithful native bentConnector2/3, including vertical-first/outside bends.

    Return xfrm and preset parameters, not disconnected decorative segments.
    Both true endpoint connections remain on the single p:cxnSp object.
    Unsupported multi-bend/diagonal routes fail closed, not silently flattened.
    """
    from unified_figure.model.geometry import ModelValidationError
    cleaned = []
    for p in points:
        if cleaned and p == cleaned[-1]: continue
        if len(cleaned) >= 2:
            a, b = cleaned[-2:]
            if (a.x == b.x == p.x) or (a.y == b.y == p.y): cleaned.pop()
        cleaned.append(p)
    if not 3 <= len(cleaned) <= 4:
        raise ModelValidationError('REVIEW_REQUIRED: elbow needs a supported two/three-segment native route')
    axes = []
    for a, b in zip(cleaned, cleaned[1:]):
        if a.y == b.y: axes.append('H')
        elif a.x == b.x: axes.append('V')
        else: raise ModelValidationError('REVIEW_REQUIRED: non-orthogonal elbow route')
    if any(a == b for a,b in zip(axes,axes[1:])):
        raise ModelValidationError('REVIEW_REQUIRED: invalid elbow turns')
    start, end = cleaned[0], cleaned[-1]
    rotated = axes[0] == 'V'
    # Rotate the preset's horizontal-first local frame by 90 degrees for VHV.
    dx, dy = ((end.y-start.y, start.x-end.x) if rotated else (end.x-start.x, end.y-start.y))
    width, height = abs(dx), abs(dy)
    if min(width,height) == 0:
        raise ModelValidationError('REVIEW_REQUIRED: degenerate native elbow frame')
    local_x = ((cleaned[1].y-start.y) if rotated else (cleaned[1].x-start.x))
    adjustment = round(local_x/dx*100000)
    # Outside-endpoint doglegs legitimately use guides outside [0,100000].
    # Clamping would cross the endpoint objects and lose the canonical path.
    x = round(((start.x+end.x)/2-width/2)*scale)
    y = round(((start.y+end.y)/2-height/2)*scale)
    return dict(x=x,y=y,cx=max(1,round(width*scale)),cy=max(1,round(height*scale)),
                rotation=5400000 if rotated else 0,flip_h=dx<0,flip_v=dy<0,
                preset='bentConnector2' if len(cleaned)==3 else 'bentConnector3',
                adjustment=adjustment)


def endpoint_distance_to_bbox(point: Point, bbox: BBox) -> float:
    dx = max(bbox.x - point.x, 0.0, point.x - (bbox.x + bbox.width))
    dy = max(bbox.y - point.y, 0.0, point.y - (bbox.y + bbox.height))
    if dx or dy:
        return hypot(dx, dy)
    return min(
        abs(point.x - bbox.x),
        abs(point.x - (bbox.x + bbox.width)),
        abs(point.y - bbox.y),
        abs(point.y - (bbox.y + bbox.height)),
    )
