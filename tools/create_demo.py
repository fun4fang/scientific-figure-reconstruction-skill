"""Original synthetic fixture; known proposal, not image-only authoring evidence."""
from pathlib import Path
import json
from PIL import Image, ImageDraw, ImageFont

root=Path(__file__).resolve().parent.parent
folder=root/'examples'; folder.mkdir(exist_ok=True)
image=Image.new('RGB',(600,260),'white'); draw=ImageDraw.Draw(image)
# Font programs remain installed runtime prerequisites and are not copied.
font=ImageFont.truetype('arial.ttf',22)
style=dict(font_family='Arial',font_size_px=22,font_weight='normal',text_color='#16324F',
    alignment='center',fill='#EAF2F8',stroke='#16324F',stroke_width_px=2)
nodes=[]
for identity,left,label in [('acquire',20,'Acquire'),('validate',220,'Validate'),('report',420,'Report')]:
    box=dict(x=left,y=90,width=160,height=80)
    draw.rectangle((left,90,left+160,170),fill=style['fill'],outline=style['stroke'],width=2)
    draw.text((left+80,130),label,font=font,fill=style['text_color'],anchor='mm')
    nodes.append(dict(id=identity,kind='rectangle',bbox=box,text_bbox=dict(x=left+10,y=110,width=140,height=40),
        content=label,role='node-label',parent=None,style=style,confidence='HIGH',evidence='Original program-generated demo geometry'))
connectors=[]
for index,(source,target,left,right) in enumerate([('acquire','validate',180,220),('validate','report',380,420)]):
    draw.line((left,130,right,130),fill='#16324F',width=2)
    draw.polygon(((right,130),(right-8,126),(right-8,134)),fill='#16324F')
    connectors.append(dict(id='flow-'+str(index+1),source=source,target=target,source_site='right_center',target_site='left_center',
        routing='straight',points=[dict(x=left,y=130),dict(x=right,y=130)],stroke='#16324F',stroke_width_px=2,
        confidence='HIGH',evidence='Original synthetic directed relation'))
proposal=dict(version='visual-reauthoring-v0.1',width=600,height=260,background='#FFFFFF',nodes=nodes,connectors=connectors,
    preserve_regions=[],uncertainties=[],content_inventory=['Acquire','Validate','Report','Acquire to Validate','Validate to Report'],
    all_visible_content_accounted_for=True,review_notes='Hand-specified synthetic fixture; no vision model or generalization result')
for name in ('workflow.png','workflow.jpg','proposal.json'):
    if (folder/name).exists(): raise SystemExit('Refusing overwrite: '+name)
image.save(folder/'workflow.png')
image.save(folder/'workflow.jpg',quality=95,subsampling=0)
(folder/'proposal.json').write_text(json.dumps(proposal,indent=2)+'\n',encoding='utf-8')
print('Original PNG/JPEG and known synthetic proposal created')
