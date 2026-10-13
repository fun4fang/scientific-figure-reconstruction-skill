"""Conservative source-verified native label candidates, not OCR or segmentation.

Only existing semantic text in structural components is tested. Pixel agreement
and a flat background are required before removing any source content.
"""
from dataclasses import dataclass, asdict, replace
from hashlib import sha256
import json
import os
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from unified_figure.model.enums import ComponentType, RepresentationType
from unified_figure.model.geometry import BBox
from .context_safety import integer_bounds, covers, SCIENTIFIC_TYPES
from unified_figure.model.figure import make_stable_id
from unified_figure.model.records import ProvenanceReference, FontStyle, Style
from unified_figure.model.enums import AssetSourceType, FidelityStatus, DecisionStatus

POLICY_VERSION = 'selective-native-label-policy-v0.1'
MIN_GLYPH_F1 = 0.95


@dataclass(frozen=True)
class NativeLabelCandidate:
    source_component_id: str
    text: str
    bbox: BBox
    ink_bbox: BBox
    font_family: str
    font_weight: str
    font_size: float
    font_path: str
    font_sha256: str
    glyph_f1: float
    glyph_exact_f1: float


def fonts():
    root = Path(os.environ.get('WINDIR', 'C:/Windows'))/'Fonts'
    for stem, family in [('arial','Arial'),('calibri','Calibri'),('segoeui','Segoe UI'),
                         ('tahoma','Tahoma'),('verdana','Verdana')]:
        for suffix, weight in [('', 'normal'), ('bd' if stem=='arial' else 'b', 'bold')]:
            if stem=='tahoma' and weight=='bold': suffix='bd'
            path=root/(stem+suffix+'.ttf')
            if path.is_file():
                yield path,family,weight


def binary_f1(a, b):
    height=max(a.shape[0],b.shape[0]); width=max(a.shape[1],b.shape[1])
    aa=np.zeros((height,width),bool); bb=aa.copy()
    aa[:a.shape[0],:a.shape[1]]=a; bb[:b.shape[0],:b.shape[1]]=b
    return float(2*(aa&bb).sum()/max(1,aa.sum()+bb.sum()))


def compatible_glyphs(a,b, *, minimum_exact=.90):
    """One-pixel rasterizer tolerance, bounded by exact ink agreement.

    This is not font-exact recovery. Require both directed coverage, similar
    ink area, and >=90% exact agreement; changed text cannot pass by dilation
    alone. Final native rendering uses this same predeclared criterion.
    """
    exact=binary_f1(a,b)
    h=max(a.shape[0],b.shape[0]); w=max(a.shape[1],b.shape[1])
    aa=np.zeros((h,w),bool); bb=aa.copy()
    aa[:a.shape[0],:a.shape[1]]=a; bb[:b.shape[0],:b.shape[1]]=b
    def dilate(mask):
        padded=np.pad(mask,1)
        return np.logical_or.reduce([padded[y:y+h,x:x+w] for y in range(3) for x in range(3)])
    precision=float((aa&dilate(bb)).sum()/max(1,aa.sum()))
    recall=float((bb&dilate(aa)).sum()/max(1,bb.sum()))
    area=min(aa.sum(),bb.sum())/max(1,aa.sum(),bb.sum())
    return (min(precision,recall) if exact>=minimum_exact and area>=.90 else 0.),exact


def rendered_ink_agreement(reference, pixels):
    """Antialias-aware ink conservation, with at most one-pixel tolerance.

    A thresholded area is not conserved across GDI and browser antialiasing.
    Integrated ink is. Do not mistake lighter edge pixels for missing letters.
    """
    a=1-reference.astype(float).mean(axis=2)/255.
    b=1-pixels.astype(float).mean(axis=2)/255.
    h,w=a.shape
    def near(mask):
        p=np.pad(mask,1)
        return np.maximum.reduce([p[y:y+h,x:x+w] for y in range(3) for x in range(3)])
    mass=min(a.sum(),b.sum())/max(1.,a.sum(),b.sum())
    directed=min(np.minimum(a,near(b)).sum()/max(1.,a.sum()),
                 np.minimum(b,near(a)).sum()/max(1.,b.sum()))
    return float(directed if mass>=.90 else 0.),float(mass)


