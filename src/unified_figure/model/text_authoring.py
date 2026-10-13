"""Optional evidence-backed text authoring payload in canonical metadata.

No schema rewrite: legacy canonical states remain byte-compatible. Bounds and
insets are SOURCE_PIXELS, never inferred from labels or raster content.
"""
import json
import math
import re
from .geometry import BBox, ModelValidationError

KEY = 'authoring.text.'
PRESETS = {'rect', 'roundRect', 'ellipse', 'can'}

def validate_text_authoring(data, geometry):
    if not isinstance(data, dict) or data.get('version') != 'text-authoring-v0.1':
        raise ModelValidationError('Unsupported text authoring payload')
    allowed={'version','evidence_refs','text_bounds','container','margins','vertical_anchor','wrap','autofit','paragraphs'}
    refs = data.get('evidence_refs')
    if set(data)-allowed or not isinstance(refs,list) or not refs or not all(isinstance(x,str) and x for x in refs):
        raise ModelValidationError('Text authoring needs explicit evidence and known fields')
    def box(value):
        if not isinstance(value,dict) or set(value)!={'x','y','width','height'}:
            raise ModelValidationError('Text authoring bounds must be explicit SOURCE_PIXELS')
        if any(isinstance(v,bool) or not isinstance(v,(int,float)) for v in value.values()):
            raise ModelValidationError('Text authoring bounds must be numeric')
        b=BBox(**value)
        if not all(math.isfinite(x) for x in (b.x,b.y,b.width,b.height)) or min(b.x,b.y)<0 or min(b.width,b.height)<=0:
            raise ModelValidationError('Invalid text authoring bounds')
        if b.x+b.width>geometry.decoded_width_px+1e-6 or b.y+b.height>geometry.decoded_height_px+1e-6:
            raise ModelValidationError('Text authoring bounds exceed source canvas')
        return b
    text=box(data.get('text_bounds'))
    if not isinstance(data.get('paragraphs', []), list):
        raise ModelValidationError('Rich label paragraphs must be an array')
    for p in data.get('paragraphs', []):
        if not isinstance(p,dict) or set(p) != {'content','bbox','font_family','font_size_px','font_weight','text_color','alignment'} or not isinstance(p['content'],str) or not p['content']:
            raise ModelValidationError('Invalid rich label text block')
        b=box(p['bbox'])
        if b.x<text.x or b.y<text.y or b.x+b.width>text.x+text.width+1e-6 or b.y+b.height>text.y+text.height+1e-6:
            raise ModelValidationError('Rich paragraph exceeds independent text bounds')
        if not isinstance(p['font_size_px'],(int,float)) or not math.isfinite(p['font_size_px']) or p['font_size_px']<=0 or p['font_weight'] not in {'normal','bold'} or p['alignment'] not in {'left','center','right'} or not re.fullmatch(r'#[0-9a-fA-F]{6}',p['text_color']):
            raise ModelValidationError('Invalid rich paragraph style')
    blocks=data.get('paragraphs', [])
    if any(a['bbox']['y']+a['bbox']['height'] > b['bbox']['y']+1e-6 for a,b in zip(blocks,blocks[1:])):
        raise ModelValidationError('Overlapping/columnar rich text requires review; one sequential text body cannot realize it safely')
    container=data.get('container')
    if container is not None:
        if not isinstance(container,dict) or set(container)-{'bbox','preset','fill','stroke','stroke_width_px','adjustments'} or container.get('preset') not in PRESETS:
            raise ModelValidationError('Unsupported label container preset/fields')
        outer=box(container.get('bbox'))
        if text.x<outer.x or text.y<outer.y or text.x+text.width>outer.x+outer.width+1e-6 or text.y+text.height>outer.y+outer.height+1e-6:
            raise ModelValidationError('Independent text bounds must be inside label container')
        for color in ('fill','stroke'):
            value=container.get(color)
            if value not in (None,'none') and (not isinstance(value,str) or not re.fullmatch(r'#[0-9a-fA-F]{6}',value)):
                raise ModelValidationError('Container color needs an explicit hex value')
        width=container.get('stroke_width_px',0)
        if not isinstance(width,(int,float)) or not math.isfinite(width) or width<0:
            raise ModelValidationError('Invalid container stroke width')
        adjustments=container.get('adjustments',[])
        if not isinstance(adjustments,list):
            raise ModelValidationError('Preset adjustments must be a list')
        for adj in adjustments:
            if not isinstance(adj,dict) or set(adj)!={'name','fmla'} or not isinstance(adj['name'],str) or not isinstance(adj['fmla'],str) or not re.fullmatch(r'adj\d*',adj['name']) or not re.fullmatch(r'val \d+',adj['fmla']):
                raise ModelValidationError('Unsupported preset adjustment')
    margins=data.get('margins',{})
    if not isinstance(margins,dict) or set(margins)-{'left','right','top','bottom'}:
        raise ModelValidationError('Unknown text inset')
    if any(not isinstance(v,(int,float)) or not math.isfinite(v) or v<0 for v in margins.values()):
        raise ModelValidationError('Invalid text inset')
    if margins.get('left',0)+margins.get('right',0)>=text.width or margins.get('top',0)+margins.get('bottom',0)>=text.height:
        raise ModelValidationError('Text insets consume entire text bounds')
    for name,options in [('vertical_anchor',{'t','ctr','b'}),('wrap',{'WRAP','NO_WRAP'}),('autofit',{'NONE','SHRINK_TO_FIT'})]:
        if name in data and data[name] not in options: raise ModelValidationError('Unsupported '+name)
    return data

def encode_text_authoring(data, geometry):
    return json.dumps(validate_text_authoring(data,geometry),sort_keys=True,separators=(',',':'),allow_nan=False)

def get_text_authoring(figure, component):
    value=figure.metadata.get(KEY+component.component_id)
    return validate_text_authoring(json.loads(value),figure.input_geometry) if value is not None else None

def text_inner_bounds(data):
    b=BBox(**data['text_bounds']);m=data.get('margins',{})
    return BBox(b.x+m.get('left',0),b.y+m.get('top',0),b.width-m.get('left',0)-m.get('right',0),b.height-m.get('top',0)-m.get('bottom',0))
