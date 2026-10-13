"""Deterministic local visual QA for first-party reconstruction artifacts."""

from .assets import (
    AssetVerification,
    RenderedAssetVerification,
    verify_rendered_asset,
    verify_source_asset,
)
from .comparison import ComparisonReceipt, build_comparison_artifacts, compute_pixel_metrics
from .raster import RasterReceipt, PowerPointExportError, rasterize_pptx, rasterize_svg
from .relations import RelationGeometryItem, relation_geometry_summary

__all__ = [
    "AssetVerification",
    "ComparisonReceipt",
    "PowerPointExportError",
    "RasterReceipt",
    "RenderedAssetVerification",
    "RelationGeometryItem",
    "build_comparison_artifacts",
    "compute_pixel_metrics",
    "rasterize_pptx",
    "rasterize_svg",
    "relation_geometry_summary",
    "verify_rendered_asset",
    "verify_source_asset",
]
