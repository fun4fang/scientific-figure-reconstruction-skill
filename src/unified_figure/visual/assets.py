from __future__ import annotations

from dataclasses import asdict, dataclass
from hashlib import sha256
from pathlib import Path

import numpy as np
from PIL import Image, ImageChops

from unified_figure.model.geometry import BBox


@dataclass(frozen=True, slots=True)
class AssetVerification:
    asset_id: str
    source_sha256: str
    crop_sha256: str
    crop_width_px: int
    crop_height_px: int
    source_pixel_correspondence: bool
    variance: float
    black_fraction: float
    white_fraction: float
    nonempty: bool
    collapsed_black_or_white: bool
    status_levels: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class RenderedAssetVerification:
    asset_id: str
    renderer: str
    expected_bbox: dict[str, float]
    rendered_crop_width_px: int
    rendered_crop_height_px: int
    aspect_ratio_error: float
    rendered_variance: float
    rendered_black_fraction: float
    rendered_white_fraction: float
    source_render_mae_0_255: float
    placement_matches_source_region: bool
    render_visible: bool
    render_content_preserved: bool
    status_levels: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def _digest_bytes(data: bytes) -> str:
    return "sha256:" + sha256(data).hexdigest()


def verify_source_asset(
    *,
    asset_id: str,
    reference_path: str | Path,
    crop_path: str | Path,
    source_region: BBox,
) -> AssetVerification:
    reference_path = Path(reference_path)
    crop_path = Path(crop_path)
    source_data = reference_path.read_bytes()
    crop_data = crop_path.read_bytes()
    with Image.open(reference_path) as source_image, Image.open(crop_path) as crop_image:
        x0 = max(0, round(source_region.x))
        y0 = max(0, round(source_region.y))
        x1 = min(source_image.width, round(source_region.x + source_region.width))
        y1 = min(source_image.height, round(source_region.y + source_region.height))
        # Compare visible RGB content directly. RGBA ``getbbox`` can report an
        # empty difference when the alpha-difference band is all zero even
        # though RGB channels differ, allowing a black placeholder to pass.
        declared = source_image.convert("RGB").crop((x0, y0, x1, y1))
        crop = crop_image.convert("RGB")
        correspondence = declared.size == crop.size and ImageChops.difference(declared, crop).getbbox() is None
        array = np.asarray(crop.convert("RGB"), dtype=np.float32)
        variance = float(array.var()) if array.size else 0.0
        black_fraction = float(np.all(array <= 5, axis=2).mean()) if array.size else 1.0
        white_fraction = float(np.all(array >= 250, axis=2).mean()) if array.size else 1.0
        collapsed = black_fraction >= 0.995 or white_fraction >= 0.995
        nonempty = bool(array.size and variance >= 1.0 and not collapsed)
        levels = ["ASSET_DECLARED", "SOURCE_REGION_BOUND", "CROP_CREATED"]
        if nonempty:
            levels.append("CROP_NONEMPTY")
        if correspondence:
            levels.append("CROP_CONTENT_MATCHES_SOURCE_REGION")
    return AssetVerification(
        asset_id=asset_id,
        source_sha256=_digest_bytes(source_data),
        crop_sha256=_digest_bytes(crop_data),
        crop_width_px=crop.width,
        crop_height_px=crop.height,
        source_pixel_correspondence=correspondence,
        variance=round(variance, 6),
        black_fraction=round(black_fraction, 6),
        white_fraction=round(white_fraction, 6),
        nonempty=nonempty,
        collapsed_black_or_white=collapsed,
        status_levels=tuple(levels),
    )


def verify_rendered_asset(
    *,
    asset_id: str,
    asset_path: str | Path,
    render_path: str | Path,
    expected_bbox: BBox,
    renderer: str,
    mae_limit: float = 18.0,
    underlay_path: str | Path | None = None,
) -> RenderedAssetVerification:
    """Verify that an asset's declared region is visible at its rendered bbox."""

    with Image.open(asset_path) as asset_image, Image.open(render_path) as render_image:
        asset_rgba = asset_image.convert("RGBA")
        asset = asset_rgba.convert("RGB")
        render = render_image.convert("RGB")
        x0 = max(0, round(expected_bbox.x))
        y0 = max(0, round(expected_bbox.y))
        x1 = min(render.width, round(expected_bbox.x + expected_bbox.width))
        y1 = min(render.height, round(expected_bbox.y + expected_bbox.height))
        rendered_crop = render.crop((x0, y0, x1, y1))
        expected_size = (max(1, x1 - x0), max(1, y1 - y0))
        resized_rgba = asset_rgba.resize(expected_size, Image.Resampling.LANCZOS)
        if underlay_path is not None:
            with Image.open(underlay_path) as underlay_image:
                underlay = underlay_image.convert("RGBA").crop((x0, y0, x1, y1))
            underlay.alpha_composite(resized_rgba)
            expected = underlay.convert("RGB")
        else:
            expected = resized_rgba.convert("RGB")
        rendered_array = np.asarray(rendered_crop, dtype=np.float32)
        expected_array = np.asarray(expected, dtype=np.float32)
        variance = float(rendered_array.var()) if rendered_array.size else 0.0
        black_fraction = (
            float(np.all(rendered_array <= 5, axis=2).mean()) if rendered_array.size else 1.0
        )
        white_fraction = (
            float(np.all(rendered_array >= 250, axis=2).mean()) if rendered_array.size else 1.0
        )
        mae = (
            float(np.mean(np.abs(rendered_array - expected_array)))
            if rendered_array.size and rendered_array.shape == expected_array.shape
            else 255.0
        )
        asset_ratio = asset.width / max(1, asset.height)
        bbox_ratio = expected_bbox.width / max(1.0, expected_bbox.height)
        aspect_error = abs(asset_ratio - bbox_ratio) / max(1e-9, asset_ratio)
        placement_matches = (
            expected_bbox.x >= 0
            and expected_bbox.y >= 0
            and expected_bbox.x + expected_bbox.width <= render.width + 0.5
            and expected_bbox.y + expected_bbox.height <= render.height + 0.5
            and aspect_error <= 0.02
        )
        visible = bool(rendered_array.size and variance >= 1.0 and black_fraction < 0.995 and white_fraction < 0.995)
        preserved = visible and placement_matches and mae <= mae_limit
        levels = ["ASSET_DECLARED", "SOURCE_REGION_BOUND", "CROP_CREATED"]
        if visible:
            levels.append("RENDER_VISIBLE")
        if placement_matches:
            levels.append("PLACEMENT_MATCHES_SOURCE_REGION")
        if preserved:
            levels.append("RENDER_CONTENT_PRESERVED")
    return RenderedAssetVerification(
        asset_id=asset_id,
        renderer=renderer,
        expected_bbox={
            "x": expected_bbox.x,
            "y": expected_bbox.y,
            "width": expected_bbox.width,
            "height": expected_bbox.height,
        },
        rendered_crop_width_px=rendered_crop.width,
        rendered_crop_height_px=rendered_crop.height,
        aspect_ratio_error=round(aspect_error, 6),
        rendered_variance=round(variance, 6),
        rendered_black_fraction=round(black_fraction, 6),
        rendered_white_fraction=round(white_fraction, 6),
        source_render_mae_0_255=round(mae, 6),
        placement_matches_source_region=placement_matches,
        render_visible=visible,
        render_content_preserved=preserved,
        status_levels=tuple(levels),
    )
