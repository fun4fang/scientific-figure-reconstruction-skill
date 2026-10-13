from __future__ import annotations

from dataclasses import dataclass
import math

from unified_figure.model.geometry import BBox
from unified_figure.model.enums import ComponentType, RepresentationType
from unified_figure.model.records import Component


@dataclass(frozen=True, slots=True)
class FittedText:
    lines: tuple[str, ...]
    font_size_px: float
    rotation_degrees: int
    estimated_width_px: float
    estimated_height_px: float
    fits_bbox: bool


def _measure(lines: tuple[str, ...], size: float) -> tuple[float, float]:
    width = max((len(line) for line in lines), default=0) * size * 0.56
    height = max(1, len(lines)) * size * 1.15
    return width, height


def _wrapped_lines(text: str, max_characters: int) -> tuple[str, ...]:
    words = text.split()
    if not words:
        return ("",)
    lines: list[str] = []
    current = words[0]
    for word in words[1:]:
        candidate = f"{current} {word}"
        if len(candidate) <= max_characters:
            current = candidate
        else:
            lines.append(current)
            current = word
    lines.append(current)
    return tuple(lines)


def fit_text_to_bbox(
    text: str,
    bbox: BBox,
    requested_size_px: float | None,
    *,
    minimum_size_px: float = 5.0,
) -> FittedText:
    """Fit text deterministically to its source bbox without free reflow.

    Explicit line breaks are authoritative. A single long line may be wrapped
    only when shrinking it to the minimum size still cannot fit. A tall,
    narrow source bbox is treated as evidence for a 90-degree label.
    """

    requested = max(minimum_size_px, float(requested_size_px or 18.0))
    source_lines = tuple(text.splitlines()) or ("",)
    rotation = 0
    if (
        len(source_lines) == 1
        and len(text.strip()) >= 3
        and bbox.height >= bbox.width * 1.8
        and bbox.height >= len(text) * minimum_size_px * 0.45
    ):
        rotation = -90
    available_width = max(1.0, bbox.height if rotation else bbox.width) * 0.96
    available_height = max(1.0, bbox.width if rotation else bbox.height) * 0.94

    longest = max((len(line) for line in source_lines), default=1)
    fitted = min(
        requested,
        available_width / max(1.0, longest * 0.56),
        available_height / max(1.0, len(source_lines) * 1.15),
    )
    lines = source_lines
    if fitted < minimum_size_px and len(source_lines) == 1 and " " in text:
        max_characters = max(1, int(available_width / (minimum_size_px * 0.56)))
        candidate = _wrapped_lines(text, max_characters)
        candidate_size = min(
            requested,
            available_width / max(1.0, max(len(line) for line in candidate) * 0.56),
            available_height / max(1.0, len(candidate) * 1.15),
        )
        if candidate_size > fitted:
            lines = candidate
            fitted = candidate_size

    fitted = max(1.0, fitted)
    width, height = _measure(lines, fitted)
    return FittedText(
        lines=lines,
        font_size_px=fitted,
        rotation_degrees=rotation,
        estimated_width_px=width,
        estimated_height_px=height,
        fits_bbox=width <= available_width + 0.5 and height <= available_height + 0.5,
    )


def text_anchor_x(bbox: BBox, alignment: str | None) -> float:
    normalized = (alignment or "left").lower()
    if normalized == "center":
        return bbox.x + bbox.width / 2
    if normalized == "right":
        return bbox.x + bbox.width
    return bbox.x


def _installed_label_font(block: dict):
    """Measure the explicitly requested installed font, without substitution."""
    import os
    from pathlib import Path
    from PIL import ImageFont
    from unified_figure.model.geometry import ModelValidationError
    family = block['font_family']
    name = family + (' Bold' if block['font_weight']=='bold' else '') + ' (TrueType)'
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                r'SOFTWARE\Microsoft\Windows NT\CurrentVersion\Fonts') as key:
            file = winreg.QueryValueEx(key, name)[0]
        path = Path(file)
        if not path.is_absolute(): path = Path(os.environ['WINDIR'])/'Fonts'/path
        return ImageFont.truetype(str(path), round(block['font_size_px']*4))
    except (OSError, KeyError, ImportError) as exc:
        raise ModelValidationError('REVIEW_REQUIRED: exact rich-label font metrics unavailable: '+name) from exc


def _label_lines(block: dict, bounds: BBox, fit: bool):
    lines = block['content'].split('\n')
    if not fit: return lines
    font = _installed_label_font(block)
    from unified_figure.model.geometry import ModelValidationError
    # A single paragraph's overflow can be reflowed without changing tokens.
    # Preserve separate list-item breaks; they carry grouping information.
    if (any(font.getlength(line)/4 > bounds.width+0.5 for line in lines)
            and not any(line.lstrip().startswith(('-', '•')) for line in lines)):
        lines = [' '.join(lines)]
    result = []
    for line in lines:
        if font.getlength(line)/4 <= bounds.width+0.5:
            result.append(line);continue
        # Retain all words, weight and size. Never abbreviate a scientific term
        # or shrink an unfit word. List-item boundaries remain authoritative.
        current = ''
        for word in line.split():
            if font.getlength(word)/4 > bounds.width+0.5:
                raise ModelValidationError('REVIEW_REQUIRED: scientific text word cannot fit at supplied font size')
            next_line = current+' '+word if current else word
            if font.getlength(next_line)/4 > bounds.width+0.5:
                result.append(current);current=word
            else: current=next_line
        result.append(current)
    return result


