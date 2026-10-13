"""Disjoint source-context complements; no OCR, labels, or case heuristics."""
from .context_safety import integer_bounds, SCIENTIFIC_TYPES
from unified_figure.model.geometry import BBox


def uncovered_rectangles(width, height, boxes):
    bounded=[integer_bounds(b,width,height) for b in boxes]
    ys=sorted({0,height,*[int(b.y) for b in bounded],*[int(b.y+b.height) for b in bounded]})
    active={}; result=[]
    for y0,y1 in zip(ys,ys[1:]):
        spans=sorted((int(b.x),int(b.x+b.width)) for b in bounded if b.y<=y0 and b.y+b.height>=y1)
        gaps=[]; end=0
        for left,right in spans:
            if left>end: gaps.append((end,left))
            end=max(end,right)
        if end<width: gaps.append((end,width))
        current={}
        for interval in gaps:
            previous=active.pop(interval,None)
            current[interval]=BBox(interval[0],previous.y if previous else y0,interval[1]-interval[0],
                                   y1-(previous.y if previous else y0))
        result.extend(active.values()); active=current
    result.extend(active.values())
    return tuple(sorted(result,key=lambda b:(b.y,b.x,b.height,b.width)))


def scientific_content_gaps(figure, regions, source):
    # Only complete existing scientific preservation. A native-only workflow
    # is not turned into a screenshot by this safety rule.
    if not regions or not any(c.component_type in SCIENTIFIC_TYPES for c in figure.components):
        return ()
    boxes=[r.bbox for r in regions]
    gaps=uncovered_rectangles(source.width,source.height,boxes)
    # Geometry does not establish that uncovered pixels are expendable. Keep
    # every nonuniform/nonwhite gap exact, including compression/antialias halo.
    return tuple(b for b in gaps if source.crop((int(b.x),int(b.y),int(b.x+b.width),int(b.y+b.height)))
                 .convert('RGB').getextrema()!=((255,255),(255,255),(255,255)))
