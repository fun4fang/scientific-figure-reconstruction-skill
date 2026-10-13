from __future__ import annotations

from dataclasses import asdict, dataclass
from hashlib import sha256
import json
from math import isfinite

from .proposal import ProposalBBox


GEOMETRY_NORMALIZATION_POLICY_VERSION = "GEOMETRY_NORMALIZATION_POLICY_V0_2"


@dataclass(frozen=True, slots=True)
class GeometryNormalizationRecord:
    target_id: str
    field_name: str
    disposition: str
    reason: str
    original: ProposalBBox
    normalized: ProposalBBox | None


@dataclass(frozen=True, slots=True)
class GeometryNormalizationPolicyV0_2:
    """Conservative, coordinate-aware geometry recovery policy."""

    policy_version: str = GEOMETRY_NORMALIZATION_POLICY_VERSION
    boundary_tolerance_ratio: float = 0.005
    minimum_pixel_tolerance: float = 1.0

    def __post_init__(self) -> None:
        if not 0.0 <= self.boundary_tolerance_ratio <= 0.02:
            raise ValueError("boundary_tolerance_ratio must be within [0, 0.02]")
        if not 0.0 <= self.minimum_pixel_tolerance <= 4.0:
            raise ValueError("minimum_pixel_tolerance must be within [0, 4]")

    def policy_hash(self) -> str:
        payload = json.dumps(asdict(self), sort_keys=True, separators=(",", ":"))
        return "sha256:" + sha256(payload.encode("utf-8")).hexdigest()

    def normalize(
        self,
        bbox: ProposalBBox,
        *,
        target_id: str,
        field_name: str,
        authoritative_width: int,
        authoritative_height: int,
    ) -> GeometryNormalizationRecord:
        values = (bbox.x, bbox.y, bbox.width, bbox.height)
        if not all(isfinite(float(value)) for value in values):
            return GeometryNormalizationRecord(
                target_id,
                field_name,
                "REJECTED",
                "NONFINITE_GEOMETRY",
                bbox,
                None,
            )
        if bbox.coordinate_space not in {"SOURCE_PIXELS", "NORMALIZED"}:
            return GeometryNormalizationRecord(
                target_id,
                field_name,
                "REJECTED",
                "IMPOSSIBLE_COORDINATE_SPACE",
                bbox,
                None,
            )
        if bbox.width < 0 or bbox.height < 0:
            return GeometryNormalizationRecord(
                target_id,
                field_name,
                "REJECTED",
                "NEGATIVE_SIZE",
                bbox,
                None,
            )

        limit_x = 1.0 if bbox.coordinate_space == "NORMALIZED" else float(authoritative_width)
        limit_y = 1.0 if bbox.coordinate_space == "NORMALIZED" else float(authoritative_height)
        tolerance_x = (
            self.boundary_tolerance_ratio
            if bbox.coordinate_space == "NORMALIZED"
            else max(self.minimum_pixel_tolerance, limit_x * self.boundary_tolerance_ratio)
        )
        tolerance_y = (
            self.boundary_tolerance_ratio
            if bbox.coordinate_space == "NORMALIZED"
            else max(self.minimum_pixel_tolerance, limit_y * self.boundary_tolerance_ratio)
        )
        left_overflow = max(0.0, -bbox.x)
        top_overflow = max(0.0, -bbox.y)
        right_overflow = max(0.0, bbox.x + bbox.width - limit_x)
        bottom_overflow = max(0.0, bbox.y + bbox.height - limit_y)
        if not any((left_overflow, top_overflow, right_overflow, bottom_overflow)):
            return GeometryNormalizationRecord(
                target_id,
                field_name,
                "ACCEPTED",
                "IN_BOUNDS",
                bbox,
                bbox,
            )

        overlaps = (
            bbox.x < limit_x
            and bbox.y < limit_y
            and bbox.x + bbox.width > 0
            and bbox.y + bbox.height > 0
        )
        minor = (
            left_overflow <= tolerance_x
            and right_overflow <= tolerance_x
            and top_overflow <= tolerance_y
            and bottom_overflow <= tolerance_y
        )
        if overlaps and minor:
            x = max(0.0, bbox.x)
            y = max(0.0, bbox.y)
            right = min(limit_x, bbox.x + bbox.width)
            bottom = min(limit_y, bbox.y + bbox.height)
            normalized = ProposalBBox(
                x=x,
                y=y,
                width=max(0.0, right - x),
                height=max(0.0, bottom - y),
                coordinate_space=bbox.coordinate_space,
            )
            return GeometryNormalizationRecord(
                target_id,
                field_name,
                "NORMALIZED",
                "MINOR_BOUNDARY_OVERFLOW",
                bbox,
                normalized,
            )
        return GeometryNormalizationRecord(
            target_id,
            field_name,
            "QUARANTINED",
            "MAJOR_OUT_OF_BOUNDS",
            bbox,
            None,
        )


DEFAULT_GEOMETRY_NORMALIZATION_POLICY_V0_2 = GeometryNormalizationPolicyV0_2()
