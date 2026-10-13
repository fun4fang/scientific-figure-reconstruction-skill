from __future__ import annotations

from dataclasses import asdict, dataclass
from hashlib import sha256
from pathlib import Path

import numpy as np
from PIL import Image, ImageChops, ImageDraw


@dataclass(frozen=True, slots=True)
class ComparisonReceipt:
    width_px: int
    height_px: int
    side_by_side_path: str
    overlay_path: str
    difference_path: str
    blink_path: str
    artifact_hashes: dict[str, str]
    metrics: dict[str, float]

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def _digest(path: Path) -> str:
    return "sha256:" + sha256(path.read_bytes()).hexdigest()


def _rgb(path: str | Path, size: tuple[int, int]) -> Image.Image:
    with Image.open(path) as image:
        frame = image.convert("RGB")
        if frame.size != size:
            frame = frame.resize(size, Image.Resampling.LANCZOS)
        return frame.copy()


def _edge_map(array: np.ndarray) -> np.ndarray:
    gray = array.mean(axis=2)
    gx = np.abs(np.diff(gray, axis=1, prepend=gray[:, :1]))
    gy = np.abs(np.diff(gray, axis=0, prepend=gray[:1, :]))
    return (gx + gy) >= 24.0


def compute_pixel_metrics(reference: Image.Image, candidate: Image.Image) -> dict[str, float]:
    ref = np.asarray(reference.convert("RGB"), dtype=np.float32)
    out = np.asarray(candidate.convert("RGB"), dtype=np.float32)
    if ref.shape != out.shape:
        raise ValueError("pixel metrics require a common frame")
    mae = float(np.mean(np.abs(ref - out)))
    x = ref.mean(axis=2)
    y = out.mean(axis=2)
    c1 = (0.01 * 255) ** 2
    c2 = (0.03 * 255) ** 2
    ux, uy = float(x.mean()), float(y.mean())
    vx, vy = float(x.var()), float(y.var())
    covariance = float(((x - ux) * (y - uy)).mean())
    ssim = ((2 * ux * uy + c1) * (2 * covariance + c2)) / (
        (ux * ux + uy * uy + c1) * (vx + vy + c2)
    )
    ref_edge, out_edge = _edge_map(ref), _edge_map(out)
    true_positive = int(np.logical_and(ref_edge, out_edge).sum())
    precision = true_positive / max(1, int(out_edge.sum()))
    recall = true_positive / max(1, int(ref_edge.sum()))
    edge_f1 = 2 * precision * recall / max(1e-12, precision + recall)
    foreground_ref = np.any(ref < 245, axis=2)
    foreground_out = np.any(out < 245, axis=2)
    intersection = int(np.logical_and(foreground_ref, foreground_out).sum())
    union = int(np.logical_or(foreground_ref, foreground_out).sum())
    return {
        "mae_0_255": round(mae, 6),
        "global_ssim_observational": round(float(ssim), 6),
        "edge_f1_observational": round(float(edge_f1), 6),
        "foreground_mask_iou_observational": round(intersection / max(1, union), 6),
    }


def build_comparison_artifacts(
    reference_path: str | Path,
    candidate_path: str | Path,
    output_dir: str | Path,
    *,
    width_px: int,
    height_px: int,
    label: str,
) -> ComparisonReceipt:
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    size = (width_px, height_px)
    reference = _rgb(reference_path, size)
    candidate = _rgb(candidate_path, size)

    header = 34
    side = Image.new("RGB", (width_px * 2, height_px + header), "white")
    side.paste(reference, (0, header))
    side.paste(candidate, (width_px, header))
    draw = ImageDraw.Draw(side)
    draw.text((8, 8), "REFERENCE", fill="black")
    draw.text((width_px + 8, 8), label, fill="black")
    side_path = output / "side_by_side.png"
    side.save(side_path)

    overlay = Image.blend(reference, candidate, 0.5)
    overlay_path = output / "alpha_overlay.png"
    overlay.save(overlay_path)

    difference = ImageChops.difference(reference, candidate)
    difference_path = output / "absolute_difference.png"
    difference.save(difference_path)

    blink_path = output / "blink.gif"
    reference.save(
        blink_path,
        save_all=True,
        append_images=[candidate],
        duration=[650, 650],
        loop=0,
    )
    paths = {
        "side_by_side": side_path,
        "overlay": overlay_path,
        "difference": difference_path,
        "blink": blink_path,
    }
    return ComparisonReceipt(
        width_px=width_px,
        height_px=height_px,
        side_by_side_path=str(side_path),
        overlay_path=str(overlay_path),
        difference_path=str(difference_path),
        blink_path=str(blink_path),
        artifact_hashes={key: _digest(path) for key, path in paths.items()},
        metrics=compute_pixel_metrics(reference, candidate),
    )
