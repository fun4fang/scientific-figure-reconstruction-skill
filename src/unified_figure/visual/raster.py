from __future__ import annotations

from dataclasses import asdict, dataclass
from hashlib import sha256
import json
from pathlib import Path
import subprocess
import tempfile

from PIL import Image


DEFAULT_EDGE_PATHS = (
    Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"),
    Path(r"C:\Program Files\Microsoft\Edge\Application\msedge.exe"),
)


class RasterizationError(RuntimeError):
    pass


class PowerPointExportError(RasterizationError):
    pass


@dataclass(frozen=True, slots=True)
class RasterReceipt:
    renderer: str
    renderer_version: str
    input_path: str
    input_sha256: str
    output_path: str
    output_sha256: str
    width_px: int
    height_px: int
    background: str
    crop_policy: str
    scale_policy: str
    repeat_hash_match: bool | None = None

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def _digest(path: Path) -> str:
    return "sha256:" + sha256(path.read_bytes()).hexdigest()


def _validated_png(path: Path, width: int, height: int) -> None:
    if not path.is_file() or path.stat().st_size == 0:
        raise RasterizationError(f"renderer did not create {path}")
    with Image.open(path) as image:
        if image.format != "PNG":
            raise RasterizationError(f"renderer output is not PNG: {path}")
        if image.size != (width, height):
            raise RasterizationError(
                f"renderer output size {image.size} does not match {(width, height)}"
            )


def _edge_path(explicit: str | Path | None) -> Path:
    candidates = (Path(explicit),) if explicit else DEFAULT_EDGE_PATHS
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    raise RasterizationError("Microsoft Edge runtime is unavailable")


def _file_version(path: Path) -> str:
    command = (
        "(Get-Item -LiteralPath '"
        + str(path).replace("'", "''")
        + "').VersionInfo.FileVersion"
    )
    result = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", command],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    return result.stdout.strip() or "NOT_EXPOSED"


def rasterize_svg(
    svg_path: str | Path,
    output_path: str | Path,
    *,
    width_px: int,
    height_px: int,
    edge_path: str | Path | None = None,
) -> RasterReceipt:
    """Rasterize SVG with Edge/Chromium so embedded data-URI PNGs are rendered."""

    source = Path(svg_path).resolve()
    from unified_figure.file_boundary import checked_absolute
    target = checked_absolute(output_path)
    if target.exists():
        raise ValueError("render output already exists; use a fresh path")
    if not source.is_file():
        raise RasterizationError(f"SVG input missing: {source}")
    target.parent.mkdir(parents=True, exist_ok=True)
    from unified_figure.security import checked_svg, bounded_bytes, MAX_DOCUMENT_BYTES
    checked_svg(bounded_bytes(source, MAX_DOCUMENT_BYTES))
    edge = _edge_path(edge_path)
    with tempfile.TemporaryDirectory(prefix="uf-edge-", dir=target.parent) as profile:
        arguments = (
            "@('--headless','--disable-gpu','--hide-scrollbars',"
            "'--force-device-scale-factor=1','--disable-background-networking','--no-first-run',"
            f"'--window-size={width_px},{height_px}',"
            f"'--user-data-dir={str(profile).replace("'", "''")}',"
            f"'--screenshot={str(target).replace("'", "''")}',"
            f"'{source.as_uri().replace("'", "''")}')"
        )
        script = (
            "$process=Start-Process -FilePath "
            + _powershell_literal(str(edge))
            + " -ArgumentList "
            + arguments
            + " -Wait -PassThru -WindowStyle Hidden; exit $process.ExitCode"
        )
        result = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
    if result.returncode != 0:
        raise RasterizationError(
            f"Edge SVG rasterization failed ({result.returncode}): {result.stderr.strip()}"
        )
    _validated_png(target, width_px, height_px)
    return RasterReceipt(
        renderer="Microsoft Edge headless SVG",
        renderer_version=_file_version(edge),
        input_path=str(source),
        input_sha256=_digest(source),
        output_path=str(target),
        output_sha256=_digest(target),
        width_px=width_px,
        height_px=height_px,
        background="renderer-default transparent composited on white",
        crop_policy="NONE",
        scale_policy="CSS viewport equals authoritative decoded dimensions at device-scale-factor 1",
    )


def _powershell_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def rasterize_pptx(
    pptx_path: str | Path,
    output_path: str | Path,
    *,
    width_px: int,
    height_px: int,
) -> RasterReceipt:
    """Export slide 1 through the installed Microsoft PowerPoint COM runtime."""

    source = Path(pptx_path).resolve()
    from unified_figure.file_boundary import checked_absolute
    target = checked_absolute(output_path)
    if target.exists():
        raise ValueError("render output already exists; use a fresh path")
    if not source.is_file():
        raise PowerPointExportError(f"PPTX input missing: {source}")
    target.parent.mkdir(parents=True, exist_ok=True)
    from unified_figure.security import checked_pptx
    checked_pptx(source)
    script = f"""
$ErrorActionPreference='Stop'
$app=$null
$presentation=$null
$existing=@(Get-Process POWERPNT -ErrorAction SilentlyContinue).Count -gt 0
try {{
  $app=New-Object -ComObject PowerPoint.Application
  $version=$app.Version
  $presentation=$app.Presentations.Open({_powershell_literal(str(source))},-1,0,0)
  if($presentation.Slides.Count -lt 1){{throw 'presentation has no slides'}}
  $presentation.Slides.Item(1).Export({_powershell_literal(str(target))},'PNG',{width_px},{height_px})
  [pscustomobject]@{{status='PASS';version=$version;slides=$presentation.Slides.Count}} | ConvertTo-Json -Compress
}} catch {{
  [pscustomobject]@{{status='FAIL';error=$_.Exception.Message;hresult=('0x{{0:X8}}' -f ($_.Exception.HResult -band 0xffffffff))}} | ConvertTo-Json -Compress
  exit 2
}} finally {{
  if($null -ne $presentation){{try{{$presentation.Close()}}catch{{}}}}
  if($null -ne $app -and -not $existing){{try{{$app.Quit()}}catch{{}}}}
}}
"""
    result = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
        capture_output=True,
        text=True,
        timeout=90,
        check=False,
    )
    payload: dict[str, object] = {}
    for line in reversed([item.strip() for item in result.stdout.splitlines() if item.strip()]):
        try:
            payload = json.loads(line)
            break
        except json.JSONDecodeError:
            continue
    if result.returncode != 0 or payload.get("status") != "PASS":
        detail = payload.get("error") or result.stderr.strip() or result.stdout.strip()
        raise PowerPointExportError(f"PowerPoint export failed: {detail}")
    _validated_png(target, width_px, height_px)
    return RasterReceipt(
        renderer="Microsoft PowerPoint COM slide export",
        renderer_version=str(payload.get("version", "NOT_EXPOSED")),
        input_path=str(source),
        input_sha256=_digest(source),
        output_path=str(target),
        output_sha256=_digest(target),
        width_px=width_px,
        height_px=height_px,
        background="PowerPoint slide background",
        crop_policy="NONE",
        scale_policy="Slide.Export width/height equal authoritative decoded dimensions",
    )