def discover_native_labels(figure, reference_path):
    # Native glyph replacement is deliberately unavailable in mixed scientific
    # image/chart figures. Do not infer safe diagram labels from visual type alone.
    if any(c.component_type in SCIENTIFIC_TYPES for c in figure.components):
        return (), ({'reason':'SCIENTIFIC_ASSET_CONTEXT_REMAINS_SOURCE_PRESERVED'},)
    if not any(c.asset_ref for c in figure.components):
        return (), ({'reason':'NO_SOURCE_LAYER_REPLACEMENT_NEEDED'},)
    reference=Path(reference_path)
    if 'sha256:'+sha256(reference.read_bytes()).hexdigest()!=figure.input_geometry.source_hash:
        raise ValueError('Native verification source hash mismatch')
    candidates=[]; observations=[]
    with Image.open(reference) as im:
        if im.mode not in ('RGB','RGBA','L','LA') or 'transparency' in im.info:
            return (),({'reason':'UNVERIFIED_PALETTE_OR_COLORKEY_BACKGROUND_KEEP_SOURCE'},)
        if im.mode in ('RGBA','LA') and im.getchannel('A').getextrema()!=(255,255):
            return (),({'reason':'TRANSPARENT_BACKGROUND_NOT_SUPPORTED_FOR_NATIVE_REPLACEMENT'},)
        source=np.asarray(im.convert('RGB'))
        for c in figure.components:
            text=figure.metadata.get('text.'+c.component_id,'')
            if c.component_type not in {ComponentType.TEXT,ComponentType.SIMPLE_SHAPE} or not text:
                continue
            if '\n' in text or ';' in text or len(text)>70 or not text.isascii():
                observations.append({'component_id':c.component_id,'reason':'NO_SAFE_SINGLE_LABEL_GEOMETRY'})
                continue
            b=integer_bounds(c.geometry.bbox,im.width,im.height)
            mx=max(3,int(b.width*.10)); my=max(3,int(b.height*.20))
            x0,y0=int(b.x)+mx,int(b.y)+my
            x1,y1=int(b.x+b.width)-mx,int(b.y+b.height)-my
            area=source[y0:y1,x0:x1]
            if area.size==0: continue
            dark=(area.max(axis=2)<128)
            yy,xx=np.where(dark)
            if not len(xx): continue
            l,t,r,d=int(xx.min()),int(yy.min()),int(xx.max())+1,int(yy.max())+1
            target=dark[t:d,l:r]
            if d-t>b.height*.5:
                observations.append({'component_id':c.component_id,'reason':'SHAPE_EDGE_OR_MULTILINE_CONFLICT'})
                continue
            ink=BBox(x0+l,y0+t,r-l,d-t)
            # Include the antialias/compression halo, not merely dark ink. A
            # white ring must be established without crossing component bounds.
            patch=None
            for pad in range(3,13):
                test=BBox(ink.x-pad,ink.y-pad,ink.width+2*pad,ink.height+2*pad)
                if not covers(b,test): break
                pixels=source[int(test.y):int(test.y+test.height),int(test.x):int(test.x+test.width)]
                border=np.concatenate([pixels[:3].reshape(-1,3),pixels[-3:].reshape(-1,3),
                                       pixels[:,:3].reshape(-1,3),pixels[:,-3:].reshape(-1,3)])
                if border.min()>=252 and np.abs(pixels.astype(int).max(axis=2)-pixels.astype(int).min(axis=2)).max()<=3:
                    patch=test
                    break
            if patch is None:
                observations.append({'component_id':c.component_id,'reason':'BACKGROUND_NOT_VERIFIABLY_FLAT_WHITE',
                    'ink_bbox':asdict(ink),'border_min':int(border.min()),
                    'border_nonwhite':int((border.min(axis=1)<252).sum()),'border_pixels':len(border)})
                continue
            best=None
            for path,family,weight in fonts():
                for quarter_size in range(32,4*min(48,int(b.height))+1):
                    size=quarter_size/4
                    font=ImageFont.truetype(str(path),quarter_size)
                    fb=font.getbbox(text)
                    if fb[2]-fb[0]<=0 or fb[3]-fb[1]<=0: continue
                    if abs((fb[2]-fb[0])/4-target.shape[1])>2 or abs((fb[3]-fb[1])/4-target.shape[0])>2: continue
                    for dx in range(4):
                        for dy in range(4):
                            canvas=Image.new('L',(fb[2]-fb[0]+8,fb[3]-fb[1]+8),255)
                            ImageDraw.Draw(canvas).text((4+dx-fb[0],4+dy-fb[1]),text,font=font,fill=0)
                            canvas=canvas.resize((max(1,round(canvas.width/4)),max(1,round(canvas.height/4))),Image.Resampling.LANCZOS)
                            mask=np.asarray(canvas)<128
                            pos=np.where(mask)
                            if not len(pos[0]): continue
                            mask=mask[pos[0].min():pos[0].max()+1,pos[1].min():pos[1].max()+1]
                            f1,exact=compatible_glyphs(target,mask)
                            if best is None or (f1,exact)>(best[0],best[-1]): best=(f1,path,family,weight,size,mask,exact)
            score=best[0] if best else 0.
            observations.append({'component_id':c.component_id,'glyph_f1':score,
                                 'reason':'MATCHED' if score>=MIN_GLYPH_F1 else 'GLYPH_MISMATCH_KEEP_SOURCE'})
            if score>=MIN_GLYPH_F1:
                f1,path,family,weight,size,mask,exact=best
                font=ImageFont.truetype(str(path),round(size*4))
                boundaries=[0,*[round(font.getlength(text[:i])/4) for i in range(1,len(text))],target.shape[1]]
                glyph_scores=[binary_f1(target[:,max(0,l):min(target.shape[1],r)],
                    mask[:,max(0,l):min(mask.shape[1],r)])
                    for char,l,r in zip(text,boundaries,boundaries[1:]) if not char.isspace() and r>l]
                observations[-1]['per_glyph_exact_f1']=glyph_scores
                if not glyph_scores or min(glyph_scores)<.90:
                    observations[-1]['reason']='INDIVIDUAL_GLYPH_AMBIGUITY_KEEP_SOURCE'
                    continue
                candidates.append(NativeLabelCandidate(c.component_id,text,patch,ink,family,weight,
                    float(size),str(path),sha256(path.read_bytes()).hexdigest(),f1,exact))
    return tuple(candidates),tuple(observations)


