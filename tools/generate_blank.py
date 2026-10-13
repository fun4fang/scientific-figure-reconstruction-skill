"""Independently authored minimal OPC/PresentationML skeleton; standard library only.

No bytes from Office templates, SF/I2P snapshots or library default templates.
Format references (not copied sample source): Microsoft Learn PresentationML
structure/create-presentation and DrawingML FormatScheme/FillStyleList.
MIT License. Copyright (c) 2026 Fang Zhe. See the project LICENSE.
"""
from pathlib import Path
import hashlib
import json
import zipfile
from xml.etree import ElementTree as E

P='http://schemas.openxmlformats.org/presentationml/2006/main'
A='http://schemas.openxmlformats.org/drawingml/2006/main'
R='http://schemas.openxmlformats.org/officeDocument/2006/relationships'
REL='http://schemas.openxmlformats.org/package/2006/relationships'
CT='http://schemas.openxmlformats.org/package/2006/content-types'
E.register_namespace('p',P);E.register_namespace('a',A);E.register_namespace('r',R)
def add(parent,ns,tag_name,**attrs):return E.SubElement(parent,'{'+ns+'}'+tag_name,{k:str(v) for k,v in attrs.items()})
def root(ns,tag_name,**attrs):return E.Element('{'+ns+'}'+tag_name,{k:str(v) for k,v in attrs.items()})
def xml(node):return E.tostring(node,encoding='utf-8',xml_declaration=True)
def relationships(items):
    n=root(REL,'Relationships')
    for identity,kind,target in items:add(n,REL,'Relationship',Id=identity,Type=R+'/'+kind,Target=target)
    return xml(n)
def empty_tree(parent):
    data=add(parent,P,'cSld');tree=add(data,P,'spTree');nv=add(tree,P,'nvGrpSpPr')
    add(nv,P,'cNvPr',id=1,name='');add(nv,P,'cNvGrpSpPr');add(nv,P,'nvPr')
    transform=add(add(tree,P,'grpSpPr'),A,'xfrm')
    for name,attrs in (('off',dict(x=0,y=0)),('ext',dict(cx=0,cy=0)),('chOff',dict(x=0,y=0)),('chExt',dict(cx=0,cy=0))):add(transform,A,name,**attrs)