def rich_label_lines(authoring: dict, *, fit_to_container: bool = False):
    """Explicit SOURCE_PIXELS line boxes shared by both rich-text serializers.

    No scientific inference or per-case offsets. The same line-box center drives
    SVG's middle baseline and PPTX's fixed line spacing/paragraph placement.
    """
    for block in authoring['paragraphs']:
        bounds=BBox(**block['bbox'])
        original=block['content'].split('\n')
        container=authoring.get('container')
        # A single paragraph in a real label-bearing container has known blank
        # space available. Standalone/independent multi-block layouts do not:
        # preserve those authoritative layouts rather than guessing new bounds.
        fit = fit_to_container and container and len(authoring['paragraphs'])==1
        if fit:
            font=_installed_label_font(block)
            widest=max(font.getlength(line)/4 for line in original)
            if widest > bounds.width+0.5:
                outer=BBox(**container['bbox']);clearance=max(1,container.get('stroke_width_px',0))
                left,right=outer.x+clearance,outer.x+outer.width-clearance
                width=min(widest,right-left)
                anchor=text_anchor_x(bounds,block['alignment'])
                x={'left':anchor,'center':anchor-width/2,'right':anchor-width}[block['alignment']]
                x=max(left,min(x,right-width))
                bounds=BBox(x,bounds.y,width,bounds.height)
        lines=_label_lines(block, bounds, bool(fit))
        height=block['font_size_px']*1.15
        first=bounds.y+(bounds.height-len(lines)*height)/2
        if fit and lines != original and len(lines)*height > bounds.height:
            from unified_figure.model.geometry import ModelValidationError
            # Only one block can safely reclaim blank vertical container space.
            # Multiple independent blocks must not be shifted into each other.
            if len(authoring['paragraphs'])==1 and container:
                outer=BBox(**container['bbox']);clearance=max(1,container.get('stroke_width_px',0))
                low, high = outer.y+clearance, outer.y+outer.height-clearance
            else: low,high=bounds.y,bounds.y+bounds.height
            if len(lines)*height > high-low+0.5:
                raise ModelValidationError('REVIEW_REQUIRED: rich label cannot fit without text/style loss')
            first=max(low,min(first,high-len(lines)*height))
        for i,text in enumerate(lines):
            yield dict(block=block, text=text, bounds=bounds,
                       top=first+i*height, height=height,
                       center=first+(i+0.5)*height)


def preset_text_rect_inset(container: dict | None) -> float:
    """DrawingML preset's intrinsic text rectangle, in source pixels.

    roundRect: corner radius=min(w,h)*adj/100000 (default adj=16667),
    its inscribed text rectangle is inset by radius*(1-cos(45 degrees)).
    Explicit authoring blocks are measured from the OUTER bbox, so adapters
    must not add this preset inset a second time. The preset/connection sites
    themselves remain untouched.
    """
    if not container or container['preset']=='rect': return 0.0
    if container['preset']!='roundRect':
        raise ValueError('Rich labelled preset needs an explicit supported text rectangle')
    adjustment=16667
    for value in container.get('adjustments', []):
        if value['name']=='adj': adjustment=int(value['fmla'].split()[1])
    box=BBox(**container['bbox'])
    radius=min(box.width,box.height)*max(0,min(50000,adjustment))/100000
    return radius*(1-math.sqrt(0.5))


def component_paint_key(component: Component) -> tuple[float, int, float, str]:
    """Return a fidelity-oriented paint order without changing semantics.

    Explicit canonical z-order remains authoritative. Within the same z-order,
    large panel/background shapes are painted first, native shapes next,
    source-preserved assets above those shapes, and editable text last.
    """

    if component.representation in {
        RepresentationType.EXACT_RASTER_ASSET,
        RepresentationType.RASTER_PRESERVE_COMPLEX,
        RepresentationType.VECTOR_ASSET,
    }:
        layer = 2
    elif component.component_type is ComponentType.PANEL:
        layer = 0
    elif component.representation is RepresentationType.NATIVE_SIMPLE_SHAPE:
        layer = 1
    elif component.representation is RepresentationType.NATIVE_TEXT:
        layer = 3
    else:
        layer = 2
    area_order = -component.geometry.bbox.width * component.geometry.bbox.height
    return (float(component.style.z_order), layer, area_order, component.component_id)


def is_coherent_preserved_panel(component: Component) -> bool:
    # Local scientific context may belong to a chart, text annotation or photo
    # rather than PANEL. Both adapters must protect the same exact pixels from
    # unvalidated duplicate text/shape/connector overlays.
    return (component.component_type is ComponentType.PANEL or
            component.provenance.operation == "route-coherent-panel-to-source-preservation") and component.representation in {
        RepresentationType.EXACT_RASTER_ASSET,
        RepresentationType.RASTER_PRESERVE_COMPLEX,
    }


def is_verified_native_label(component: Component) -> bool:
    return (component.component_type is ComponentType.TEXT and
            component.representation is RepresentationType.NATIVE_TEXT and
            component.provenance.operation == 'source-verified-native-label')