def materialize_labels(figure, candidates, root, output):
    """Build a reversible derivative; authoritative assets are never edited."""
    from unified_figure.file_boundary import checked_absolute, checked_target, save_asset
    root=checked_absolute(root); output=checked_target(root, output)
    if root not in output.parents: raise ValueError('native derivative must be inside repository')
    output.mkdir(parents=True,exist_ok=True)
    assets=list(figure.assets); components=list(figure.components); decisions=list(figure.representation_decisions)
    metadata=dict(figure.metadata); labels=[]
    for i,candidate in enumerate(candidates):
        donor=next(c for c in figure.components if c.component_id==candidate.source_component_id)
        prior=next(d for d in decisions if d.decision_id==donor.representation_decision_id)
        cid=make_stable_id(figure.figure_id,'verified-native-label',i,donor.component_id)
        did=make_stable_id(figure.figure_id,'verified-native-label-decision',i,cid)
        font=ImageFont.truetype(candidate.font_path,round(candidate.font_size))
        fb=font.getbbox(candidate.text,anchor='ls')
        layout={'x':candidate.ink_bbox.x-fb[0],'baseline':candidate.ink_bbox.y-fb[1],
                'proof':asdict(candidate)}
        label=replace(donor,component_id=cid,component_type=ComponentType.TEXT,semantic_role='source-verified editable label',
            parent_component_id=donor.component_id,geometry=replace(donor.geometry,bbox=candidate.bbox),
            style=Style(fill='#000000',font=FontStyle(candidate.font_family,candidate.font_weight),
                        font_size=candidate.font_size,text_alignment='center'),
            representation=RepresentationType.NATIVE_TEXT,representation_decision_id=did,asset_ref=None,renderer_realizations=(),
            provenance=ProvenanceReference(donor.provenance.evidence_refs,POLICY_VERSION,'source-verified-native-label'))
        components.append(label); labels.append(label)
        decisions.append(replace(prior,decision_id=did,component_id=cid,
            selected_representation=RepresentationType.NATIVE_TEXT,candidate_representations=(RepresentationType.NATIVE_TEXT,),
            decision_reason='source glyph/background verified; renderer acceptance pending',status=DecisionStatus.SELECTED))
        metadata['text.'+cid]=candidate.text; metadata['native.label.'+cid]=json.dumps(layout,sort_keys=True)
    for index,asset in enumerate(assets):
        users=[c for c in figure.components if c.asset_ref==asset.asset_id]
        if not users: continue
        # Replacement requires exact identity placement. Never clear a scaled,
        # shared, vector, or generated asset optimistically.
        region=asset.source_region
        intersects=lambda a,b: max(a.x,b.x)<min(a.x+a.width,b.x+b.width) and max(a.y,b.y)<min(a.y+a.height,b.y+b.height)
        affected=[c for c in candidates if region and intersects(region,c.bbox)]
        if not affected: continue
        if any(not covers(region,c.bbox) for c in affected):
            raise ValueError('native replacement crosses an asset boundary; preserve local source')
        if any(c.geometry.bbox!=region for c in users): raise ValueError('unsafe source asset transform')
        with Image.open(root/asset.file_ref) as im:
            if (im.width,im.height)!=(int(region.width),int(region.height)): raise ValueError('unsafe asset crop scale')
            if im.mode in ('RGBA','LA') and im.getchannel('A').getextrema()!=(255,255):
                raise ValueError('unsafe transparent source background')
            derivative=im.convert('RGB')
            draw=ImageDraw.Draw(derivative)
            for c in affected:
                b=c.bbox
                draw.rectangle((int(b.x-region.x),int(b.y-region.y),int(b.x+b.width-region.x)-1,
                                int(b.y+b.height-region.y)-1),fill='white')
            path=save_asset(derivative, output, asset.asset_id, root=root, kind='label-background')
        assets[index]=replace(asset,file_ref=path.relative_to(root).as_posix(),
            content_hash='sha256:'+sha256(path.read_bytes()).hexdigest(),source_type=AssetSourceType.UNKNOWN,
            scientific_fidelity_status=FidelityStatus.UNKNOWN,
            provenance=ProvenanceReference(asset.provenance.evidence_refs,POLICY_VERSION,'verified-label-background-derivative'))
    result=replace(figure,components=tuple(components),assets=tuple(assets),representation_decisions=tuple(decisions),
                   metadata=metadata,revision=replace(figure.revision,canonical_state_hash='')).with_computed_revision_hash()
    result.validate()
    return result,tuple(labels)


