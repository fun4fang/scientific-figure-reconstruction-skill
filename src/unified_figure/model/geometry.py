from __future__ import annotations

from dataclasses import dataclass

from .enums import CoordinateSpace, Orientation


class ModelValidationError(ValueError):
    """Raised when canonical state violates a fail-closed invariant."""


def require_nonempty(value: str, field_name: str) -> None:
    if not value or not value.strip():
        raise ModelValidationError(f"{field_name} must be non-empty")


@dataclass(frozen=True, slots=True)
class Point:
    x: float
    y: float

    def __post_init__(self) -> None:
        object.__setattr__(self, "x", float(self.x))
        object.__setattr__(self, "y", float(self.y))


@dataclass(frozen=True, slots=True)
class BBox:
    x: float
    y: float
    width: float
    height: float

    def __post_init__(self) -> None:
        object.__setattr__(self, "x", float(self.x))
        object.__setattr__(self, "y", float(self.y))
        object.__setattr__(self, "width", float(self.width))
        object.__setattr__(self, "height", float(self.height))

    def validate(self) -> None:
        if self.width < 0:
            raise ModelValidationError("bbox width must be nonnegative")
        if self.height < 0:
            raise ModelValidationError("bbox height must be nonnegative")


@dataclass(frozen=True, slots=True)
class Polyline:
    points: tuple[Point, ...]

    def validate(self) -> None:
        if len(self.points) < 2:
            raise ModelValidationError("polyline requires at least two points")


@dataclass(frozen=True, slots=True)
class PathReference:
    path_id: str
    content_hash: str

    def validate(self) -> None:
        require_nonempty(self.path_id, "path_id")
        require_nonempty(self.content_hash, "content_hash")


@dataclass(frozen=True, slots=True)
class Geometry:
    bbox: BBox
    coordinate_space: CoordinateSpace = CoordinateSpace.SOURCE_PIXELS
    polyline: Polyline | None = None
    path_reference: PathReference | None = None

    def validate(self) -> None:
        self.bbox.validate()
        if self.polyline is not None:
            self.polyline.validate()
        if self.path_reference is not None:
            self.path_reference.validate()


@dataclass(frozen=True, slots=True)
class AuthoritativeInputGeometry:
    source_width_px: int
    source_height_px: int
    decoded_width_px: int
    decoded_height_px: int
    coordinate_space: CoordinateSpace
    orientation: Orientation
    source_hash: str
    geometry_source: str

    def validate(self) -> None:
        for field_name in (
            "source_width_px",
            "source_height_px",
            "decoded_width_px",
            "decoded_height_px",
        ):
            if getattr(self, field_name) <= 0:
                raise ModelValidationError(f"{field_name} must be positive")
        require_nonempty(self.source_hash, "source_hash")
        require_nonempty(self.geometry_source, "geometry_source")