def solid(parent,color):add(add(parent,A,'solidFill'),A,'schemeClr',val=color)
def generate():
    entries={}
    presentation=root(P,'presentation')
    add(add(presentation,P,'sldMasterIdLst'),P,'sldMasterId',id=2147483648,**{'{'+R+'}id':'rId1'})
    add(add(presentation,P,'sldIdLst'),P,'sldId',id=256,**{'{'+R+'}id':'rId2'})
    add(presentation,P,'sldSz',cx=9144000,cy=6858000);add(presentation,P,'notesSz',cx=6858000,cy=9144000)
    entries['ppt/presentation.xml']=xml(presentation)
    slide=root(P,'sld');empty_tree(slide);add(add(slide,P,'clrMapOvr'),A,'masterClrMapping')
    entries['ppt/slides/slide1.xml']=xml(slide)
    master=root(P,'sldMaster');empty_tree(master)
    add(master,P,'clrMap',bg1='lt1',tx1='dk1',bg2='lt2',tx2='dk2',accent1='accent1',accent2='accent2',accent3='accent3',accent4='accent4',accent5='accent5',accent6='accent6',hlink='hlink',folHlink='folHlink')
    add(add(master,P,'sldLayoutIdLst'),P,'sldLayoutId',id=2147483649,**{'{'+R+'}id':'rId1'})
    styles=add(master,P,'txStyles')
    for name in ('titleStyle','bodyStyle'):add(styles,P,name)
    other=add(styles,P,'otherStyle')
    # Non-placeholder text inherits this standard 12-point kerning threshold.
    # This numeric interoperability setting is authored directly, not copied XML.
    default_run=add(add(other,A,'lvl1pPr'),A,'defRPr',sz=1800,kern=1200)
    add(default_run,A,'latin',typeface='+mn-lt')
    entries['ppt/slideMasters/slideMaster1.xml']=xml(master)
    layout=root(P,'sldLayout',type='blank',preserve=1);empty_tree(layout);add(add(layout,P,'clrMapOvr'),A,'masterClrMapping')
    entries['ppt/slideLayouts/slideLayout1.xml']=xml(layout)
    theme=root(A,'theme',name='Generated neutral theme');elements=add(theme,A,'themeElements');colors=add(elements,A,'clrScheme',name='Neutral')
    for name,value in (('dk1','000000'),('lt1','FFFFFF'),('dk2','222222'),('lt2','EEEEEE'),('accent1','345678'),('accent2','567834'),('accent3','783456'),('accent4','456789'),('accent5','678945'),('accent6','894567'),('hlink','0000FF'),('folHlink','800080')):add(add(colors,A,name),A,'srgbClr',val=value)
    fonts=add(elements,A,'fontScheme',name='User installed font references')
    # References only; no font files. Paragraph-end metrics inherit the minor
    # font even when the visible runs explicitly select Arial.
    for name,family in (('majorFont','等线 Light'),('minorFont','等线')):
        f=add(fonts,A,name);add(f,A,'latin',typeface=family);add(f,A,'ea',typeface='');add(f,A,'cs',typeface='')
    formats=add(elements,A,'fmtScheme',name='Minimal standard style matrix')
    fills=add(formats,A,'fillStyleLst')
    for i in range(3):solid(fills,'phClr')
    lines=add(formats,A,'lnStyleLst')
    for width in (6350,12700,19050):
        line=add(lines,A,'ln',w=width,cap='flat',cmpd='sng',algn='ctr');solid(line,'phClr');add(line,A,'prstDash',val='solid');add(line,A,'miter',lim=800000)
    effects=add(formats,A,'effectStyleLst')
    for i in range(3):add(add(effects,A,'effectStyle'),A,'effectLst')
    background=add(formats,A,'bgFillStyleLst')
    for i in range(3):solid(background,'phClr')
    entries['ppt/theme/theme1.xml']=xml(theme)
    entries['ppt/presProps.xml']=xml(root(P,'presentationPr'))
    entries['_rels/.rels']=relationships([('rId1','officeDocument','ppt/presentation.xml')])
    entries['ppt/_rels/presentation.xml.rels']=relationships([('rId1','slideMaster','slideMasters/slideMaster1.xml'),('rId2','slide','slides/slide1.xml'),('rId3','presProps','presProps.xml'),('rId4','theme','theme/theme1.xml')])
    entries['ppt/slides/_rels/slide1.xml.rels']=relationships([('rId100','slideLayout','../slideLayouts/slideLayout1.xml')])
    entries['ppt/slideLayouts/_rels/slideLayout1.xml.rels']=relationships([('rId1','slideMaster','../slideMasters/slideMaster1.xml')])
    entries['ppt/slideMasters/_rels/slideMaster1.xml.rels']=relationships([('rId1','slideLayout','../slideLayouts/slideLayout1.xml'),('rId2','theme','../theme/theme1.xml')])
    types=root(CT,'Types')
    add(types,CT,'Default',Extension='rels',ContentType='application/vnd.openxmlformats-package.relationships+xml')
    add(types,CT,'Default',Extension='xml',ContentType='application/xml')
    # Writer appends PNG declarations using a literal </Types> replacement.
    # Declare PNG up front so XML namespace-prefix serialization cannot defeat it.
    add(types,CT,'Default',Extension='png',ContentType='image/png')
    for part,kind in (('presentation','presentation.main'),('slides/slide1','slide'),('slideMasters/slideMaster1','slideMaster'),('slideLayouts/slideLayout1','slideLayout'),('presProps','presProps')):
        add(types,CT,'Override',PartName='/ppt/'+part+'.xml',ContentType='application/vnd.openxmlformats-officedocument.presentationml.'+kind+'+xml')
    add(types,CT,'Override',PartName='/ppt/theme/theme1.xml',ContentType='application/vnd.openxmlformats-officedocument.theme+xml')
    entries['[Content_Types].xml']=xml(types)
    return entries
def write_package(path,entries):
    if path.exists():raise FileExistsError(path)
    path.parent.mkdir(parents=True,exist_ok=True)
    with zipfile.ZipFile(path,'x',compression=zipfile.ZIP_DEFLATED) as z:
        for name,data in sorted(entries.items()):
            i=zipfile.ZipInfo(name,(1980,1,1,0,0,0));i.compress_type=zipfile.ZIP_DEFLATED;z.writestr(i,data)
if __name__=='__main__':
    target=Path(__file__).parent/'generated_blank_v4.pptx';write_package(target,generate())
    print(json.dumps({'file':target.name,'sha256':hashlib.sha256(target.read_bytes()).hexdigest(),'generated_parts':len(generate()),'copied_template_bytes':0,'embedded_fonts_images':0}))