def verify_native_realization(figure, candidate, label, root, output, reference):
    from unified_figure import PptxRendererAdapter, SvgRendererAdapter, render_figure
    from unified_figure.visual import rasterize_pptx, rasterize_svg
    out=Path(output); results={}
    width=figure.input_geometry.decoded_width_px; height=figure.input_geometry.decoded_height_px
    b=candidate.bbox; rect=tuple(int(v) for v in (b.x,b.y,b.x+b.width,b.y+b.height))
    with Image.open(reference) as im: ref=np.asarray(im.convert('RGB').crop(rect))
    metadata=dict(figure.metadata); key='native.label.'+label.component_id
    layout=json.loads(metadata[key])
    for name,adapter,rasterizer in [('svg',SvgRendererAdapter,rasterize_svg),('pptx',PptxRendererAdapter,rasterize_pptx)]:
        for pass_index in range(2):
            calibrated=replace(figure,metadata=metadata,revision=replace(figure.revision,canonical_state_hash='')).with_computed_revision_hash()
            path=out/('trial-'+str(pass_index)+'.'+name)
            render_figure(calibrated,adapter(path,source_root=root),routing_applied=True)
            raster_receipt=rasterizer(path,out/(name+'-'+str(pass_index)+'.png'),width_px=width,height_px=height)
            with Image.open(out/(name+'-'+str(pass_index)+'.png')) as im: pixels=np.asarray(im.convert('RGB').crop(rect))
            a=ref.max(axis=2)<128; z=pixels.max(axis=2)<128
            if pass_index==0 and z.any() and a.any():
                ay,ax=np.where(a); zy,zx=np.where(z)
                dx=int(ax.min()-zx.min()); dy=int(ay.min()-zy.min())
                # One deterministic baseline/ink-origin calibration, not a
                # quality retry or font search. Unbounded shifts fail closed.
                if abs(dx)>2 or abs(dy)>2: break
                layout[name+'.dx']=dx; layout[name+'.dy']=dy
                metadata[key]=json.dumps(layout,sort_keys=True)
                continue
            break
        # Glyph identity has already passed the stricter source/font proof.
        # Different native rasterizers may hint edges differently. Allow only
        # one-pixel edge tolerance and small local MAE, not missing text.
        _,exact=compatible_glyphs(a,z,minimum_exact=.85)
        score,mass=rendered_ink_agreement(ref,pixels)
        mae=float(np.abs(ref.astype(float)-pixels.astype(float)).mean())
        results[name]={'glyph_compatibility':score,'exact_ink_f1':exact,'ink_mass_ratio':mass,'mae':mae,
                       'raster_receipt':raster_receipt.to_dict(),
                       'calibration':{k:layout.get(name+'.'+k,0) for k in ('dx','dy')},
                       'accepted':score>=MIN_GLYPH_F1 and exact>=.85}
    return all(r['accepted'] for r in results.values()),results,layout


