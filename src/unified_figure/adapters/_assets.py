from __future__ import annotations

from hashlib import sha256
from pathlib import Path

from unified_figure.model.geometry import ModelValidationError
from unified_figure.model.records import AssetReference


def resolve_asset(asset: AssetReference, source_root: Path) -> tuple[Path, bytes, str]:
    if not asset.file_ref:
        raise ModelValidationError(f"asset {asset.asset_id} has no file_ref")
    root = source_root.resolve()
    path = (root / asset.file_ref).resolve()
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise ModelValidationError(f"asset {asset.asset_id} escapes source_root") from exc
    if not path.is_file():
        raise ModelValidationError(f"asset {asset.asset_id} bytes are missing: {asset.file_ref}")
    from unified_figure.security import bounded_bytes, checked_svg, checked_image, MAX_DOCUMENT_BYTES
    data = bounded_bytes(path, MAX_DOCUMENT_BYTES)
    if path.suffix.lower() == ".svg":
        checked_svg(data)
    else:
        checked_image(path)
    digest = "sha256:" + sha256(data).hexdigest()
    if not asset.content_hash:
        raise ModelValidationError(f"asset {asset.asset_id} has no content_hash")
    if asset.content_hash.lower() != digest.lower():
        raise ModelValidationError(f"asset {asset.asset_id} content hash mismatch")
    return path, data, digest


def artifact_hash(path: Path) -> str:
    return "sha256:" + sha256(path.read_bytes()).hexdigest()
