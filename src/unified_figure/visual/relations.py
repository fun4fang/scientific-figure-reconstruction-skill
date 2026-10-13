from __future__ import annotations

from dataclasses import asdict, dataclass
from math import hypot

from unified_figure.adapters._relations import endpoint_distance_to_bbox, relation_path
from unified_figure.model.enums import ComponentType
from unified_figure.model.figure import CanonicalFigure
from unified_figure.model.geometry import BBox, Point


@dataclass(frozen=True, slots=True)
class RelationGeometryItem:
    relation_id: str
    route_type: str
    route_source: str
    point_count: int
    source_endpoint_distance_px: float
    target_endpoint_distance_px: float
    non_endpoint_collision_count: int
    path_length_ratio: float
    arrow_expected: bool


def _segment_intersects_bbox(start: Point, end: Point, bbox: BBox) -> bool:
    steps = max(2, min(100, int(max(abs(end.x - start.x), abs(end.y - start.y))) + 1))
    for index in range(steps + 1):
        fraction = index / steps
        x = start.x + (end.x - start.x) * fraction
        y = start.y + (end.y - start.y) * fraction
        if bbox.x <= x <= bbox.x + bbox.width and bbox.y <= y <= bbox.y + bbox.height:
            return True
    return False


def relation_geometry_summary(figure: CanonicalFigure) -> dict[str, object]:
    components = {item.component_id: item for item in figure.components}
    items: list[RelationGeometryItem] = []
    for relation in figure.relations:
        source = components[relation.source_component_id].geometry.bbox
        target = components[relation.target_component_id].geometry.bbox
        path = relation_path(relation, source, target)
        collisions = 0
        for component in figure.components:
            if component.component_id in {relation.source_component_id, relation.target_component_id}:
                continue
            if component.component_type is ComponentType.PANEL:
                continue
            if any(
                _segment_intersects_bbox(start, end, component.geometry.bbox)
                for start, end in zip(path.points, path.points[1:])
            ):
                collisions += 1
        length = sum(
            hypot(end.x - start.x, end.y - start.y)
            for start, end in zip(path.points, path.points[1:])
        )
        direct = hypot(path.points[-1].x - path.points[0].x, path.points[-1].y - path.points[0].y)
        items.append(
            RelationGeometryItem(
                relation_id=relation.relation_id,
                route_type=relation.route_type.value,
                route_source=path.derived_from,
                point_count=len(path.points),
                source_endpoint_distance_px=round(endpoint_distance_to_bbox(path.points[0], source), 4),
                target_endpoint_distance_px=round(endpoint_distance_to_bbox(path.points[-1], target), 4),
                non_endpoint_collision_count=collisions,
                path_length_ratio=round(length / max(1.0, direct), 4),
                arrow_expected=bool(
                    relation.visual_style.arrowhead_start
                    or relation.visual_style.arrowhead_end
                    or relation.direction.value != "UNDIRECTED"
                ),
            )
        )
    return {
        "relation_count": len(items),
        "zero_distance_source_endpoints": sum(item.source_endpoint_distance_px <= 0.5 for item in items),
        "zero_distance_target_endpoints": sum(item.target_endpoint_distance_px <= 0.5 for item in items),
        "non_endpoint_collision_total": sum(item.non_endpoint_collision_count for item in items),
        "path_length_anomaly_count": sum(item.path_length_ratio > 5 for item in items),
        "items": [asdict(item) for item in items],
    }