def apply_selective_native(figure, reference, root, output):
    candidates,observed=discover_native_labels(figure,reference)
    accepted=[]; proofs=[]; layouts={}
    for candidate in candidates:
        try:
            trial,labels=materialize_labels(figure,(*accepted,candidate),root,Path(output)/candidate.source_component_id)
            ok,metrics,layout=verify_native_realization(trial,candidate,labels[-1],root,
                Path(output)/candidate.source_component_id,reference)
            proofs.append({'candidate':asdict(candidate),'accepted':ok,'renderers':metrics})
            if ok:
                accepted.append(candidate); layouts[labels[-1].component_id]=layout
        except (ValueError,OSError,RuntimeError) as error:
            proofs.append({'candidate':asdict(candidate),'accepted':False,'safe_local_fallback':str(error)})
    result=materialize_labels(figure,accepted,root,Path(output)/'accepted')[0] if accepted else figure
    metadata={**result.metadata,'native.selective.receipt':json.dumps({'policy':POLICY_VERSION,
        'observations':observed,'render_proofs':proofs,'accepted':len(accepted)},sort_keys=True)}
    metadata.update({'native.label.'+cid:json.dumps(layout,sort_keys=True) for cid,layout in layouts.items()})
    return replace(result,metadata=metadata,revision=replace(result.revision,canonical_state_hash='')).with_computed_revision_hash()
